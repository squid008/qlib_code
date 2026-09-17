# -*- coding: utf-8 -*-
"""常量折叠 / 纯常量子树（v1.19.90）。

用户 2026-09-17 报：LLT / 黏合强突破 / 过顶0 / 过顶 / 蹦极新生
「特征计算失败: 'numpy.int64' object has no attribute 'name'」。

根因：`codegen` 会把**纯常量子树**原样输出（如 `Div(2,Add(30,1))`），
而 qlib 加载表达式时遇到**没有任何 `$字段` 的子树**会在内部对常量取 `.name` ⇒ 崩 ✗。
复现与定位见 `ai_test/dbg_llt.py`（`Add(2,3)` ❌ / `Div(2,Add(30,1))` ❌ /
`Add($close,1)` ✅ / `Mul(2,$close)` ✅）。
"""
import pytest

from app.factors.parser import translate_formula
from app.factors.parser.codegen import CodeGenError

LLT = ("D:=30;\nA1:=2/(D+1);\nW0:=-(A1-3*A1*A1/4)/((1-A1)*(1-A1));\n"
       "E1:=EMA(C,D);\nE2:=EMA(E1,D);\nLLT:2*E1-E2+W0*(C-2*E1+E2);")


def test_constant_subtree_is_folded():
    """`2/(30+1)` 必须折成一个数字字面量（不再出现 `Div(2,Add(30,1))`）。"""
    t = translate_formula("A:=2/(30+1); 输出:A*CLOSE;")
    assert "Add(30,1)" not in t.expression
    assert "Div(2," not in t.expression


def test_llt_has_no_constant_only_subtree():
    """LLT 是用户实际踩雷的公式：修好后不应再有纯常数子树。"""
    e = translate_formula(LLT).expression
    assert "Add(30,1)" not in e
    assert "Div(2," not in e


def test_constant_only_formula_is_rejected_clearly():
    """整条公式不含字段 ⇒ 报清晰中文（而不是 qlib 那句天书 AttributeError）。"""
    with pytest.raises(CodeGenError) as ei:
        translate_formula("输出:2+3;")
    assert "不含任何行情字段" in str(ei.value)


def test_div_by_zero_still_reported():
    """除零检测不能被新折叠路径吃掉。"""
    with pytest.raises(CodeGenError) as ei:
        translate_formula("输出:CLOSE/(5-5);")
    assert "除数为 0" in str(ei.value)


def test_field_expressions_unchanged():
    """含字段的常规表达式不受影响（数字仍可直接做操作数）。"""
    t = translate_formula("输出:CLOSE/2+1;")
    assert "Div($close,2)" in t.expression
