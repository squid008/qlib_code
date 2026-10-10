# -*- coding: utf-8 -*-
"""tushare 财务四表 → 与 `dump_finance.py` **同口径**的 qlib 基本面字段 bin（`fin_*`）。

为什么有它（用户 2026-10-09）：米筐 rqalpha bundle 是**本机快照**（财务只到 2026 中报 ✗），
tushare 能持续更新，且同样带 `ann_date`(公告日) + `update_flag`(更正标记) ⇒ 一样能重建
PIT / 无未来函数 ✓。先对拍（`ai_test/probe_crosscheck_fin.py` ✓），核对通过后新数据走本脚本 ✓。

用法：
    python tools/dump_tushare_finance.py --verify sz300073,sh600519   # 只核对，不写盘
    python tools/dump_tushare_finance.py --limit 20 / --codes sz300073 / --force
    python tools/dump_tushare_finance.py --mc-tushare                 # 市值超出本地轮次时用 tushare 续
凭证：仓库根 `config.local.json` 的 `tushare.token`（**不入库** ✓；可用 `TUSHARE_TOKEN` 覆盖 ✓）。
限速：3000 积分档 200 次/分钟 ⇒ **串行** + `--rate`（默认 0.31s/次 ≈ 193/分 ✓）⇒ 全市场 4 表
      ≈ 5573×4 次 ≈ **110 分钟**（一次性；缓存后可断点续跑 ✓）。

## 口径（★ 与 dump_finance.py 逐项一致，只换数据源、不换公式）
| q | 字段 | tushare 来源 |
|---|------|--------------|
| 1 | fin_pe_ttm | 当日总市值 ÷ 最新已披露**归母净利润TTM**（TTM=累计(Q)+累计(上年年报)−累计(上年同期Q)） |
| 2 | fin_pb | 当日总市值 ÷ `total_hldr_eqy_exc_min_int`（归母权益） |
| 3 | fin_rev_yoy | `revenue` 累计同比 |
| 4 | fin_np_yoy | `n_income_attr_p`（归母净利）累计同比 |
| 5 | fin_gross_margin | (revenue − oper_cost) ÷ revenue |
| 6 | fin_roe | `fina_indicator.roe_waa`（加权平均 ROE，与米筐同名口径 ✓） |
| 7 | fin_roa | `n_income`（净利润，含少数股东）÷ `total_assets` |
| 8 | fin_eps | `income.basic_eps`（基本每股收益，累计） |
| 9 | fin_op_yoy | `operate_profit`（营业利润）累计同比 |
| 10 | fin_fcf | (经营现金流净额 − 资本开支) 的 TTM，资本开支=`c_pay_acq_const_fiolta` |

⚠ 单位：tushare 财务金额单位=**元**（与米筐一致 ✓）；`daily_basic.total_mv` 是**万元** ⇒ ×1e4 ✓。
⚠ 只取 `report_type='1'`（合并报表 ✓）；同报告期多行（原始 + 更正）= **PIT 的命门**：
  「T 日能看到的数字」= `ann_date <= T` 的**最后一行**（同日多行取 `update_flag` 大的 ✓）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import (                                    # noqa: E402  口径唯一来源 ✓
    FIN_FIELDS, FIN_META_NAME, FINANCE_SEMANTICS, load_calendar, write_bin,
)

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "tushare_cache" / "fin"
MC_TUSHARE_CACHE = ROOT / "data" / "tushare_cache" / "total_mv.json"

# ROE 毛刺：记录清单（dump 完写 features/_finance_warnings.json ✓）
ROE_SUSPECTS: List[dict] = []
# `--roe-guard`：把毛刺 ROE **替换**成自算值（默认 False = 只记录、不动数据 ✓）
ROE_GUARD = False
# 「本地缓存 JSON 损坏、已删除并重新下载」的股票清单（进度行 / 收尾统计 ✓）
CORRUPT_CACHE: List[str] = []
# 市值（`daily_basic.total_mv`）续更的**下限日**（int YYYYMMDD ✓）—— 由 `--mc-from` 设定 ✓，
# 见 `load_market_cap` 的注释：不设下限会从 2000 年起逐日调 API ✗（每只 ~6484 次 ✗✗）
MC_FROM = 20260801
# 本轮真正发生的 `daily_basic` 调用次数（进度行可见 ✓；正常应恒为 0 ✓）
MC_FETCHED = 0


def _d8_offset(d8: int) -> int:
    """`20260824` → **datetime64[D] 的天数偏移**（与 `load_calendar` 的 `cal_int` 同域 ✓）。

    ⚠ 必须换域再比（踩过 ✓）：`cal_int` 里存的是 20726 这种天数偏移，拿 YYYYMMDD 直接
      `searchsorted` 会恒返回末尾 ⇒ 循环不执行、静默不动数据 ✗。
    """
    s = str(int(d8))
    return int(np.datetime64("%s-%s-%s" % (s[:4], s[4:6], s[6:8]), "D").astype(np.int64))


def _default_mc_from() -> int:
    """默认下限 = tushare 日线缓存里**最早**的一天（= 2026-08-24 ✓，也正是预取覆盖的起点 ✓）；
    没有日线缓存时退回 20260801 ✓。"""
    ds = sorted(p.stem for p in (ROOT / "data" / "tushare_cache" / "daily").glob("*.json")
                if len(p.stem) == 8 and p.stem.isdigit())
    return int(ds[0]) if ds else 20260801

# tushare 表 → 我们要的字段（声明 fields 参数 ⇒ 传输与缓存都小 ✓）
TABLES = {
    "income": "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
              "total_revenue,revenue,oper_cost,operate_profit,n_income,n_income_attr_p,basic_eps",
    "balancesheet": "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
                    "total_assets,total_hldr_eqy_exc_min_int",
    "cashflow": "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
                "n_cashflow_act,c_pay_acq_const_fiolta",
    "fina_indicator": "ts_code,ann_date,end_date,roe_waa",
}
# 报告期量（累计）字段 → 我们内部名
PIT_FIELDS = {
    # ⚠ 「营业收入」用 tushare 的 **total_revenue（营业总收入）** ✓，不能用 `revenue`（营业收入）✗：
    #   两者对多数公司相等，但凡有「利息收入/已赚保费/手续费及佣金收入」的公司就不同 ✗ ——
    #   实测茅台 systematic 差 **1.86%**（它有财务公司利息收入 ✓），米筐 pit 的 `revenue` = 营业总收入 ✓。
    "revenue": ("income", "total_revenue"),
    "oper_cost": ("income", "oper_cost"),
    "operate_profit": ("income", "operate_profit"),
    "net_profit": ("income", "n_income"),
    "net_profit_parent_company": ("income", "n_income_attr_p"),
    "basic_earnings_per_share": ("income", "basic_eps"),
    "total_assets": ("balancesheet", "total_assets"),
    "equity_parent_company": ("balancesheet", "total_hldr_eqy_exc_min_int"),
    "ocf": ("cashflow", "n_cashflow_act"),
    "capex": ("cashflow", "c_pay_acq_const_fiolta"),
    "roe_waa": ("fina_indicator", "roe_waa"),
}


def atomic_json_dump(path: Path, obj) -> None:
    """**原子**写 JSON（tmp + `os.replace` ✓）。

    ⚠ 为什么不能直接 `open(w)`（2026-10-09 血泪 ✓）：
      `data/tushare_cache/total_mv.json` 被写到 **3421194 字节**时进程被杀 ⇒ 文件停在
      `"20260915|601825.SH"`（冒号都没写完 ✗）⇒ 之后**每次** `_mc_cache()` 都抛
      `JSONDecodeError: Expecting ':' delimiter ... char 3421194` ✗ ⇒ `dump_tushare_finance.py
      --mc-tushare` 每只股票都在第一步就失败、4 秒跑完、写盘 0 个 bin ✗（很难看出是缓存的问题）。
      `os.replace` 在同一文件系统上是原子的 ⇒ 要么旧内容、要么新内容，**绝不会半截** ✓。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def read_json_tolerant(path: Path, what: str = "缓存") -> Tuple[Optional[dict], bool]:
    """读 JSON ⇒ `(对象, 是否损坏)`。损坏时**逐对抢救**（保住完整部分 ✓），抢救不到返回 `(None, True)`。

    抢救原理：文件被截断只可能停在**尾部** ✓ ⇒ 用 `JSONDecoder.raw_decode` 从 `{` 后按
    (key, value) 一对一对推进，遇到解析不动的地方就停 —— 前面已解析的键值对全部是好的 ✓。
    """
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text), False
    except ValueError:
        pass
    dec = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "{":
        print("  ⚠ %s 损坏且无法抢救（不是 JSON 对象）⇒ 整份丢弃" % what, flush=True)
        return None, True
    i += 1
    out: dict = {}
    while True:
        while i < n and (text[i].isspace() or text[i] == ","):
            i += 1
        if i >= n or text[i] == "}":
            break
        try:
            key, i = dec.raw_decode(text, i)
        except ValueError:
            break
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != ":":
            break
        i += 1
        while i < n and text[i].isspace():
            i += 1
        try:
            val, i = dec.raw_decode(text, i)
        except ValueError:
            break
        out[key] = val
    print("  ⚠ %s 损坏（JSON 截断）⇒ 抢救出 %d 条完整记录、尾部丢弃" % (what, len(out)), flush=True)
    return (out or None), True


