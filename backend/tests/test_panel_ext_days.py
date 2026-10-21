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

import numpy as np
import pytest

from app.factors.panel_expr import _dyn_kernel, _expr_ext_days, _tree_ext_days, parse_expr


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


# --------------------------------------------------------------- _expr_ext_days
def test_expr_ext_days_matches_tree():
    """正常表达式 ⇒ 与 `_tree_ext_days(parse_expr(...))` **同一结果** ✓
    （两条路必须一致 ✓，否则"能解析"与"解析失败"会得到不同预热天数 ✗）。"""
    for expr, want in [
        ("EMA(Max($high,34),4)", 36),
        ("Mean($close,5)", 4),
        ("$close", 0),
    ]:
        assert _expr_ext_days(expr) == want, expr


def test_expr_ext_days_unparsable_falls_back_to_zero():
    """解析不了的串 ⇒ 兜底正则扫不到 EMA ⇒ **0** ✓，且**不许抛** ✓。"""
    assert _expr_ext_days("@@@ 完全不是表达式 @@@") == 0


def test_expr_ext_days_fallback_matches_main_path_for_fractional_window():
    """⭐⭐ 兜底路径必须与主路径**给出同一个数** ✓。

    ⚠ v2.10.67 实测到一处不一致 ✗：主路径对 `0<N<1` 返回 `int(log/log) - 1` ✓，
      而**解析失败**时的正则兜底**不减 1** ✗ ⇒ 同一表达式两条路差 **1 天** ✓
      ⇒ 少读一天 ⇒ EMA 类序列整体偏移 ✓（不报错 ✗）。
      触发条件真实存在：表达式能编译但 `parse_expr` 抛异常（新算子/边界语法 ✓）。
    """
    # 让整串解析失败、但正则仍能扫到 `EMA(...,0.5)` ✓
    expr = "EMA($close, 0.5) + @@@@"
    main_path = _tree_ext_days(parse_expr("EMA($close, 0.5)"))
    assert _expr_ext_days(expr) == main_path, "兜底必须与主路径一致 ✓（差 1 天就是偏移 ✗）"


# ---------------------------------------------------------------- _dyn_kernel


class _FakeOps:
    """假 ops_ext：记录**哪个函数被调用** + **传了什么** ✓（不真算 ✓）。"""

    def __init__(self):
        self.calls = []
        for name in ("dyn_ref_vec", "_dyn_rmq_vec", "dyn_window_sum_vec",
                     "dyn_window_count_vec", "dyn_window_mean_vec", "dyn_bars_vec"):
            setattr(self, name, self._mk(name))

    def _mk(self, name):
        def f(*a):
            self.calls.append((name, a))
            return name
        return f


VALS, NVALS = [1.0, 2.0], [2.0, 2.0]


def test_dyn_kernel_dispatch_map():
    """⭐ 八种动态窗口各自分派到**正确**的实现 ✓（分派错了 ⇒ 静默算成另一个算子 ✗）。"""
    cases = {
        "ref": "dyn_ref_vec",
        "min": "_dyn_rmq_vec",
        "max": "_dyn_rmq_vec",
        "sum": "dyn_window_sum_vec",
        "count": "dyn_window_count_vec",
        "mean": "dyn_window_mean_vec",
        "hhvbars": "dyn_bars_vec",
        "llvbars": "dyn_bars_vec",
    }
    for kind, fn in cases.items():
        ops = _FakeOps()
        got = _dyn_kernel(kind, VALS, NVALS, ops)
        assert got == fn, kind
        assert ops.calls[0][0] == fn, kind
        assert ops.calls[0][1][:2] == (VALS, NVALS), "必须原样转发数据与窗口 ✓"


def test_dyn_kernel_min_max_use_the_right_comparator():
    """⭐⭐ `min` 必须用 `np.fmin` ✓、`max` 必须用 `np.fmax` ✓ ——
    弄反 ⇒ **最高价当最低价**（或反之）✓，一路算下来全是"看起来正常"的错数 ✗。"""
    for kind, cmp in (("min", np.fmin), ("max", np.fmax)):
        ops = _FakeOps()
        _dyn_kernel(kind, VALS, NVALS, ops)
        assert ops.calls[0][1][2] is cmp, kind


def test_dyn_kernel_bars_direction_flag():
    """⭐⭐ `hhvbars` ⇒ 方向 **True**、`llvbars` ⇒ **False** ✓ ——
    弄反 ⇒「距最高点」与「距最低点」**互换** ✓（两者都是正数，肉眼分不出 ✗）。"""
    ops = _FakeOps()
    _dyn_kernel("hhvbars", VALS, NVALS, ops)
    assert ops.calls[0][1][2] is True
    ops = _FakeOps()
    _dyn_kernel("llvbars", VALS, NVALS, ops)
    assert ops.calls[0][1][2] is False


def test_dyn_kernel_unknown_kind_raises_with_name():
    """未知 kind ⇒ **抛**（且消息里带 kind ✓）—— 静默返回原值会把"没算"伪装成"算过" ✗。"""
    with pytest.raises(ValueError) as ei:
        _dyn_kernel("no-such-kind", VALS, NVALS, _FakeOps())
    assert "no-such-kind" in str(ei.value)
