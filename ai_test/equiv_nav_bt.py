# -*- coding: utf-8 -*-
"""**新旧对拍**：把 `run_backtest` 的输出逐位序列化 + 取指纹，供 v1.20.36 vs v1.20.37 比对。

用法：`python equiv_nav_bt.py <输出.json>`
配套流程（安全 ✓）：
  1) 备份新文件 → `git checkout HEAD -- backend/app/signals/engine.py`（= 旧版）
  2) 跑本脚本 → `old.json`
  3) 还原新文件 → 再跑 → `new.json`
  4) 比 `hash`：**全等才敢上** ✓
"""
import hashlib
import json
import os
import sys

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


def _fp(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def main(out_path):
    all_codes = sorted(n for n in os.listdir(D) if os.path.isdir(os.path.join(D, n)))
    step = max(1, len(all_codes) // 300)
    codes = all_codes[::step][:300]
    ev = PanelEvaluator(codes, START, END)
    s = ev.eval_expr(_f("过顶"))
    trig = (s > 0) & s.notna()
    idx = trig[trig].index
    events = pd.DataFrame({"date": pd.to_datetime(idx.get_level_values(1)),
                           "code": idx.get_level_values(0).astype(str).str.upper(),
                           "side": 1})
    panel = fill_limits(load_price_panel(sorted(set(events["code"])), START, END,
                                        need_open=True), strict=True)

    rec = {}
    # 覆盖两种资金方案 + 多种持仓期（触发不同的再平衡路径 ✓）
    for hold in (20, 60, 120):
        for cost in (0.0, 0.002):
            bt = run_backtest(events, panel, hold_days=hold, fill="t1_open",
                              cost=cost, capital=1e7, strict_limit=True,
                              alloc_modes=("event_even", "cash_even"))
            nav_csv = bt.nav.to_csv(float_format="%.17g")
            tr_csv = bt.trades.to_csv(index=False, float_format="%.17g")
            rj_csv = bt.rejects.to_csv(index=False)
            st = json.dumps(bt.stats, sort_keys=True, ensure_ascii=False,
                            default=str)
            key = "h%d_c%s" % (hold, cost)
            rec[key] = {
                "nav": [len(nav_csv), _fp(nav_csv)],
                "trades": [len(tr_csv), _fp(tr_csv)],
                "rejects": [len(rj_csv), _fp(rj_csv)],
                "stats": [len(st), _fp(st)],
                "nav_last": float(bt.nav.iloc[-1, 0]) if len(bt.nav) else None,
            }
            print("  %-12s nav=%s trades=%s rejects=%s"
                  % (key, rec[key]["nav"][1], rec[key]["trades"][1],
                     rec[key]["rejects"][1]))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("已写", out_path)


def _f(name):
    items = json.load(open(FP, encoding="utf-8"))
    items = items.get("items") if isinstance(items, dict) else items
    for it in items:
        if str(it.get("name")) == name:
            return it.get("expression") or it.get("text") or ""
    raise KeyError(name)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"d:\quant\qlib_code\ai_test\equiv_out.json")
