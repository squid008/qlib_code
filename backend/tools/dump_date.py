# -*- coding: utf-8 -*-
"""物化 `DATE` 字段（`features/{code}/date.day.bin`，2026-10-15 ✓）。

## 为什么必须物化

- **qlib 路径**（回测 / 训练 / `D.features`）只能按 bin 读特征（`$date`）——盘上**没有**日期字段 ✗
  ⇒ 不物化的话 `$date` 在回测链读不到（报"特征不存在"✗）；
- **面板路径**（单因子测试 / 事件研究）可以由面板 index **现合成**（`panel_expr.field("$date")` ✓
  零 IO ✓，`panel_expr.py` 里那个分支 ✓）；
- 两条路径**必须同值** ⇒ 换算统一由 `app/factors/calc_date.py` 提供 ✓
  （`tests/test_date_field.py` 逐位对拍"面板合成值 vs bin 值" ✓）。

## 值是什么（★ 千万别改成 YYYYMMDD ✗）

**日序号**（距 1970-01-01 的天数，如 2026-03-05 → 20517）。为什么不是 YYYYMMDD：
qlib bin 是 **float32**（`<f4`），只能精确表示 ≤ 2**24 = 16,777,216 的整数 ⇒ 实测
`np.float32(20260305) = 20260304`、`np.float32(20070901) = 20070900` ✗✗
（"2016-07-23 之后的日期"以及"截止日自己"都会失真 ✗）。日序号（~2 万）精确 ✓。

## 用法（在 backend 目录下）

    python tools/dump_date.py --dry-run                     # 只统计（当前激活数据集）
    python tools/dump_date.py                               # 写（已存在则跳过）
    python tools/dump_date.py --dataset-dir ../data/cn_data # 指定数据集（两套都要有 ✓）
    python tools/dump_date.py --overwrite                   # 全量重写（改了换算口径才需要）

⚠ **每个数据集各写一份** ✗：bin 的 header 是"该数据集日历里的下标"✓，日期轴必须与**同目录**的
`close.day.bin` 逐位一致（否则多股一起加载时 qlib 报
`Can only compare identically-labeled Series objects`，筹码物化踩过 ✓）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.factors import market_stat  # noqa: E402
from app.factors.calc_date import DATE_FIELD, serial_array  # noqa: E402


def _read_calendar(dataset_dir: str) -> pd.DatetimeIndex:
    """读 `<dataset>/calendars/day.txt`（纯日期列表，**没有**偏移首行 ✓，实测两个数据集都是 ✓）。"""
    p = os.path.join(dataset_dir, "calendars", "day.txt")
    with open(p, "r", encoding="utf-8") as f:
        days = [ln.strip() for ln in f if ln.strip()]
    return pd.to_datetime(days)


def _bin_info(path: str):
    """读 `.day.bin` 的 (起始日历下标, 长度)；读不了返回 None ✓。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(4)
            if len(raw) < 4:
                return None
            first = int(np.frombuffer(raw, dtype="<f4")[0])
            size = os.path.getsize(path)
    except OSError:
        return None
    return first, (size - 4) // 4


def run(dataset_dir: str, overwrite: bool, dry_run: bool) -> int:
    fdir = os.path.join(dataset_dir.rstrip("/\\"), "features")
    if not os.path.isdir(fdir):
        print("特征目录不存在：%s" % fdir)
        return 1
    cal = _read_calendar(dataset_dir)
    ser_all = serial_array(cal)                  # 每个日历日 → 日序号（与面板同一换算 ✓）
    print("数据集目录：%s" % dataset_dir)
    print("日历：%s ~ %s（%d 天）" % (cal[0].date(), cal[-1].date(), len(cal)))

    codes = sorted(d for d in os.listdir(fdir) if os.path.isdir(os.path.join(fdir, d)))
    t0 = time.time()
    n_ok = n_skip = n_noclose = n_err = 0
    samples = []
    for code in codes:
        close_path = os.path.join(fdir, code, "close.day.bin")
        if not os.path.exists(close_path):
            n_noclose += 1
            continue
        info = _bin_info(close_path)
        if info is None or info[1] <= 0:
            n_err += 1
            continue
        first, n_ref = info
        if first + n_ref > len(cal):
            print("[warn] %s 超出日历（first=%d, n=%d > %d）⇒ 跳过" % (code, first, n_ref, len(cal)))
            n_err += 1
            continue
        path = os.path.join(fdir, code, "%s.day.bin" % DATE_FIELD)
        if os.path.exists(path) and not overwrite:
            n_skip += 1
            continue
        vals = ser_all[first:first + n_ref]
        if not dry_run:
            market_stat._write_bin(path, first, vals)
        n_ok += 1
        if len(samples) < 3:
            samples.append("%s：%d 格，日序号 %d ~ %d" % (code, n_ref, int(vals[0]), int(vals[-1])))
    for s in samples:
        print("  例 %s" % s)
    print("%s：%d 只%s ｜ 已存在跳过 %d ｜ 无 close %d ｜ 异常 %d ｜ 用时 %.1fs"
          % ("将写" if dry_run else "已写", n_ok, "（dry-run，未写盘 ✓）" if dry_run else "",
             n_skip, n_noclose, n_err, time.time() - t0))
    return 0


def main():
    ap = argparse.ArgumentParser(description="物化 DATE 字段（features/{code}/date.day.bin）")
    ap.add_argument("--dataset-dir", default=None,
                    help="数据集目录（默认 = 当前激活数据集，如 ../data/cn_data2）")
    ap.add_argument("--all-datasets", action="store_true",
                    help="把 data/ 下所有数据集各写一份（cn_data / cn_data2 ✓）")
    ap.add_argument("--overwrite", action="store_true", help="已存在也覆盖重写")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写盘")
    args = ap.parse_args()

    if args.all_datasets:
        root = Path(market_stat.QLIB_PROVIDER_URI).resolve().parent
        # 平台在用的两套（cn_data 米筐 / cn_data2 tushare ✓）；
        # ⚠ `cn_data3` 是**原始 qlib 数据**（用户 2026-10-14 定："不用管" ✓）⇒ 默认不写 ✓，
        #   确需写它就用 `--dataset-dir ../data/cn_data3` 显式指定 ✓。
        _ACTIVE = ("cn_data", "cn_data2")
        dirs = [str(d) for d in sorted(root.iterdir())
                if d.name in _ACTIVE and (d / "calendars" / "day.txt").exists()]
        if not dirs:
            print("没找到数据集目录（%s 下无 cn_data/cn_data2）" % root)
            return 1
    elif args.dataset_dir:
        dirs = [os.path.abspath(args.dataset_dir)]
    else:
        dirs = [str(market_stat.QLIB_PROVIDER_URI)]

    rc = 0
    for i, d in enumerate(dirs):
        if i:
            print("-" * 80)
        rc = max(rc, run(d, args.overwrite, args.dry_run))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
