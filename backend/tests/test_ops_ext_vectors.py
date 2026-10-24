# -*- coding: utf-8 -*-
"""`app/factors/ops_ext.py` 的**向量化算子**（★ v2.11.72）—— 纯 numpy，不需要任何行情数据。

## 为什么挑它们
`ops_ext.py` 缺 **303 行 (62.3%)** ✓，而其中一大半是 `*_vec()` 纯 numpy 实现 ✓（qlib 注册那层才需要数据 ✗）。
这些函数是**公式引擎的地基**：`BARSLAST` / `DYN_REF` / `DYN_SUM` / `DYN_COUNT` / `DYN_MEAN` / `BARSSINCEN`
—— 算错了**不会报错**，只会让公式结果悄悄偏 ✓（和 v2.11.64 那个科创板 bug 同一性质 ✗）。

## 口径要点（测试要钉住的）
· **NaN 的三种处理各不相同** ✗ —— 这是最容易写错的地方：
  `DYN_SUM` 把 NaN 当 0 ✓ ／ `DYN_COUNT` 数"非 0 且非 NaN" ✓ ／
  `DYN_MEAN` 分母是"**非 NaN 个数**" ✓（≠ COUNT ✗，全 NaN 窗口 ⇒ NaN ✓）。
· **窗口几何**：`[i - N_i + 1, i]`，左端**截到 0** ✓（开头窗口不足按可用天数 ✓，等价 `min_periods=1` ✓）。
"""
from __future__ import annotations

import numpy as np

from app.factors.ops_ext import (
    _dyn_rmq_vec,
    barsincen_vec,
    barslast_vec,
    dyn_ref_vec,
    dyn_window_count_vec,
    dyn_window_mean_vec,
    dyn_window_sum_vec,
)


def _a(*xs) -> np.ndarray:
    return np.array(xs, dtype=float)


class TestBarslast:
    def test_distance_since_last_hit(self):
        """`[0,1,0,0,2,NaN,0]` ⇒ 1 在 idx1、2 在 idx4 ⇒ 距离 `[0,0,1,2,0,1,2]` ✓。"""
        out = barslast_vec(_a(0, 1, 0, 0, 2, np.nan, 0))
        assert out.tolist() == [0, 0, 1, 2, 0, 1, 2]
        assert out[-1] == 2, "NaN 不算命中 ✓（跳到上一个有效点 ✓）"

    def test_never_hit_is_zero(self):
        assert barslast_vec(_a(0, 0, 0)).tolist() == [0, 0, 0]

    def test_empty(self):
        assert len(barslast_vec(np.zeros(0))) == 0


class TestDynRef:
    def test_shift_by_given_length(self):
        """`N=[0,1,2,10]` ⇒ 取 `i-N_i`：`[v0, v0, v0, NaN]` ✓（越界给 NaN ✓）。"""
        out = dyn_ref_vec(_a(1, 2, 3, 4), _a(0, 1, 2, 10))
        assert out[0] == 1 and out[1] == 1 and out[2] == 1
        assert np.isnan(out[3]), "越界应给 NaN（不能被 0 顶替 ✗）"

    def test_nan_length_treated_as_zero(self):
        out = dyn_ref_vec(_a(7, 8), _a(np.nan, 0))
        assert out[0] == 7 and out[1] == 8, "N 为 NaN 当作 0 ⇒ 取当前值 ✓"

    def test_truncates_fractional_length(self):
        out = dyn_ref_vec(_a(1, 2, 3), _a(0, 1.9, 2.9))
        assert out[1] == 1 and out[2] == 1, "要向零截断 1.9→1 ✓"


