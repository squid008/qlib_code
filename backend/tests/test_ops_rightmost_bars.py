# -*- coding: utf-8 -*-
"""`HHVBARS`/`LLVBARS` 的"**最右**极值"实现 vs 朴素暴力参考（★ v2.10.77，2026-10-21 ✓）。

## 为什么用"暴力参考"而不是逐个断言具体值

`_rightmost_bars_seg` 有三条实现路径（源码注释里写得很清楚 ✓）：
· `N == 1` ⇒ 极值即自身 ✓；
· `N <= _BARS_SLIDE_MAX_N(16)` ⇒ **分块滑窗** + 反转 argmax ✓；
· `N > 16` ⇒ **van Herk / Gil-Werman**（块内前缀-后缀极值 ✓，复杂度与 N 无关 ✓）。

三条路径必须**同一个答案** ✓ —— 而"答案"本身只有一句定义：
> `i - (段内窗口最右极值下标)`；同值取**最右**；NaN 不参与比较；`vals[i]` 为 NaN 或窗口内无有限值 ⇒ NaN。

⇒ 与其猜值，不如**把这句定义写成 20 行暴力循环** ✓，再拿三个 N（1 / 4 / 24）去对 ✓。
暴力版慢，但它**几乎不可能错**（一眼能读懂 ✓），这才是好参考实现 ✓。

⚠ 顺带钉住"同值取**最右**"这条 ✗→✓：取**最早**那次的话，图上**长得一模一样** ✓
（极值位置没变 ✓，只是"距它几根"不同 ✓），但下游用它做"距高点 N 日"的条件就会差几根 ✓。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.factors import ops_ext as O

RNG = np.random.default_rng(77)


def naive_rightmost_bars(vals, N: int, seg_start, is_max: bool):
    """把契约直接写成循环 ✓（`>=` / `<=` 就是为了**同值取最右** ✓）。"""
    vals = np.asarray(vals, dtype=float)
    n = len(vals)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        if np.isnan(vals[i]):
            continue                                  # vals[i] 是 NaN ⇒ 输出 NaN ✓
        lo = max(int(seg_start[i]), i - N + 1)
        best_j, best_v = -1, None
        for j in range(lo, i + 1):
            v = vals[j]
            if np.isnan(v):
                continue                              # NaN 不参与比较 ✓
            if best_v is None or (v >= best_v if is_max else v <= best_v):
                best_v, best_j = v, j                 # >= / <= ⇒ 同值取**最右** ✓
        if best_j >= 0:
            out[i] = i - best_j
    return out


def _data(n=120, nan_at=(5, 40, 41, 90)):
    vals = RNG.normal(size=n)
    for i in nan_at:
        if i < n:
            vals[i] = np.nan
    # 故意造几组"同值"情形 ✓（否则"最右"这条根本测不到 ✗）
    vals[10:14] = 5.0
    vals[60:66] = -5.0
    return vals


@pytest.mark.parametrize("is_max", [True, False])
@pytest.mark.parametrize("N", [1, 4, 24])
def test_rightmost_bars_matches_naive(N, is_max):
    """⭐⭐ 三个 N 覆盖三条实现路径（1 / ≤16 / >16）✓，都必须是**同一个答案** ✓。"""
    vals = _data()
    ss = np.zeros(len(vals), dtype=np.int64)
    got = O._rightmost_bars_seg(vals, ss, N, is_max)
    expect = naive_rightmost_bars(vals, N, ss, is_max)
    np.testing.assert_array_equal(np.asarray(got, dtype=float), expect)


def test_rightmost_bars_tie_breaks_to_the_right():
    """⭐⭐ 同值必须取**最右** ✓（`[3,3,3]`、N=3 ⇒ 每个位置都指向**最后一个** 3 ✓）。

    取最早那次 ⇒ 结果会是 `[0,0,0]` ✗ 而不是 `[0,1,2]` ✓ —— 两者在图上**完全一样** ✓，
    只有拿它做"距高点 N 根"的条件时才分叉 ✗。
    """
    vals = np.array([3.0, 3.0, 3.0])
    ss = np.zeros(3, dtype=np.int64)
    got = np.asarray(O._rightmost_bars_seg(vals, ss, 3, True), dtype=float)
    assert list(got) == [0.0, 0.0, 0.0], "前两个位置窗口内最后一个 3 就是自己 ⇒ 0 ✓"
    # 第三个位置：窗口 [3,3,3] ⇒ 最右在 index 2 ⇒ 0 ✓
    # （若取最左 ⇒ 也是 0 于 index 2 位置，但 index 1 位置会是 1 ✗ ⇒ 下面这条才分叉）
    got4 = np.asarray(O._rightmost_bars_seg(np.array([3.0, 3.0, 3.0, 1.0]), np.zeros(4, dtype=np.int64), 3, True), dtype=float)
    assert got4[1] == 0.0, "index1 的窗口 [3,3] ⇒ 最右是 index1 ⇒ 0 ✓（取最左会得 1 ✗）"


@pytest.mark.parametrize("N", [4, 24])
def test_rightmost_bars_respects_segment_boundaries(N):
    """⭐⭐ 多段：每段**独立**算 ✓ —— 第二段开头不许看到第一段的极值 ✓。

    构造：段 1 = 10 个 100（极高 ✓），段 2 = 10 个 1 ✓ ⇒ 段 2 每个位置的窗口里
    **只有 1** ✓ ⇒ 输出全 0 ✓；若跨段 ⇒ 段 2 前几个位置会指向段 1 的 100 ⇒ 输出非 0 ✗。
    """
    vals = np.array([100.0] * 10 + [1.0] * 10)
    ss = np.array([0] * 10 + [10] * 10, dtype=np.int64)
    got = np.asarray(O._rightmost_bars_seg(vals, ss, N, True), dtype=float)
    assert list(got[10:]) == [0.0] * 10, "段 2 全是 1 ⇒ 最右极值就是自己 ✓（跨段会得 10+ ✗）"


def test_rightmost_bars_all_nan_and_large_window():
    """全 NaN ⇒ 全 NaN ✓；`N` 大于数据长度 ⇒ 等价于"整段到 i"✓（不许越界 ✗）。"""
    nan_vals = np.full(10, np.nan)
    ss = np.zeros(10, dtype=np.int64)
    assert np.isnan(np.asarray(O._rightmost_bars_seg(nan_vals, ss, 4, True), dtype=float)).all()

    vals = np.array([1.0, 5.0, 2.0, 4.0])
    ss4 = np.zeros(4, dtype=np.int64)
    got = np.asarray(O._rightmost_bars_seg(vals, ss4, 99, True), dtype=float)
    np.testing.assert_array_equal(got, naive_rightmost_bars(vals, 99, ss4, True))