def token() -> str:
    t = os.environ.get("TUSHARE_TOKEN")
    if t:
        return t.strip()
    p = ROOT / "config.local.json"
    if not p.exists():
        raise SystemExit("找不到 config.local.json（需要 tushare.token）")
    with open(p, encoding="utf-8") as f:
        return json.load(f)["tushare"]["token"]


def ts_call(api: str, params: dict, fields: str = "", tok: str = "", retry: int = 3) -> dict:
    """调一次 tushare HTTP API；频率超限自动退避重试 ✓（权限不足直接抛清楚 ✗）。"""
    tok = tok or token()
    body = json.dumps({"api_name": api, "token": tok, "params": params, "fields": fields},
                      ensure_ascii=False).encode("utf-8")
    last = {}
    for _ in range(retry):
        req = urllib.request.Request("https://api.tushare.pro", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                last = json.loads(r.read().decode("utf-8"))
        except Exception as e:                                # noqa: BLE001
            last = {"code": -1, "msg": "%s: %s" % (type(e).__name__, e)}
        code = last.get("code")
        if code == 0:
            return last
        if code in (40203, 40204):                            # 无权限 / 积分不足
            raise SystemExit("tushare 无权限调 %s：%s" % (api, last.get("msg")))
        if code in (-2001, 40202):                            # 频率超限
            time.sleep(20)
            continue
        break
    raise RuntimeError("tushare 调用失败 %s: %s" % (api, last.get("msg")))


def to_qlib_code(ts_code: str) -> str:
    """000001.SZ → sz000001（与 dump_finance.py 的命名口径一致 ✓）。"""
    c = ts_code.strip().upper()
    num, _, suf = c.partition(".")
    pre = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf)
    return (pre + num) if pre else c.lower()


