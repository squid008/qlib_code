# -*- coding: utf-8 -*-
"""实测 `PanelEvaluator.field()` 取各字段的耗时（用户 2026-09-18："都物化了怎么还慢"）。

目的：把"直读 bin"与"现算递推"分开计时 ⇒ 一眼看出 chip 现在到底走哪条路 ✓。
"""
import os
import sys
import time

sys.path.insert(0, r"d:\quant\qlib_code\backend")

FEATURES = r"d:\quant\qlib_code\data\cn_data\features"
N_INST = int(os.environ.get("BC_N", "300"))


def main():
    from app.services.qlib_runtime import ensure_qlib_init
    ensure_qlib_init()

    from app.factors.panel_expr import PanelEvaluator

    codes = [n for n in sorted(os.listdir(FEATURES))
             if os.path.isdir(os.path.join(FEATURES, n))][:N_INST]
    print("测试股票数 =", len(codes))

    t0 = time.time()
    ev = PanelEvaluator(codes, "2021-01-01", "2022-12-31")
    print("  构造面板 = %.2f s" % (time.time() - t0))

    for f in ("$close", "$high", "$chip_cost_95", "$chip_cost_5",
              "$chip_win_close", "$chip_cost_36"):
        t = time.time()
        try:
            s = ev.field(f)
            print("  %-18s %.3f s   n=%d  nan=%.2f"
                  % (f, time.time() - t, len(s), float(s.isna().mean())))
        except Exception as e:                                  # noqa: BLE001
            print("  %-18s ERR %s: %s" % (f, type(e).__name__, str(e)[:90]))
    print("done")


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归，见 2026-09-18 教训 ✓）
    main()
