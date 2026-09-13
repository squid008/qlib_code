# -*- coding: utf-8 -*-
"""`app/engine/stats.py` 纯函数单测（v1.19.24 从 `factors/single_test.py` 搬出）。

为什么要锁：这两个统计量是**单因子测试表「日配对稳定」与事件研究弹窗第④条的判据**，
搬模块时若数值漂了，判定结论会静默改变 ⇒ 用**独立实现的朴素公式**对拍。
"""
import numpy as np
import pytest

from app.engine import stats


def _naive_hac_t(x, maxlags=None):
    """独立实现的 Newey-West HAC t（**不调用被测代码**），用于对拍。"""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if maxlags is None:
        maxlags = max(1, min(int(4 * (n / 100.0) ** (2 / 9.0)), n - 2))
    m = float(x.mean())
    xc = x - m
    gam = [float(np.sum(xc[: n - k] * xc[k:]) / n) for k in range(maxlags + 1)]
    var = gam[0] + 2.0 * sum((1 - j / (maxlags + 1)) * gam[j] for j in range(1, maxlags + 1))
    return m / np.sqrt(max(var, 1e-18) / n)


class TestStats:
    def test_hac_t_matches_naive(self):
        rng = np.random.default_rng(7)
        x = rng.normal(0.0, 0.01, 400) + 0.002
        assert stats.hac_t(x) == pytest.approx(_naive_hac_t(x), rel=1e-12)

    def test_hac_t_needs_three_points(self):
        assert stats.hac_t(np.array([1.0, 2.0])) is None
        assert stats.hac_t(np.array([1.0, 2.0, 3.0])) is not None

    def test_hac_t_explicit_maxlags(self):
        x = np.array([0.1, -0.2, 0.3, 0.15, -0.05, 0.2, -0.1, 0.05])
        assert stats.hac_t(x, 2) == pytest.approx(_naive_hac_t(x, 2), rel=1e-12)

    def test_acf(self):
        x = np.array([1.0, 2.0, 3.0, 4.0])
        xc = x - x.mean()
        assert stats.acf(x, 1) == pytest.approx(np.sum(xc[:3] * xc[1:]) / np.sum(xc ** 2))
        assert stats.acf(x, 10) == 0.0           # lag ≥ n → 0
        assert stats.acf(np.ones(5), 1) == 0.0   # 零方差 → 0

    def test_pair_stability(self):
        x = np.array([0.01, -0.02, 0.03, 0.04, -0.01])
        t, w, n = stats.pair_stability(x)
        assert n == 5
        assert w == pytest.approx(3 / 5)
        assert t == pytest.approx(_naive_hac_t(x))

    def test_pair_stability_edge_cases(self):
        # 配对日 < 2 ⇒ 与行级 `daily_t` 同一门限：不产出 t/胜率，但日数照报
        assert stats.pair_stability(np.array([0.01])) == (None, None, 1)
        assert stats.pair_stability(np.array([]))[2] == 0
        # NaN 不计入（口径：只在"当日两组都有样本"的配对日上统计）
        t2, w2, n2 = stats.pair_stability(np.array([0.01, np.nan, 0.03]))
        assert (n2, w2) == (2, 1.0)
        # 配对日 = 2：HAC t 需 ≥3 点（否则方差无法可靠估计）⇒ t 为空、胜率仍给
        # （与行级一致：daily_win n≥2 即有、daily_t_hac 需 n≥3）
        assert t2 is None
        assert stats.pair_stability(np.array([0.01, 0.02, 0.03]))[0] is not None