def to_ts_code(qlib_code: str) -> str:
    c = qlib_code.strip().lower()
    num = "".join(ch for ch in c if ch.isdigit())
    return "%s.%s" % (num, {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(c[:2], "SZ"))


def d8(ann: str) -> Optional[str]:
    """tushare 的 `YYYYMMDD` → `YYYY-MM-DD` ✓。

    ⚠ 必须规范化（踩过 ✓）：`np.datetime64('20260425','D')` **不是**解析成日期 ✗
      （实测得到 7399248751，既不是 day 也不是 ns ⇒ 后续 searchsorted 全部落空、取值恒 NaN ✗）；
      而 `np.datetime64('2026-04-25','D')` 才是规范写法 ✓（米筐 pit 的 info_date 也正好是这种格式 ✓）。
    """
    s = str(ann or "")
    if len(s) != 8 or not s.isdigit():
        return None
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])


def period_to_quarter(end_date: str) -> Optional[str]:
    """'20260630' → '2026q2'（只认 0331/0630/0930/1231 ✓）。"""
    if not end_date or len(end_date) != 8:
        return None
    mmdd = end_date[4:]
    q = {"0331": "q1", "0630": "q2", "0930": "q3", "1231": "q4"}.get(mmdd)
    return "%sq%s" % (end_date[:4], q[1:]) if q else None


def prev_year_quarter(q: str) -> Optional[str]:
    try:
        return "%d%s" % (int(q[:4]) - 1, q[4:])
    except Exception:                                         # noqa: BLE001
        return None


class TsPit:
    """tushare 财务 → 与米筐 `PitTable` **同构**的 PIT 表（报告期 + 公告日 + 值 ✓）。"""

    def __init__(self, data: Dict[str, List[dict]]):
        rows: List[Tuple[str, str, Dict[str, float]]] = []     # (ann_date, quarter, 值)
        for fld, (table, col) in PIT_FIELDS.items():
            for r in data.get(table) or []:
                if str(r.get("report_type") or "1") != "1":     # 只合并报表 ✓
                    continue
                q = period_to_quarter(str(r.get("end_date") or ""))
                ann = d8(r.get("ann_date") or r.get("f_ann_date"))   # ⇒ 'YYYY-MM-DD' ✓（见 d8 注释 ✗）
                if not q or not ann:
                    continue
                v = r.get(col)
                rows.append((fld, ann, q, v, int(r.get("update_flag") or 0)))
        # (字段, 报告期, 公告日) 去重：同日多行取 update_flag 大者（更正后为准 ✓）
        best: Dict[Tuple[str, str, str], Tuple[int, float]] = {}
        for fld, ann, q, v, uf in rows:
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                v = float("nan")
            k = (fld, q, ann)
            if k not in best or uf >= best[k][0]:
                best[k] = (uf, float(v))
        self.by_quarter: Dict[str, Dict[str, Tuple[np.ndarray, np.ndarray]]] = {}
        for (fld, q, ann), (_uf, v) in best.items():
            self.by_quarter.setdefault(fld, {}).setdefault(q, ([], []))
            self.by_quarter[fld][q][0].append(ann)
            self.by_quarter[fld][q][1].append(v)
        for fld in self.by_quarter:
            for q in self.by_quarter[fld]:
                anns, vals = self.by_quarter[fld][q]
                order = np.argsort(anns)
                self.by_quarter[fld][q] = (np.asarray([anns[i] for i in order], dtype="datetime64[D]")
                                           .astype(np.int64),
                                           np.asarray([vals[i] for i in order], dtype=np.float64))
        # 全部报告期（升序）；报告期口径指标按它遍历 ✓
        self.quarter: List[str] = sorted({q for d in self.by_quarter.values() for q in d})

    def is_empty(self) -> bool:
        return not self.quarter

    def val_at(self, fld: str, quarter: str, when: int) -> float:
        """fld 在报告期 quarter 上、公告日 ≤ when 的最后一行（PIT ✓）。"""
        ent = self.by_quarter.get(fld, {}).get(quarter)
        if ent is None:
            return float("nan")
        anns, vals = ent
        k = int(np.searchsorted(anns, when, side="right")) - 1
        return float(vals[k]) if k >= 0 else float("nan")

    def ttm(self, fld: str, q: str, when: int) -> float:
        """累计 → TTM：累计(Q) + 累计(上年年报) − 累计(上年同期Q)（Q4 自动退化为累计(Q) ✓）。"""
        c = self.val_at(fld, q, when)
        pq = prev_year_quarter(q)
        fy = "%dq4" % (int(q[:4]) - 1) if q[:4].isdigit() else None
        if q.endswith("q4"):
            return c
        if not (pq and fy):
            return float("nan")
        f = self.val_at(fld, fy, when)
        p = self.val_at(fld, pq, when)
        if not (np.isfinite(c) and np.isfinite(f) and np.isfinite(p)):
            return float("nan")
        return c + f - p


