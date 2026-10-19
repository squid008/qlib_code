# -*- coding: utf-8 -*-
"""`factors/ops_ext.py` 向量内核 **朴素参考实现对拍**（★ 覆盖率 ratchet，2026-10-19 ✓）。

## 为什么这么写（而不是"跑一遍别报错"✗）

`ops_ext` 是全仓**未覆盖最多**的纯数组内核（583 行 ✗，覆盖率 47.6% ✓）。
smoke 式断言（"不抛异常"）对数值代码**毫无意义** ✗ —— 算错了一样不抛 ✓。
⇒ 每个内核都配一个**逐位置朴素实现**（O(n·w) 双重循环 ✓，慢但显然正确 ✓），
  在同一组输入上**逐位比对** ✓。这也是本项目 `test_ops_ext_vec.py` 一直用的手法 ✓。

## 覆盖的口径（每条都来自内核 docstring ✓，不是我猜的 ✗）

| 内核 | 口径 |
|---|---|
| `_win_lens_vec` | NaN→1 ✓、<1→1 ✓、其余 `floor` ✓ |
| `barslast_vec` | 距最近一次"非 0 且非 NaN"的周期数，**无则 0** ✓ |
| `dyn_ref_vec` | 取 `i - trunc(N_i)`，NaN 窗算 0 ✓、越界保持 NaN ✓ |
| `dyn_window_sum/count/mean` | 窗口 `[i-N+1, i]` ✓；sum 把 NaN 当 0 ✓；count 数"非 0 且非 NaN" ✓；mean 分母是**非 NaN 个数** ✓、全 NaN ⇒ NaN ✓ |
| `barsincen_vec` | N 窗内**最早**满足距当前周期数，无则 0 ✓ |
| `_dyn_geom` | `lens=min(N, i-seg+1)` ✓、`js=floor(log2 lens)` ✓、`l1=i-lens+1` ✓、`l2=i-2^js+1` ✓ |
| `_dyn_rmq_vec` | 动态窗口最小值（**忽略 NaN** ✓，全 NaN ⇒ NaN ✓） |
"""
import numpy as np
import pytest

from app.factors import ops_ext as O


def _vals():
    return np.array([1.0, np.nan, 0.0, 2.0, np.nan, np.nan, 3.0, 0.0, 0.0, 4.0])


def _nvals():
    # 混合：正数 / 0 / 负数 / NaN / 小数（floor 与 <1 两条分支都要走到 ✓）
    return np.array([1.0, 2.0, 0.0, -3.0, np.nan, 2.7, 10.0, 0.4, 3.0, 1.0])


def _len_of(nvals):
    """朴素版 `_win_lens_vec` ✓。"""
    out = []
    for v in nvals:
        if np.isnan(v):
            out.append(1)
        else:
            out.append(max(1, int(np.floor(v))))
    return np.array(out, dtype=np.int64)


def test_win_lens_matches_naive():
    got = O._win_lens_vec(_nvals())
    np.testing.assert_array_equal(got, _len_of(_nvals()))
    # 边界：±inf 走 floor→int 那条（内核注释里专门警告过顺序 ✓）
    assert list(O._win_lens_vec(np.array([np.inf, -np.inf, np.nan, 0.0]))) == [1, 1, 1, 1]


def test_barslast_matches_naive():
    v = _vals()
    want = np.zeros(len(v))
    for i in range(len(v)):
        for j in range(i, -1, -1):
            if v[j] != 0 and not np.isnan(v[j]):
                want[i] = i - j
                break
    np.testing.assert_array_equal(O.barslast_vec(v), want)


def test_dyn_ref_matches_naive():
    v, nv = _vals(), _nvals()
    want = np.full(len(v), np.nan)
    for i in range(len(v)):
        shift = 0 if np.isnan(nv[i]) else int(np.trunc(nv[i]))
        j = i - shift
        if 0 <= j < len(v):
            want[i] = v[j]
    got = O.dyn_ref_vec(v, nv)
    np.testing.assert_allclose(got, want, equal_nan=True)


