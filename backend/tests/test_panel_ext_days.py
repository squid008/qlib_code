# -*- coding: utf-8 -*-
"""表达式"预热天数"（extended window）的计算（★ v2.10.66，2026-10-21 ✓ —— P2-6 第三刀）。

## 这是整条因子链里**最不能算错**的一段

面板按 `read_start = 业务起点 - ext_days` 往前多读若干天 ✓。`ext_days` 算**少了**：
EMA / 滚动类算子从**错误的起点**开始递推 ⇒ **整条序列永久偏移** ✓ ——
而它**不报错、不崩、图也画得出来** ✗，只会让 0/1 阈值比较**翻面** ✓
（源码注释里记着实测：同一日值差可达 **0.37** ✓）。算**多了**只是白读几天 ✓（慢一点）。

⇒ 所以要钉的不是"能不能算出一个数"✓，而是下面这些**嵌套叠加**规则 ✓：
| 情形 | 规则 |
|---|---|
| 叶子 / 常量 | `0` ✓ |
| `Mean(x, N)`（N≥1） | `ext(x) + (N-1)` ✓ |
| **嵌套** `EMA(Max($h,34),4)` | `(34-1) + (4-1) = 36` ✓ —— **沿树累加**，不是取 max ✗ |
| `Ref(x, k)` | `ext(x) + max(k, 0)` ✓（负 k = 取未来，不往前读 ✓） |
| `Corr(a,b,N)` | `max(ext(a),ext(b)) + (N-1)` ✓ |
| 二元/一元（非窗口） | 两侧取 **max** ✓（不是相加 ✗） |
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from app.factors.panel_expr import _tree_ext_days


def F(name: str = "$close") -> NS:
    """叶子字段 ✓。"""
    return NS(op="field", args=[], raw=name)


def C(v) -> NS:
    """常量 ✓。"""
    return NS(op="const", args=[], raw=v)


def OP(op: str, *args) -> NS:
    return NS(op=op, args=list(args), raw=None)


def test_leaves_and_consts_are_zero():
    """叶子与常量不需要预热 ✓。"""
    assert _tree_ext_days(F()) == 0
    assert _tree_ext_days(C(5)) == 0


def test_simple_rolling_window():
    """`Mean(x, 5)` ⇒ **4**（N-1 ✓，不是 N ✗ —— 差一天就整体偏移 ✓）。"""
    assert _tree_ext_days(OP("Mean", F(), C(5))) == 4


def test_ema_of_rolling_accumulates_along_the_tree():
    """⭐⭐⭐ 源码里举的原例：`EMA(Max($h,34),4)` ⇒ **36**。

    ⚠ 这是最容易错的一条 ✗：如果按"取最大"实现（33 vs 3）会得 **33** ✓
    ⇒ 少预热 3 天 ⇒ **EMA 整条序列永久偏移** ✓（不报错、只在阈值附近翻面 ✗）。
    """
    inner = OP("Max", F("$high"), C(34))
    outer = OP("EMA", inner, C(4))
    assert _tree_ext_days(outer) == 36


def test_ref_adds_positive_only():
    """`Ref(x, k)`：正 k ⇒ `+k` ✓；**负 k（取未来）不许往前读** ✗；0 ⇒ 不加 ✓。"""
    assert _tree_ext_days(OP("Ref", F(), C(3))) == 3
    assert _tree_ext_days(OP("Ref", F(), C(0))) == 0
    assert _tree_ext_days(OP("Ref", F(), C(-2))) == 0, "负 k 不能变成负数窗口 ✗"


def test_ref_stacks_on_rolling():
    """`Mean(Ref($c,3),5)` ⇒ `4 + 3 = 7` ✓（**累加** ✓ 不是取最大 ✗）。"""
    assert _tree_ext_days(OP("Mean", OP("Ref", F(), C(3)), C(5))) == 7


def test_binary_ops_take_max_not_sum():
    """⭐ 非窗口算子的两侧**取 max** ✓ —— 相加会多读一堆天 ✓（慢，且与 qlib 不一致 ✗）。"""
    # Max(Mean($c,10), Ref($c,4)) ⇒ max(9, 4) = 9 ✓（相加的话是 13 ✗）
    node = OP("Max", OP("Mean", F(), C(10)), OP("Ref", F(), C(4)))
    assert _tree_ext_days(node) == 9


def test_corr_uses_max_of_children_plus_window():
    """`Corr(a, b, N)` ⇒ `max(两侧) + (N-1)` ✓（qlib PairRolling ✓）。"""
    a = OP("Mean", F("$close"), C(10))       # 9
    b = OP("Ref", F("$vol"), C(3))           # 3
    assert _tree_ext_days(OP("Corr", a, b, C(10))) == 9 + 9


def test_fractional_window_uses_log_convergence():
    """`0 < N < 1`（alpha 型）⇒ 按 `log(1e-6)/log(1-N)` 估收敛天数 ✓（与 qlib 同式 ✓）。"""
    v = _tree_ext_days(OP("Mean", F(), C(0.5)))
    # ⚠ 别自己算成 19 ✗→✓：式子 = `int(log(1e-6)/log(1-N)) - 1`
    #   ⇒ `log(1e-6)/log(0.5) = 19.93` ⇒ int ⇒ 19 ⇒ **再减 1** ⇒ **18** ✓（实测 ✓）。
    #   那个 `-1` 与 `N>=1` 时的 `N-1` 是同一个语义 ✓（窗口要覆盖 N 个点的跨度 ✓）。
    assert v == 18, "N=0.5 ⇒ 18 ✓（不是 19 ✗ —— 别忘了最后那个 -1）"
    v2 = _tree_ext_days(OP("Mean", F(), C(0.1)))
    assert v2 > v, "N 越小收敛越慢 ✓（0.1 要比 0.5 多读）"


def test_non_const_window_falls_back_to_zero_not_crash():
    """窗口参数**不是常量**（如 `Mean($c, $k)`）⇒ 当作 0 ✓、**不许抛** ✓
    （表达式能编译但跑不动的事，不能把整块面板拖崩 ✗）。"""
    assert _tree_ext_days(OP("Mean", F(), F("$k"))) == 0


@pytest.mark.parametrize("op", ["Mean", "Std", "Max", "Min", "Sum"])
def test_all_common_rolling_ops_share_the_rule(op):
    """常用滚动算子必须**同一套规则** ✓（漏登记一个 ⇒ 它少预热 ✓ 且无人察觉 ✗）。"""
    assert _tree_ext_days(OP(op, F(), C(20))) == 19
