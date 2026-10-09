# -*- coding: utf-8 -*-
"""补救 v1.20.92 第一版 `repair_daily_bins.py` 的缺陷 + 补齐最后一天（用户 2026-10-09）。

## 缺陷（已确认）
第一版对"超长 28"的股票写成 `drop = min(d, first)` ⇒ 当 `first >= 28` 时**也**从前端丢了 28 个值，
同时把 header 减了 28 ⇒ 净效果 = 值又整体后移 28 槽（末段 28 天变成没有值 ⇒ 价格读到 NaN ✗）。

## 现在怎么修（两步，都可验证）
1. **移回头部**：判据 `first + n == base_len - 28`（`base_len = len(cal) - 1 = 6483`）⇒ 把 header **加回 28**
   ⇒ `first + n == base_len` ✓（值不动 ✓ —— 因为那些值本来就是"日期对齐、只是头被减了 28" ✓）
2. **补最后一天**：判据 `first + n == base_len` ⇒ 用缓存补齐 `cal[first+n : len(cal)]`（即 10/09 ✓，
   公式同 `dump_tushare_daily`：`close=raw×f(d)`、`volume=vol÷f(d)`、`f(d)=f(锚)×adj(d)/adj(锚)` ✓，
   锚 = 该 bin 末日期 10/08 ✓，`f(锚)` = 该股 factor bin 末值 ✓）
   ⇒ 有**安全闸**：末值必须 ≈ 缓存(10/08) × `f(锚)` ✓，不过就不写 ✓

用法：python tools/fix_bin_alignment.py [--apply]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                       # noqa: E402
from dump_tushare_daily import (                                        # noqa: E402
    CACHE, EXTRA_FIELDS, PRICE_FIELDS, read_day,
)
from dump_tushare_finance import read_bin                               # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIELDS = PRICE_FIELDS + EXTRA_FIELDS


def main():
    ap = argparse.ArgumentParser(description="补救 bin 对齐：移回头部 28 槽 + 补最后一天")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    cal, _ = load_calendar(qlib)
    n_cal = len(cal)
    base_len = n_cal - 1                          # 对齐到 10/08 的判据 ✓
    last_day = cal[-1]                            # 10/09 ✓
    feats = qlib / "features"
    codes = sorted(d.name for d in feats.iterdir()
                   if d.is_dir() and d.name[:2] in ("sh", "sz", "bj"))
    print("日历 %d 天（%s ~ %s）| 判据：base_len=%d、末日期=%s"
          % (n_cal, cal[0], last_day, base_len, cal[base_len - 1]), flush=True)

    day_cache: Dict[str, tuple] = {}

    def cached_raw_close(day: str, code: str):
        if day not in day_cache:
            day_cache[day] = read_day(day)
        r = day_cache[day][0].get(code)
        return float(r["close"]) if (r and r.get("close") is not None) else float("nan")

    fixed = appended = skipped = 0
    mismatch = []
    for c in codes:
        bins = {}
        for f in FIELDS:
            p = feats / c / ("%s.day.bin" % f)
            h = read_bin(p) if p.exists() else None
            if h is not None:
                bins[f] = (p, h[0], h[1])
        if not bins or "factor" not in bins or "close" not in bins:
            skipped += 1
            continue
        _p0, first, v_close = bins["close"]
        n = v_close.size
        fac_last = float(bins["factor"][2][-1])

        # ---- 步骤 1：头被减了 28 的 ⇒ 加回来（值不动 ✓）----
        if first + n == base_len - 28:
            if args.apply:
                for f, (p, s, v) in bins.items():
                    write_bin(p, s + 28, v)
            first += 28
            fixed += 1
        # ---- 步骤 1b：`first==0` 的全历史股（第一版 `min(d, first)=0` ⇒ 没动过 ✓）----
        #      它的值从索引 0 对齐、真数据到 10/08（索引 6482）⇒ 尾部多出 28 个空槽 ⇒ **截掉尾部** ✓
        elif first + n == base_len + 28:
            if args.apply:
                for f, (p, s, v) in bins.items():
                    write_bin(p, s, v[:base_len - s])
            n = base_len - first
            fixed += 1                      # 计入"修过"（头部加回 / 尾部截断 都算 ✓）

        # ---- 步骤 2：对齐到 10/08 的 ⇒ 补 10/09 ✓ ----
        if first + n != base_len:
            skipped += 1
            continue
        got = float(v_close[-1])
        exp = cached_raw_close(cal[base_len - 1], c) * fac_last
        if np.isfinite(got) and np.isfinite(exp) and abs(exp) > 1e-6:
            if abs(got - exp) / abs(exp) > 5e-3:
                mismatch.append((c, round(got, 4), round(exp, 4)))
                continue
        if not args.apply:
            appended += 1
            continue
        miss = [last_day]
        new_vals = {f: np.full(len(miss), np.nan) for f in FIELDS if f in bins}
        anchor_adj = day_cache.setdefault(
            cal[base_len - 1], read_day(cal[base_len - 1]))[1].get(c)
        if not anchor_adj:
            skipped += 1
            continue
        scale = fac_last / float(anchor_adj)
        if last_day not in day_cache:
            day_cache[last_day] = read_day(last_day)
        daily, adj = day_cache[last_day]
        r, ad = daily.get(c), adj.get(c)
        if r is not None and ad is not None:
            fd = scale * ad
            for f in PRICE_FIELDS:
                if f in new_vals and r.get(f) is not None:
                    new_vals[f][0] = float(r[f]) * fd
            if r.get("vol") and "volume" in new_vals:
                new_vals["volume"][0] = float(r["vol"]) / fd
                new_vals["vwap"][0] = (float(r["amount"]) * 1000.0
                                       / (float(r["vol"]) * 100.0)) * fd
            if r.get("amount") is not None and "amount" in new_vals:
                new_vals["amount"][0] = float(r["amount"])
            if r.get("pct_chg") is not None and "change" in new_vals:
                new_vals["change"][0] = float(r["pct_chg"]) / 100.0
            if "factor" in new_vals:
                new_vals["factor"][0] = fd
            if r.get("close") is not None and "adjclose" in new_vals:
                new_vals["adjclose"][0] = float(r["close"]) * ad
        for f, (p, s, v) in bins.items():
            if f in new_vals:
                write_bin(p, s, np.concatenate([v, new_vals[f].astype(np.float64)]))
        appended += 1

    print("汇总：移回头部 %d 只 | 补 %s %d 只 | 跳过（缺口不在此列/校验不过）%d"
          % (fixed, last_day, appended, skipped))
    if mismatch:
        print("末值不匹配（未写）%d 只，样例 %s" % (len(mismatch), mismatch[:3]))
    print("写入：%s" % ("已完成 ✓" if args.apply else "dry-run（加 --apply 才写盘 ✓）"))


if __name__ == "__main__":
    main()