class TestDynWindows:
    def test_sum_window(self):
        """`N=[1,1,2,2]` ⇒ 窗 `[i, i] [i, i] [i-1,i] [i-1,i]` ⇒ `[1,2,5,7]` ✓。"""
        out = dyn_window_sum_vec(_a(1, 2, 3, 4), _a(1, 1, 2, 2))
        assert out.tolist() == [1, 2, 5, 7]

    def test_sum_treats_nan_as_zero(self):
        out = dyn_window_sum_vec(_a(1, np.nan, 3), _a(2, 2, 2))
        assert out[1] == 1.0, "NaN 当 0 参与和 ✓（不能把整窗变 NaN ✗）"
        assert out[2] == 3.0

    def test_count_excludes_zero_and_nan(self):
        """`DYN_COUNT` 数的是**非 0 且非 NaN** ✓ —— 与 `DYN_SUM` 的 NaN 口径不同 ✗。"""
        out = dyn_window_count_vec(_a(1, 0, 3, np.nan), _a(1, 1, 2, 2))
        assert out.tolist() == [1, 0, 1, 1]

    def test_mean_matches_rolling_min_periods_1(self):
        """⭐⭐ `DYN_MEAN` 口径 = `rolling(N, min_periods=1).mean()` ✓ —— 用 pandas 现场对拍 ✓。"""
        import pandas as pd

        vals = _a(1, np.nan, 3, 4, np.nan, 6)
        lens = _a(1, 1, 2, 3, 2, 3)
        got = dyn_window_mean_vec(vals, lens)
        s = pd.Series(vals)
        want = np.array([s.iloc[max(0, i - int(lens[i]) + 1): i + 1].mean() for i in range(len(vals))])
        assert np.allclose(got, want, equal_nan=True), "got=%s want=%s" % (got, want)

    def test_mean_all_nan_window_is_nan(self):
        """全 NaN 窗口 ⇒ **NaN** ✓（不能给 0 ✗ —— 0 会被当成"真实收益为 0"混进统计 ✓）。"""
        out = dyn_window_mean_vec(_a(np.nan, np.nan, 5), _a(2, 2, 1))
        assert np.isnan(out[0]) and np.isnan(out[1])
        assert out[2] == 5.0


class TestBarsincen:
    def test_earliest_in_window(self):
        """`N=3`、命中在 idx0 ⇒ 距离 `[0,1,2]` ✓（最早那次，不是最近那次 ✗）。"""
        assert barsincen_vec(_a(1, 0, 0), 3).tolist() == [0, 1, 2]

    def test_no_hit_within_window_is_zero(self):
        """窗口内**没有**命中 ⇒ 0 ✓（注意：不是"到上一个命中的距离" ✗）。"""
        out = barsincen_vec(_a(1, 0, 0, 0, 0), 2)
        assert out[0] == 0
        assert out[3] == 0 and out[4] == 0, "窗口只有 2 天、命中已出窗 ⇒ 0 ✓"

    def test_picks_earliest_not_latest(self):
        """⭐⭐ 这条最容易写错：`BARSSINCEN` 要的是**最早**命中 ⇒ 若误写成"最近"，
        同一天会有两个不同答案 ✓（这条能把两种实现区分开 ✓）。"""
        # 命中在 idx1 和 idx2，N=3：i=3 的窗口 [1,3] ⇒ 最早 = idx1 ⇒ 距离 2（最近会是 1 ✗）
        out = barsincen_vec(_a(0, 1, 1, 0), 3)
        assert out[3] == 2, "应取最早命中（距离 2），实际 %s ⇒ 可能写成了\"最近命中\" ✗" % out[3]

    def test_zero_and_nan_are_not_hits(self):
        out = barsincen_vec(_a(np.nan, 0, 0), 3)
        assert out.tolist() == [0, 0, 0]


class TestDynRmq:
    def test_max_and_min(self):
        """`_dyn_rmq_vec` 是动态窗口最值的稀疏表版本 ⇒ 与朴素窗口最值逐位一致 ✓。"""
        vals = _a(1, 3, 2, 5, 4)
        lens = _a(1, 2, 2, 3, 2)
        for func, ref in ((np.fmax, np.max), (np.fmin, np.min)):
            got = _dyn_rmq_vec(vals, lens, func)
            want = np.array([ref(vals[max(0, i - int(lens[i]) + 1): i + 1]) for i in range(len(vals))])
            assert np.allclose(got, want), "%s: got=%s want=%s" % (func.__name__, got, want)

    def test_empty(self):
        assert len(_dyn_rmq_vec(np.zeros(0), np.zeros(0), np.fmax)) == 0
