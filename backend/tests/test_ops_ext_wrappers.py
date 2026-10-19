# -*- coding: utf-8 -*-
"""`ops_ext` 的**纯 numpy 算子层**对拍（★ 2026-10-19 ✓ 覆盖率补强第二批）。

## 为什么挑这几个（而不是 qlib 包装层 ✗）

`ops_ext` 有 580 行未覆盖 ✓，其中大头是 `Operators.register` 的包装（要构造 qlib DataFrame ✗）。
但里面还住着一批**纯 numpy 函数** ✓ —— 它们是"**qlib 与面板共用的同一数值源**"
（docstring 原话 ✓）⇒ 直接传 numpy 就能测 ✓，而且**它们才是真正的语义所在** ✓
（qlib 那层只是把 Series 转成 ndarray 再调它们 ✓）。

## 口径（逐条来自 docstring ✓，不是猜的 ✗）

| 函数 | 语义 |
|---|---|
| `filter_vec(X, N)` | 条件成立输出 1 ✓，其后 **N-1** 个周期**抑制**重复触发 ✓；`NaN/0` 视为未触发 ✓ |
| `barsince_vec(X)` | 距**首次**成立周期数 ✓；首次之前（含一直没成立）**返回 0** ✓ |
| `seg_start_arr` / `seg_end_arr` | 由段边界 `bnd`（长 nseg+1 ✓）生成每位置所属段的**起点 / 终点（不含）** ✓ |
| `dyn_bars_vec(X, N_i, is_max)` | 动态窗口内**最近极值**距今天数 ✓；`NaN` 日 ⇒ `NaN` ✓、全 NaN 窗 ⇒ `NaN` ✓ |
"""
import numpy as np
import pytest

from app.factors import ops_ext as O


# ---------------------------------------------------------------- filter_vec
def _filter_naive(vals, N):
    out = np.zeros(len(vals), dtype=float)
    n = max(1, int(N))
    reopen = 0
    for i, v in enumerate(vals):
        if v == 0 or np.isnan(v):
            continue
        if i >= reopen:
            out[i] = 1.0
            reopen = i + n
    return out


@pytest.mark.parametrize("N", [1, 2, 3, 5])
def test_filter_vec_matches_naive(N):
    vals = np.array([1, 1, 0, 1, 1, 1, np.nan, 1, 1, 1, 1, 0, 1], dtype=float)
    np.testing.assert_array_equal(O.filter_vec(vals, N), _filter_naive(vals, N),
                                  err_msg="N=%d 时与朴素实现不一致 ✗" % N)


def test_filter_vec_edges():
    # 全 0 / 全 NaN ⇒ 一个都不触发 ✓
    assert not np.any(O.filter_vec(np.zeros(5), 3))
    assert not np.any(O.filter_vec(np.full(5, np.nan), 3))
    # N<=0 被夹到 1 ✓ ⇒ 等价于"原样输出 0/1" ✓
    v = np.array([1.0, 0.0, 2.0], dtype=float)
    np.testing.assert_array_equal(O.filter_vec(v, 0), np.array([1.0, 0.0, 1.0]))


# -------------------------------------------------------------- barsince_vec
def _barsince_naive(vals):
    out = np.zeros(len(vals), dtype=float)
    first = -1
    for i, v in enumerate(vals):
        if first < 0 and v != 0 and not np.isnan(v):
            first = i
        if first >= 0:
            out[i] = i - first
    return out


def test_barsince_vec_matches_naive():
    vals = np.array([0, 0, 3, 0, np.nan, 1, 0, 0, 2], dtype=float)
    np.testing.assert_array_equal(O.barsince_vec(vals), _barsince_naive(vals))


def test_barsince_vec_never_triggered_is_zero():
    """⚠ 一直没成立 ⇒ **全 0**（不是 NaN、不是 -1 ✗）。"""
    got = O.barsince_vec(np.zeros(6))
    assert got.shape == (6,)
    assert not np.any(got)


# --------------------------------------------------- seg_start_arr / seg_end_arr
def test_seg_arrays_map_each_position_to_its_segment():
    bnd = np.array([0, 3, 5, 9])            # 三段：[0,3) [3,5) [5,9) ✓
    n = int(bnd[-1])
    starts = O.seg_start_arr(bnd, n)
    ends = O.seg_end_arr(bnd, n)
    assert list(starts) == [0, 0, 0, 3, 3, 5, 5, 5, 5]
    assert list(ends) == [3, 3, 3, 5, 5, 9, 9, 9, 9]
    # 每个位置都必须满足 start <= i < end ✓（唯一的正确性判据 ✓）
    for i in range(n):
        assert starts[i] <= i < ends[i], "位置 %d 的段不包含自己 ✗" % i
    assert (ends - starts <= 0).sum() == 0, "出现了空段/负段 ✗"


# ------------------------------------------------------------- dyn_bars_vec
def _dyn_bars_naive(vals, nvals, is_max):
    n = len(vals)
    lens = np.maximum(np.floor(np.where(np.isnan(nvals), 1.0, nvals)).astype(np.int64), 1)
    out = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(vals[i]):
            continue                              # 当日 NaN ⇒ 当日 NaN ✓
        lo = max(0, i - int(lens[i]) + 1)
        win = vals[lo:i + 1]
        ok = ~np.isnan(win)
        if not ok.any():
            continue                              # 全 NaN 窗 ⇒ NaN ✓
        w = np.where(ok, win, -np.inf if is_max else np.inf)
        extreme = w.max() if is_max else w.min()
        # 等值取**最近** ✓（np.where 从右往左找第一个相等 ✓）
        idx = i - max(j for j in range(lo, i + 1)
                      if not np.isnan(vals[j]) and vals[j] == extreme)
        out[i] = idx
    return out


@pytest.mark.parametrize("is_max", [True, False])
def test_dyn_bars_matches_naive(is_max):
    vals = np.array([1.0, 3.0, 3.0, np.nan, 2.0, 5.0, np.nan, np.nan, 4.0], dtype=float)
    nvals = np.array([1.0, 2.0, 3.0, 2.0, np.nan, 4.0, 3.0, 2.0, 5.0], dtype=float)
    got = O.dyn_bars_vec(vals, nvals, is_max)
    want = _dyn_bars_naive(vals, nvals, is_max)
    np.testing.assert_allclose(got, want, equal_nan=True,
                               err_msg="is_max=%s 时与朴素实现不一致 ✗" % is_max)


def test_dyn_bars_edges():
    # 全 NaN ⇒ 全 NaN ✓（不能返回 0 ✗ —— 0 意味着"今天就是极值"✓ 会被误读 ✓）
    out = O.dyn_bars_vec(np.full(4, np.nan), np.full(4, 2.0), True)
    assert np.all(np.isnan(out))
    # 单元素 ✓
    np.testing.assert_allclose(O.dyn_bars_vec(np.array([7.0]), np.array([1.0]), True),
                               np.array([0.0]))
