# -*- coding: utf-8 -*-
"""`factors/ops_ext.py` 的**向量化基元**（★ v2.10.12，2026-10-20 ✓）。

## 为什么这批值得测

它们全是**性能重写的产物** ✓，注释里对每个 `_seg` 版本都写着「与×××逐位一致」✓ ——
而"逐位一致"这种承诺，**只有对拍能验证** ✓：

* 非 seg 版本 = 整条序列上算 ✓（单标的、无分段 ✓）；
* `_seg` 版本 = 先算全局前缀和/前缀最大值 ✓，再用**段起点**把窗口/查找截住 ✓
  （省掉 per-段 Python 循环 ✓）。

⇒ 若"截断"写错一格，结果**只在"某只票的第 1~N 根"上错**✗ —— 正是最容易漏测的位置 ✓
（实盘表现为"每只票开头几天的因子值不对"✓，而中段完全正常 ✗）。
所以这里既对拍"**朴素逐元素实现**"✓，也对拍"**单段时应与非 seg 版完全相等**"✓。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.factors.ops_ext import (
    _win_lens_vec,
    bars_count_seg,
    barslast_vec,
    barslast_vec_seg,
    dyn_ref_vec,
    dyn_window_count_vec,
    dyn_window_count_vec_seg,
    dyn_window_sum_vec,
    dyn_window_sum_vec_seg,
    seg_end_arr,
    seg_start_arr,
)

V = np.array([1.0, np.nan, 3.0, 0.0, 5.0, np.nan, 7.0])
N = np.array([2.0, np.nan, 3.0, 0.5, 1.0, 4.0, 2.0])


# ------------------------------------------------------------------ 小工具
def test_win_lens_vec_normalizes():
    """⭐ 文档口径：`NaN → 1` ✓、`<1 → 1` ✓、其余向零截断 ✓（`0.5 → 0 → 夹到 1` ✓）。"""
    assert list(_win_lens_vec(np.array([2.9, np.nan, 0.5, 0.0, 7.0]))) == [2, 1, 1, 1, 7]


def test_seg_start_end_arrays():
    bnd = np.array([0, 3, 5])
    assert list(seg_start_arr(bnd, 5)) == [0, 0, 0, 3, 3]
    assert list(seg_end_arr(bnd, 5)) == [3, 3, 3, 5, 5]


# ------------------------------------------------------- barslast / bars_count
def test_barslast_matches_naive():
    """距最近一次"**非 0 且非 NaN**"的周期数 ✓（从未成立 ⇒ 0 ✓）。"""
    got = barslast_vec(V)
    exp, last = [], -1
    for i, v in enumerate(V):
        if not np.isnan(v) and v != 0:
            last = i
        exp.append(0.0 if last < 0 else float(i - last))
    np.testing.assert_allclose(got, exp)


def test_barslast_seg_ignores_hits_before_segment_start():
    """⭐ 段感知：满足点若落在**段起点之前** ⇒ 记 0 ✓（不许把上一段的信号带进来 ✗）。"""
    vals = np.array([1.0, 0.0, 0.0, 0.0, 0.0])
    seg_start = np.array([0, 0, 2, 2, 2])
    got = barslast_vec_seg(vals, None, seg_start, None)
    assert got[0] == 0.0 and got[1] == 1.0
    assert got[2] == 0.0, "段起点之前那次命中不算 ⇒ 新段内从未成立 ⇒ 0 ✓"
    # ⚠ 我第一版写 `[1, 2]` ✗ —— 那是"从旧命中点继续数"✓；而口径是"**段内**从未成立 ⇒ 0" ✓
    assert list(got[3:]) == [0.0, 0.0]


def test_bars_count_seg_resets_each_segment():
    vals = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0])
    seg_start = np.array([0, 0, 0, 3, 3, 3])
    got = bars_count_seg(vals, None, seg_start, None)
    assert list(got) == [1.0, 2.0, 2.0, 1.0, 2.0, 3.0], "NaN 不计 ✓、每段从 1 重新数 ✓"


# ------------------------------------------------------------- dyn_* 系列
def _naive_dyn_sum(vals, nvals):
    out = []
    for i in range(len(vals)):
        w = 1 if np.isnan(nvals[i]) else max(1, int(np.trunc(nvals[i])))
        lo = max(0, i - w + 1)
        seg = np.nan_to_num(vals[lo:i + 1], nan=0.0)
        out.append(float(seg.sum()))
    return np.array(out)


def test_dyn_window_sum_matches_naive():
    np.testing.assert_allclose(dyn_window_sum_vec(V, N), _naive_dyn_sum(V, N))


def test_dyn_window_count_matches_naive():
    w = ((V != 0) & ~np.isnan(V)).astype(float)
    exp = _naive_dyn_sum(w, N)
    np.testing.assert_allclose(dyn_window_count_vec(V, N), exp)


def test_dyn_ref_matches_naive():
    """`DYN_REF`：第 i 位取 `i - trunc(N_i)` ✓；越界/NaN 窗口 ⇒ NaN ✓。"""
    got = dyn_ref_vec(V, N)
    exp = []
    for i in range(len(V)):
        nv = 0.0 if np.isnan(N[i]) else float(np.trunc(N[i]))
        j = i - int(nv)
        exp.append(V[j] if 0 <= j < len(V) else np.nan)
    np.testing.assert_allclose(got, exp, equal_nan=True)


# ------------------------------------------------- 段感知 ≡ 非段感知（单段）
def test_seg_variants_equal_plain_when_single_segment():
    """⭐⭐ 单段（`seg_start` 全 0）时，`_seg` 版必须与**非 seg 版逐位相等** ✓ ——
    这是"性能重写不改语义"的直接证据 ✓（写错一格截断，这里立刻红 ✓）。"""
    n = len(V)
    seg_start = np.zeros(n, dtype=np.int64)
    seg_end = np.full(n, n, dtype=np.int64)
    np.testing.assert_allclose(dyn_window_sum_vec_seg(V, N, seg_start, seg_end),
                               dyn_window_sum_vec(V, N))
    np.testing.assert_allclose(dyn_window_count_vec_seg(V, N, seg_start, seg_end),
                               dyn_window_count_vec(V, N))


def test_seg_window_is_clipped_at_segment_start():
    """⭐ 段感知：窗口下界要**截在段起点** ✓（否则第二段开头会算进上一段的数 ✗）。"""
    vals = np.array([10.0, 10.0, 10.0, 1.0, 2.0])
    nvals = np.full(5, 3.0)
    seg_start = np.array([0, 0, 0, 3, 3])
    got = dyn_window_sum_vec_seg(vals, nvals, seg_start, None)
    assert list(got) == [10.0, 20.0, 30.0, 1.0, 3.0], "第二段开头必须只有段内的数 ✓"
    # 对照：若不截断会得到 21.0 / 23.0 ✗（把上一段的 10 带进来了 ✓）
    plain = dyn_window_sum_vec(vals, nvals)
    assert plain[3] == pytest.approx(21.0) and got[3] == pytest.approx(1.0)
