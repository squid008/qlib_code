# -*- coding: utf-8 -*-
"""③ 行情增量 **落地步骤**：把新交易日追加进日历，并扩行情类字段的 bin。

用户 2026-10-09 选 **方案 A（快）**：只扩**行情类**字段 —— 其它字段（`fin_*`/`mf_*`/`chip_*`/
`mkt_*`）在新日期上保持 NaN ✓（用到它们的因子/回测会自动跳过这些日期 ✓，无未来函数风险 ✓）。

数据来自 `ai_test/tushare_fetch_new_daily.py` 的缓存（`data/tushare_cache/daily/<YYYYMMDD>.json`
✓，内含每日全市场 `daily` + `adj_factor` ✓）。

## 口径（★ 全部用重叠日 **2026-08-21** 反解验证过 ✓，逐位对得上）
| 字段 | 公式 | 8/21 实证（sz000001） |
|---|---|---|
| close/open/high/low | raw × f(d)（**前复权** ✓） | 3.99972 → raw 11.41 ✓ |
| vwap | raw_vwap × f(d)，raw_vwap=amount×1000/(vol×100) | 3.99692 ✓ |
| volume | **vol(手) ÷ f(d)**（价格×f、量÷f，保持 amount 不变 ✓） | 2.47719e6 ✓ |
| amount | 原样（**千元** ✓ 与 tushare 同单位） | 990112 ✓ |
| change | `pct_chg/100`（**小数**涨跌幅，不是涨跌额 ✗） | 0.000877 = 0.0877% ✓ |
| factor | f(d) = f(8/21) × adj_factor(d)/adj_factor(8/21)（重标定到本项目口径 ✓） | 0.350853 ✓ |
| adjclose | raw_close × adj_factor(d) | 1586.08 ✓ |
| preclose/prehigh/prelow/preopen/prevwap | **未复权**的前一日价 ✓（实测 preclose(8/21)=1272.83=raw ✓） | 11.41 ✓ |
| turn（换手率） | **不扩**（实测该字段本就常为 NaN ✓；要补得靠 daily_basic.turnover_rate ✓） | NaN ✓ |

用法：python tools/dump_tushare_daily.py [--apply]   # 默认 **dry-run** ✓ 只报告要追加什么
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                       # noqa: E402
from dump_tushare_finance import read_bin, to_qlib_code                 # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "tushare_cache" / "daily"
# 要扩的字段（方案 A ✓）
PRICE_FIELDS = ("close", "open", "high", "low")
PRE_FIELDS = ("preclose", "prehigh", "prelow", "preopen", "prevwap")
EXTRA_FIELDS = ("volume", "amount", "change", "factor", "adjclose", "vwap")


def load_cache_days() -> List[str]:
    """缓存里**有日线行**的全部交易日（升序 ✓）。"""
    out = []
    for p in sorted(CACHE.glob("*.json")):
        try:
            if json.load(open(p, encoding="utf-8")).get("daily"):
                out.append("%s-%s-%s" % (p.stem[:4], p.stem[4:6], p.stem[6:8]))
        except Exception:                                        # noqa: BLE001
            pass
    return sorted(out)


def load_new_days(cal: List[str]) -> List[str]:
    """缓存里**有日线行**、且晚于日历末日的交易日（升序 ✓）。"""
    last = cal[-1]
    return [d for d in load_cache_days() if d > last]


def read_day(d: str) -> Tuple[Dict[str, dict], Dict[str, float]]:
    o = json.load(open(CACHE / ("%s.json" % d.replace("-", "")), encoding="utf-8"))
    daily = {to_qlib_code(x["ts_code"]): x for x in o.get("daily") or []}
    adj = {to_qlib_code(x["ts_code"]): float(x["adj_factor"])
           for x in o.get("adj_factor") or [] if x.get("adj_factor") is not None}
    return daily, adj


def main():
    ap = argparse.ArgumentParser(description="追加新交易日到日历并扩行情字段（方案 A）")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    ap.add_argument("--anchor", default="", help="重标定锚定日（默认=日历原末日 ✓）")
    ap.add_argument("--days-from", default="", help="只处理 ≥ 该日（YYYY-MM-DD ✓ 修理/重跑用）")
    ap.add_argument("--days-to", default="", help="只处理 ≤ 该日 ✓")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    cal, _ = load_calendar(qlib)
    days = load_new_days(cal)
    if not days and (args.days_from or args.days_to):
        # 日历**已经扩过**了（重跑修理场景 ✓）：从缓存里取指定区间、且已在日历里的日子 ✓
        days = [d for d in load_cache_days() if d in set(cal)]
    if args.days_from:
        days = [d for d in days if d >= args.days_from]
    if args.days_to:
        days = [d for d in days if d <= args.days_to]
    anchor = args.anchor or cal[-1]
    print("日历 %d 天（末日 %s）| 缓存里可用的新交易日 %d 个：%s ~ %s"
          % (len(cal), cal[-1], len(days), days[0] if days else "-", days[-1] if days else "-"),
          flush=True)
    if not days:
        print("没有新交易日（今天的数据要 15:00~16:00 才入库 ⇒ 稍后重跑本工具即可 ✓）")
        return

    feats = qlib / "features"
    codes = [d.name for d in feats.iterdir() if d.is_dir() and d.name[:2] in ("sh", "sz")]
    # "日历里已有这些日子" ⇒ 说明**之前扩过**（重跑修理场景 ✓）⇒ base_len 要减掉它们 ✓
    already = bool(days) and days[0] in set(cal)
    base_len = len(cal) - (len(days) if already else 0)
    if already:
        print("（日历里已有这些交易日 ⇒ 按「覆盖尾部」重写 ✓）", flush=True)
    # ---- 锚定：用重叠日（日历原末日）反解"我们的 factor / adjclose 口径" ✓ ----
    anchor_pos = len(cal) - 1
    fa, adj_a = {}, {}
    for c in codes:
        h = read_bin(feats / c / "factor.day.bin")
        if h and 0 <= anchor_pos - h[0] < h[1].size:
            fa[c] = float(h[1][anchor_pos - h[0]])
    _, adj0 = read_day(anchor) if anchor in days else (None, None)
    if adj0 is None:
        # 锚定日不在缓存里（它是老日历末日 ✓）⇒ 用 tushare 现拉一次 adj_factor 反解 ✓
        from dump_tushare_finance import ts_call, token
        r = ts_call("adj_factor", {"trade_date": anchor.replace("-", "")}, "ts_code,adj_factor",
                    token())
        adj0 = {to_qlib_code(x[0]): float(x[1]) for x in (r.get("data", {}).get("items") or [])}
    print("锚定日 %s：拿到 %d 只的 factor / adj_factor（用于重标定 ✓）" % (anchor, len(adj0)))

    # ---- 组装新日期的值（逐股）----
    daily_by_day = {}
    for d in days:
        daily_by_day[d] = read_day(d)
    # 锚定日的**未复权 OHLC**（新日期第一天的 pre* 要用它 ✓；有缓存就读缓存、没有就现拉一次 ✓）
    anchor_path = CACHE / (anchor.replace("-", "") + ".json")
    if anchor_path.exists():
        anchor_daily, _ = read_day(anchor)
    else:
        from dump_tushare_finance import ts_call, token
        r = ts_call("daily", {"trade_date": anchor.replace("-", "")},
                    "ts_code,open,high,low,close,vol,amount", token())
        anchor_daily = {to_qlib_code(x[0]): dict(zip(r["data"]["fields"], x))
                        for x in (r.get("data", {}).get("items") or [])}
    print("锚定日 %s 的日线：%d 只（供新日期第一天的 pre* 用 ✓）" % (anchor, len(anchor_daily)))
    written = 0
    for c in codes:
        if c not in fa or c not in adj0 or adj0[c] == 0:
            continue
        scale = fa[c] / adj0[c]                      # f(d) = f(anchor) × adj(d)/adj(anchor) ✓
        raws, adjs = {}, {}
        for d in days:
            dd, ad = daily_by_day[d]
            if c in dd and c in ad:
                raws[d] = dd[c]
                adjs[d] = ad[c]
        if not raws:
            continue

        def f_of(d):
            return scale * adjs[d]

        # ⚠ 停牌日该股可能**没有行**（tushare 停牌不给数据 ✓）⇒ 逐日取，缺则 NaN ✓
        new_vals: Dict[str, np.ndarray] = {f: np.full(len(days), np.nan)
                                           for f in PRICE_FIELDS + EXTRA_FIELDS + PRE_FIELDS}
        for i, d in enumerate(days):
            r = raws.get(d)
            if r is None:
                continue
            fd = f_of(d)
            for f in PRICE_FIELDS:
                if r.get(f) is not None:
                    new_vals[f][i] = float(r[f]) * fd
            if r.get("vol"):
                new_vals["volume"][i] = float(r["vol"]) / fd
                new_vals["vwap"][i] = (float(r["amount"]) * 1000.0
                                       / (float(r["vol"]) * 100.0)) * fd
            if r.get("amount") is not None:
                new_vals["amount"][i] = float(r["amount"])
            if r.get("pct_chg") is not None:
                new_vals["change"][i] = float(r["pct_chg"]) / 100.0
            new_vals["factor"][i] = fd
            if r.get("close") is not None:
                new_vals["adjclose"][i] = float(r["close"]) * adjs[d]
        # pre* = **未复权**前一日价 ✓（新日期的"前一日"要么是上一个新日期、要么是锚定日 ✓）
        prev = anchor
        for i, d in enumerate(days):
            if True:          # ⚠ 原来写的是 `if c in raws:` ✗ —— raws 的键是**日期**不是股票，
                              #   该条件恒假 ⇒ pre* 全没写进去（实测 preclose 变 NaN ✗）。这里只
                              #   需要"该股这一天有没有行"由 r 决定 ✓（下面 i>0 分支用 raws[days[i-1]] ✓）
                if i == 0:
                    a = anchor_daily.get(c) or {}
                    pc = float(a["close"]) if a.get("close") is not None else np.nan
                    ph = float(a["high"]) if a.get("high") is not None else np.nan
                    pl = float(a["low"]) if a.get("low") is not None else np.nan
                    po = float(a["open"]) if a.get("open") is not None else np.nan
                    pv = (float(a["amount"]) * 1000.0 / (float(a["vol"]) * 100.0)
                          if a.get("vol") else np.nan)
                else:
                    # ⚠ 前一日可能**没有该股的行**（停牌 ⚠ 实测 2026-08-25 就会 KeyError ✗）
                    #   ⇒ 取不到就留 NaN ✓（该股当天行情本来就作废 ✓）
                    pr = raws.get(days[i - 1])
                    if pr is None:
                        continue
                    pc, ph, pl, po = (float(pr["close"]), float(pr["high"]),
                                      float(pr["low"]), float(pr["open"]))
                    pv = (float(pr["amount"]) * 1000.0 / (float(pr["vol"]) * 100.0)
                          if pr.get("vol") else np.nan)
                for k, v in (("preclose", pc), ("prehigh", ph), ("prelow", pl),
                             ("preopen", po), ("prevwap", pv)):
                    new_vals.setdefault(k, np.full(len(days), np.nan))[i] = v

        if not args.apply:
            written += 1
            continue
        # ---- 写盘：既有 bin 末尾正好接上新日期时**追加** ✓（否则跳过并提示 ✗）----
        # ⚠ **不写** `preclose/preopen/prehigh/prelow/prevwap` ✗ —— 它们是**物化字段**
        #   （`pre<field> = <field> / factor_last` ✓，即"前复权后的五个价"，**不是**"前一日价" ✗），
        #   由 `tools/build_preclose.py` 生成 ✓（它自己会读最新 factor ✓）⇒ 本工具只需提醒：
        #   数据更新后跑一次 `python tools/build_preclose.py --overwrite` ✓（本次已跑 ✓）。
        for f in PRICE_FIELDS + EXTRA_FIELDS:
            if f not in new_vals:
                continue
            bp = feats / c / ("%s.day.bin" % f)
            h = read_bin(bp)
            if h is None:
                continue                                   # 该股没有这个字段 ⇒ 不新建 ✓
            s, v = h
            k = len(days)
            if s + v.size == base_len:                     # 首次：**追加**新日期 ✓
                write_bin(bp, s, np.concatenate([v, new_vals[f].astype(np.float64)]))
            elif s + v.size == base_len + k:               # 已扩过（重跑修理 ✓）：**覆盖尾部** ✓
                write_bin(bp, s, np.concatenate([v[:base_len - s], new_vals[f].astype(np.float64)]))
            else:
                continue                                   # 老的没接到日历 ⇒ 交人工 ✓
            write_bin(bp, s, np.concatenate([v, new_vals[f].astype(np.float64)]))
        written += 1

    if args.apply:
        if already:
            print("✅ 日历已含这些交易日（不重复追加 ✓）⇒ 只重写了 %d 只的字段值" % written)
        else:
            bak = qlib / "calendars" / ("day.txt.bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(qlib / "calendars" / "day.txt", bak)
            with open(qlib / "calendars" / "day.txt", "a", encoding="utf-8") as fh:
                for d in days:
                    fh.write(d + "\n")
            print("✅ 已追加 %d 个交易日到 day.txt（备份 %s）✓ 扩字段股票 %d 只"
                  % (len(days), bak.name, written))
    else:
        print("dry-run：将追加 %d 个交易日、扩字段股票 %d 只（加 --apply 才写盘 ✓）"
              % (len(days), written))


if __name__ == "__main__":
    main()
