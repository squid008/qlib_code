# -*- coding: utf-8 -*-
"""量「持仓周期 k 越长 ⇒ 净值回测越慢」的规律（用户 2026-09-21 观察 ✓）。

假设：`run_backtest` 的每日成本 ≈ O(交易日 × **并发持仓数**)，
      而 k 越长 ⇒ 同时挂着的票越多 ⇒ 每日盯市/到期判定/再平衡遍历都变长 ⇒ 线性变慢 ✓。
本脚本对同一批 过顶 事件、同一面板，只改 `hold_days`，记录耗时与成交/被拒数 ✓。
"""
import json
import os
import sys
import time

sys.path.insert(0, r"d:\quant\qlib_code\backend")

from app.services.qlib_runtime import ensure_qlib_init  # noqa: E402

ensure_qlib_init()

import pandas as pd  # noqa: E402

from app.factors.panel_expr import PanelEvaluator  # noqa: E402
from app.signals.engine import run_backtest  # noqa: E402
from app.signals.pricing import fill_limits, load_price_panel  # noqa: E402

D = r"d:\quant\qlib_code\data\cn_data\features"
FP = r"d:\quant\qlib_code\backend\workdir\custom_formulas.json"
START, END = "2021-01-01", "2026-12-31"
N = 600
KS = (5, 20, 50, 60, 120)


def _f(name):
    items = json.load(open(FP, encoding="utf-8"))
    items = items.get("items") if isinstance(items, dict) else items
    for it in items:
        if str(it.get("name")) == name:
            return it.get("expression") or it.get("text") or ""
    raise KeyError(name)


def main():
    all_codes = sorted(n for n in os.listdir(D) if os.path.isdir(os.path.join(D, n)))
    step = max(1, len(all_codes) // N)
    codes = all_codes[::step][:N]
    ev = PanelEvaluator(codes, START, END)
    s = ev.eval_expr(_f("过顶"))
    trig = (s > 0) & s.notna()
    idx = trig[trig].index
    events = pd.DataFrame({"date": pd.to_datetime(idx.get_level_values(1)),
                           "code": idx.get_level_values(0).astype(str).str.upper(),
                           "side": 1})
    t0 = time.perf_counter()
    panel = fill_limits(load_price_panel(sorted(set(events["code"])), START, END,
                                        need_open=True), strict=True)
    t_px = time.perf_counter() - t0
    print("股票 %d 只 | 事件 %d | 涉及 %d 只 | 交易日 %d | 取价面板 %.2fs"
          % (len(codes), len(events), events["code"].nunique(),
             len(panel["CALENDAR"]), t_px))
    print("")
    print("  %6s %10s %8s %8s %7s" % ("k", "回测秒", "成交", "被拒", "相对k5"))
    base = None
    for k in KS:
        t0 = time.perf_counter()
        bt = run_backtest(events, panel, hold_days=k, fill="t1_open", cost=0.004,
                          capital=1e7, strict_limit=True,
                          alloc_modes=("event_even", "cash_even"))
        dt = time.perf_counter() - t0
        if base is None:
            base = dt
        print("  %6d %10.3f %8d %8d %7s"
              % (k, dt, len(bt.trades), len(bt.rejects),
                 "%.2fx" % (dt / base)))


if __name__ == "__main__":
    main()