def build_report_metrics(tab: TsPit, code: str = "") -> Dict[str, List[Tuple[str, str, float]]]:
    """报告期口径指标 → [(ann_date, quarter, value)]（与 dump_finance 的公式逐项一致 ✓）。

    ⚠ 顺带**记录 ROE 毛刺**（不改数据 ✓）：tushare 的 `fina_indicator.roe_waa` 偶尔给出明显
      不合常理的值 —— 实测 平安银行 2026q1 = **0.24**（同股上一期 2.8、下一期 5.22 ✗），
      而米筐给 2.83 ✓。这里用"报告值 vs 自算值(累计归母净利/期末归母权益)"做粗筛，
      偏离 >50% 就记进 `ROE_SUSPECTS` ⇒ dump 结束写进 `features/_finance_warnings.json`
      （**只报告、不替换** ✗ —— 换不换由人决定 ✓；用了 `--roe-guard` 才替换 ✓）。
    """
    out: Dict[str, List[Tuple[str, str, float]]] = {k: [] for k, _u in FIN_FIELDS}
    for q in tab.quarter:
        # 该报告期的"事件公告日" = income 的公告日（没有就跳过 ✓）
        ent = tab.by_quarter.get("revenue", {}).get(q)
        if ent is None:
            continue
        for a_int in ent[0]:
            ann = str(np.datetime64(int(a_int), "D"))
            t = int(a_int)
            pq = prev_year_quarter(q)
            rev = tab.val_at("revenue", q, t)
            gp = rev - tab.val_at("oper_cost", q, t) if np.isfinite(rev) else float("nan")
            np_parent = tab.val_at("net_profit_parent_company", q, t)
            np_all = tab.val_at("net_profit", q, t)
            op = tab.val_at("operate_profit", q, t)
            ta = tab.val_at("total_assets", q, t)
            roe = tab.val_at("roe_waa", q, t)
            eps = tab.val_at("basic_earnings_per_share", q, t)

            ocf = tab.ttm("ocf", q, t)
            capex = tab.ttm("capex", q, t)
            fcf = ocf - capex if (np.isfinite(ocf) and np.isfinite(capex)) else float("nan")
            vals = {
                "fin_rev_yoy": yoy2(rev, tab, "revenue", pq, t),
                "fin_np_yoy": yoy2(np_parent, tab, "net_profit_parent_company", pq, t),
                "fin_op_yoy": yoy2(op, tab, "operate_profit", pq, t),
                "fin_gross_margin": (gp / rev * 100.0) if (np.isfinite(gp) and rev > 0) else float("nan"),
                "fin_roe": roe,
                "fin_roa": (np_all / ta * 100.0) if (np.isfinite(np_all) and np.isfinite(ta) and ta > 0)
                else float("nan"),
                "fin_eps": eps,
                "fin_fcf": fcf,
            }
            eq_end = tab.val_at("equity_parent_company", q, t)
            if np.isfinite(roe) and np.isfinite(np_parent) and np.isfinite(eq_end) and eq_end > 0:
                roe_calc = np_parent / eq_end * 100.0
                if abs(roe - roe_calc) > max(1.0, 0.5 * abs(roe_calc)):
                    ROE_SUSPECTS.append({"code": code, "quarter": q, "ann_date": ann,
                                         "roe_reported": round(float(roe), 4),
                                         "roe_calc_cum": round(float(roe_calc), 4),
                                         "action": "replaced" if ROE_GUARD else "kept"})
                    if ROE_GUARD:                              # --roe-guard：用自算值顶上 ✓
                        vals["fin_roe"] = roe_calc
            for k, v in vals.items():
                out[k].append((ann, q, v))
    return out


def yoy2(cur: float, tab: TsPit, fld: str, pq: Optional[str], t: int) -> float:
    base = tab.val_at(fld, pq, t) if pq else float("nan")
    if np.isfinite(cur) and np.isfinite(base) and base > 0:
        return (cur / base - 1.0) * 100.0
    return float("nan")


def expand_daily(events: List[Tuple[str, str, float]], cal_int: np.ndarray) -> np.ndarray:
    """(公告日, 报告期, 值) 事件表 → 日频（公告日前向填充；首个公告前 NaN ✓）。"""
    out = np.full(len(cal_int), np.nan, dtype=np.float64)
    if not events:
        return out
    ev = sorted(events, key=lambda x: x[0])
    ann = np.asarray([int(np.datetime64(e[0], "D").astype(np.int64)) for e in ev], dtype=np.int64)
    vals = np.asarray([e[2] for e in ev], dtype=np.float64)
    idx = np.searchsorted(ann, cal_int, side="right") - 1
    ok = idx >= 0
    out[ok] = vals[idx[ok]]
    return out


def read_bin(path: Path) -> Optional[Tuple[int, np.ndarray]]:
    """读 qlib 字段 bin：header(float32 起始日历下标) + float32 数据 ✓（与 write_bin 对称 ✓）。"""
    if not path.exists():
        return None
    a = np.fromfile(str(path), dtype="<f4")
    if a.size < 2:
        return None
    return int(a[0]), a[1:].astype(np.float64)


