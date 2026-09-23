# -*- coding: utf-8 -*-
"""按**公式名**从公式库取原文 → 翻译 → 用 qlib 真机求值（诊断"编译能过、算不出来"✗）。

背景（高频问题）：`特征计算失败: 'numpy.bool' / 'numpy.int64' object has no attribute 'name'` ✗
—— qlib 对"**没有任何 `$字段` 的子树**"敏感：纯常量子树（`Lt(10,3)` 这种）必须被
`_const_fold` 折成字面量 ✓，否则求值即崩 ✗（v1.19.90 治过 `numpy.int64` ✓；
2026-09-23 用户报 `深跌10` 撞上 **`numpy.bool`** ✓ —— 比较/逻辑算子当时没折 ✗）。

用法：
    python ai_test/dbg_eval_formula.py 深跌10                # 库里的公式
    python ai_test/dbg_eval_formula.py 深跌10 SH600000 2026-08-01 2026-08-21
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "backend")))

from app.services.qlib_runtime import ensure_qlib_init   # noqa: E402

ensure_qlib_init()

from qlib.data import D                                   # noqa: E402

from app.factors.parser import build_library, translate_formula   # noqa: E402

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend",
                     "workdir", "custom_formulas.json")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "深跌10"
    code = sys.argv[2] if len(sys.argv) > 2 else "SH600000"
    start = sys.argv[3] if len(sys.argv) > 3 else "2026-08-01"
    end = sys.argv[4] if len(sys.argv) > 4 else "2026-08-21"

    raw = json.load(open(STORE, encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("items", [])
    hit = [x for x in items if (x.get("name") or "") == name]
    if not hit:
        print("  ✗ 公式库里没有 %r" % name)
        return 2
    text = hit[0].get("text") or ""
    lib = build_library([(x.get("text") or "") for x in items])

    t = translate_formula(text, library=lib)
    print("  公式 %s 原文: %s" % (name, text.replace("\n", " | ")[:200]))
    print("  expression  = %s" % t.expression[:300])
    # ⚠ 先自查"纯常量子树"（不带 `$字段` 的算子节点）—— 这正是 qlib 崩的那类 ✗
    import re
    bad = []
    for m in re.finditer(r"([A-Za-z_]\w*)\(([^()]*(?:\([^()]*\)[^()]*)*)\)", t.expression):
        inner = m.group(2)
        if "$" not in inner and not inner.replace(".", "").replace("-", "").isdigit():
            bad.append(m.group(0)[:60])
    print("  可能的纯常量子树: %s" % (bad[:4] if bad else "无 ✓"))

    try:
        df = D.features([code], [t.expression], start_time=start, end_time=end)
    except Exception as e:                                  # noqa: BLE001
        print("  ✗ 求值失败：%s: %s" % (type(e).__name__, str(e)[:200]))
        return 1
    print("  ✅ 求值成功 | %s %s~%s | shape=%s" % (code, start, end, df.shape))
    print(df.tail(3).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
