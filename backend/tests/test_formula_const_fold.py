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


# ---------------- ★ v1.20.58：**比较 / 逻辑**常量也要折（同一类坑的第 3 次 ✗）----------------
# 用户 2026-09-23：「深跌10（周期 60 天）：特征计算失败: 'numpy.bool' object has no attribute 'name'」
# 公式：`n=10; 深跌10:IF(n<3, 原始值, MA(…));` ⇒ n 是常量 ⇒ 生成 `Lt(10,3)` ✗ —— 一棵
# **没有任何 `$字段` 的算子子树** ⇒ qlib 求值崩 ✓（真机复现一致 ✓）。
# ⚠ `>`/`<`/`AND` 在 AST 里是 **`BinOp`** ✗（不是 `FuncCall` ✗，`Lt(...)` 只在生成阶段出现 ✓）
#   ⇒ 折叠必须写在 `_const_fold` 的 `BinOp` 分支 ✓（第一版加到 FuncCall 分支 ⇒ 打空靶、仍然崩 ✗）。

def test_const_comparison_folded():
    assert translate_formula("用:CLOSE*(1>0);").expression == "Mul($close,1)"
    assert translate_formula("用:CLOSE*(1>2);").expression == "Mul($close,0)"
    assert translate_formula("用:CLOSE*(2>=2);").expression == "Mul($close,1)"


def test_const_logical_uses_nonzero_is_true():
    """`AND/OR` 必须照抄**运行期口径**「非 0 即真」✓（`ops_ext.And/Or` = `(a!=0)&(b!=0)` ✓），
    而不是 Python 的 `and/or` ✗（后者返回操作数本身，`0.5 and 2` ⇒ 2 ✗）。"""
    assert translate_formula("用:CLOSE*(5>3 AND 2>1);").expression == "Mul($close,1)"
    assert translate_formula("用:CLOSE*(5>3 AND 2<1);").expression == "Mul($close,0)"
    assert translate_formula("用:CLOSE*(0 OR 2);").expression == "Mul($close,1)"
    assert translate_formula("用:CLOSE*(0 OR 0);").expression == "Mul($close,0)"


def test_const_if_picks_branch():
    """★ `IF(恒定条件, A, B)` ⇒ **整棵折成被选中的那一支** ✓。

    ⚠ 不能"只折条件" ✗ —— 真机实测：`Lt(10,3)` 折成 `0` 后留下 `If(0,A,B)` ⇒ qlib 报
      **`'int' object has no attribute 'load'`** ✗（它要求条件本身是**可 `load` 的表达式** ✓，
      纯字面量不行 ✗）⇒ 唯一的出路是让这棵 `If` 整个消失 ✓。
    """
    assert translate_formula("n=10;\n用:IF(n<3,CLOSE,MA(CLOSE,10));").expression == "Mean($close,10)"
    assert translate_formula("n=2;\n用:IF(n<3,CLOSE*2,MA(CLOSE,10));").expression == "Mul($close,2)"


def test_user_case_shen_die():
    """用户原案：`n=10` ⇒ 走 `MA` 那支；`n=2` ⇒ 走 `原始值` 那支 ✓（真机求值都成功 ✓）。"""
    body = ("条件:=CLOSE <= MA(CLOSE, N);\n"
            "原始值:=COUNT(条件,20)/20;\n"
            "用:IF(n<3,原始值,MA((C-LLV(C,N))/(HHV(C,N)-LLV(C,N)),20));")
    t10 = translate_formula("n=10;\n" + body)
    assert t10.expression.startswith("Mean(Div(Sub($close,Min($close,10))")
    t2 = translate_formula("n=2;\n" + body)
    assert t2.expression.startswith("Div(Count(Le($close,Mean($close,2)),20),20)")


def test_nonconst_if_is_preserved():
    """⚠ 非恒定条件 ⇒ **必须原样保留 `If(...)`** ✓（绝不能把有数据依赖的支路折掉 ✗）。"""
    t = translate_formula("用:IF(CLOSE>MA(CLOSE,5),CLOSE,0);")
    assert t.expression.startswith("If(Gt($close,Mean($close,5))")
    assert "If(" in t.expression
