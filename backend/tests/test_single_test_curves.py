# -*- coding: utf-8 -*-
"""`single_test` 的分位曲线三件套（★ 2026-10-19 ✓ 覆盖率补强第四批）。

这三个函数是**纯 pandas/numpy** ✓（闭包分析实测：不含 qlib ✓）⇒ 直接造 DataFrame 就能测 ✓。

## 为什么值得测（每条都对应一个线上 bugfix ✓）

| 函数 | 钉住的口径 |
|---|---|
| `_shift_cum` | **右移一格** ✓：点 k = "截至调仓日 k 的累计" ✓，首点 0.0 ✓（v2.1.43：原来末点混进了"调仓日之后"的数据 ✗） |
| `_decile_matrix` | 必须转 **float64** ✓（面板 `LABEL` 是 float32 ⇒ cumsum 累积误差 ~1e-4 ✗，与 topk 路径不一致 ✗）；可用组 <2 或有效日 <2 ⇒ `None` ✓ |
| `_quantile_curves` | **多空 = 最强组 − 最弱组** ✓（v1.19.5 修：曾误用"第 1 组 − 最后一组" ⇒ **负向因子的多空曲线朝下、方向反了** ✗，用户 2026-09-12 发现 ✓） |
"""
import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import _decile_matrix, _quantile_curves, _shift_cum


# ------------------------------------------------------------------ _shift_cum
def test_shift_cum_moves_right_by_one():
    assert _shift_cum([1.0, 2.0, 3.0]) == [0.0, 1.0, 2.0]


def test_shift_cum_edges():
    assert _shift_cum([]) == []          # 空 ⇒ 空 ✓（不能抛 ✗）
    assert _shift_cum([5.0]) == [0.0]    # 单点 ⇒ 首点即 0 ✓
    # 末点永远是**倒数第二个**输入值 ✓（这正是"末点=该调仓日收盘净值"的关键 ✓）
    assert _shift_cum([1.0, 2.0, 3.0, 4.0])[-1] == 3.0


def test_shift_cum_rounds_to_six():
    out = _shift_cum([0.123456789])
    assert out == [0.0]
    out2 = _shift_cum([1.0, 0.123456789])
    assert out2[1] == 1.0 and len(out2) == 2
    assert _shift_cum([0.0, 1.23456789])[1] == 0.0


# -------------------------------------------------------------- _decile_matrix
def _series(vals, start="2024-01-02"):
    idx = pd.date_range(start, periods=len(vals), freq="B")
    return pd.Series([float(v) for v in vals], index=idx)


def test_decile_matrix_needs_two_groups():
    assert _decile_matrix({}) is None
    assert _decile_matrix({0: _series([1, 2])}) is None


def test_decile_matrix_needs_two_common_days():
    a = _series([1.0], "2024-01-02")            # 只有 1 天
    b = _series([2.0], "2024-01-02")
    assert _decile_matrix({0: a, 1: b}) is None


def test_decile_matrix_aligns_sorts_and_drops_nan():
    a = _series([0.01, np.nan, 0.03, 0.04])
    b = _series([0.02, 0.02, 0.02, np.nan])
    mat = _decile_matrix({1: b, 0: a})          # 故意乱序传入 ✓
    assert list(mat.columns) == [0, 1], "列必须按组号升序 ✓，实际 %s ✗" % list(mat.columns)
    assert len(mat) == 2, "只保留两组都有值的交易日 ✓，实际 %d ✗" % len(mat)
    assert not mat.isna().any().any()


def test_decile_matrix_is_float64():
    """⚠ float32 会被 `Series.cumsum()` 累积出 ~1e-4 误差 ✗（与 topk 路径不一致 ✗）。"""
    a = _series([0.01, 0.02, 0.03]).astype(np.float32)
    b = _series([0.02, 0.03, 0.04]).astype(np.float32)
    mat = _decile_matrix({0: a, 1: b})
    assert mat.dtypes.tolist() == [np.dtype("float64")] * 2, "必须转 float64 ✓"


# ------------------------------------------------------------ _quantile_curves
def _mat(rows):
    idx = pd.date_range("2024-01-02", periods=len(rows), freq="B")
    return pd.DataFrame(rows, index=idx, columns=[0, 1, 2])


def test_quantile_curves_basic_shape():
    mat = _mat([[0.01, 0.02, 0.03], [0.02, 0.02, 0.02], [0.03, 0.02, 0.01]])
    out = _quantile_curves(mat)
    assert out["n_groups"] == 3
    assert out["n_periods"] == 3
    assert len(out["dates"]) == 3
    for g in out["groups"]:
        assert len(g["cum"]) == 3
        assert g["cum"][0] == 0.0, "首点必须是 0.0（右移一格 ✓）"


def test_quantile_curves_best_worst_and_long_short_direction():
    """⭐ v1.19.5 的 bugfix：多空 = **最强 − 最弱** ✓ —— 不能是"第 1 组 − 最后一组" ✗。"""
    # 组 0 最强（均值最高），组 2 最弱 ⇒ best=0, worst=2
    mat = _mat([[0.05, 0.00, -0.03], [0.04, 0.00, -0.02], [0.06, 0.00, -0.04]])
    out = _quantile_curves(mat)
    assert out["best_quantile"] == 0, "最弱/最强必须由**数据**定 ✓，实际 best=%s ✗" % out["best_quantile"]
    assert out["worst_quantile"] == 2
    ls = out["long_short"]
    assert ls["quantile"] == [0, 2]
    # 多空累计必须**朝上** ✓（最强 − 最弱 = 0.08/0.06/0.10 每期 ✓）
    assert ls["cum"][-1] > 0, "多空曲线方向反了 ✗（这就是 2026-09-12 用户发现的那个 bug ✓）"


def test_quantile_curves_negative_factor_still_points_up():
    """负向因子（数值越大越差）⇒ 仍是"最强 − 最弱" ✓，曲线仍朝上 ✓。"""
    # 组 0 一直最差、组 2 一直最好 ⇒ best=2 ✓（不是"第 1 组"✗）
    mat = _mat([[-0.03, 0.00, 0.05], [-0.02, 0.00, 0.04]])
    out = _quantile_curves(mat)
    assert out["best_quantile"] == 2
    assert out["long_short"]["cum"][-1] > 0


def test_quantile_curves_baseline_and_excess():
    mat = _mat([[0.01, 0.02, 0.03]])
    out = _quantile_curves(mat)
    base = out["baseline_mean"]
    assert base == pytest.approx(0.02, abs=1e-6), "等频分组 ⇒ 基准 = 各组均值之平均 ✓"
    for g in out["groups"]:
        assert g["excess"] == pytest.approx(g["mean_ret"] - base, abs=2e-6)


def test_quantile_curves_monotonicity_sign():
    mat = _mat([[0.01, 0.02, 0.03], [0.01, 0.02, 0.03]])
    out = _quantile_curves(mat)
    assert out["monotonicity"] is not None
    assert out["monotonicity"] > 0.9, "严格递增的档位均值 ⇒ Spearman ≈ +1 ✓"
