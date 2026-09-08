# -*- coding: utf-8 -*-
"""ops_ext 向量化算子 vs 朴素逐行参考实现 的数值对拍回归。

背景（v1.16.7）：BARSLAST / BARSSINCEN / DYN_MIN / DYN_MAX / DYN_COUNT /
DYN_REF / DYN_SUM 从 Python 逐位置 for 循环重写为 numpy 全向量化（消除
CWH_BREAK_WAIT20_F1 等巨型公式里 19 处动态窗口算子的逐行 Python 热点）。
本文件用内嵌的朴素参考实现逐位对拍，防止向量化引入数值偏差（含 NaN /
0 / 负窗口 / 超大窗口截断边界）。
"""
import numpy as np
import pandas as pd
import pytest

from app.factors.ops_ext import (
    BARSLAST,
    BARSSINCEN,
    DYN_COUNT,
    DYN_MAX,
    DYN_MIN,
    DYN_REF,
    DYN_SUM,
)


class _FakeFeature:
    """模拟 qlib Expression：load 返回预设 Series（算子只依赖 .load 结果）。"""

    def __init__(self, data):
        self._data = data

    def load(self, instrument, start_index, end_index, *args):
        return pd.Series(self._data)


# ---------------------------------------------------------------------------
# 朴素逐行参考实现（语义真源：对照 ops_ext 历史实现）
# ---------------------------------------------------------------------------


def _ref_barslast(vals):
    n = len(vals)
    out = np.zeros(n, dtype=float)
    last = -1
    for i in range(n):
        v = vals[i]
        if v != 0 and not np.isnan(v):
            last = i
        if last >= 0:
            out[i] = i - last
    return out


def _ref_barssincen(vals, N):
    n = len(vals)
    out = np.zeros(n, dtype=float)
    for i in range(n):
        lo = max(0, i - N + 1)
        win = vals[lo:i + 1]
        mask = (win != 0) & ~np.isnan(win)
        idx = np.flatnonzero(mask)
        if len(idx):
            out[i] = (i - lo) - int(idx[0])
    return out


