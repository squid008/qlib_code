# -*- coding: utf-8 -*-
"""Profile 含 `COST`（= `$chip_cost_*`）的因子求值，找出到底哪个函数慢（用户 2026-09-18）。

"过顶"这类公式形如 `Gt($close,$chip_cost_95)`（见 `chip_store.py` 模块头的事故记录 ✓）。
本脚本用 cProfile 跑一遍真实面板求值，按 **cumtime** 打印热点 ⇒ 直接指认慢函数 ✓。
"""
import cProfile
import os
import pstats
import sys
import time

sys.path.insert(0, r"d:\quant\qlib_code\backend")

FEATURES = r"d:\quant\qlib_code\data\cn_data\features"
EXPR = os.environ.get("PC_EXPR", "Gt($close,$chip_cost_95)")
N = int(os.environ.get("PC_N", "300"))


def main():
    import json
    # ---- ① 列出公式库里用到 COST / WINNER 的公式（定位用户说的"过顶"✓）----
    for p in (r"d:\quant\qlib_code\backend\workdir\custom_formulas.json",):
        try:
            j = json.load(open(p, encoding="utf-8"))
            items = j if isinstance(j, list) else (j.get("items") or [])
            print("公式库 %d 条；含 COST/WINNER 的：" % len(items))
            for it in items:
                ex = (it.get("expression") or "") if isinstance(it, dict) else ""
                up = ex.upper()
                if "COST" in up or "WINNER" in up:
                    import re
                    print("   %-18s COST=%s WINNER=%s len=%d"
                          % (it.get("name"), re.findall(r"COST\((\d+)\)", up),
                             re.findall(r"WINNER\(([A-Z]+)\)", up), len(ex)))
        except Exception as e:                                   # noqa: BLE001
            print("读公式库失败:", e)

    from app.services.qlib_runtime import ensure_qlib_init
    ensure_qlib_init()
    from app.factors.panel_expr import PanelEvaluator

    codes = [n for n in sorted(os.listdir(FEATURES))
             if os.path.isdir(os.path.join(FEATURES, n))][:N]
    print("\n股票数 = %d   expr = %s" % (len(codes), EXPR))

    t0 = time.time()
    ev = PanelEvaluator(codes, "2021-01-01", "2022-12-31")
    print("构造面板 = %.2f s" % (time.time() - t0))

    # ---- ② 分步计时：各字段取值 ----
    for f in ("$close", "$high", "$chip_cost_95", "$chip_cost_5", "$chip_cost_36"):
        t = time.time()
        try:
            s = ev.field(f)
            print("  field(%-16s) %7.3f s  n=%d" % (f, time.time() - t, len(s)))
        except Exception as e:                                   # noqa: BLE001
            print("  field(%-16s) ERR %s" % (f, type(e).__name__))

    # ---- ③ cProfile：真实求值（含算子/对齐/缓存）----
    from app.factors import parser as _  # noqa: F401
    t = time.time()
    pr = cProfile.Profile()
    pr.enable()
    try:
        out = ev.eval_expr(EXPR) if hasattr(ev, "eval_expr") else None
    except Exception as e:                                       # noqa: BLE001
        print("  eval 失败:", type(e).__name__, str(e)[:120])
        out = None
    pr.disable()
    print("\n求值 %s => %.2f s" % (EXPR, time.time() - t))
    if out is not None:
        print("  结果 n=%d  nan=%.3f" % (len(out), float(out.isna().mean())))
    st = pstats.Stats(pr)
    st.sort_stats("cumulative")
    print("\n=== Top 22 by cumtime ===")
    st.print_stats(22)
    print("done")


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归，见 2026-09-18 教训 ✓）
    main()
