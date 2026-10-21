# -*- coding: utf-8 -*-
"""窗口预热天数 ＋ 递推组拆分（★ v2.10.91，技术债 P1-#2 的第十一刀）。

## 为什么这两块值得钉

**`_warm_days`** ✓：它决定"读盘时往前多读多少天"✓。**少读** ⇒ 区间首日的滚动窗口
不完整 ⇒ `Mean/Std/EMA/Ref` 从错误起点递推 ⇒ **整条序列永久偏移** ✓，
而且**不报错、不崩、图照样画** ✗（与 `_tree_ext_days` 是同一个后果 ✓）。
⚠ 但它和 `_tree_ext_days` 是**两套实现**（一个是**正则扫字符串** ✗、一个是走语法树 ✓）
—— 两套给的数不一样时，就会出现"同一公式在不同入口预热天数不同"✗，极难查 ✓。

**`_recur_split`** ✓：把递推组（`REF(变量,k)` 编译出来的）拆成
「**骨架**（少量标量逐根算）+ **预计算子树**（整条面板一次算完）」✓。
拆错的后果是**慢到不可用**（6000 股 × 6000 根 × 每次重算 MA ✗）或**算错**（把递归子树当常量 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.panel_expr import (
    _RECUR_CUR_OP,
    _RECUR_PREV_OP,
    Node,
    _recur_split,
    _warm_days,
)

# ---------------------------------------------------------------- _warm_days


def test_warm_days_empty_is_just_margin():
    """没有任何表达式 ⇒ 只剩 **30 天余量** ✓（不是 0 ✗ —— 余量是给节假日/停牌留的 ✓）。"""
    assert _warm_days([]) == 30
    assert _warm_days(["$close"]) == 30


@pytest.mark.parametrize("expr, want", [
    ("Mean($close,20)", 50),          # 20 + 30
    ("Ref($close,5)", 35),            # 5 + 30
    ("Ref($close,-5)", 35),           # ⭐ **负号取绝对值** ✓（取未来也要多读 5 天 ✓）
    ("Corr($close,$volume,10)", 40),  # Corr 的**第三参** ✓（不是第二参 ✗）
    ("EMA($close,12)", 42),
    ("EMA_TDX($close,8)", 38),
    ("HHVBARS($high,60)", 90),
    ("LLVBARS($low,60)", 90),
    # ⚠⚠ 我第一版写成了 `Quantile($close,0.5,20)` ✗ —— 那是个**非法调用** ✓：
    #   求值器里是 `N = int(args[1]); q = args[2] if op == "Quantile"` ✓
    #   ⇒ **窗口是第二参、分位才是第三参** ✓（与 `Corr(a,b,N)` 不同 ✗ —— 那个 N 在第三位 ✓）。
    #   ⇒ 我差点据此「修」`_warm_days` 的正则 ✗ —— 幸好在改生产代码前先读了求值器 ✓。
    ("Quantile($close,20,0.5)", 50),
    ("Slope($close,15)", 45),
])
def test_warm_days_reads_window_from_each_operator(expr, want):
    """⭐⭐ 每个固定窗口算子都要被认出来 ✓ —— **漏登记一个** ⇒ 它静默少预热 ✓
    （而漏的往往是 `EMA_TDX` / `HHVBARS` / Alpha158 那批不常用的 ✓）。"""
    assert _warm_days([expr]) == want


def test_warm_days_takes_max_across_expressions():
    """多条表达式 ⇒ 取**最大**窗口 ✓（不是最后一条 ✗、也不是相加 ✗）。"""
    assert _warm_days(["Mean($close,5)", "Mean($close,60)", "Mean($close,20)"]) == 90


def test_warm_days_sr_needs_lookback_250():
    """⭐⭐ `SR(...)`（停牌删行）⇒ 至少 **250 + 30** ✓ —— 不额外前扩就覆盖不了停牌段 ✓
    （表现：停牌复牌后的前几天指标全是 NaN ✓，用户只会觉得"数据缺了"✗）。"""
    assert _warm_days(["SR($close)"]) == 280


def test_warm_days_sr_keeps_the_larger_window():
    """`SR` 与更大窗口同时出现 ⇒ 取更大的那个 ✓（250 不是上限 ✗）。"""
    assert _warm_days(["SR(Mean($close,300))"]) == 330


def test_warm_days_ignores_non_window_operators():
    """非窗口算子（加减乘除/比较/逻辑）⇒ **只有 30 天余量** ✓
    （把括号里的数字误当窗口 ⇒ 白白多读几十年数据 ✗）。"""
    assert _warm_days(["Add($close,$open)", "Gt($close,10)", "Mul($close,2)"]) == 30


# ---------------------------------------------------------------- _recur_split


class _FakeEv:
    """假求值器：按 `node.op` 返回预设值 ✓（只验拆分逻辑，不真算面板 ✓）。"""

    def __init__(self, ret):
        self.ret = ret
        self.calls = []

    def eval(self, node, sr=True):
        self.calls.append(node.op)
        return self.ret[node.op]


def _num(x):
    return Node("const", [x], str(x))


def test_recur_split_prev_and_cur():
    """⭐ `RecurPrev(j,k)` ⇒ `("p", j, k)` ✓、`RecurCur(j)` ⇒ `("m", j)` ✓ ——
    这两个是**组内引用**，必须原样带下标 ✓（把 j 弄错 ⇒ 取成别的成员的值 ✗）。"""
    ev = _FakeEv({})
    pre: list = []
    assert _recur_split(ev, Node(_RECUR_PREV_OP, [_num(2), _num(3)], "p"), True, pre) == ("p", 2, 3)
    assert _recur_split(ev, Node(_RECUR_CUR_OP, [_num(1)], "m"), True, pre) == ("m", 1)
    assert pre == [], "这两个不该产生预计算子树 ✓"


def test_recur_split_const_is_a_scalar_node():
    """常量 ⇒ `("c", float)` ✓（不是 `("a", …)` ✗ —— 常量不该占一份面板 ✓）。"""
    ev = _FakeEv({})
    pre: list = []
    assert _recur_split(ev, _num(2.5), True, pre) == ("c", 2.5)
    assert pre == []


def test_recur_split_non_recur_series_becomes_precomputed():
    """⭐⭐ 与递归无关的子树 ⇒ **整条一次算完并入 `pre`** ✓，骨架里只留下标 `("a", i)` ✓
    —— 这正是"6000 股 × 6000 根不重算 MA"的关键 ✓。"""
    s = pd.Series([1.0, 2.0, 3.0])
    ev = _FakeEv({"Mean": s})
    pre: list = []
    got = _recur_split(ev, Node("Mean", [Node("field", ["$close"], "$close"), _num(5)], "Mean(...)"), True, pre)
    assert got == ("a", 0)
    assert len(pre) == 1 and pre[0] is s
    assert ev.calls == ["Mean"]


def test_recur_split_non_recur_scalar_becomes_const():
    """⭐ 非递归子树算出来是**标量** ⇒ `("c", float)` ✓ 且**不进 `pre`** ✓
    （进了会浪费一份面板 ✓，而且下标对不上 ✗）。"""
    ev = _FakeEv({"Pow": np.float64(8.0)})
    pre: list = []
    got = _recur_split(ev, Node("Pow", [_num(2), _num(3)], "POW(2,3)"), True, pre)
    assert got == ("c", 8.0)
    assert pre == []


def test_recur_split_keeps_operator_shape_around_recur_children():
    """⭐⭐ 含递归子树的算子 ⇒ **保留算子与子树结构** ✓（`("Max", [子1, 子2])` ✓）——
    结构丢了就等于把整个递推组压成一个值 ✗。"""
    ev = _FakeEv({"Mean": pd.Series([1.0, 2.0])})
    pre: list = []
    node = Node(
        "Max",
        [Node(_RECUR_PREV_OP, [_num(0), _num(1)], "p"), Node("Mean", [Node("field", ["$x"], "$x"), _num(3)], "Mean")],
        "Max(...)",
    )
    got = _recur_split(ev, node, True, pre)
    assert got[0] == "Max"
    assert got[1][0] == ("p", 0, 1)
    assert got[1][1] == ("a", 0), "非递归的另一侧应被整条预计算 ✓"
    assert len(pre) == 1
