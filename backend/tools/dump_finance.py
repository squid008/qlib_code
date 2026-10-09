# -*- coding: utf-8 -*-
"""把米筐 rqalpha 的 **pit 财报表 + 总市值** 转成 qlib 基本面字段 bin（`fin_*`）。

用法：
    python tools/dump_finance.py                     # 全量 dump（5567 只 / 9 个字段）
    python tools/dump_finance.py --limit 20          # 冒烟：只处理前 N 只
    python tools/dump_finance.py --codes sz300073,sh600519   # 只处理指定股票
    python tools/dump_finance.py --verify sz300073,sh600519  # 只打印核对值，**不写盘**
    python tools/dump_finance.py --force             # 已存在也覆盖重写
    python tools/dump_finance.py --pit-dir E:/rq/finance/pit --mc E:/rq/others/market-cap/market_cap.h5

源（米筐 rqalpha bundle，均为本地文件，无需 rqdatac 账号）：
- `E:/rq/finance/pit/{code}.h5`：**point-in-time 财报**。每个 h5 的结构
  `quarter`(报告期，如 2026q2) / `info_date`(公告日) / `if_adjusted`(0=首次披露,1=追溯调整后)
  / `rice_create_tm` / `fields`(394 个字段的并列数组)。**同一个报告期会有多行**（每次后续定期报告
  都会把同期数字作为可比期再披露一次）⇒ 「某日能看到的最新数字」= info_date ≤ 该日 的最后一行 ✓。
- `E:/rq/others/market-cap/market_cap.h5`：日频总市值（元）。

产物：`{qlib_dir}/features/{code}/{field}.day.bin`（header=起始日历下标 + float32，与其它字段同格式）
      + `{qlib_dir}/features/_finance_meta.json`（口径戳，便于事后核对是哪个口径、什么时候 dump 的）。

## 口径（★ 全部按**公告日**对齐 ⇒ 无未来函数）
对某个交易日 T：只用 `info_date <= T` 的财报行（同一报告期取 info_date 最新的一行）
⇒ T 日看到的永远是"当时已经公告过"的数字；财报被追溯调整时，调整后的行 info_date 更晚，
也只有在它的公告日之后才生效 ✓（**修改期**同样处理 ✓）。

| q | 字段 | 单位 | 口径 |
|---|------|------|------|
| 1 | fin_pe_ttm      | 倍 | 当日总市值 / 最新已披露**归母净利润TTM**（TTM=米筐滚动四季；亏损为负值，同益盟） |
| 2 | fin_pb          | 倍 | 当日总市值 / 最新已披露**归母所有者权益**（净资产为负 ⇒ NaN） |
| 3 | fin_rev_yoy     | %  | 报告期**营业总收入** / 去年同期同报告期 - 1（累计同比，同益盟"营业收入增长率"） |
| 4 | fin_np_yoy      | %  | 报告期**归母净利润** / 去年同期同报告期 - 1（累计同比） |
| 5 | fin_gross_margin| %  | 报告期**毛利 / 营业总收入**（累计；金融股无毛利 ⇒ NaN） |
| 6 | fin_roe         | %  | 报告期**加权平均净资产收益率**（米筐 return_on_equity_weighted_average，累计） |
| 7 | fin_roa         | %  | 报告期**净利润 / 期末总资产**（累计；未用平均总资产，见下方"口径差异"） |
| 8 | fin_eps         | 元 | 报告期**基本每股收益**（累计） |
| 9 | fin_op_yoy      | %  | 报告期**营业利润** / 去年同期同报告期 - 1（累计同比；益盟同列指标） |
| 10 | fin_fcf        | 元 | **自由现金流TTM** = 经营现金流净额TTM − 资本开支TTM（CAPEX=购建固定资产、无形资产和其他长期资产支付的现金）；TTM = 累计(Q)+累计(上年年报)−累计(上年同期Q) |

注意事项：
- 3~9 是**报告期口径**：日频序列只在**公告日**跳变（公告日前向填充），与益盟盘口显示一致 ✓。
- 1/2 是**日频**：用当日市值 ÷ 最新已披露的财务数字 ⇒ 每天都会变（与益盟"整体量TTM"一致 ✓）。
- 与益盟可能存在的差异（可接受，已在公式手册写明）：① 益盟 ROA 可能用平均总资产；
  ② 益盟"市盈率"口径可能是整体法（含少数股东）而本表用归母；③ 米筐 pit 是**快照**（本机为
  2026-08 的 bundle），报告期覆盖到 2026q2，更晚的公告需要重新下载 bundle 后重跑本脚本。
- 幂等：目标 .bin 已存在时默认跳过（`--force` 覆盖重写）。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import h5py
import numpy as np
import pandas as pd

# 输出字段：顺序 = FINANCE(q) 的 q（1..9），与公式手册、codegen 的表必须一致
FIN_FIELDS = [
    ("fin_pe_ttm", "倍"),
    ("fin_pb", "倍"),
    ("fin_rev_yoy", "%"),
    ("fin_np_yoy", "%"),
    ("fin_gross_margin", "%"),
    ("fin_roe", "%"),
    ("fin_roa", "%"),
    ("fin_eps", "元"),
    ("fin_op_yoy", "%"),
    # ★ 2026-10-09 追加（用户要求「加个自由现金流的指标」）：自由现金流TTM（元）
    #   = 经营活动现金流净额TTM − 资本开支TTM（CAPEX = "购建固定资产、无形资产和其他长期资产支付的现金"）
    ("fin_fcf", "元"),
]

# pit h5 里需要用到的原始字段（其余 380 多个不读）
NEED_PIT = [
    "revenue", "gross_profit", "net_profit", "net_profit_parent_company",
    "profit_from_operation", "net_profitTTM", "np_parent_company_ownersTTM",
    "equity_parent_company", "total_equity", "total_assets",
    "return_on_equity_weighted_average", "basic_earnings_per_share",
    # 自由现金流用（v1.20.81）：经营现金流净额 + 购建固定资产等支付的现金（均为**年内累计**）
    "cash_flow_from_operating_activities", "cash_paid_for_asset",
]

FIN_META_NAME = "_finance_meta.json"
# 口径语义版本：改口径（字段含义 / 对齐方式 / 派生公式）必须递增并重跑 dump
FINANCE_SEMANTICS = "1.0.0"


def to_qlib_code(code: str) -> str:
    """rqalpha 代码（000001.XSHE / 600000.XSHG）→ qlib 小写（sz000001 / sh600000）。

    ⚠ 与 tools/dump_market_cap.py / dump_moneyflow.py 里的同名函数保持一致（命名口径唯一）。
    """
    upper = code.strip().upper()
    digits = "".join(ch for ch in upper if ch.isdigit())
    if upper.endswith("XSHG"):
        return f"sh{digits}"
    if upper.endswith("XSHE"):
        return f"sz{digits}"
    return upper.lower()


def qlib_code_to_rq(code: str) -> str:
    """方向相反：sz300073 → 300073.XSHE（--verify 用）。"""
    c = code.strip().lower()
    digits = "".join(ch for ch in c if ch.isdigit())
    if c.startswith("sh"):
        return f"{digits}.XSHG"
    if c.startswith("sz"):
        return f"{digits}.XSHE"
    if c.startswith("bj"):
        return f"{digits}.XBSE"
    return code


# ----------------------------------------------------------------------------
# 日历 / 市值
# ----------------------------------------------------------------------------
def load_calendar(qlib_dir: Path) -> Tuple[List[str], np.ndarray]:
    """交易日历（字符串列表 + datetime64[D] 整数数组，供 searchsorted）。"""
    cal_path = qlib_dir / "calendars" / "day.txt"
    if not cal_path.exists():
        raise SystemExit(f"qlib 日历不存在：{cal_path}（确认 --qlib-dir 正确）")
    cal = pd.read_csv(cal_path, header=None)[0].astype(str).tolist()
    cal_int = np.asarray(cal, dtype="datetime64[D]").astype(np.int64)
    return cal, cal_int


def load_market_cap(mc_path: Path, cal_int: np.ndarray) -> Dict[str, Tuple[int, np.ndarray]]:
    """读总市值 h5 → {qlib代码: (起始日历下标, 值数组)}。

    源与 dump_market_cap.py 完全相同（rqalpha others 的 pandas HDF5，MultiIndex 展平）：
    行 → (股票 code 下标 axis1_label0, 时间戳下标 axis1_label1)，值取第 0 列。
    """
    if not mc_path.exists():
        raise SystemExit(f"市值源不存在：{mc_path}")
    with h5py.File(mc_path, "r") as f:
        g = f["data"]
        codes = np.array([x.decode() if isinstance(x, bytes) else str(x) for x in g["axis1_level0"][:]])
        ts = g["axis1_level1"][:]
        lab0 = g["axis1_label0"][:]
        lab1 = g["axis1_label1"][:]
        blk = g["block0_values"]
        vals = blk[:, 0] if (blk.ndim == 2 and blk.shape[1] == 1) else blk
    dates_int = pd.to_datetime(ts).normalize().values.astype("datetime64[D]").astype(np.int64)

    # 行按 (code, date) 有序（pandas MultiIndex 展平）⇒ 用 searchsorted 切分；否则退回 argsort
    order = None
    if not np.all(lab0[:-1] <= lab0[1:]):
        order = np.lexsort((lab1, lab0))
        lab0 = lab0[order]
        lab1 = lab1[order]
        vals = vals[order]
    bounds = np.searchsorted(lab0, np.arange(len(codes) + 1))

    out: Dict[str, Tuple[int, np.ndarray]] = {}
    for i, code in enumerate(codes):
        lo, hi = int(bounds[i]), int(bounds[i + 1])
        if hi <= lo:
            continue
        d_int = dates_int[lab1[lo:hi]]
        v = np.asarray(vals[lo:hi], dtype=np.float64)
        ok = np.isfinite(v)
        if not ok.any():
            continue
        # 落到日历上（源日期可能含日历外日期 ⇒ 用 searchsorted 定位，缺失日为 NaN）
        pos = np.searchsorted(cal_int, d_int)
        pos = np.clip(pos, 0, len(cal_int) - 1)
        pos = np.where(cal_int[pos] == d_int, pos, -1)  # 不在日历内的丢掉
        keep = pos >= 0
        if not keep.any():
            continue
        pos, v = pos[keep], v[keep]
        # 同年同月同日重复（源不应有）：后出现者覆盖
        uniq_pos, uniq_idx = np.unique(pos, return_index=True)
        arr = np.full(len(cal_int), np.nan, dtype=np.float64)
        arr[uniq_pos] = v[uniq_idx]
        first = int(np.argmax(np.isfinite(arr)))
        last = len(arr) - 1 - int(np.argmax(np.isfinite(arr[::-1])))
        out[to_qlib_code(code)] = (first, arr[first:last + 1])
    return out


# ----------------------------------------------------------------------------
# pit 财报
# ----------------------------------------------------------------------------
class PitTable:
    """某只股票的 pit 财报表：按公告日去重排序后的行 + 按报告期的索引。"""

    def __init__(self, path: Path):
        with h5py.File(path, "r") as f:
            if "quarter" not in f or "fields" not in f:
                raise ValueError(f"不是 pit 财报表：{path}")
            q = [_dec(x) for x in f["quarter"][:]]
            d = [_dec(x) for x in f["info_date"][:]]
            adj = np.asarray(f["if_adjusted"][:])
            g = f["fields"]
            cols = {}
            for name in NEED_PIT:
                if name in g:
                    cols[name] = np.asarray(g[name][:], dtype=np.float64)
        rows = []
        for i in range(len(q)):
            if not q[i] or not d[i]:
                continue
            rows.append((d[i], q[i], int(adj[i]), i))
        # 去重：同一 (报告期, 公告日) 只留 if_adjusted 最大的一行（追溯调整后的为准）
        best: Dict[Tuple[str, str], Tuple[int, int]] = {}
        for dstr, qstr, a, i in rows:
            key = (qstr, dstr)
            if key not in best or a > best[key][0]:
                best[key] = (a, i)
        idxs = [i for _a, i in best.values()]
        idxs.sort(key=lambda i: (d[i], q[i]))
        self.info = np.asarray([d[i] for i in idxs], dtype="datetime64[D]").astype(np.int64)
        self.info_str = [d[i] for i in idxs]
        self.quarter = [q[i] for i in idxs]
        self.vals: Dict[str, np.ndarray] = {
            k: np.asarray([v[i] for i in idxs], dtype=np.float64) for k, v in cols.items()
        }
        # 报告期 → (公告日 int 升序, 值数组)；用于"去年同期"的 PIT 取值
        self.by_quarter: Dict[str, Dict[str, Tuple[np.ndarray, np.ndarray]]] = {}
        for k, v in self.vals.items():
            d_ = {}
            for qstr in set(self.quarter):
                m = np.asarray([x == qstr for x in self.quarter])
                d_[qstr] = (self.info[m], v[m])
            self.by_quarter[k] = d_

    def is_empty(self) -> bool:
        return len(self.info) == 0

    def val_at(self, field: str, quarter: str, when: int) -> float:
        """field 在报告期 quarter 上、公告日 ≤ when 的**最后一行**值（PIT 取值）。"""
        ent = self.by_quarter.get(field, {}).get(quarter)
        if ent is None:
            return float("nan")
        ii, vv = ent
        k = int(np.searchsorted(ii, when, side="right")) - 1
        if k < 0:
            return float("nan")
        return float(vv[k])


def prev_year_quarter(q: str) -> Optional[str]:
    """'2026q2' → '2025q2'。"""
    try:
        y = int(q[:4])
        return f"{y - 1}{q[4:]}"
    except Exception:
        return None


def _dec(x) -> str:
    return x.decode() if isinstance(x, bytes) else str(x)


# ----------------------------------------------------------------------------
# 派生序列
# ----------------------------------------------------------------------------
def build_report_metrics(tab: PitTable) -> Dict[str, np.ndarray]:
    """报告期口径指标（行级数组，与 tab.info 对齐）—— 增长率/毛利率/ROE/ROA/EPS。"""
    n = len(tab.info)
    out = {k: np.full(n, np.nan) for k, _u in FIN_FIELDS}
    rev = tab.vals.get("revenue")
    gp = tab.vals.get("gross_profit")
    np_all = tab.vals.get("net_profit")
    np_parent = tab.vals.get("net_profit_parent_company")
    op = tab.vals.get("profit_from_operation")
    ta = tab.vals.get("total_assets")
    roe = tab.vals.get("return_on_equity_weighted_average")
    eps = tab.vals.get("basic_earnings_per_share")
    for i in range(n):
        q = tab.quarter[i]
        pq = prev_year_quarter(q)
        t = int(tab.info[i])
        if rev is not None and np.isfinite(rev[i]) and pq:
            base = tab.val_at("revenue", pq, t)
            if np.isfinite(base) and base > 0:
                out["fin_rev_yoy"][i] = (rev[i] / base - 1.0) * 100.0
        if np_parent is not None and np.isfinite(np_parent[i]) and pq:
            base = tab.val_at("net_profit_parent_company", pq, t)
            if np.isfinite(base) and base > 0:
                out["fin_np_yoy"][i] = (np_parent[i] / base - 1.0) * 100.0
        if op is not None and np.isfinite(op[i]) and pq:
            base = tab.val_at("profit_from_operation", pq, t)
            if np.isfinite(base) and base > 0:
                out["fin_op_yoy"][i] = (op[i] / base - 1.0) * 100.0
        if gp is not None and rev is not None and np.isfinite(gp[i]) and np.isfinite(rev[i]) and rev[i] > 0:
            out["fin_gross_margin"][i] = gp[i] / rev[i] * 100.0
        if roe is not None and np.isfinite(roe[i]):
            out["fin_roe"][i] = roe[i]
        if np_all is not None and ta is not None and np.isfinite(np_all[i]) and np.isfinite(ta[i]) and ta[i] > 0:
            out["fin_roa"][i] = np_all[i] / ta[i] * 100.0
        if eps is not None and np.isfinite(eps[i]):
            out["fin_eps"][i] = eps[i]
        # ---- 自由现金流TTM（元，v1.20.81）----
        #   FCF_ttm = 经营现金流净额TTM − 资本开支TTM，两者都用**累计 → TTM** 的标准式：
        #       TTM(Q) = 累计(Q) + 累计(上年年报) − 累计(上年同期 Q)
        #   （对 Q4 该式自动退化为"累计(Q)" ✓）—— 四个取值都按 `info_date <= t` 取 ✓（PIT 安全 ✓）
        ocf_c = tab.val_at("cash_flow_from_operating_activities", q, t)
        capex_c = tab.val_at("cash_paid_for_asset", q, t)
        fy_prev = "%dq4" % (int(q[:4]) - 1)
        ocf_f = tab.val_at("cash_flow_from_operating_activities", fy_prev, t)
        capex_f = tab.val_at("cash_paid_for_asset", fy_prev, t)
        ocf_p = tab.val_at("cash_flow_from_operating_activities", pq, t) if pq else float("nan")
        capex_p = tab.val_at("cash_paid_for_asset", pq, t) if pq else float("nan")
        _vals = (ocf_c, capex_c, ocf_f, capex_f, ocf_p, capex_p)
        if all(np.isfinite(v) for v in _vals):
            out["fin_fcf"][i] = (ocf_c - capex_c) + (ocf_f - capex_f) - (ocf_p - capex_p)
    return out


def expand_daily(tab: PitTable, row_vals: np.ndarray, cal_int: np.ndarray) -> np.ndarray:
    """行级（按公告日的事件）数组 → 日频（公告日前向填充；公告前为 NaN）。"""
    n = len(cal_int)
    out = np.full(n, np.nan, dtype=np.float64)
    if tab.is_empty() or row_vals.size == 0:
        return out
    idx = np.searchsorted(tab.info, cal_int, side="right") - 1
    ok = idx >= 0
    out[ok] = row_vals[idx[ok]]
    return out


def write_bin(path: Path, cal_pos: int, values: np.ndarray) -> None:
    """qlib FileFeatureStorage 格式：header(float32 起始日历下标) + float32 数据。"""
    out = np.hstack([np.array([cal_pos], dtype=np.float64), values]).astype("<f")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.tofile(str(path))


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def dump_one(code_file: Path, mc: Dict[str, Tuple[int, np.ndarray]], cal_int: np.ndarray,
             qlib_dir: Path, force: bool, verify: bool) -> Dict[str, Optional[float]]:
    """处理一只股票；返回 {字段: 最新有效值}（verify 用）或 {}。"""
    rq_code = code_file.stem
    qcode = to_qlib_code(rq_code)
    tab = PitTable(code_file)
    if tab.is_empty():
        return {}
    report = build_report_metrics(tab)

    # 报告期口径 → 日频
    daily: Dict[str, np.ndarray] = {
        k: expand_daily(tab, report[k], cal_int) for k, _u in FIN_FIELDS
        if k not in ("fin_pe_ttm", "fin_pb")
    }

    # 市值（用于 PE/PB，日频）
    mc_daily = np.full(len(cal_int), np.nan)
    hit = mc.get(qcode)
    if hit is not None:
        start, arr = hit
        mc_daily[start:start + arr.size] = arr
    ttm = expand_daily(tab, tab.vals.get("np_parent_company_ownersTTM",
                                         np.full(len(tab.info), np.nan)), cal_int)
    eq = expand_daily(tab, tab.vals.get("equity_parent_company",
                                       np.full(len(tab.info), np.nan)), cal_int)
    pe = np.full(len(cal_int), np.nan)
    pb = np.full(len(cal_int), np.nan)
    m_ok = np.isfinite(mc_daily)
    t_ok = np.isfinite(ttm) & (ttm != 0.0)
    np.divide(mc_daily, ttm, out=pe, where=m_ok & t_ok)
    e_ok = np.isfinite(eq) & (eq > 0.0)
    np.divide(mc_daily, eq, out=pb, where=m_ok & e_ok)
    daily["fin_pe_ttm"] = pe
    daily["fin_pb"] = pb

    result: Dict[str, Optional[float]] = {}
    written = 0
    for field, _unit in FIN_FIELDS:
        arr = daily[field]
        finite = np.isfinite(arr)
        if not finite.any():
            continue
        last = int(np.where(finite)[0][-1])
        result[field] = round(float(arr[last]), 4)   # 供 --verify 核对 / 统计"有数据的字段数"
        if verify:
            continue
        bin_path = qlib_dir / "features" / qcode / f"{field}.day.bin"
        if bin_path.exists() and not force:
            continue
        first = int(np.where(finite)[0][0])
        write_bin(bin_path, first, arr[first:last + 1])
        written += 1
    result["_written"] = written
    if verify:
        # 附带报告期，便于核对"当前能看到的是哪一期"。
        # ⚠ 用**最后一个** info_date 最大的行（同一天可能公告多个报告期，如年报+一季报）
        #   —— `np.argmax` 返回**第一个**最大位置，会把"2025q4"当成本期 ✗（踩过一次）。
        last_i = len(tab.info) - 1 - int(np.argmax(tab.info[::-1]))
        result["_latest_quarter"] = tab.quarter[last_i]
        result["_latest_info_date"] = tab.info_str[last_i]
        result["_rows"] = len(tab.info)
    return result


def write_meta(qlib_dir: Path, stats: Dict) -> None:
    payload = dict(stats)
    payload["finance_semantics"] = FINANCE_SEMANTICS
    payload["written_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["fields"] = [{"q": i + 1, "field": f, "unit": u} for i, (f, u) in enumerate(FIN_FIELDS)]
    try:
        p = qlib_dir / "features" / FIN_META_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception as e:  # 只影响可发现性，不影响数据
        print(f"[warn] 写 {FIN_META_NAME} 失败：{e}", flush=True)


def _default_qlib_dir() -> str:
    env = os.environ.get("QLIB_PROVIDER_URI")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "cn_data")


def main():
    ap = argparse.ArgumentParser(description="米筐 pit 财报 + 市值 → qlib 基本面字段 bin（fin_*）")
    ap.add_argument("--pit-dir", default=r"E:\rq\finance\pit", help="pit 财报表目录")
    ap.add_argument("--mc", default=r"E:\rq\others\market-cap\market_cap.h5", help="总市值 h5")
    ap.add_argument("--qlib-dir", default=_default_qlib_dir(), help="qlib 数据目录")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟）")
    ap.add_argument("--codes", default="", help="只处理指定 qlib 代码（逗号分隔，如 sz300073,sh600519）")
    ap.add_argument("--verify", action="store_true", help="只打印核对值，不写盘")
    ap.add_argument("--force", action="store_true", help="已存在也覆盖重写")
    args = ap.parse_args()

    t0 = time.time()
    qlib_dir = Path(args.qlib_dir)
    cal, cal_int = load_calendar(qlib_dir)
    print(f"日历 {len(cal)} 天（{cal[0]} ~ {cal[-1]}）；qlib_dir={qlib_dir}", flush=True)

    if args.codes:
        files = [Path(args.pit_dir) / f"{qlib_code_to_rq(c)}.h5" for c in args.codes.split(",") if c.strip()]
        files = [p for p in files if p.exists()]
    else:
        files = sorted(Path(args.pit_dir).glob("*.h5"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        raise SystemExit("没有可处理的 pit 文件")
    print(f"待处理 {len(files)} 只股票", flush=True)

    if args.verify:
        mc = load_market_cap(Path(args.mc), cal_int)
        for p in files:
            print(f"\n=== {to_qlib_code(p.stem)} （{p.name}） ===", flush=True)
            res = dump_one(p, mc, cal_int, qlib_dir, force=False, verify=True)
            for k, v in res.items():
                if k.startswith("_"):
                    continue
                print(f"  {k:20s} {v}", flush=True)
            print(f"  {'(最近一期)':20s} {res.get('_latest_quarter')} @ {res.get('_latest_info_date')}"
                  f"（共 {res.get('_rows')} 行）", flush=True)
        print(f"\n核对完毕，用时 {time.time() - t0:.1f}s", flush=True)
        return

    print("读总市值 h5 ...", flush=True)
    mc = load_market_cap(Path(args.mc), cal_int)
    print(f"市值覆盖 {len(mc)} 只（{time.time() - t0:.1f}s）", flush=True)

    written = skipped = empty = 0
    n_bins = 0
    for i, p in enumerate(files, 1):
        try:
            res = dump_one(p, mc, cal_int, qlib_dir, force=args.force, verify=False)
        except Exception as e:
            print(f"[warn] {p.name} 处理失败：{type(e).__name__}: {e}", flush=True)
            empty += 1
            continue
        n_fields = sum(1 for k in res if not k.startswith("_"))
        if n_fields == 0:
            empty += 1
        else:
            written += 1
            n_bins += int(res.get("_written", 0))
            if int(res.get("_written", 0)) == 0:
                skipped += 1
        if i % 200 == 0 or i == len(files):
            el = time.time() - t0
            print(f"  进度 {i}/{len(files)}  有数据 {written} 只（其中本轮写盘 {n_bins} 个 bin）"
                  f" / 全部已存在跳过 {skipped} / 空 {empty}"
                  f"  {el:.0f}s  预计剩余 {el / i * (len(files) - i):.0f}s", flush=True)

    stats = {
        "source_pit_dir": str(args.pit_dir),
        "source_market_cap": str(args.mc),
        "calendar_last_day": cal[-1],
        "stocks_total": len(files),
        "stocks_written": written,
        "stocks_empty": empty,
        "bin_files": n_bins,
        "force": bool(args.force),
    }
    write_meta(qlib_dir, stats)
    print(f"\n完成：处理 {len(files)} 只，有数据 {written}，空 {empty}，写入 bin {n_bins} 个，"
          f"用时 {time.time() - t0:.0f}s", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)


if __name__ == "__main__":
    main()
