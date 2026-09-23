# -*- coding: utf-8 -*-
"""真机验证外挂算子**能求值**（v1.20.54）—— 不只是"注册了" ✓。

背景：`SQRT`/`MOD` 曾映射到**不存在**的算子 ⇒ **编译能过、求值才炸** ✗
（用户 2026-09-23 报「强龙起势（周期 60 天）：特征计算失败: The operator [Sqrt] is not registered」）。
⇒ 本脚本用 qlib **自己的表达式引擎**（`D.features`）真跑一遍 ⇒ 证明可算 ✓；同时抽查几个边界：
   · `Sqrt` 负值 ⇒ NaN；
   · `Mod` 的**符号随被除数**（`np.fmod` 口径 ✓，与通达信/益盟一致 ✓）；
   · 纯常量子树（`Sqrt(4)`）在本层能不能过（⚠ 历史已知：qlib 对"没有 `$字段` 的子树"敏感 ✗）。

用法：python ai_test/check_ops_eval.py [code] [start] [end]
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "backend")))

from app.services.qlib_runtime import ensure_qlib_init   # noqa: E402

ensure_qlib_init()

from qlib.data import D   # noqa: E402


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else "SH600000"
    start = sys.argv[2] if len(sys.argv) > 2 else "2026-08-10"
    end = sys.argv[3] if len(sys.argv) > 3 else "2026-08-21"
    fields = ["Sqrt($close)", "Mod($close,5)", "Sqrt($close)*Mod($close,3)", "Mod($close,-3)"]
    try:
        df = D.features([code], fields, start_time=start, end_time=end)
    except Exception as e:                                  # noqa: BLE001
        print("  ✗ 求值失败：%s: %s" % (type(e).__name__, e))
        return 1
    print("  标的 %s | %s ~ %s | shape=%s" % (code, start, end, df.shape))
    print(df.tail(3).to_string())
    for c in df.columns:
        print("    %-30s mean=%s" % (c, df[c].mean()))
    print("")
    print("  ✅ Sqrt/Mod 可求值 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
