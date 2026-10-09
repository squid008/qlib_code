# -*- coding: utf-8 -*-
"""**通达信财务包**（`gpcw*.zip`）→ qlib 基本面字段 bin（`ftdx_*`）= `FINANCE_TDX(q)` 的数据源。

用法（backend 目录下）：
    python tools/dump_finance_tdx.py                      # 全量（148 个报告期 × ~5500 只）
    python tools/dump_finance_tdx.py --limit 20           # 冒烟：只处理前 N 只
    python tools/dump_finance_tdx.py --verify sz000001,sh600519,sz300073   # 只打印核对值
    python tools/dump_finance_tdx.py --force
    python tools/dump_finance_tdx.py --cw-dir D:\\new_tdx\\vipdoc\\cw

源：`D:\\new_tdx\\vipdoc\\cw\\gpcw<报告期>.zip`（通达信会员"财务数据下载"产物；格式见
    `app/datasource/tdx_gpcw.py` 顶部，字段下标已用已知值逐个核对过 ✓）。

产物：`{qlib_dir}/features/{code}/ftdx_*.day.bin`（9 个字段，编号与 `FINANCE(q)` **完全一致**）
      + `{qlib_dir}/features/_finance_tdx_meta.json`（口径戳）。

## 字段（q 与 FINANCE(q) 同编号 —— 便于两源**交叉校验** ✓）
| q | 字段 | 通达信字段 | 口径 |
|---|------|-----------|------|
| 1 | ftdx_pe_ttm | 总股本 × 未复权价 ÷ 近一年净利润 | 日频（随市值变） |
| 2 | ftdx_pb | 总股本 × 未复权价 ÷ 归母权益 | 日频 |
| 3 | ftdx_rev_yoy | 营业收入 | 报告期**累计同比**（%） |
| 4 | ftdx_np_yoy | 归母净利润 | 累计同比（%） |
| 5 | ftdx_gross_margin | 销售毛利率(%) | **通达信直接给的**值（与益盟同口径 ✓） |
| 6 | ftdx_roe | 加权净资产收益率(%) | 与米筐 `return_on_equity_weighted_average` 逐位一致 ✓ |
| 7 | ftdx_roa | 净利润 ÷ 资产总计 × 100 | 报告期累计（未用平均总资产） |
| 8 | ftdx_eps | 基本每股收益 | 报告期累计（元） |
| 9 | ftdx_op_yoy | 营业利润 | 累计同比（%） |
| 10 | ftdx_fcf | 经营现金流净额(106) − 资本开支(113) | **自由现金流TTM**（元）；TTM = 累计(Q)+累计(上年年报)−累计(上年同期Q) |

⚠⚠ 通达信 gpcw 里**同文件内口径不统一**（实测 2026-10-09，务必记住）：
  · **单季**：营业收入(229)/营业成本(74)/营业利润(230)/净利润(94)/归母净利(231) —— 按年累加才得到累计 ✓
  · **年内累计**：经营现金流净额(106)/资本开支(113)（实测 300750 2026q2 与米筐同报告期**比值 1.0000** ✓）
    ⇒ **不要**把它们也去累加 ✗（会双计 ✗）

未复权价 = `$close / $factor`（本机实测：米筐市值 ÷ (close/factor) == 通达信总股本，比值 **1.0000** ✓）。

## ★★ PIT（避免未来函数）—— 与米筐源的**关键差异**，必须记住
gpcw **没有公告日**，也**没有逐次修正快照**（每次下载都是"按最新口径回填"）✗ ⇒ 无法做到严格 PIT ✓。
本脚本采用**法定披露截止日**作可用日（保守：宁晚不错 ✓）：
    一季报 4/30 · 中报 8/31 · 三季报 10/31 · 年报次年 4/30
⇒ 提前披露的公司要等到截止日才用得上（代价），但**绝不会用到当时还没公告的数字** ✓。
⚠ 若将来能拿到公告日（如通达信 F10/其它源），把 `avail_date()` 一换、重跑即可 ✓（口径戳会变 ✓）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datasource.tdx_gpcw import DEFAULT_CW_DIR, list_packs, quarter_of, read_pack  # noqa: E402
from app.factors.parser.codegen import FINANCE_TDX_FIELDS  # noqa: E402

FIN_TDX_META_NAME = "_finance_tdx_meta.json"
# 口径语义版本：改字段口径 / 可用日规则 / 派生公式 ⇒ 必须递增并重跑
FINANCE_TDX_SEMANTICS = "1.0.0"


# ---------------------------------------------------------------------------
# PIT 可用日（法定披露截止日）
# ---------------------------------------------------------------------------
def avail_date(report_date: int) -> str:
    """报告期 → **法定披露截止日**（保守可用日）。"""
    y, md = divmod(int(report_date), 10000)
    m = md // 100
    if m == 3:
        return "%d-04-30" % y
    if m == 6:
        return "%d-08-31" % y
    if m == 9:
        return "%d-10-31" % y
    return "%d-04-30" % (y + 1)          # 年报：次年 4/30


def _d(s: str) -> int:
    return int(np.datetime64(s, "D").astype(np.int64))


# ---------------------------------------------------------------------------
# 读日历 / 字段 bin
# ---------------------------------------------------------------------------
def load_calendar(qlib_dir: Path) -> Tuple[List[str], np.ndarray]:
    cal_path = qlib_dir / "calendars" / "day.txt"
    if not cal_path.exists():
        raise SystemExit("qlib 日历不存在：%s" % cal_path)
    cal = pd.read_csv(cal_path, header=None)[0].astype(str).tolist()
    return cal, np.asarray(cal, dtype="datetime64[D]").astype(np.int64)


def known_codes(qlib_dir: Path) -> Dict[str, str]:
    """`features/` 下的股票代码 → `{6 位数字: qlib 代码}`（通达信包里只有 6 位数字 ✓）。"""
    fdir = qlib_dir / "features"
    out: Dict[str, str] = {}
    try:
        for name in os.listdir(fdir):
            if len(name) == 8 and name[:2] in ("sh", "sz", "bj") and name[2:].isdigit():
                if os.path.isdir(fdir / name):
                    out[name[2:]] = name
    except OSError:
        pass
    return out


def read_bin_full(qlib_dir: Path, code: str, field: str, n_cal: int) -> Optional[np.ndarray]:
    """把某字段 bin 铺成**完整日历**长度的一维数组（缺失/区间外 = NaN）。"""
    p = qlib_dir / "features" / code / ("%s.day.bin" % field)
    if not p.exists():
        return None
    a = np.fromfile(str(p), dtype="<f4")
    if a.size < 2:
        return None
    first, vals = int(a[0]), a[1:].astype(np.float64)
    out = np.full(n_cal, np.nan)
    out[first:first + vals.size] = vals
    return out


def write_bin(path: Path, cal_pos: int, values: np.ndarray) -> None:
    out = np.hstack([np.array([cal_pos], dtype=np.float64), values]).astype("<f")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.tofile(str(path))


# ---------------------------------------------------------------------------
# 事件表（逐报告期 → 逐股指标）
# ---------------------------------------------------------------------------
def prev_quarter(q: str, k: int = 1) -> str:
    """'2026q2' 往前数 k 个季度 → '2026q1' / '2025q4' …"""
    y, qn = int(q[:4]), int(q[-1])
    for _ in range(k):
        qn -= 1
        if qn == 0:
            qn, y = 4, y - 1
    return "%dq%d" % (y, qn)


def build_events(cw_dir: str, code_map: Dict[str, str], since: int = 19991231,
                 progress=None) -> Dict[str, List[Tuple[int, Dict[str, float]]]]:
    """遍历全部财务包，产出 `{qlib代码: [(可用日 int, 指标 dict), ...]}`（按可用日升序）。

    ★★ 通达信 gpcw 的**口径陷阱**（实测 2026-10-09，务必记住）：
      · **单季**值：营业收入 / 营业成本 / 营业利润 / 净利润 / 归母净利润
        （实测平安银行 2025q2 包 = 356.8 亿，是**Q2 单季**；H1 累计是 694 亿 ✗）
      · **累计/期末**值：基本每股收益 / 加权ROE / 销售毛利率 / 总资产 / 总股本 / 归母权益
      ⇒ 要给出与 `FINANCE(q)` 一致的"**累计同比**"和"累计 ROA/毛利率"，必须先按年累加单季值 ✓
        （第一版直接拿单季当累计 ⇒ 增长率/Q2 之后的数值全错 ✗，2026-10-09 踩过）。
      · 归母净利润 TTM = **最近四个单季**之和（每个单季都取自它自己被公告的那个包 ⇒ 无未来函数 ✓）。
    """
    events: Dict[str, List[Tuple[int, Dict[str, float]]]] = {}
    hist: Dict[str, Dict[str, Dict[str, float]]] = {}          # code -> 报告期 -> 指标
    ytd: Dict[str, Dict[int, Dict[str, float]]] = {}           # code -> 年 -> 年内累计（单季累加）
    packs = [p for p in list_packs(cw_dir) if p[0] >= since]
    for n, (rd, path) in enumerate(packs, 1):
        report_date, d = read_pack(path)
        q = quarter_of(report_date)
        y, qn = int(q[:4]), int(q[-1])
        ad = _d(avail_date(report_date))
        codes = d["__codes__"]
        for i in range(codes.size):
            code = code_map.get(str(codes[i]))
            if code is None:
                continue
            # ---- 单季（原始）----
            rev_q = float(d["revenue"][i])
            cost_q = float(d["cost"][i])
            op_q = float(d["op_profit"][i])
            net_q = float(d["net_profit"][i])
            npx_q = float(d["np_parent"][i])
            ta = float(d["total_assets"][i])
            gm_tdx = float(d["gross_margin"][i])         # 通达信自己的销售毛利率（金融股恒为 0）
            # ⚠ 现金流两项是**年内累计**（不是单季 ✗）⇒ 直接取用、**不进**下面的累加 ✓
            ocf_ytd = float(d["ocf"][i])
            capex_ytd = float(d["capex"][i])


            # ---- 年内累计（单季 → 累计）：缺上一季就判 NaN（宁可没有，也别给错数 ✓）----
            cum = ytd.setdefault(code, {}).setdefault(y, {})
            keys = (("_rev", rev_q), ("_cost", cost_q), ("_op", op_q),
                    ("_net", net_q), ("_npx", npx_q))
            bad = False
            for k, v in keys:
                if qn == 1:
                    cum[k] = v
                else:
                    base = cum.get(k) if cum.get("_seen_q") == qn - 1 else None
                    cum[k] = (base + v) if (base is not None and np.isfinite(base) and np.isfinite(v)) else float("nan")
                    if not np.isfinite(cum[k]):
                        bad = True
            cum["_seen_q"] = qn
            rev_c, cost_c, op_c = cum["_rev"], cum["_cost"], cum["_op"]
            net_c, npx_c = cum["_net"], cum["_npx"]

            # ---- 同比（累计 vs 上年同期累计 ✓ 与 FINANCE(q) 同口径）----
            prev_m = hist.get(code, {}).get("%dq%d" % (y - 1, qn))

            def _yoy(cur: float, base: Optional[float]) -> float:
                if base is None or not np.isfinite(base) or base <= 0 or not np.isfinite(cur):
                    return float("nan")
                return (cur / base - 1.0) * 100.0

            # ---- 归母净利 TTM = 最近四个单季之和（缺任一季 ⇒ 退回通达信"近一年净利润" ✓）----
            four = [npx_q] + [hist.get(code, {}).get(prev_quarter(q, k), {}).get("_npx_q", float("nan"))
                              for k in (1, 2, 3)]
            np_par_ttm = float(np.sum(four)) if all(np.isfinite(x) for x in four) else float("nan")

            # ---- 自由现金流TTM（元，q=10）：累计 → TTM 标准式（与米筐路线同口径 ✓）----
            fy_prev_q = "%dq4" % (y - 1)
            fm = hist.get(code, {}).get(fy_prev_q)
            fcf_ttm = float("nan")
            if prev_m is not None and fm is not None:
                _parts = (ocf_ytd - capex_ytd,
                          fm.get("_ocf", float("nan")) - fm.get("_capex", float("nan")),
                          prev_m.get("_ocf", float("nan")) - prev_m.get("_capex", float("nan")))
                if all(np.isfinite(x) for x in _parts):
                    fcf_ttm = _parts[0] + _parts[1] - _parts[2]

            m = {
                "_quarter": q,
                "_npx_q": npx_q,                             # 单季归母（供下一年的 TTM 滚用 ✓）
                "pe_ttm": float("nan"),                      # 日频，下面填
                "pb": float("nan"),
                "rev_yoy": _yoy(rev_c, None if prev_m is None else prev_m.get("_rev")),
                "np_yoy": _yoy(npx_c, None if prev_m is None else prev_m.get("_npx")),
                "op_yoy": _yoy(op_c, None if prev_m is None else prev_m.get("_op")),
                # 毛利率：用**累计**营收/成本自己算（与 FINANCE(5) 同口径 ✓）。
                # ⚠ 金融股要出 NaN（与 FINANCE(5) 一致 ✓）：判据用**通达信自己的 201 字段**
                #   （它标着"非金融类指标"，金融股恒为 0 ✓）—— 实测若不拦，平安银行会算出 49.29% ✗
                #   （银行没有"营业成本"这个概念，硬算出来的数字毫无意义 ✗）。
                "gross_margin": ((rev_c - cost_c) / rev_c * 100.0
                                 if (np.isfinite(gm_tdx) and gm_tdx != 0.0 and np.isfinite(cost_c)
                                     and cost_c > 0 and np.isfinite(rev_c) and rev_c > 0)
                                 else float("nan")),
                "roe": float(d["roe"][i]),                   # 通达信给的就是**累计加权ROE** ✓
                "roa": (net_c / ta * 100.0) if (np.isfinite(ta) and ta > 0 and np.isfinite(net_c)) else float("nan"),
                "eps": float(d["eps"][i]),                   # 累计基本每股收益 ✓
                "fcf": fcf_ttm,                              # 自由现金流TTM（元）
                # 原料（同比基数 + PE/PB 的"财务腿"）
                "_rev": rev_c, "_cost": cost_c, "_op": op_c, "_net": net_c, "_npx": npx_c,
                "_np_ttm": (np_par_ttm if np.isfinite(np_par_ttm) else float(d["np_ttm"][i])),
                "_ocf": ocf_ytd, "_capex": capex_ytd,        # 供后续报告期算 FCF-TTM（累计值 ✓）
                "_equity": float(d["equity_parent"][i]),
                "_shares": float(d["shares"][i]),
            }
            hist.setdefault(code, {})[q] = m
            events.setdefault(code, []).append((ad, m))
        if progress and (n % 20 == 0 or n == len(packs)):
            progress("  报告期包 %d/%d（%s）" % (n, len(packs), report_date))
    for evs in events.values():
        evs.sort(key=lambda x: x[0])
    return events


def expand(avail: np.ndarray, vals: np.ndarray, cal_int: np.ndarray) -> np.ndarray:
    """事件（可用日）→ 日频前向填充：**可用日之前一律 NaN**（绝不提前用 ✓）。"""
    out = np.full(cal_int.size, np.nan)
    if avail.size == 0:
        return out
    idx = np.searchsorted(avail, cal_int, side="right") - 1
    ok = idx >= 0
    out[ok] = vals[idx[ok]]
    return out


# ---------------------------------------------------------------------------
# 单只股票
# ---------------------------------------------------------------------------
def dump_one(code: str, evs: List[Tuple[int, Dict[str, float]]], qlib_dir: Path,
             cal: List[str], cal_int: np.ndarray, force: bool, verify: bool) -> Dict[str, Optional[float]]:
    n_cal = cal_int.size
    aux = ["_np_ttm", "_equity", "_shares"]
    avail = np.asarray([e[0] for e in evs], dtype=np.int64)
    daily: Dict[str, np.ndarray] = {}
    for fld, _u in FINANCE_TDX_FIELDS:
        # ⚠ 事件里的键是**短名**（'pe_ttm'/'rev_yoy'…），字段名带 `ftdx_` 前缀 ⇒ 必须剥掉 ✓
        #   （第一版忘了剥 ⇒ 全取到 NaN、整列被跳过，核对时全是 None ✗，2026-10-09 踩过）
        arr = np.asarray([e[1].get(fld[len("ftdx_"):], np.nan) for e in evs], dtype=np.float64)
        daily[fld] = expand(avail, arr, cal_int)
    aux_daily = {k: expand(avail, np.asarray([e[1][k] for e in evs], dtype=np.float64), cal_int)
                 for k in aux}

    close = read_bin_full(qlib_dir, code, "close", n_cal)
    factor = read_bin_full(qlib_dir, code, "factor", n_cal)
    if close is not None and factor is not None:
        with np.errstate(invalid="ignore", divide="ignore"):
            raw = close / factor                      # 未复权价（实测口径 ✓）
            mc = aux_daily["_shares"] * raw           # 当日总市值
            # ⚠ 键名必须用**完整字段名**（'ftdx_pe_ttm'）—— 下面统一按字段名取值 ✓
            #   （第一版写成 'pe_ttm' ⇒ 写进了一个没人读的键 ⇒ PE/PB 静默全 NaN ✗，2026-10-09 踩过）
            daily["ftdx_pe_ttm"] = np.where(np.isfinite(mc) & (aux_daily["_np_ttm"] != 0),
                                            mc / aux_daily["_np_ttm"], np.nan)
            daily["ftdx_pb"] = np.where(np.isfinite(mc) & (aux_daily["_equity"] > 0),
                                        mc / aux_daily["_equity"], np.nan)

    res: Dict[str, Optional[float]] = {}
    written = 0
    for fld, _u in FINANCE_TDX_FIELDS:
        arr = daily.get(fld)
        if arr is None:
            continue
        finite = np.isfinite(arr)
        if not finite.any():
            continue
        last = int(np.where(finite)[0][-1])
        res[fld] = round(float(arr[last]), 4)
        if verify:
            continue
        p = qlib_dir / "features" / code / ("%s.day.bin" % fld)
        if p.exists() and not force:
            continue
        first = int(np.where(finite)[0][0])
        write_bin(p, first, arr[first:last + 1])
        written += 1
    res["_written"] = written
    if verify:
        ad = int(evs[-1][0])
        pos = int(np.searchsorted(cal_int, ad, side="left"))
        res["_latest"] = "报告期 %s / 可用日 %s（首个交易日 %s）" % (
            evs[-1][1].get("_quarter"), np.datetime64(ad, "D"),
            cal[pos] if pos < len(cal) else "超出日历")
    return res


def write_meta(qlib_dir: Path, stats: Dict) -> None:
    payload = dict(stats)
    payload["finance_tdx_semantics"] = FINANCE_TDX_SEMANTICS
    payload["written_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["fields"] = [{"q": i + 1, "field": f, "unit": u} for i, (f, u) in enumerate(FINANCE_TDX_FIELDS)]
    payload["pit_rule"] = "法定披露截止日（一季报 4/30、中报 8/31、三季报 10/31、年报次年 4/30）"
    try:
        p = qlib_dir / "features" / FIN_TDX_META_NAME
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception as e:                                    # noqa: BLE001
        print("[warn] 写 meta 失败：%s" % e)


def _default_qlib_dir() -> str:
    env = os.environ.get("QLIB_PROVIDER_URI")
    return env or str(Path(__file__).resolve().parents[2] / "data" / "cn_data")


def main():
    ap = argparse.ArgumentParser(description="通达信 gpcw 财务包 → qlib ftdx_* 字段 bin")
    ap.add_argument("--cw-dir", default=DEFAULT_CW_DIR)
    ap.add_argument("--qlib-dir", default=_default_qlib_dir())
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--codes", default="", help="只处理指定 qlib 代码（逗号分隔）")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    qlib_dir = Path(args.qlib_dir)
    cal, cal_int = load_calendar(qlib_dir)
    cmap = known_codes(qlib_dir)
    print("日历 %d 天（%s ~ %s）；features 里识别到 %d 只股票；财务包目录 %s"
          % (len(cal), cal[0], cal[-1], len(cmap), args.cw_dir), flush=True)

    if args.verify:
        cmap = {k: v for k, v in cmap.items() if v in {c.strip() for c in args.codes.split(",") if c.strip()}}
    print("开始解析财务包 ...", flush=True)
    events = build_events(args.cw_dir, cmap, progress=lambda m: print(m, flush=True))
    codes = sorted(events)
    if args.limit:
        codes = codes[:args.limit]
    print("解析完成：%d 只股票有财报事件（用时 %.0fs）" % (len(codes), time.time() - t0), flush=True)

    if args.verify:
        print("\n%-10s %-9s %-14s %20s %20s" % ("代码", "q", "字段", "通达信源", "米筐源(fin_*)"))
        for code in codes:
            res = dump_one(code, events[code], qlib_dir, cal, cal_int, force=False, verify=True)
            for i, (fld, _u) in enumerate(FINANCE_TDX_FIELDS, 1):
                ref = read_bin_full(qlib_dir, code, fld.replace("ftdx_", "fin_"), cal_int.size)
                ref_last = None
                if ref is not None and np.isfinite(ref).any():
                    ref_last = round(float(ref[np.where(np.isfinite(ref))[0][-1]]), 4)
                print("%-10s %-9s %-14s %20s %20s" % (code, i, fld, res.get(fld), ref_last))
            print("  最近一期：%s\n" % res.get("_latest"))
        return

    written = empty = 0
    n_bins = 0
    for i, code in enumerate(codes, 1):
        try:
            res = dump_one(code, events[code], qlib_dir, cal, cal_int, force=args.force, verify=False)
        except Exception as e:                                # noqa: BLE001
            print("[warn] %s 失败：%s: %s" % (code, type(e).__name__, e), flush=True)
            empty += 1
            continue
        nf = sum(1 for k in res if not k.startswith("_"))
        if nf:
            written += 1
            n_bins += int(res.get("_written", 0))
        else:
            empty += 1
        if i % 300 == 0 or i == len(codes):
            el = time.time() - t0
            print("  进度 %d/%d  有数据 %d / 空 %d  本轮写盘 %d 个 bin  %.0fs  剩余约 %.0fs"
                  % (i, len(codes), written, empty, n_bins, el, el / i * (len(codes) - i)), flush=True)

    stats = {
        "source_cw_dir": args.cw_dir,
        "source_packs": len(list_packs(args.cw_dir)),
        "calendar_last_day": cal[-1],
        "stocks_total": len(codes),
        "stocks_written": written,
        "stocks_empty": empty,
        "bin_files": n_bins,
        "force": bool(args.force),
    }
    write_meta(qlib_dir, stats)
    print("\n完成：%d 只，有数据 %d，空 %d，写盘 %d 个 bin，用时 %.0fs"
          % (len(codes), written, empty, n_bins, time.time() - t0), flush=True)
    for k, v in stats.items():
        print("  %s: %s" % (k, v), flush=True)


if __name__ == "__main__":
    main()