def _ref_win_len(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 1
    n = int(v)
    return n if n >= 1 else 1


def _build_sparse(vals, func):
    n = len(vals)
    k = int(np.log2(n)) + 1
    st = [np.asarray(vals, dtype=float).copy()]
    for j in range(1, k):
        prev = st[-1]
        half = 1 << (j - 1)
        cur = np.empty(n, dtype=float)
        cur[:n - half] = func(prev[:n - half], prev[half:])
        cur[n - half:] = prev[n - half:]
        st.append(cur)
    return st


def _rmq(st, l, r, func):
    length = r - l + 1
    j = int(np.log2(length))
    return float(func(st[j][l], st[j][r - (1 << j) + 1]))


def _ref_dyn_minmax(vals, nvals, func):
    n = len(vals)
    if n == 0:
        return np.zeros(0, dtype=float)
    st = _build_sparse(vals, func)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        N = _ref_win_len(nvals[i])
        out[i] = _rmq(st, max(0, i - N + 1), i, func)
    return out


def _ref_dyn_count(vals, nvals):
    n = len(vals)
    mask = (vals != 0) & ~np.isnan(vals)
    pre = np.concatenate([[0.0], np.cumsum(mask.astype(float))])
    out = np.zeros(n, dtype=float)
    for i in range(n):
        N = _ref_win_len(nvals[i])
        lo = max(0, i - N + 1)
        out[i] = pre[i + 1] - pre[lo]
    return out


def _ref_dyn_ref(vals, nvals):
    n = len(vals)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        nv = nvals[i]
        N = int(nv) if not np.isnan(nv) else 0
        j = i - N
        if 0 <= j < n:
            out[i] = vals[j]
    return out


def _ref_dyn_sum(vals, nvals):
    n = len(vals)
    v = np.nan_to_num(vals, nan=0.0)
    pre = np.concatenate([[0.0], np.cumsum(v)])
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        N = _ref_win_len(nvals[i])
        lo = max(0, i - N + 1)
        out[i] = pre[i + 1] - pre[lo]
    return out


def _mk_series(n, seed, p_nan=0.1, p_zero=0.25, maxval=5.0):
    rng = np.random.default_rng(seed)
    x = rng.uniform(-maxval, maxval, n)
    x[rng.random(n) < p_zero] = 0.0
    x[rng.random(n) < p_nan] = np.nan
    return x


def _eq(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return bool(np.all(np.isclose(a, b, equal_nan=True, rtol=0, atol=0)))


def _case_dyn_windows(n, seed):
    rng = np.random.default_rng(1000 + seed)
    nw = rng.uniform(-2, 30, n)
    nw[rng.random(n) < 0.1] = np.nan
    nw[rng.random(n) < 0.1] = 0.0
    return nw


@pytest.mark.parametrize("n,seed", [(1, 0), (2, 1), (5, 2), (50, 3), (1000, 4)])
def test_barslast_matches_ref(n, seed):
    vals = _mk_series(n, seed)
    got = BARSLAST(_FakeFeature(vals))._load_internal(None, None, None).to_numpy()
    assert _eq(got, _ref_barslast(vals))


@pytest.mark.parametrize("n,N", [(1, 1), (5, 2), (50, 3), (50, 10), (100, 10 ** 6)])
def test_barssincen_matches_ref(n, N):
    vals = _mk_series(n, 7)
    got = BARSSINCEN(_FakeFeature(vals), N)._load_internal(None, None, None).to_numpy()
    assert _eq(got, _ref_barssincen(vals, max(1, N)))


@pytest.mark.parametrize("n,seed", [(1, 0), (5, 2), (50, 3), (500, 4)])
def test_dyn_min_max_matches_ref(n, seed):
    vals = _mk_series(n, seed)
    nw = _case_dyn_windows(n, seed)
    for op, func in ((DYN_MIN, np.fmin), (DYN_MAX, np.fmax)):
        got = op(_FakeFeature(vals), _FakeFeature(nw))._load_internal(None, None, None).to_numpy()
        assert _eq(got, _ref_dyn_minmax(vals, nw, func)), type(op).__name__


@pytest.mark.parametrize("n", [1, 5, 50, 1000])
def test_dyn_min_max_big_window_clip(n):
    """超大窗口（覆盖数组起点截断路径）。"""
    vals = _mk_series(n, 11)
    big = np.full(n, 1e12)
    for op, func in ((DYN_MIN, np.fmin), (DYN_MAX, np.fmax)):
        got = op(_FakeFeature(vals), _FakeFeature(big))._load_internal(None, None, None).to_numpy()
        assert _eq(got, _ref_dyn_minmax(vals, big, func)), type(op).__name__


@pytest.mark.parametrize("n", [1, 5, 50, 500])
def test_dyn_count_ref_sum_matches_ref(n):
    vals = _mk_series(n, 21)
    nw = _case_dyn_windows(n, 21)
    got = DYN_COUNT(_FakeFeature(vals), _FakeFeature(nw))._load_internal(None, None, None).to_numpy()
    assert _eq(got, _ref_dyn_count(vals, nw))
    got = DYN_SUM(_FakeFeature(vals), _FakeFeature(nw))._load_internal(None, None, None).to_numpy()
    assert _eq(got, _ref_dyn_sum(vals, nw))


@pytest.mark.parametrize("n", [1, 5, 50, 500])
def test_dyn_ref_matches_ref(n):
    vals = _mk_series(n, 33)
    nw = _case_dyn_windows(n, 33)
    got = DYN_REF(_FakeFeature(vals), _FakeFeature(nw))._load_internal(None, None, None).to_numpy()
    assert _eq(got, _ref_dyn_ref(vals, nw))
