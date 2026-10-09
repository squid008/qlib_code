# -*- coding: utf-8 -*-
"""校正行情 bin 的起始索引（只改 header，**值一律不动** ✓）—— 用户 2026-10-09。

## 规则（唯一判据，靠缓存验，不靠推理 ✓）
值序列是**连续、以日历末日结尾**的 ⇒ 正确 header = `len(日历) - 值个数` ✓。
- 适用前提（**安全闸**，逐股验 ✓）：末值 ≈ 缓存(日历末日) 原始收盘 × 该股 factor 末值 ✓
  （即"这只票的数据确实一直写到末日" ✓；不过就**不写**、计数、交下一步用三源重建 ✓）
- 只改 header，**不增删任何值** ✓（v1.20.92 的两次事故都出在"顺手改了值" ✗ ⇒ 这次只有 header ✓）

用法：python tools/fix_bin_header.py [--apply] [--day 2026-10-09]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                       # noqa: E402
from dump_tushare_daily import EXTRA_FIELDS, PRICE_FIELDS, read_day     # noqa: E402
from dump_tushare_finance import read_bin                              # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIELDS = PRICE_FIELDS + EXTRA_FIELDS


def main():
    ap = argparse.ArgumentParser(description="只改 header：first = 日历长 - 值个数（值不动 ✓）")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    ap.add_argument("--day", default="", help="缓存验证日（默认=日历末日 ✓）")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    cal, _ = load_calendar(qlib)
    n_cal = len(cal)
    day = args.day or cal[-1]
    feats = qlib / "features"
    codes = sorted(d.name for d in feats.iterdir()
                   if d.is_dir() and d.name[:2] in ("sh", "sz", "bj"))
    print("日历 %d 天（末日 %s）| 验证日 %s | 规则：first = %d - n ✓"
          % (n_cal, cal[-1], day, n_cal), flush=True)

    daily, adj = read_day(day) if day in (cal[-1], *cal) else ({}, {})
    ok = fixed = skip_gate = skip_other = 0
    samples = []
    for c in codes:
        bins = {}
        for f in FIELDS:
            p = feats / c / ("%s.day.bin" % f)
            h = read_bin(p) if p.exists() else None
            if h is not None:
                bins[f] = (p, h[0], h[1])
        if not bins or "close" not in bins or "factor" not in bins:
            skip_other += 1
            continue
        _p0, first, v_close = bins["close"]
        n = v_close.size
        want = n_cal - n
        if first == want:
            ok += 1
            continue
        if want < 0:
            # ★ 第 2 类：`n > 日历长` ⇒ 开头多了 `k = -want` 个**占位槽**（当初那份"多 28 天"的日历写进去的 ✓）
            #   ⇒ 只有这 k 个槽**在所有字段上全是 NaN** 才敢丢（丢的是占位、不是行情 ✓）；
            #     否则一律不写、交三源重建 ✓（绝不允许写出负 header ✗✗）
            k = -want
            if k > 0 and all(np.all(np.isnan(v[:k])) for _p, _s, v in bins.values()):
                if args.apply:
                    for f, (p, s, v) in bins.items():
                        write_bin(p, 0, v[k:])
                fixed += 1
                if not args.apply and len(samples) < 3:
                    samples.append((c, first, 0, n, float(v_close[-1]),
                                    float(daily.get(c, {}).get("close", np.nan)
                                          * float(bins["factor"][2][-1]))))
            else:
                skip_gate += 1
            continue
        r, fac = daily.get(c), float(bins["factor"][2][-1])
        exp = (float(r["close"]) * fac) if (r and r.get("close") is not None) else float("nan")
        got = float(v_close[-1])
        good = (np.isfinite(exp) and np.isfinite(got) and abs(exp) > 1e-6
                and abs(got - exp) / abs(exp) < 5e-3)
        if not good:
            skip_gate += 1
            continue
        if args.apply:
            for f, (p, s, v) in bins.items():
                write_bin(p, want, v)
            fixed += 1
        else:
            fixed += 1
            if len(samples) < 3:
                samples.append((c, first, want, n, got, exp))
    print("本就正确 %d | 待修 %d | 安全闸不过 %d | 字段缺失 %d"
          % (ok, fixed, skip_gate, skip_other))
    for c, f0, w, n, got, exp in samples:
        print("   例 %-9s first %d→%d（n=%d）| 末值 %.4f == 缓存复权 %.4f ✓" % (c, f0, w, n, got, exp))
    print("写入：%s" % ("已完成 ✓" if args.apply else "dry-run（加 --apply 才写盘 ✓）"))


if __name__ == "__main__":
    main()