def load_market_cap(qlib_dir: Path, cal_int: np.ndarray, code: str,
                    use_tushare: bool, tok: str, rate: float) -> np.ndarray:
    """当日总市值（**元**）：优先本地 `market_cap` bin（米筐口径 ✓，与旧 PE/PB 可比 ✓）；
    `use_tushare=True` 时用 tushare `daily_basic.total_mv`（万元 ⇒ ×1e4 ✓）**续**本地覆盖之后的日期 ✓。

    ⚠⚠ **`MC_FROM` 下限绝不能去掉**（2026-10-09 挖出的"37 分钟无输出"真凶 ✓）：
      本机有 **618 只**（588 只北交所 + 30 只）**没有** `market_cap.day.bin` ✗ ⇒ 若不设下限，
      这里会从 **2000-01-04** 起逐日调 `daily_basic`（每只 **6484 次 × 0.31s ≈ 33 分钟/只** ✗✗），
      而且那时每月只有 1~2 只股票有数据 ⇒ 几乎全部返回空值、还被写进缓存 ✗。
      副作用有两层：① 全量跑起来看着"卡死"（≥200 只才打印一次进度 ✗）；
      ② `_mc_cache(save=)` 每来一条新键就整份重写 3.4MB 的 `total_mv.json` ✗ ⇒ 进程被杀时
      正好停在半个键值对上 ⇒ **files 被截断**、之后每次 `json.load` 都抛 `JSONDecodeError` ✗✗
      （这正是 2026-10-09 那次缓存损坏的成因 ✓）。
      ⇒ 现在只在 `[MC_FROM, 日历末]` 区间内续更 ✓，而这段全在预取缓存里 ⇒ **零 API 调用** ✓。
    """
    global MC_FETCHED
    arr = np.full(len(cal_int), np.nan, dtype=np.float64)
    hit = read_bin(qlib_dir / "features" / code / "market_cap.day.bin")
    if hit is not None:
        start, vals = hit
        arr[start:start + vals.size] = vals
    if not use_tushare:
        return arr
    fin = np.isfinite(arr)
    last = int(np.where(fin)[0][-1]) if fin.any() else -1
    if last >= len(cal_int) - 1:
        return arr                                        # 本地已覆盖到末日 ⇒ 无需续 ✓
    ts_code = to_ts_code(code)
    pending = 0
    # ⚠ `cal_int` 是 **datetime64[D] 的天数偏移**（如 20726），不是 YYYYMMDD ✗（2026-10-09 踩过：
    #   直接 `searchsorted(cal_int, 20260824)` 恒返回末尾 ⇒ 续更循环一次都没跑、PE/PB 停在 8/21 ✗）
    lo_pos = int(np.searchsorted(cal_int, _d8_offset(MC_FROM)))
    start_pos = max(last + 1, lo_pos)                     # ★ 下限：绝不从 2000 年起逐日调 API ✗
    for pos in range(start_pos, len(cal_int)):
        d = str(np.datetime64(int(cal_int[pos]), "D")).replace("-", "")
        cache = _mc_cache()
        v = cache.get("%s|%s" % (d, ts_code))
        if v is None:
            r = ts_call("daily_basic", {"trade_date": d, "ts_code": ts_code}, "ts_code,total_mv", tok)
            MC_FETCHED += 1
            dd = r.get("data") or {}
            items = dd.get("items") or []
            v = float(items[0][1]) * 1e4 if items and items[0][1] is not None else float("nan")
            cache["%s|%s" % (d, ts_code)] = v
            pending += 1
            # ⚠ 攒 50 条再落盘（原来每来一条就整份重写 ⇒ 16 万条时写放大 ~GB 级、被杀的窗口也大 ✗）
            if pending >= 50:
                _mc_cache(save=cache)
                pending = 0
            time.sleep(rate)
        arr[pos] = v
    if pending:
        _mc_cache(save=cache)
    return arr


_MC_CACHE: Optional[dict] = None


def _mc_cache(save: Optional[dict] = None) -> dict:
    """total_mv 的小缓存（按 (日期, 股票) 键 ✓）—— 只缓存"续"出来的那几十天 ✓。

    ⚠ 读写都要**抗损坏**（2026-10-09 踩过 ✗）：原来 `json.load` 裸调 ⇒ 文件一旦被截断
      （`prefetch_daily_basic.py` / 本脚本写盘时被杀）就**每次都炸**、整轮 dump 全废 ✗；
      现在 ⇒ 抢救完整记录 + **就地原子修复** ⇒ 自愈 ✓，缺的键由 `load_market_cap` 按需重下 ✓。
    """
    global _MC_CACHE
    if save is not None:
        atomic_json_dump(MC_TUSHARE_CACHE, save)
        _MC_CACHE = save
        return save
    if _MC_CACHE is None:
        if MC_TUSHARE_CACHE.exists():
            obj, broken = read_json_tolerant(MC_TUSHARE_CACHE, "total_mv 缓存")
            _MC_CACHE = obj if obj is not None else {}
            if broken:                       # 抢救完立刻原子写回 ⇒ 下次不再报错 ✓
                atomic_json_dump(MC_TUSHARE_CACHE, _MC_CACHE)
                print("  ⇒ 已就地修复 total_mv 缓存（%d 条）；若缺很多天，"
                      "跑 python tools/prefetch_daily_basic.py --apply 一次补齐最快 ✓"
                      % len(_MC_CACHE), flush=True)
        else:
            _MC_CACHE = {}
    return _MC_CACHE


