# -*- coding: utf-8 -*-
"""诊断 `And/Or` 两侧**长度不一致**（`operands could not be broadcast together`）。

背景（2026-09-23 任务 `07bf2c5ba41e` 失败 ✓）：
    100% - 失败: operands could not be broadcast together with shapes (4992,) (5110,)
    栈：`ops_ext.py:657 _LogicalAndOr._load_internal` → `:681 And._op` 的 `(a!=0)&(b!=0)`
逐条加载 49 条公式定位到 **第 35 条 `强龙起势`** ✓（`(5246,) (6446,)` —— 同一支股票不同天数 ✗）。
本脚本用来**逐个缩小**嫌疑：打印各子表达式在真机上加载出来的长度 ✓。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "backend")))

from app.services.qlib_runtime import ensure_qlib_init   # noqa: E402

ensure_qlib_init()

from qlib.data import D                                   # noqa: E402

CASES = [
    "Gt(BARSCOUNT($close),0)",
    "Gt($close,0)",
    "Gt(Ref($close,1),0)",
    "Gt(DYN_MEAN($close,5),0)",
    "Gt(EMA($close,3),0)",
    "And(Gt($close,0),Gt(Ref($close,1),0))",
    "And(Gt(BARSCOUNT($close),0),Gt($close,0))",
    "DYN_COUNT(And(Gt(BARSCOUNT($close),0),Gt($close,0)),5)",
]


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else "SH600000"
    start = sys.argv[2] if len(sys.argv) > 2 else "2021-01-01"
    end = sys.argv[3] if len(sys.argv) > 3 else "2021-03-31"
    print("  标的 %s | %s ~ %s" % (code, start, end))
    for e in CASES:
        try:
            df = D.features([code], [e], start_time=start, end_time=end)
            print("  OK   %-52s rows=%d" % (e, len(df)))
        except Exception as ex:                                # noqa: BLE001
            print("  FAIL %-52s %s: %s" % (e, type(ex).__name__, str(ex)[:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
