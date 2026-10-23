# -*- coding: utf-8 -*-
"""`panel_expr.parse_expr` 的解析语义（★ 2026-10-19 ✓ 覆盖率补强第三批）。

## 为什么值得测

`parse_expr` 是**所有公式的入口** ✓（公式库、单因子、事件研究都从这里进 ✓），
而它此前只有 5 处零星引用 ✓。解析错一个优先级 ⇒ **整条公式算错** ✗，
且错误往往"看起来很像对的"✓（比如 `a-b-c` 结合成 `a-(b-c)` ✗）。

## 断言策略（不碰内部实现细节 ✓）

只依赖两样**有文档保证**的东西 ✓：
  · `Node.op` / `Node.args`（`__slots__` 里的字段 ✓）；
  · 中缀映射 `+ - * / ⇒ Add Sub Mul Div`（代码里 `_INFIX` 写死的 ✓）。
⇒ 不假设字段节点长什么样 ✓、不假设数字是 int 还是 str ✗（只断言"有 2 个参数"✓）。
"""
import pytest

from app.factors.panel_expr import parse_expr


def test_field_parses_to_a_node():
    n = parse_expr("$close")
    assert n is not None
    assert isinstance(n.op, str) and n.op
    assert n.raw.strip() == "$close"


def test_infix_precedence_mul_before_add():
    """`1+2*3` 必须解析成 `Add(1, Mul(2,3))` ✓ —— 反过来就是算错 ✗。"""
    n = parse_expr("1+2*3")
    assert n.op == "Add", "顶层应是 Add ✓，实际 %r ✗" % n.op
    assert len(n.args) == 2
    assert n.args[1].op == "Mul", "右子树应是 Mul ✓，实际 %r ✗" % n.args[1].op


def test_infix_parentheses_win():
    n = parse_expr("(1+2)*3")
    assert n.op == "Mul", "括号优先 ✓，实际顶层 %r ✗" % n.op
    assert n.args[0].op == "Add"


def test_infix_left_associativity():
    """`1-2-3` 必须左结合 ✓（`Sub(Sub(1,2),3)` ✓）—— 右结合会得到 −4 而不是 −4… 语义不同 ✗。"""
    n = parse_expr("1-2-3")
    assert n.op == "Sub"
    assert n.args[0].op == "Sub", "左子树应先算 ✓，实际 %r ✗" % n.args[0].op
    assert not hasattr(n.args[1], "op") or n.args[1].op != "Sub"


def test_function_call_collects_args():
    n = parse_expr("Mean($close,5)")
    assert n.op == "Mean"
    assert len(n.args) == 2, "应有两个参数 ✓，实际 %d ✗" % len(n.args)


def test_nested_functions():
    n = parse_expr("Mean(Std($close,20),5)")
    assert n.op == "Mean"
    inner = n.args[0]
    assert inner.op == "Std"
    assert len(inner.args) == 2


def test_whitespace_is_ignored():
    a = parse_expr("$close")
    b = parse_expr("   $close   ")
    assert a.key == b.key, "空白不应改变结构 ✓"
    c = parse_expr("Mean( $close , 5 )")
    d = parse_expr("Mean($close,5)")
    assert c.key == d.key


def test_dash_inside_field_is_not_subtraction():
    """字段名里带下划线/数字很常见 ✓ —— 不能被当成减号 ✗。"""
    n = parse_expr("$pe_ttm")
    assert n.op != "Sub", "`$pe_ttm` 被解析成减法了 ✗"
    assert "_" in n.raw


@pytest.mark.parametrize("bad", ["Mean($close", "1 + ", "$", "Foo(", "", "()"])
def test_bad_expressions_raise(bad):
    """坏表达式必须**抛** ✓（不能"解析出一棵残树、后面算出垃圾"✗）。"""
    # ★ v2.11.61（ruff **B017**）：原来是 `pytest.raises(Exception)` ✗ —— 那等于
    #   "只要炸了就算对" ✓，连 `KeyError` / `TypeError` 这种**实现 bug** 也会被当成"预期行为" ✗。
    #   实测（2026-10-23）这 6 个坏输入抛的是 `ValueError`（语法/语义层）或 `IndexError`（截断输入）✓
    #   ⚠ `IndexError` 其实是个**小 wart** ✓ —— 解析器本该抛语法错误 ✓；
    #     但改成 `SyntaxError` 会**改变调用方行为** ✗（有地方 catch `Exception`），留待专门一轮 ✓。
    with pytest.raises((ValueError, IndexError)):
        parse_expr(bad)


def test_unary_minus_does_not_crash():
    """一元负号是 qlib 合法写法 ✓（`-1` / `-$close`）—— 至少要能解析 ✓。"""
    for expr in ("-1", "-$close"):
        n = parse_expr(expr)
        assert n is not None