def fetch_financials(qlib_code: str, tok: str, rate: float, refresh: bool = False) -> Dict[str, List[dict]]:
    """拉（或读缓存）某股的四表。缓存 ⇒ 断点续跑、重跑几乎免费 ✓。

    ⚠ 缓存损坏必须**自动重下**（2026-10-09）：某份缓存 JSON 被截断（实测 char 3421194 ✗）时，
      绝不允许"整只跳过" ✗（那等于这只股票静默无数据）；这里 ⇒ 记进 `CORRUPT_CACHE`（进度行可见 ✓）
      + 删掉坏文件 + 走完整重下路径 ✓。写盘一律走 `atomic_json_dump` ✓。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = CACHE_DIR / ("%s.json" % qlib_code)
    if cp.exists() and not refresh:
        data = None
        try:
            data, broken = read_json_tolerant(cp, "财务缓存 %s" % qlib_code)
            if data is not None and not broken:
                return data
        except Exception as e:                                # noqa: BLE001
            print("  ⚠ 财务缓存 %s 读取失败：%s: %s" % (qlib_code, type(e).__name__, e), flush=True)
        CORRUPT_CACHE.append(qlib_code)
        try:
            cp.unlink()                                       # 删掉 ⇒ 下面重下并覆写 ✓（不跳过 ✗）
        except OSError:
            pass
    ts_code = to_ts_code(qlib_code)
    data: Dict[str, List[dict]] = {}
    for api, flds in TABLES.items():
        r = ts_call(api, {"ts_code": ts_code}, flds, tok)
        d = r.get("data") or {}
        cols = d.get("fields") or []
        data[api] = [dict(zip(cols, it)) for it in (d.get("items") or [])]
        time.sleep(rate)
    atomic_json_dump(cp, data)
    return data


def ttm_events(tab: TsPit, fld: str) -> List[Tuple[str, str, float]]:
    """某字段的 TTM 事件表（公告日, 报告期, TTM 值）—— 公告日取 income 的（与 report 一致 ✓）。"""
    out: List[Tuple[str, str, float]] = []
    for q in tab.quarter:
        ent = tab.by_quarter.get("revenue", {}).get(q)
        if ent is None:
            continue
        for a in ent[0]:
            out.append((str(np.datetime64(int(a), "D")), q, tab.ttm(fld, q, int(a))))
    return out


def build_daily_bins(qlib_code: str, data: Dict[str, List[dict]], cal_int: np.ndarray,
                     qlib_dir: Path, tok: str, rate: float,
                     mc_tushare: bool) -> Tuple[Dict[str, np.ndarray], TsPit]:
    """（纯计算，不写盘）→ {字段: 日频数组} + PIT 表 ✓。对拍脚本与 dump 共用同一套 ✓。"""
    tab = TsPit(data)
    if tab.is_empty():
        return {}, tab
    report = build_report_metrics(tab, code=qlib_code)
    daily: Dict[str, np.ndarray] = {
        k: expand_daily(report[k], cal_int) for k, _u in FIN_FIELDS
        if k not in ("fin_pe_ttm", "fin_pb")
    }
    mc = load_market_cap(qlib_dir, cal_int, qlib_code, mc_tushare, tok, rate)
    ttm = expand_daily(ttm_events(tab, "net_profit_parent_company"), cal_int)
    eq_events: List[Tuple[str, str, float]] = []
    for q in tab.quarter:
        ent = tab.by_quarter.get("revenue", {}).get(q)
        if ent is None:
            continue
        for a in ent[0]:
            eq_events.append((str(np.datetime64(int(a), "D")), q,
                              tab.val_at("equity_parent_company", q, int(a))))
    eq = expand_daily(eq_events, cal_int)
    pe = np.full(len(cal_int), np.nan)
    pb = np.full(len(cal_int), np.nan)
    m_ok = np.isfinite(mc)
    np.divide(mc, ttm, out=pe, where=m_ok & np.isfinite(ttm) & (ttm != 0.0))
    np.divide(mc, eq, out=pb, where=m_ok & np.isfinite(eq) & (eq > 0.0))
    daily["fin_pe_ttm"] = pe
    daily["fin_pb"] = pb
    return daily, tab


def dump_one(qlib_code: str, cal_int: np.ndarray, qlib_dir: Path, tok: str, rate: float,
             force: bool, verify: bool, mc_tushare: bool, refresh: bool) -> Dict[str, Optional[float]]:
    """处理一只股票：算日频 → 写 bin；返回 {字段: 最新有效值}（verify / 统计用）✓。"""
    data = fetch_financials(qlib_code, tok, rate, refresh=refresh)
    # ⚠ 覆盖不到就**整只跳过**（实测：北交所 430xxx 等，tushare 只有 `fina_indicator`、
    #   三张表返回 **0 行** ✗）—— 若照旧往下走，会只写 `fin_roe` 而把其它字段留在米筐值上
    #   ⇒ **半覆盖**（同一只股票的字段来自两个源 ✗，且分不清哪来的 ✗✗）⇒ 必须整只不碰 ✓。
    if not (data.get("income") or []):
        return {"_skipped_no_income": 1}
    daily, tab = build_daily_bins(qlib_code, data, cal_int, qlib_dir, tok, rate, mc_tushare)
    if tab.is_empty():
        return {}
    result: Dict[str, Optional[float]] = {}
    written = 0
    for field, _unit in FIN_FIELDS:
        arr = daily[field]
        finite = np.isfinite(arr)
        if not finite.any():
            continue
        last = int(np.where(finite)[0][-1])
        result[field] = round(float(arr[last]), 4)
        if verify:
            continue
        bin_path = qlib_dir / "features" / qlib_code / ("%s.day.bin" % field)
        if bin_path.exists() and not force:
            continue
        first = int(np.where(finite)[0][0])
        write_bin(bin_path, first, arr[first:last + 1])
        written += 1
    result["_written"] = written
    return result


def write_meta(qlib_dir: Path, n_codes: int, n_bins: int, overwrite: bool, cal_last: str) -> Path:
    """口径戳（与 dump_finance 同键 + `source` 标明来源 ✓ —— 便于事后核对是哪条链 dump 的 ✓）。"""
    p = qlib_dir / "features" / FIN_META_NAME
    old = {}
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:                                     # noqa: BLE001
            old = {}
    meta = {
        "finance_semantics": FINANCE_SEMANTICS,
        "source": "tushare",
        "fields": [f for f, _u in FIN_FIELDS],
        "n_codes": n_codes,
        "n_bins_written": n_bins,
        "overwrite": overwrite,
        "calendar_last": cal_last,
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "由 tools/dump_tushare_finance.py 生成（口径与 dump_finance.py 逐项一致）；"
                "同一报告期多行按 ann_date 取最后一行 ⇒ PIT、无未来函数 ✓",
        "previous": old or None,
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return p


def _declared_convention(qlib_dir: Path) -> str:
    """读数据集**自报**的口径：优先 `.dataset.json` 的 `convention` ✓；
    退回 `features/_finance_meta.json` 的 `source` ✓（米筐链的口径戳没有 `source` ✗，
    但带 `source_pit_dir: E:\\rq\\finance\\pit` ✓ ⇒ 也能识别成米筐 ✓）；都没有 ⇒ `""` ✓。
    """
    try:
        d = json.loads((qlib_dir / ".dataset.json").read_text(encoding="utf-8")) or {}
        c = str(d.get("convention") or "").strip().lower()
        if c:
            return c
    except Exception:                                                     # noqa: BLE001
        pass
    try:
        m = json.loads((qlib_dir / "features" / "_finance_meta.json").read_text(encoding="utf-8")) or {}
        src = str(m.get("source") or "").strip().lower()
        if src:
            return src
        if m.get("source_pit_dir"):
            return "ricequant"
    except Exception:                                                     # noqa: BLE001
        pass
    return ""


def guard_convention(qlib_dir: Path, allow_non_tushare: bool) -> None:
    """★★ 硬规矩（2026-10-10 用户澄清后加）：本工具是 **tushare 财务链** ⇒ 只许写
    `convention=tushare` 的数据集（如 `data/cn_data2` ✓）。

    **Why**：`cn_data` 是**米筐源**数据集（`.dataset.json`: `convention=ricequant` ✓，
    财务 = 米筐 pit 带发布日/修订日 ✓），而本工具的 `--qlib-dir` **默认值恰好就是 `data/cn_data`** ✗
    ⇒ 2026-10-09 我照着默认值跑了一次，把 tushare 财务灌进了米筐数据集 ✗✗（虽然随后被米筐那条链
    force 重灌覆盖 ✓、没留下残留 ✓，但白跑了 3 小时 ✗，而且差点变成"同一数据集混两个源"✗）。
    **How to apply**：换数据集先看该目录的 `.dataset.json` ✓；真要往非 tushare 数据集写，
    必须显式 `--allow-non-tushare` ✓（= 明示你清楚在混源 ✗）。
    """
    conv = _declared_convention(qlib_dir)
    if conv and conv != "tushare" and not allow_non_tushare:
        raise SystemExit(
            "✗ 拒绝写入：目标 %s 的口径是 %r，不是 tushare ✗\n"
            "  本工具只生成 **tushare 财务** ⇒ 只应写 tushare 口径的数据集（如 data/cn_data2 ✓）。\n"
            "  · 米筐源数据集（data/cn_data ✓）请改用 tools/dump_finance.py ✓；\n"
            "  · 确实要往这里强写：加 --allow-non-tushare ✓（= 明示你在混源 ✗）。"
            % (qlib_dir, conv))


def main():
    ap = argparse.ArgumentParser(description="tushare 财务 → qlib fin_* 字段（与 dump_finance 同口径）")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--codes", default="", help="只处理这些（qlib 代码，逗号分隔，如 sz300073,sh600519）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟）")
    ap.add_argument("--force", action="store_true", help="已存在的 bin 也覆盖重写")
    ap.add_argument("--verify", action="store_true", help="只打印核对值，不写盘")
    ap.add_argument("--rate", type=float, default=0.31, help="每次 API 调用后的间隔秒（200/分钟档）")
    ap.add_argument("--mc-tushare", action="store_true",
                    help="本地 market_cap 覆盖不到的日期用 tushare daily_basic.total_mv 续")
    ap.add_argument("--mc-from", type=int, default=0,
                    help="市值续更的下限日（YYYYMMDD ✓；默认=tushare 日线缓存最早日；"
                         "**不要设成很早** ✗ 见 load_market_cap 注释）")
    ap.add_argument("--refresh", action="store_true", help="忽略本地缓存，重新拉取")
    ap.add_argument("--allow-non-tushare", action="store_true",
                    help="★ 允许往**非 tushare 口径**的数据集里写（会混源 ✗；默认拒绝 ✓ 见 guard_convention）")
    ap.add_argument("--roe-guard", action="store_true",
                    help="把明显毛刺的 ROE（tushare roe_waa）替换成自算值 累计归母净利/期末归母权益")
    args = ap.parse_args()

    global ROE_GUARD, MC_FROM
    ROE_GUARD = bool(args.roe_guard)
    MC_FROM = int(args.mc_from) or _default_mc_from()

    qlib_dir = Path(args.qlib_dir)
    # ★ 先卡口径（见函数注释：默认目录就是米筐源的 cn_data ✗ ⇒ 照默认跑会混源 ✗✗）
    guard_convention(qlib_dir, bool(args.allow_non_tushare))
    cal, cal_int = load_calendar(qlib_dir)
    tok = token()
    feats = qlib_dir / "features"
    if args.codes:
        codes = [c.strip().lower() for c in args.codes.split(",") if c.strip()]
    else:
        codes = sorted(d.name for d in feats.iterdir()
                       if d.is_dir() and d.name[:2] in ("sh", "sz", "bj") and d.name[2:].isdigit())
    if args.limit:
        codes = codes[:args.limit]
    print("日历 %d 天（%s ~ %s）| 待处理 %d 只 | rate=%.2fs | verify=%s | mc_tushare=%s"
          % (len(cal), cal[0], cal[-1], len(codes), args.rate, args.verify, args.mc_tushare), flush=True)
    if args.mc_tushare:
        print("市值续更下限 mc_from=%d（该日之后才动 daily_basic ✓；下限之前一律留 NaN ✗ 不猜）"
              % MC_FROM, flush=True)

    t0 = time.time()
    done = empty = n_bins = n_skip = 0
    for i, code in enumerate(codes, 1):
        try:
            res = dump_one(code, cal_int, qlib_dir, tok, args.rate, args.force,
                           args.verify, args.mc_tushare, args.refresh)
        except Exception as e:                                # noqa: BLE001
            print("  ✗ %s 处理失败：%s: %s" % (code, type(e).__name__, e), flush=True)
            continue
        fields = {k: v for k, v in res.items() if not k.startswith("_")}
        if res.get("_skipped_no_income"):
            n_skip += 1                         # tushare 无 income（如北交所）⇒ 保持米筐数据不动 ✓
        elif not fields:
            empty += 1
        else:
            done += 1
            n_bins += int(res.get("_written", 0))
            if args.verify:
                print("  %s: %s | 最近一期 %s"
                      % (code, {k: fields.get(k) for k in ("fin_rev_yoy", "fin_np_yoy",
                                                           "fin_pe_ttm", "fin_pb", "fin_fcf")},
                         max(fields)), flush=True)
        if i % 50 == 0 or i == len(codes):
            el = time.time() - t0
            print("  进度 %d/%d  有数据 %d / 空 %d / tushare 无覆盖跳过 %d / 损坏缓存重下 %d / mc调用 %d"
                  "  写盘 %d 个 bin  %.0fs（%.2fs/只）  预计剩余 %.0fs"
                  % (i, len(codes), done, empty, n_skip, len(CORRUPT_CACHE), MC_FETCHED,
                     n_bins, el, el / i, el / i * (len(codes) - i)), flush=True)

    if args.verify:
        print("核对模式：未写盘 ✓")
        return
    print("完成：写盘 %d 个 bin（%d 只有数据 / %d 只空）用时 %.0fs" % (n_bins, done, empty, time.time() - t0))
    if CORRUPT_CACHE:
        print("⚠ 本地缓存损坏并已重新下载 %d 只（前 20：%s）"
              % (len(CORRUPT_CACHE), ",".join(CORRUPT_CACHE[:20])))
    print("口径戳：%s" % write_meta(qlib_dir, done, n_bins, args.force, cal[-1]))
    if ROE_SUSPECTS:
        wp = qlib_dir / "features" / "_finance_warnings.json"
        with open(wp, "w", encoding="utf-8") as f:
            json.dump({"roe_suspects": ROE_SUSPECTS, "roe_guard": ROE_GUARD,
                       "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "note": "tushare roe_waa 与自算值(累计归母净利/期末归母权益)偏离 >50% 的格子；"
                               "默认只记录不替换（--roe-guard 才替换）"},
                      f, ensure_ascii=False, indent=2)
        print("⚠ ROE 毛刺 %d 处 ⇒ %s（%s）"
              % (len(ROE_SUSPECTS), wp, "已替换" if ROE_GUARD else "仅记录、未改数据"))


if __name__ == "__main__":
    main()
