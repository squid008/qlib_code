# -*- coding: utf-8 -*-
"""对比 过顶 vs AR4_COMBO：公式求值耗时 / 触发次数 / 涉及股票数 / nav 回测耗时。

用户问题（2026-09-21）：为什么过顶的事件研究净值曲线慢，而 AR4_COMBO 快很多？
假设：nav 回测（`signals.engine.run_backtest`）的开销 ≈ O(触发次数 + 交易日×持仓数)
      ⇒ 若 过顶 触发次数比 AR4_COMBO 高一个量级，就解释了一切。
本脚本把四个量分别打出来，做**同池同区间**的横向对比（不追求与生产逐位一致，
只求相对量级 ✓）。
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
HOLD = 60
N = 300
TARGETS = ("过顶", "过顶0", "AR4_COMBO")


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
    print("股票池抽样 = %d 只（全库 %d，步长 %d） 区间 %s~%s  持仓 %d 天"
          % (len(codes), len(all_codes), step, START, END, HOLD))

    ev = PanelEvaluator(codes, START, END)          # 共用一份面板 ✓
    print("面板构造完成 ✓（共用，不计入单公式耗时）")

    for name in TARGETS:
        ex = _formula(name)
        print("")
        print("=" * 74)
        print("### %s   expr_len=%d  算子树深度参考=%.0f" % (name, len(ex), len(ex) / 40.0))
        t0 = time.perf_counter()
        try:
            s = ev.eval_expr(ex)
        except Exception as e:                                    # noqa: BLE001
            print("  求值失败: %s: %s" % (type(e).__name__, str(e)[:200]))
            continue
        t_eval = time.perf_counter() - t0
        trig = (s > 0) & s.notna()
        n_rows = len(s)
        n_trig = int(trig.sum())
        stk_any = trig.groupby(level=0).any()
        print("  ① 公式求值      : %7.2f s" % t_eval)
        print("  ② 面板总行数    : %7d" % n_rows)
        print("  ③ 触发次数      : %7d   密度 %.4f%%" % (n_trig, 100.0 * n_trig / max(1, n_rows)))
        print("  ④ 涉及股票数    : %7d / %d  (%.1f%%)"
              % (int(stk_any.sum()), len(codes), 100.0 * int(stk_any.sum()) / len(codes)))
        if n_trig == 0:
            print("  （无触发，跳过 nav）")
            continue
        idx = trig[trig].index
        # ⚠ qlib 面板列名是**大写**（`SH600008` ✓），features 目录名是小写 ✗
        events = pd.DataFrame({"date": pd.to_datetime(idx.get_level_values(1)),
                               "code": idx.get_level_values(0).astype(str).str.upper(),
                               "side": 1})
        t0 = time.perf_counter()
        panel = load_price_panel(sorted(set(events["code"])), START, END, need_open=True)
        panel = fill_limits(panel, strict=True)
        t_px = time.perf_counter() - t0
        t0 = time.perf_counter()
        bt = run_backtest(events, panel, hold_days=HOLD, fill="t1_open", cost=0.002,
                          capital=1e7, strict_limit=True,
                          alloc_modes=("event_even", "cash_even"))
        t_bt = time.perf_counter() - t0
        print("  ⑤ 取价面板      : %7.2f s" % t_px)
        print("  ⑥ nav 回测      : %7.2f s   ← ★ 用户说的\"净值曲线\"就是这段"
              % t_bt)
        _rej = getattr(bt, "rejects", None)
        if _rej is None:
            _rej = getattr(bt, "rejected", None)
        print("  ⑦ reject 条数   : %7s" % (len(_rej) if _rej is not None else "-"))
        if bt.nav is not None:
            print("  ⑧ nav 点数      : %7d" % len(bt.nav))


if __name__ == "__main__":
    main()
