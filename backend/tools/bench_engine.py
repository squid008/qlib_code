# -*- coding: utf-8 -*-
"""`run_backtest` 基准工具（★ P2-C，2026-10-18 ✓）。

## 为什么需要它

P2-C 的目标是"撮合提速 ≥5× 且**净值逐位不变**" ✓。没有可重复的基准 ✗，
就只能靠"感觉快了"✗ —— 本项目已经因为"面板墙钟波动 ±10%"吃过一次结论反的亏 ✓。
所以先有**尺子** ✓（本文件），再谈优化 ✓。

## 用法

    cd D:\\quant\\qlib_code\\backend
    python tools/bench_engine.py                     # 默认：200 只 × 500 日，每 5 日调仓
    python tools/bench_engine.py --codes 300 --days 800 --hold 20
    python tools/bench_engine.py --cadence 1         # 每天都调仓 ⇒ **成交密集**（贴近 262 万笔那种 ✓）

输出：首跑/二跑耗时、成交/拒单笔数，以及 **NAV + 成交明细的校验和** ✓ ——
优化前后跑同一个配置，**校验和必须一致** ✓（不一致就是改了数字 ✗）。

## ⚠ 数据是**合成**的 ✓

不依赖行情库 ✓（CI 也能跑 ✓）：几何随机游走 + 1% 停牌 + 涨跌停价 ✓。
它衡量的是**撮合/统计代码本身**的开销 ✓ —— 正是 P2-C 要优化的东西 ✓。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

import numpy as np
import pandas as pd

# ⚠ 从 `backend/tools/` 直接跑时，`sys.path[0]` 是 `tools/` ✗ ⇒ 找不到 `app` ✓。
#   这里把 `backend/` 顶到最前 ✓（实测：不加就是 `ModuleNotFoundError: No module named 'app'` ✓）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.signals.engine import run_backtest            # noqa: E402


def build(codes: int, days: int, cadence: int):
    cal = pd.bdate_range("2022-01-03", periods=days)
    names = ["SH600%03d" % i for i in range(codes)]
    rng = np.random.default_rng(20261018)
    ret = rng.normal(0.0003, 0.015, size=(len(cal), len(names)))
    px = pd.DataFrame(10.0 * np.cumprod(1.0 + ret, axis=0), index=cal, columns=names)
    halt = rng.random(px.shape) < 0.005
    panel = {"CALENDAR": pd.DataFrame(index=cal),
             "CLOSE": px.mask(halt), "OPEN": px.mask(halt),
             "CLOSE_RAW": px.mask(halt), "OPEN_RAW": px.mask(halt),
             "LIMIT_UP": px * 1.1, "LIMIT_DOWN": px * 0.9,
             "_limits_inferred": True}
    ds, cs = [], []
    for d in range(0, len(cal) - 30, cadence):
        for c in names:
            ds.append(cal[d])
            cs.append(c)
    sig = pd.DataFrame({"date": pd.to_datetime(ds), "code": cs, "side": 1})
    return panel, sig


def checksum(res) -> str:
    """NAV + 成交明细的指纹 ✓（优化前后必须一致 ✓）。"""
    h = hashlib.sha1()
    nav = res.nav
    if nav is not None and len(nav):
        h.update(np.asarray(nav.to_numpy(), dtype="float64").tobytes())
        h.update("|".join(map(str, nav.columns)).encode())
    tr = res.trades
    if tr is not None and len(tr):
        h.update(tr.to_csv(index=False).encode("utf-8"))
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", type=int, default=200)
    ap.add_argument("--days", type=int, default=500)
    ap.add_argument("--cadence", type=int, default=5)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()

    panel, sig = build(a.codes, a.days, a.cadence)
    kw = dict(hold_days=a.hold, fill="t1_open", cost=0.004, capital=1e9,
              strict_limit=True, alloc_modes=("cash_even", "event_even"))
    print("规模：%d 只 × %d 日 ｜ 信号 %d 条 ｜ 每 %d 日全池调仓 ｜ hold=%d"
          % (a.codes, a.days, len(sig), a.cadence, a.hold))

    best = None
    res = None
    for k in range(max(1, a.reps)):
        t0 = time.perf_counter()
        res = run_backtest(sig, panel, **kw)
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
        print("  第 %d 次 = %.3fs ｜ 成交 %d 笔 ｜ 拒单 %d 笔"
              % (k + 1, dt, len(res.trades), len(res.rejects)))
    print("  **最快 = %.3fs** ｜ 校验和 = %s" % (best, checksum(res)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
