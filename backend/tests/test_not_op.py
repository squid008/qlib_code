# -*- coding: utf-8 -*-
"""`NOT` 逻辑非（v1.20.53）单测。

来源：用户 2026-09-23 报障 —— 益盟/同花顺公式里的 `NOT` 不支持：
    量王:COUNT(量王条件,BARSLAST(NOT(量王条件))+1);

官方口径（益盟/同花顺/通达信同义）：
    `NOT(X)` 返回非 X —— **X=0 时返回 1，否则返回 0**。
⚠ 这个精确语义是"刚需"✗：`BARSLAST(NOT(条件))+1` 正是靠它统计"条件连续成立的根数" ✓。

实现要点（见 lexer/parser/codegen 内注释）：
- `NOT` 在**词法层**就成为 `TT_OP` ⇒ `NOT(X)` 与 `NOT X` **两种写法都支持** ✓；
- 优先级在**比较之下、AND 之上** ✓ ⇒ `NOT A=B` ≡ `NOT(A=B)` ✓（否则会静默错值 ✗）；
- 生成 **`Eq(X,0)`**（单个 elementwise 算子 ✓，不新增外挂算子 ✓）；纯常量 `NOT(1)` 走常量折叠 ✓。
"""
import pytest

from app.factors.parser import translate_formula
from app.factors.parser.codegen import CodeGenError


class TestNotBasics:
    def test_function_form(self):
        """`NOT(X)` 函数写法（益盟/同花顺最常见 ✓）。"""
        r = translate_formula("OUT:NOT(CLOSE>OPEN);")
        assert r.expression == "Eq(Gt($close,$open),0)"

    def test_bare_form_same_as_parenthesized(self):
        """`NOT X` 与 `NOT(X)` 等价 ✓。"""
        a = translate_formula("OUT:NOT(CLOSE>OPEN);").expression
        b = translate_formula("OUT:NOT CLOSE>OPEN;").expression
        assert a == b == "Eq(Gt($close,$open),0)"

    def test_precedence_looser_than_comparison(self):
        """⚠ 关键：`NOT A=B` 必须 ≡ `NOT(A=B)`（**不是** `(NOT A)=B` ✗ —— 那会静默错值 ✗）。"""
        r = translate_formula("OUT:NOT CLOSE=OPEN;")
        assert r.expression == "Eq(Eq($close,$open),0)"

    def test_tighter_than_and(self):
        """`NOT X AND Y` ⇒ `And(NOT X, Y)` ✓（NOT 比 AND 紧 ✓）。"""
        r = translate_formula("A:=CLOSE>OPEN; B:=VOLUME>0; OUT:NOT A AND B;")
        assert r.expression == "And(Eq(Gt($close,$open),0),Gt($volume,0))"

    def test_not_of_and(self):
        r = translate_formula("A:=CLOSE>OPEN; B:=VOLUME>0; OUT:NOT(A AND B);")
        assert r.expression == "Eq(And(Gt($close,$open),Gt($volume,0)),0)"

    def test_double_not(self):
        r = translate_formula("OUT:NOT NOT (CLOSE>OPEN);")
        assert r.expression == "Eq(Eq(Gt($close,$open),0),0)"


class TestNotConstFold:
    """纯常量 `NOT(...)` 必须被折叠 ✗（否则 `Eq(1,0)` 是"没有任何 `$字段` 的子树" ⇒ qlib 崩 ✓）。"""

    def test_not_one(self):
        r = translate_formula("OUT:CLOSE+NOT(1);")
        assert r.expression == "Add($close,0)"

    def test_not_zero(self):
        r = translate_formula("OUT:CLOSE+NOT(0);")
        assert r.expression == "Add($close,1)"

    def test_not_expression_of_consts(self):
        r = translate_formula("OUT:CLOSE+NOT(1+1);")
        assert r.expression == "Add($close,0)"


class TestUserCase:
    """用户真实公式（2026-09-23 报「不支持的函数：NOT」✓）：必须能翻译 ✓。"""

    TEXT = """
量王条件:=CLOSE>OPEN AND VOLUME>MA(VOLUME,5);
量王:COUNT(量王条件,BARSLAST(NOT(量王条件))+1);
"""

    def test_translates(self):
        """⚠ 注意 `COUNT` 的周期是**表达式**（`BARSLAST(...)+1`）⇒ 走**动态窗口**外挂算子
        `DYN_COUNT` ✓（这是既有设计：常量窗口用标准算子、变量窗口逐位置算 ✓）。"""
        r = translate_formula(self.TEXT)
        assert r.name == "量王"
        assert "Eq(" in r.expression, "NOT 应生成 Eq(X,0)"
        assert "BARSLAST(" in r.expression
        assert "COUNT(" in r.expression.upper()

    def test_not_no_longer_raises(self):
        """回归守卫：`NOT` 不能再报「不支持的函数」✗。"""
        try:
            translate_formula(self.TEXT)
        except CodeGenError as e:  # pragma: no cover - 触发即回归
            pytest.fail("NOT 仍报 CodeGenError：%s" % e)
