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
    """
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
    for pos in range(last + 1, len(cal_int)):
        d = str(np.datetime64(int(cal_int[pos]), "D")).replace("-", "")
        cache = _mc_cache()
        v = cache.get("%s|%s" % (d, ts_code))
        if v is None:
            r = ts_call("daily_basic", {"trade_date": d, "ts_code": ts_code}, "ts_code,total_mv", tok)
            dd = r.get("data") or {}
            items = dd.get("items") or []
            v = float(items[0][1]) * 1e4 if items and items[0][1] is not None else float("nan")
            cache["%s|%s" % (d, ts_code)] = v
            _mc_cache(save=cache)
            time.sleep(rate)
        arr[pos] = v
    return arr


_MC_CACHE: Optional[dict] = None


def _mc_cache(save: Optional[dict] = None) -> dict:
    """total_mv 的小缓存（按 (日期, 股票) 键 ✓）—— 只缓存"续"出来的那几十天 ✓。"""
    global _MC_CACHE
    if save is not None:
        MC_TUSHARE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(MC_TUSHARE_CACHE, "w", encoding="utf-8") as f:
            json.dump(save, f, ensure_ascii=False)
        _MC_CACHE = save
        return save
    if _MC_CACHE is None:
        if MC_TUSHARE_CACHE.exists():
            with open(MC_TUSHARE_CACHE, encoding="utf-8") as f:
                _MC_CACHE = json.load(f)
        else:
            _MC_CACHE = {}
    return _MC_CACHE


def fetch_financials(qlib_code: str, tok: str, rate: float, refresh: bool = False) -> Dict[str, List[dict]]:
    """拉（或读缓存）某股的四表。缓存 ⇒ 断点续跑、重跑几乎免费 ✓。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = CACHE_DIR / ("%s.json" % qlib_code)
    if cp.exists() and not refresh:
        try:
            with open(cp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:                                     # noqa: BLE001
            pass
    ts_code = to_ts_code(qlib_code)
    data: Dict[str, List[dict]] = {}
    for api, flds in TABLES.items():
        r = ts_call(api, {"ts_code": ts_code}, flds, tok)
        d = r.get("data") or {}
        cols = d.get("fields") or []
        data[api] = [dict(zip(cols, it)) for it in (d.get("items") or [])]
        time.sleep(rate)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
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
    ap.add_argument("--refresh", action="store_true", help="忽略本地缓存，重新拉取")
    ap.add_argument("--roe-guard", action="store_true",
                    help="把明显毛刺的 ROE（tushare roe_waa）替换成自算值 累计归母净利/期末归母权益")
    args = ap.parse_args()

    global ROE_GUARD
    ROE_GUARD = bool(args.roe_guard)

    qlib_dir = Path(args.qlib_dir)
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
    print("日历 %d 天（%s ~ %s）| 待处理 %d 只 | rate=%.2fs | verify=%s"
          % (len(cal), cal[0], cal[-1], len(codes), args.rate, args.verify), flush=True)

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
        if i % 200 == 0 or i == len(codes):
            el = time.time() - t0
            print("  进度 %d/%d  有数据 %d / 空 %d / tushare 无覆盖跳过 %d  写盘 %d 个 bin  %.0fs  预计剩余 %.0fs"
                  % (i, len(codes), done, empty, n_skip, n_bins, el, el / i * (len(codes) - i)), flush=True)

    if args.verify:
        print("核对模式：未写盘 ✓")
        return
    print("完成：写盘 %d 个 bin（%d 只有数据 / %d 只空）用时 %.0fs" % (n_bins, done, empty, time.time() - t0))
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
