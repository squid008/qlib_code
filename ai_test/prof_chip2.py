# -*- coding: utf-8 -*-
"""端到端 profile「过顶」公式的单因子测试，找出真正的慢函数（用户 2026-09-18）。

前一步已证：**面板取数不慢**（`field($chip_cost_95)` 300 只 = 0.052 s，与 `$close` 同量级 ✓，
物化直读生效 ✓）。所以"慢"在别的环节 ⇒ 本脚本用 cProfile 跑**完整** `run_single_factor_tests`。
"""
import cProfile
import io
import json
import os
import pstats
import sys
import time

sys.path.insert(0, r"d:\quant\qlib_code\backend")

ART = r"d:\quant\qlib_code\backend\workdir\custom_formulas.json"
POOL = os.environ.get("PC_POOL", "csi300")
H = int(os.environ.get("PC_H", "20"))
S = os.environ.get("PC_S", "2021-01-01")
E = os.environ.get("PC_E", "2022-12-31")


def pick_formula():
    j = json.load(open(ART, encoding="utf-8"))
    items = j if isinstance(j, list) else (j.get("items") or [])
    want = os.environ.get("PC_NAME", "过顶")
    for it in items:
        if isinstance(it, dict) and it.get("name") == want:
            return it
    for it in items:                                     # 退而求其次：含 chip_ 的第一条
        if isinstance(it, dict) and "chip_" in (it.get("expression") or ""):
            return it
    return None


def main():
    it = pick_formula()
    if it is None:
        print("未找到目标公式")
        return
    expr = it.get("expression") or ""
    print("公式名 = %s   expr_len = %d" % (it.get("name"), len(expr)))
    print("  expression 片段: %s" % expr[:220].replace("\n", " "))
    print("  含 chip_ 字段: %s" % sorted({t for t in
                                  ("chip_cost_5", "chip_cost_30", "chip_cost_75",
                                   "chip_cost_95", "chip_win_close", "chip_win_high",
                                   "chip_win_low") if t in expr}))

    import app.factors.single_test as st
    FS = [{"name": it.get("name"), "id": "probe", "expression": expr}]

    def _pc(*a):
        print("      %s" % (a[0] if a else ""), flush=True)

    t = time.time()
    pr = cProfile.Profile()
    pr.enable()
    r = st.run_single_factor_tests([H], POOL, S, E, factors=FS,
                                   topk_list=[20], price_adjust="backward",
                                   progress_cb=_pc)
    pr.disable()
    print("\n>>> 端到端 %.1f s" % (time.time() - t))
    try:
        d = (((r or {}).get(H)) or [{}])[0]
        print("    结果键:", sorted(d.keys())[:14])
    except Exception as e:                                   # noqa: BLE001
        print("    结果解析失败:", e)

    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(25)
    print("\n=== Top 25 by tottime ===")
    print(s.getvalue()[-4500:])
    print("done")


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归，见 2026-09-18 教训 ✓）
    main()