def test_dyn_window_sum_matches_naive():
    v, nv = _vals(), _nvals()
    lens = _len_of(nv)
    want = np.array([np.nansum(v[max(0, i - lens[i] + 1):i + 1]) for i in range(len(v))])
    np.testing.assert_allclose(O.dyn_window_sum_vec(v, nv), want)


def test_dyn_window_count_matches_naive():
    v, nv = _vals(), _nvals()
    lens = _len_of(nv)
    want = np.array([
        float(np.sum((v[max(0, i - lens[i] + 1):i + 1] != 0)
                    & ~np.isnan(v[max(0, i - lens[i] + 1):i + 1])))
        for i in range(len(v))])
    np.testing.assert_allclose(O.dyn_window_count_vec(v, nv), want)


def test_dyn_window_mean_matches_naive():
    """⭐ 分母是**非 NaN 个数** ✓ —— ≠ `DYN_COUNT`（那数的是"非 0 且非 NaN"✗）。"""
    v, nv = _vals(), _nvals()
    lens = _len_of(nv)
    want = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[max(0, i - lens[i] + 1):i + 1]
        ok = w[~np.isnan(w)]
        if ok.size:
            want[i] = ok.mean()
    np.testing.assert_allclose(O.dyn_window_mean_vec(v, nv), want, equal_nan=True)


def test_dyn_window_mean_all_nan_is_nan():
    v = np.array([np.nan, np.nan, np.nan])
    nv = np.array([3.0, 2.0, 1.0])
    got = O.dyn_window_mean_vec(v, nv)
    assert np.all(np.isnan(got)), "全 NaN 窗口必须给 NaN ✓（不能给 0 或 inf ✗）"


def test_barsincen_matches_naive():
    v = _vals()
    for N in (1, 2, 3, 5, 99):
        want = np.zeros(len(v))
        for i in range(len(v)):
            lo = max(0, i - max(1, N) + 1)
            for j in range(lo, i + 1):
                if v[j] != 0 and not np.isnan(v[j]):
                    want[i] = i - j
                    break
        np.testing.assert_array_equal(O.barsincen_vec(v, N), want,
                                      err_msg="N=%d 时与朴素实现不一致 ✗" % N)


def test_dyn_geom_matches_naive():
    nv = _nvals()
    for seg_start in (None, np.zeros(len(nv), dtype=np.int64),
                      np.array([0, 0, 0, 3, 3, 3, 3, 7, 7, 7])):
        js, l1, l2 = O._dyn_geom(nv, seg_start, len(nv))
        lens = _len_of(nv)
        for i in range(len(nv)):
            base = 0 if seg_start is None else int(seg_start[i])
            ln = min(int(lens[i]), i - base + 1)
            k = int(np.floor(np.log2(ln)))
            assert js[i] == k, "js 不符（i=%d）✗" % i
            assert l1[i] == i - ln + 1, "l1 不符（i=%d）✗" % i
            assert l2[i] == i - (1 << k) + 1, "l2 不符（i=%d）✗" % i


def test_dyn_rmq_matches_naive():
    """动态窗口最小值：**忽略 NaN** ✓，全 NaN 窗 ⇒ NaN ✓。"""
    v, nv = _vals(), _nvals()
    lens = _len_of(nv)
    want = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[max(0, i - lens[i] + 1):i + 1]
        acc = np.nan
        for x in w:                       # 与内核同一个 `fmin` 口径 ✓（不是 nanmin ✗）
            acc = x if np.isnan(acc) else (acc if np.isnan(x) else min(acc, x))
        want[i] = acc
    np.testing.assert_allclose(O._dyn_rmq_vec(v, nv, np.fmin), want, equal_nan=True)


def test_dyn_rmq_empty_and_single():
    assert O._dyn_rmq_vec(np.zeros(0), np.zeros(0), np.fmin).size == 0
    got = O._dyn_rmq_vec(np.array([5.0]), np.array([3.0]), np.fmin)
    np.testing.assert_allclose(got, np.array([5.0]))


@pytest.mark.parametrize("n", [0, 1, 5])
def test_barslast_edges(n):
    v = np.zeros(n) if n else np.zeros(0)
    got = O.barslast_vec(v)
    assert got.shape == (n,)
    assert not np.any(got), "全 0 ⇒ 全部 0 ✓（无满足点）"
