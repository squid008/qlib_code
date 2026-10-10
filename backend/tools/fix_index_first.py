# -*- coding: utf-8 -*-
"""修"指数 bin 的起始索引错位"——**只改 header，值一律不动** ✓（用户 2026-10-10："先修指数"）

## 症状（2026-10-10 实测 ✓）
`data/cn_data2`（tushare 口径、平台当前生效 ✓）里 **5 个指数**的 **10 个行情字段**
（`close/open/high/low/volume/amount/factor/adjclose/change/vwap` ✓）的 `first` 是 **1228** ✗，
而正确的是 **1200** ✓（= `cn_data` / `cn_data3` 里同代码同字段的值 ✓）。后果：
指数的**收盘序列被整体贴到晚 28 个交易日**的日期上 ✗ ⇒ 单因子/回测页的**基准（沪深300等）**
日期全错 ⇒ 年化也不同（用户对照测试：米筐 −1.1% ✓ / tushare +0.1% ✗）。

真值判据（不靠推理，靠**可核对的真实历史** ✓）：
- 2024-09-30 沪深300 **+8.48%**（9·24 行情那天 ✓）、2025-04-07 **−7.05%**（关税大跌 ✓）、
  2024-10-09 **−7.05%** —— 这三个真实日子都落在 `first=1200` 的标法上 ✓，`1228` 的标法把它们
  分别挪到 2024-11-14 / 2025-05-20 / 2024-11-18 ✗。

## 安全闸（三条全过才写 ✓）
1. 目标 bin 的 **值序列** 必须与对照集（默认 `data/cn_data` ✓）同代码同字段**逐位一致**
   （相对差 ≤ 1e-6 ✓）⇒ 证明"只是 header 错、数据本是同一串" ✓；
2. 采用对照集的 `first` 后，必须满足 `first >= 0` 且 `first + n <= 日历长` ✓；
3. 只动 header（**绝不增删值** ✗ —— v1.20.92/93 的事故都出在"顺手改值" ✗）。

用法：
    python tools/fix_index_first.py [--qlib-dir ...] [--ref-dir ...] [--apply]
默认 dry-run ✓；`--apply` 前会把原文件备份到 `ai_test/backup_index_fix_<时间戳>/` ✓。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                       # noqa: E402
from dump_tushare_finance import read_bin                               # noqa: E402

# 出问题的指数（2026-10-10 实测：first=1228 应为 1200 ✓）
INDEX_CODES = ("sh000300", "sh000905", "sh000906", "sh000852", "sz399300")
# 受影响的行情字段（派生字段 chip_*/turn/mkt_* 本来就是 1200 ✓ 不动）
PRICE_FIELDS = ("close", "open", "high", "low", "volume", "amount",
                "factor", "adjclose", "change", "vwap")


def active_dir() -> str:
    """默认目标 = 平台当前生效的数据集（读 `data/active_dataset.json` ✓；不猜 ✗）。"""
    try:
        import json
        with open(ROOT / "data" / "active_dataset.json", "r", encoding="utf-8-sig") as fh:
            return str(json.load(fh).get("dir") or "")
    except Exception:                                                   # noqa: BLE001
        return ""


def rel_diff(a: np.ndarray, b: np.ndarray) -> float:
    n = min(a.size, b.size)
    x, y = a[:n], b[:n]
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() == 0:
        return 0.0 if x.size == y.size else float("inf")
    if not np.array_equal(np.isfinite(x), np.isfinite(y)):
        return float("inf")
    return float(np.max(np.abs(x[m] - y[m]) / np.maximum(np.abs(y[m]), 1e-12)))


def main():
    ap = argparse.ArgumentParser(description="修指数 bin 的 first（只改 header ✓）")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI") or active_dir()
                    or str(ROOT / "data" / "cn_data2"), help="要修的数据集（默认=当前生效的 ✓）")
    ap.add_argument("--ref-dir", default=str(ROOT / "data" / "cn_data"),
                    help="对照集（默认 data/cn_data = 米筐源、日期已核对 ✓）")
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    ap.add_argument("--tol", type=float, default=1e-6, help="值逐位一致的相对容差")
    args = ap.parse_args()

    tgt, ref = Path(args.qlib_dir), Path(args.ref_dir)
    cal, _ = load_calendar(tgt)
    n_cal = len(cal)
    print("目标 %s（日历 %d 天，末 %s）" % (tgt, n_cal, cal[-1]))
    print("对照 %s" % ref)
    print("模式：%s\n" % ("APPLY（真写盘 ✓）" if args.apply else "DRY-RUN（只看不改 ✓）"))

    backup = ROOT / "ai_test" / ("backup_index_fix_%s" % time.strftime("%Y%m%d-%H%M%S"))
    n_fix = n_ok = n_skip = 0
    for code in INDEX_CODES:
        for field in PRICE_FIELDS:
            pt = tgt / "features" / code / ("%s.day.bin" % field)
            pr = ref / "features" / code / ("%s.day.bin" % field)
            if not pt.exists() or not pr.exists():
                print("  %-9s %-9s 缺文件（目标 %s / 对照 %s）⇒ 跳过" % (code, field, pt.exists(), pr.exists()))
                n_skip += 1
                continue
            ht = read_bin(pt)
            hr = read_bin(pr)
            if ht is None or hr is None:
                n_skip += 1
                continue
            first_t, val_t = ht
            first_r, val_r = hr
            if first_t == first_r:
                n_ok += 1
                continue
            # 安全闸 1：值序列必须逐位一致（只是 header 错 ✓）
            d = rel_diff(val_t, val_r)
            if d > args.tol:
                print("  ✗ %-9s %-9s 值与对照不一致（最大相对差 %.2e）⇒ **不写**（交三源重建 ✗）"
                      % (code, field, d))
                n_skip += 1
                continue
            # 安全闸 2：新 header 合法
            if first_r < 0 or first_r + val_t.size > n_cal:
                print("  ✗ %-9s %-9s 采用 first=%d 后越界（first+n=%d > %d）⇒ 不写"
                      % (code, field, first_r, first_r + val_t.size, n_cal))
                n_skip += 1
                continue
            print("  ✓ %-9s %-9s first %d → %d（n=%d，值逐位一致，最大相对差 %.1e）"
                  % (code, field, first_t, first_r, val_t.size, d))
            n_fix += 1
            if args.apply:
                backup.mkdir(parents=True, exist_ok=True)
                bdst = backup / ("%s_%s.day.bin" % (code, field))
                if not bdst.exists():
                    shutil.copy2(pt, bdst)
                write_bin(pt, int(first_r), val_t)                       # 只改 header ✓

    print("\n合计：待修 %d 个 bin / 已一致 %d / 跳过 %d" % (n_fix, n_ok, n_skip))
    if args.apply and n_fix:
        print("原文件已备份到 %s ✓" % backup)
    elif not args.apply:
        print("（dry-run：未写盘 ✓；加 --apply 生效 ✓）")


if __name__ == "__main__":
    main()
