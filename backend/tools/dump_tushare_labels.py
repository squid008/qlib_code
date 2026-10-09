# -*- coding: utf-8 -*-
"""tushare → **涨跌停价 + ST 状态**三个字段（字段名与 `dump_states.py` 完全一致 ✓）。

字段（与米筐那版同源同义 ⇒ 应用层零改动 ✓）：
  · `limit_up`  当日**涨停价**（元）← `stk_limit.up_limit`
  · `limit_down`当日**跌停价**（元）← `stk_limit.down_limit`
  · `is_st`     **是否风险警示（ST/*ST）**，1/0 ← `stock_st`（**3000 积分档起可用** ✓）

为什么有它：米筐 bundle 是快照（these 三列只到 2026-08-21 ✗），tushare 可续到最新交易日 ✓。
⚠ 覆盖率差异：`stk_limit`/`stock_st` 实测**2010 起有数据**（2005 查得 0 行 ✗）⇒ 本工具默认
  `--start 2010-01-01`，且写盘时**与既有 bin 合并**（先把老值读进来再覆盖 ✓）⇒ 2010 之前
  仍是米筐值 ✓（**绝不截断历史** ✗✗ —— 直接按 tushare 覆盖会把 2000~2010 抹掉 ✗）。

用法：python tools/dump_tushare_labels.py [--start 2010-01-01] [--limit-days 20] [--force]
成本：每交易日 2 次调用 ⇒ 2010~今 ≈ 4050 天 ≈ 8100 次 ≈ **45 分钟**（200 次/分钟档 ✓）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                      # noqa: E402
from dump_tushare_finance import read_bin, to_qlib_code, ts_call, token  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("limit_up", "limit_down", "is_st")


def main():
    ap = argparse.ArgumentParser(description="tushare → limit_up/limit_down/is_st（与米筐版字段同名 ✓）")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--start", default="2010-01-01", help="起始交易日（tushare 这两个接口实测 2010 起 ✓）")
    ap.add_argument("--limit-days", type=int, default=0, help="只处理前 N 个交易日（冒烟 ✓）")
    ap.add_argument("--rate", type=float, default=0.31, help="每次调用后的间隔秒（200/分钟档 ✓）")
    ap.add_argument("--force", action="store_true", help="保留（默认也写；此参数只为兼容习惯 ✓）")
    ap.add_argument("--verify", action="store_true", help="只统计不写盘")
    args = ap.parse_args()

    qlib_dir = Path(args.qlib_dir)
    cal, cal_int = load_calendar(qlib_dir)
    tok = token()
    feats = qlib_dir / "features"
    codes = sorted(d.name for d in feats.iterdir()
                   if d.is_dir() and d.name[:2] in ("sh", "sz", "bj") and d.name[2:].isdigit())
    n = len(cal)
    days = [d for d in cal if d >= args.start]
    if args.limit_days:
        days = days[:args.limit_days]
    print("日历 %d 天（%s ~ %s）| 股票 %d 只 | 待拉 %d 个交易日（%s 起）"
          % (n, cal[0], cal[-1], len(codes), len(days), args.start), flush=True)

    # ① 先把既有 bin 读进内存（⇒ 2010 之前保留米筐值 ✓、断点重跑幂等 ✓）
    arr: Dict[str, Dict[str, np.ndarray]] = {f: {} for f in FIELDS}
    for c in codes:
        for f in FIELDS:
            a = np.full(n, np.nan, dtype=np.float32)
            hit = read_bin(feats / c / ("%s.day.bin" % f))
            if hit is not None:
                s, v = hit
                a[s:s + v.size] = v.astype(np.float32)
            arr[f][c] = a
    print("已载入既有字段值 ✓", flush=True)

    # ② 逐交易日拉取（全市场各 1 次 ✓）
    pos_of = {d: i for i, d in enumerate(cal)}
    t0 = time.time()
    for k, d in enumerate(days, 1):
        pos = pos_of[d]
        tsd = d.replace("-", "")
        r1 = ts_call("stk_limit", {"trade_date": tsd}, "ts_code,up_limit,down_limit", tok)
        d1 = r1.get("data") or {}
        for row in d1.get("items") or []:
            code = to_qlib_code(row[0])
            if code in arr["limit_up"]:
                arr["limit_up"][code][pos] = row[1] if row[1] is not None else np.nan
                arr["limit_down"][code][pos] = row[2] if row[2] is not None else np.nan
        time.sleep(args.rate)
        r2 = ts_call("stock_st", {"trade_date": tsd}, "ts_code", tok)
        d2 = r2.get("data") or {}
        st_set = {to_qlib_code(row[0]) for row in (d2.get("items") or [])}
        for c in codes:
            arr["is_st"][c][pos] = 1.0 if c in st_set else 0.0
        time.sleep(args.rate)
        if k % 100 == 0 or k == len(days):
            el = time.time() - t0
            print("  进度 %d/%d（%s）ST %d 只  限价 %d 行  %.0fs  预计剩余 %.0fs"
                  % (k, len(days), d, len(st_set), len(d1.get("items") or []), el,
                     el / k * (len(days) - k)), flush=True)

    if args.verify:
        print("verify：未写盘 ✓")
        return
    # ③ 写盘（合并后的完整序列 ✓）
    written = {}
    for c in codes:
        for f in FIELDS:
            a = arr[f][c]
            finite = np.isfinite(a)
            if not finite.any():
                continue
            last = int(np.where(finite)[0][-1])
            first = int(np.where(finite)[0][0])
            write_bin(feats / c / ("%s.day.bin" % f), first, a[first:last + 1])
            written[f] = written.get(f, 0) + 1
    print("完成：写盘 %s" % written)


if __name__ == "__main__":
    main()
