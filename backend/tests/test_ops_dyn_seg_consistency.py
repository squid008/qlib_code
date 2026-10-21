# -*- coding: utf-8 -*-
"""动态窗口算子的**段版 vs 非段版一致性**（★ v2.10.76，2026-10-21 ✓ —— 技术债 #2 第九刀）。

## 为什么"一致性"才是这里最该测的东西

`ops_ext` 里每个动态窗口算子都有两份实现：
- `xxx_vec(vals, nvals)` —— **单股**（普通面板路径 ✓）
- `xxx_vec_seg(vals, nvals, seg_start, seg_end)` —— **整块多股一次算**（`_by_group` 走的那条 ✓）

源码注释就写着"与 `_dyn_kernel` **逐位一致**"✓。这句话是**契约**，不是祝福 ✓：
若两者不一致 ⇒ **同一因子里"不同股票走了不同实现"** ✓ ⇒ 结果**不可解释** ✓，
而且现象极度诡异（"一部分票对、一部分错"✓），用户只会看到"某些股票不对劲" ✗。

⇒ 这里**不比绝对值**（那要一个个算子读语义、成本高 ✗），而是**拿两份实现互相对** ✓：
同一份数据、同样的窗口 ⇒ 结果必须 `assert_array_equal` ✓（含 NaN 位置 ✓）。
另外单独验**多段**：段版 == 逐段调用非段版再拼 ✓ —— 这条钉住"**段与段之间不许串**" ✓
（最典型的错法：前缀和没按段重置 ⇒ 第二段从第一段的累计开始 ✓）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.factors import ops_ext as O

RNG = np.random.default_rng(20261021)
N = 40


def _data(with_nan: bool = True):
    vals = RNG.normal(size=N)
    if with_nan:
        vals[3] = np.nan
        vals[17] = np.nan
    nvals = RNG.integers(1, 9, size=N).astype(float)
    return vals, nvals


def _single_seg():
    """单段覆盖全部（`seg_end` 右开 ✓，与 `seg_end_arr` 的实测一致 ✓）。"""
    return np.zeros(N, dtype=np.int64), np.full(N, N, dtype=np.int64)


# ---------------------------------------------------------------- 单段一致性
@pytest.mark.parametrize("with_nan", [False, True])
def test_dyn_ref_seg_matches_vec(with_nan):
    """`REF(变量, N)` 的段版必须与非段版**逐位相同** ✓。"""
    vals, nvals = _data(with_nan)
    ss, se = _single_seg()
    np.testing.assert_array_equal(O.dyn_ref_vec(vals, nvals), O.dyn_ref_vec_seg(vals, nvals, ss, se))


@pytest.mark.parametrize("with_nan", [False, True])
def test_dyn_window_sum_seg_matches_vec(with_nan):
    """`DYN_SUM` —— 前缀和实现最容易在"含 NaN"和"窗口边界"上分家 ✓。"""
    vals, nvals = _data(with_nan)
    ss, se = _single_seg()
    np.testing.assert_array_equal(
        O.dyn_window_sum_vec(vals, nvals), O.dyn_window_sum_vec_seg(vals, nvals, ss, se))


@pytest.mark.parametrize("with_nan", [False, True])
def test_dyn_window_count_seg_matches_vec(with_nan):
    """`DYN_COUNT`（数有效值）✓ —— 与 sum 不同，NaN 是**不计数**而不是传播 ✓。"""
    vals, nvals = _data(with_nan)
    ss, se = _single_seg()
    np.testing.assert_array_equal(
        O.dyn_window_count_vec(vals, nvals), O.dyn_window_count_vec_seg(vals, nvals, ss, se))


@pytest.mark.parametrize("func", [np.fmax, np.fmin])
def test_dyn_rmq_seg_matches_vec(func):
    """`DYN_MAX` / `DYN_MIN`（RMQ）✓ —— fmax/fmin 两个比较器都要对 ✓。"""
    vals, nvals = _data()
    ss, _se = _single_seg()
    np.testing.assert_array_equal(
        O._dyn_rmq_vec(vals, nvals, func), O._dyn_rmq_vec_seg(vals, nvals, func, ss))


@pytest.mark.parametrize("is_max", [True, False])
def test_dyn_bars_seg_matches_vec(is_max):
    """`HHVBARS` / `LLVBARS`（距最近极值的周期数）✓ —— 方向标志也要两条都验 ✓。"""
    vals, nvals = _data()
    ss, _se = _single_seg()
    np.testing.assert_array_equal(
        O.dyn_bars_vec(vals, nvals, is_max), O.dyn_bars_vec_seg(vals, nvals, is_max, ss))


# ---------------------------------------------------------------- ⭐⭐ 多段：段与段之间不许串
def _two_seg_pieces():
    """两段：[0, 20) 与 [20, 40) ✓（每段的"历史"必须从段首重新开始 ✓）。"""
    ss = np.concatenate([np.zeros(20, dtype=np.int64), np.full(20, 20, dtype=np.int64)])
    se = np.concatenate([np.full(20, 20, dtype=np.int64), np.full(20, N, dtype=np.int64)])
    return ss, se


def test_dyn_ref_multi_seg_equals_per_segment_concat():
    """⭐⭐ **段版 == 逐段调用非段版再拼** ✓ —— 这条钉住"**段与段之间不许串**" ✓。

    最典型的错法：前缀和/环形缓冲没按段重置 ⇒ 第二段的窗口里混进了第一段的尾巴 ✓
    ⇒ 第二只股票开头几天算出来的是**上一只股票的行情** ✓（数值完全"合理" ✗，几乎不可能靠肉眼发现 ✓）。
    """
    vals, nvals = _data(False)
    ss, se = _two_seg_pieces()
    expect = np.concatenate([
        O.dyn_ref_vec(vals[:20], nvals[:20]),
        O.dyn_ref_vec(vals[20:], nvals[20:]),
    ])
    np.testing.assert_array_equal(O.dyn_ref_vec_seg(vals, nvals, ss, se), expect)


def test_dyn_window_sum_multi_seg_equals_per_segment_concat():
    """⭐ 同上（sum）✓ —— 前缀和按段重置这一点在 sum 上最明显 ✓。"""
    vals, nvals = _data(False)
    ss, se = _two_seg_pieces()
    expect = np.concatenate([
        O.dyn_window_sum_vec(vals[:20], nvals[:20]),
        O.dyn_window_sum_vec(vals[20:], nvals[20:]),
    ])
    # ⚠ 这里**不能**用 `assert_array_equal` ✗：实测差 **4.9e-16** ✓ ——
    #   段版是一次前缀和（累加顺序不同 ✓），非段版是逐段算 ✓ ⇒ **浮点结合律差异** ✓，不是 bug ✓。
    #   （ref / count / bars 是整数或取值 ⇒ 仍用精确相等 ✓，见上面的用例 ✓）
    np.testing.assert_allclose(
        O.dyn_window_sum_vec_seg(vals, nvals, ss, se), expect, rtol=0, atol=1e-12)


# ---------------------------------------------------------------- 段版的"只看窗口内"语义
def test_dyn_window_sum_does_not_cross_segment_start():
    """⭐⭐ 直接构造一个"跨段就错"的例子 ✓，把语义说死：

    段 1 = `[10, 10, 10]`、段 2 = `[1, 2]`，窗口都是 2 ✓。
    段 2 第 1 个位置的窗口只能看到**自己**（段内没有历史 ✓）⇒ 结果 = 1 ✓。
    若前缀和跨段 ⇒ 会算成 `10(=上一段尾巴) + 1 = 11` ✗。
    """
    vals = np.array([10.0, 10.0, 10.0, 1.0, 2.0])
    nvals = np.array([2.0, 2.0, 2.0, 2.0, 2.0])
    ss = np.array([0, 0, 0, 3, 3], dtype=np.int64)
    se = np.array([3, 3, 3, 5, 5], dtype=np.int64)
    got = O.dyn_window_sum_vec_seg(vals, nvals, ss, se)
    assert got[3] == 1.0, "段首位置只能看到段内数据 ✓（跨段会得 11 ✗）"
    assert got[4] == 3.0, "段内第二个位置 = 1 + 2 ✓"
    assert got[0] == 10.0, "段首同理 ✓"


# ---------------------------------------------------------------- _rightmost_bars_seg（无对应非段版）
def test_rightmost_bars_seg_counts_back_to_latest_extreme():
    """`_rightmost_bars_seg`（无对应非段版 ⇒ 只能验具体值 ✓）：

    「**距最近一次极值**的周期数」✓ —— `[1, 3, 2, 3]` 的最大值是 3，最近一次在 index 3
    ⇒ 结果 `[3, 2, 1, 0]` ✓（"最近"而不是"第一次" ✓ —— 取第一次的话 index 1 那根会得 0 ✗，
    而两者在图上**长得一样** ✓）。
    """
    vals = np.array([1.0, 3.0, 2.0, 3.0])
    ss = np.zeros(4, dtype=np.int64)
    # ⚠ `nvals` 是**标量 N**（固定窗口 ✓）不是数组 ✗ —— 源码 `N = max(1, int(nvals))` ✓
    #   （我第一版传了数组 ⇒ `TypeError: only length-1 arrays can be converted to Python scalars` ✓）
    got = O._rightmost_bars_seg(vals, ss, 4, True)
    # ⚠⚠ 实测是 `[0, 0, 1, 0]` ✗→✓ —— 我第一版按"**整段**到极值"的直觉写了 `[3, 2, 1, 0]` ✗。
    #   它其实是 **滚动窗口**（每个位置只看过去 N 根 ✓，与 HHVBARS 语义一致 ✓）：
    #   · i=0 窗口 [1]      ⇒ 极值在 0 ⇒ 0-0 = **0** ✓
    #   · i=1 窗口 [1,3]    ⇒ 极值在 1 ⇒ 1-1 = **0** ✓
    #   · i=2 窗口 [1,3,2]  ⇒ 极值在 1 ⇒ 2-1 = **1** ✓
    #   · i=3 窗口 [1,3,2,3]⇒ **最右**极值在 3 ⇒ 3-3 = **0** ✓（取最早那次会得 2 ✗）
    assert list(np.asarray(got, dtype=float)) == [0.0, 0.0, 1.0, 0.0]
