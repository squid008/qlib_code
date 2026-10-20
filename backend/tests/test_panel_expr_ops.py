# -*- coding: utf-8 -*-
"""`panel_expr` 的**模块级算子**（★ v2.10.1，2026-10-20 ✓ —— 每个因子值的直接来源 ✓）。

## 为什么这些最值钱

`_ref` / `_roll` / `_sma_apply_series` / `_ema_apply_series` / `_align` 是**所有面板公式的底层** ✓：
`MA` / `EMA` / `HHV` / `LLV` / `REF` / `SUM` / `STD` / `SMA` … 最终都落到这五个函数 ✓。
它们被 v1.18.30/33 做过**性能重写**（`groupby.rolling` → 逐段 numpy ✓ 实测 2~14× ✓），
注释保证"**逐位相同**" ✓ ⇒ 这里就用**朴素 groupby 写法对拍** ✓（这才是能钉住重写正确性的方式 ✓）。

## 还有一条容易忽略的语义：`sr`

`sr=True` 的语义是"**先剔掉 NaN 行（停牌），在压缩序列上算窗口/移位，再 scatter 回全日历**" ✓
（对齐 qlib 的实测行为 ✓）⇒ 停牌日**不参与窗口**、且输出仍是 NaN ✓。
写成普通的 `groupby.rolling` 会让停牌日**占用窗口位置** ✗ —— 结果差得很隐蔽 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.panel_expr import (
    _align,
    _ema_apply_series,
    _ref,
    _roll,
    _sma_apply_series,
)


def _mi(rows):
    """rows = [(code, value), ...]，每个 code 连续若干天 ✓。"""
    tup = []
    for code, vals in rows:
        for i, _ in enumerate(vals):
            tup.append((code, pd.Timestamp("2024-01-02") + pd.Timedelta(days=i)))
    vals = [v for _, vs in rows for v in vs]
    return pd.Series(vals, index=pd.MultiIndex.from_tuples(tup, names=["instrument", "datetime"]),
                     dtype=np.float64)


# --------------------------------------------------------------------- _ref
def test_ref_matches_groupby_shift():
    s = _mi([("A", [1.0, 2.0, 3.0]), ("B", [10.0, 20.0, 30.0])])
    got = _ref(s, 1, sr=False)
    exp = s.groupby(level=0, group_keys=False).shift(1)
    pd.testing.assert_series_equal(got, exp)


def test_ref_negative_k_is_future():
    s = _mi([("A", [1.0, 2.0, 3.0])])
    got = _ref(s, -1, sr=False)
    assert np.isnan(got.iloc[-1]) and got.iloc[0] == 2.0


def test_ref_sr_skips_nan_rows_then_scatters():
    """⭐ `sr=True`：**停牌日不占窗口** ✓ —— 值跨过缺口往前搬 ✓，停牌日仍是 NaN ✓。"""
    s = _mi([("A", [1.0, np.nan, 3.0])])
    got = _ref(s, 1, sr=True)
    assert np.isnan(got.iloc[0]), "第 1 行没有前值 ✓"
    assert np.isnan(got.iloc[1]), "停牌日必须仍是 NaN ✓（不参与、也不输出）"
    assert got.iloc[2] == 1.0, "压缩序列里 3.0 的前一个有效值是 1.0 ✓（不是 NaN ✗）"


def test_ref_empty_and_all_nan():
    s = _mi([("A", [np.nan, np.nan])])
    got = _ref(s, 1, sr=True)
    assert got.isna().all()
    empty = pd.Series([], dtype=np.float64,
                      index=pd.MultiIndex.from_arrays([[], []], names=["instrument", "datetime"]))
    assert _ref(empty, 1, sr=False).empty


# --------------------------------------------------------------------- _roll
@pytest.mark.parametrize("func", ["mean", "max", "min"])
def test_roll_matches_groupby_rolling(func):
    s = _mi([("A", [1.0, 2.0, 3.0, 4.0]), ("B", [5.0, 6.0, 7.0, 8.0])])
    got = _roll(s, func, 2, sr=False)
    exp = (s.groupby(level=0, group_keys=False)
             .rolling(2, min_periods=1).agg(func).reset_index(level=0, drop=True).sort_index())
    pd.testing.assert_series_equal(got.sort_index(), exp.sort_index(), check_names=False)


def test_roll_window_short_at_start_uses_min_periods_1():
    """⭐ 窗口不足按已有数据算 ✓（`min_periods=1`）—— 否则开头 N-1 行全 NaN ✗。"""
    s = _mi([("A", [1.0, 3.0])])
    got = _roll(s, "mean", 5, sr=False)
    assert got.iloc[0] == pytest.approx(1.0)
    assert got.iloc[1] == pytest.approx(2.0)


def test_roll_sr_skips_suspended_rows():
    """⭐ 停牌（NaN）不占窗口 ✓ —— 与"普通 rolling"的差别就在这里 ✓。"""
    s = _mi([("A", [1.0, np.nan, 3.0])])
    got = _roll(s, "mean", 2, sr=True)
    assert np.isnan(got.iloc[1])
    # 压缩序列 = [1, 3] ⇒ 第 3 行窗口 = [1, 3] ⇒ 均值 2 ✓（若含 NaN 会把窗口挤短 ✗）
    assert got.iloc[2] == pytest.approx(2.0)


def test_roll_empty():
    empty = pd.Series([], dtype=np.float64,
                      index=pd.MultiIndex.from_arrays([[], []], names=["instrument", "datetime"]))
    assert _roll(empty, "mean", 3, sr=False).empty


# --------------------------------------------------------------------- _align
def test_align_same_index_returns_originals():
    s = _mi([("A", [1.0, 2.0])])
    a, b = _align(s, s * 2)
    assert a is s, "同一 index 对象 ⇒ 直接返回 ✓（v1.18.33 性能优化 ✓，不许复制 ✗）"


def test_align_union_fills_missing_with_nan():
    """⭐ 不同行集 ⇒ **并集** ✓（缺的位置 NaN ✓ —— 不许丢掉某只票的日期 ✗）。"""
    a = pd.Series([1.0], index=pd.MultiIndex.from_tuples(
        [("A", pd.Timestamp("2024-01-02"))], names=["instrument", "datetime"]))
    b = pd.Series([2.0], index=pd.MultiIndex.from_tuples(
        [("A", pd.Timestamp("2024-01-03"))], names=["instrument", "datetime"]))
    ao, bo = _align(a, b)
    assert len(ao) == 2 and len(bo) == 2
    assert ao.isna().sum() == 1 and bo.isna().sum() == 1
    assert ao.sum() == 1.0 and bo.sum() == 2.0


# --------------------------------------------------------- _sma_apply_series
def test_sma_recursion_matches_formula():
    """`SMA(X,N,M)`：`Y=(M·X+(N−M)·Y_prev)/N` ✓（手算三行核对 ✓）。"""
    x = pd.Series([1.0, 2.0, 3.0])
    got = _sma_apply_series(x, 5, 1)
    y0 = 1.0
    y1 = (1 * 2 + 4 * y0) / 5
    y2 = (1 * 3 + 4 * y1) / 5
    assert got.iloc[0] == pytest.approx(y0)
    assert got.iloc[1] == pytest.approx(y1)
    assert got.iloc[2] == pytest.approx(y2)


def test_sma_zero_m_gives_all_nan():
    """⭐ `M=0` ⇒ **全 NaN** ✓（不是原值 ✗，也不是 0 ✗ —— 公式上 Y 恒为 0 无意义 ✓）。"""
    x = pd.Series([1.0, 2.0])
    assert _sma_apply_series(x, 5, 0).isna().all()
    assert _sma_apply_series(x, 0, 0).isna().all()


def test_sma_clamps_m_to_n():
    """`M > N` 必须**夹到 N** ✓（否则 alpha>1 ⇒ 结果发散 ✗）。"""
    x = pd.Series([1.0, 2.0, 3.0])
    got = _sma_apply_series(x, 3, 99)
    exp = x.ewm(alpha=1.0, adjust=False, min_periods=1).mean()
    pd.testing.assert_series_equal(got, exp)


# --------------------------------------------------------- _ema_apply_series
def test_ema_qlib_span_and_alpha_branches():
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    # N>=1 ⇒ ewm(span=N, min_periods=1) ✓（qlib 内建口径 ✓）
    pd.testing.assert_series_equal(_ema_apply_series(x, 5, tdx=False),
                                   x.ewm(span=5, min_periods=1).mean())
    # 0<N<1 ⇒ ewm(alpha=N) ✓
    pd.testing.assert_series_equal(_ema_apply_series(x, 0.3, tdx=False),
                                   x.ewm(alpha=0.3, min_periods=1).mean())


def test_ema_tdx_uses_adjust_false():
    """⭐ `EMA_TDX` 必须 **adjust=False** ✓（通达信递归式 ✓；`adjust=True` 只在序列开头有差 ✗）。"""
    x = pd.Series([1.0, 2.0, 3.0])
    pd.testing.assert_series_equal(_ema_apply_series(x, 3, tdx=True),
                                   x.ewm(alpha=0.5, adjust=False, min_periods=1).mean())
    # N=0 ⇒ expanding 均值 ✓
    pd.testing.assert_series_equal(_ema_apply_series(x, 0, tdx=True),
                                   x.expanding(min_periods=1).mean())


def test_ema_qlib_zero_n_hand_rolled_expanding():
    """`N=0` 的 qlib 口径是**手写 expanding 指数加权**（权重 `a=1−2/(1+n)` 递减 ✓）。"""
    x = pd.Series([1.0, 2.0, 4.0])
    got = _ema_apply_series(x, 0, tdx=False)
    # 第 3 点：n=3 ⇒ a=1-2/4=0.5；w=[0.25,0.5,1] ⇒ (0.25*1+0.5*2+1*4)/1.75 ✓
    assert got.iloc[2] == pytest.approx((0.25 * 1 + 0.5 * 2 + 1 * 4) / 1.75)
    assert not got.isna().any()
