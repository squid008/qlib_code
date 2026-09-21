# -*- coding: utf-8 -*-
"""对比「过顶」与「AR4_COMBO」两条公式：表达式 + 叶子字段 + 函数集合。

用户问题（2026-09-21）：为什么过顶慢、AR4_COMBO 快很多？
—— 先看公式本身（字段/算子/结构），再定量比触发次数。
"""
import json
import re

P = r"d:\quant\qlib_code\backend\workdir\custom_formulas.json"


def dump(name, ex):
    print("")
    print("=" * 78)
    print("==== %s ====   expr_len=%d   行数=%d" % (name, len(ex), ex.count("\n") + 1))
    print(ex if len(ex) <= 600 else ex[:600] + " ……[截断]")
    print("-" * 78)
    fields = sorted(set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", ex)))
    funcs = sorted(set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", ex)))
    print("  叶子字段(%d): %s" % (len(fields), fields))
    print("  函数(%d): %s" % (len(funcs), funcs))
    # 状态递推型算子（慢源）
    heavy = [f for f in funcs if f.upper() in
             ("COST", "WINNER", "COSTEX", "CHIP", "PPART")]
    print("  ★ 状态递推型算子: %s" % (heavy or "无"))


def main():
    import builtins
    import io
    out = io.StringIO()
    _orig = builtins.print

    def _tee(*a, **kw):
        # ⚠ 必须改 `builtins.print`：`dump()` 里的 `print` 是**模块级全局查找** ✗，
        #   只在 `main` 里定义局部 `print` 是拦不住它的（这就是上一版只写 534 字节的原因 ✗）。
        _orig(*a, **kw)
        kw["file"] = out
        _orig(*a, **kw)

    builtins.print = _tee
    try:
        j = json.load(open(P, encoding="utf-8"))
        items = j.get("items") if isinstance(j, dict) else j
        print("公式库条数 =", len(items))
        print("全部名字:", [it.get("name", "") for it in items])
        for it in items:
            nm = str(it.get("name", ""))
            ex = it.get("expression") or it.get("text") or ""
            if nm in ("过顶", "AR4_COMBO", "过顶0"):
                dump(nm, ex)
    finally:
        builtins.print = _orig
    with open(r"d:\quant\qlib_code\ai_test\dump_formulas.out.txt", "w",
              encoding="utf-8") as f:
        f.write(out.getvalue())
    _orig("已写 dump_formulas.out.txt  size=%d" % len(out.getvalue()))


if __name__ == "__main__":
    main()
