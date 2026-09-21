# -*- coding: utf-8 -*-
"""cProfile `signals.engine.run_backtest`（过顶真实事件），定位"还剩哪些热点"。

用户问题（2026-09-21）：② 向量化到底能不能做？之前是不是做过又放弃？
先做**决定性的一件事：profile**（不猜 ✗）。用 过顶 的真实触发事件（300 只抽样，
≈4502 条事件），把 run_backtest 重复若干轮凑够采样，看 top 热点落在哪。
"""
import cProfile
import io
import json
import os
import pstats
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
HOLD = 60
N = 300
REPEAT = 20


def _formula(name):
    j = json.load(open(FP, encoding="utf-8"))
    items = j.get("items") if isinstance(j, dict) else j
    for it in items:
        if str(it.get("name")) == name:
            return it.get("expression") or it.get("text") or ""
    raise KeyError(name)


def main():
    all_codes = sorted(n for n in os.listdir(D) if os.path.isdir(os.path.join(D, n)))
    step = max(1, len(all_codes) // N)
    codes = all_codes[::step][:N]
    ev = PanelEvaluator(codes, START, END)
    s = ev.eval_expr(_formula("过顶"))
    trig = (s > 0) & s.notna()
    idx = trig[trig].index
    events = pd.DataFrame({"date": pd.to_datetime(idx.get_level_values(1)),
                           "code": idx.get_level_values(0).astype(str).str.upper(),
                           "side": 1})
    panel = fill_limits(load_price_panel(sorted(set(events["code"])), START, END,
                                        need_open=True), strict=True)
    print("事件数 = %d  涉及股票 = %d  交易日 = %d"
          % (len(events), events["code"].nunique(), len(panel["CALENDAR"])))

    kw = dict(hold_days=HOLD, fill="t1_open", cost=0.002, capital=1e7,
              strict_limit=True, alloc_modes=("event_even", "cash_even"))

    t0 = time.perf_counter()
    bt = run_backtest(events, panel, **kw)
    t1 = time.perf_counter() - t0
    print("单轮 = %.3f s   成交=%d  被拒=%d  nav点数=%d"
          % (t1, len(bt.trades), len(bt.rejects), len(bt.nav)))

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(REPEAT):
        run_backtest(events, panel, **kw)
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("tottime").print_stats(28)
    txt = buf.getvalue()
    out = r"d:\quant\qlib_code\ai_test\prof_nav_bt.out.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("单轮 %.3f s × %d 轮\n" % (t1, REPEAT))
        f.write(txt)
    print("已写", out)


if __name__ == "__main__":
    main()
