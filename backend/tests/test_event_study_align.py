# -*- coding: utf-8 -*-
"""`event_study._align_returns`（★ v2.9.99，2026-10-20 ✓ —— 事件收益对齐，最典型的「差一格」✓）。

## 它决定"事件研究的每个数"

把每个事件对齐到「**T+`lag` 买入、T+`lag`+k 卖出**」✓，返回 `mat[事件 × k]` 与期内 `max/min` ✓。
所有 IC / 胜率 / 中位数 / 分布图都建立在这张矩阵上 ✓ —— 错一格就是**整条曲线平移** ✗，
而界面依然画得很好看 ✓（这是最难受的一类 ✗）。

## 六条必须成立（都来自 docstring 与实际口径）

1. **`lag=1`（默认，次日成交）** ✓：买 `px[T+1]`、`j=0` 卖 `px[T+2]` ✓ ——
   这正是 `t1_close` / `t1_open` 的口径 ✓；
2. **`lag=0`（信号日成交）** ✓：买 `px[T]`、`j=0` 卖 `px[T+1]` ✓；
3. **标的缺失 / 信号日不在日历上 / `T+lag` 越界** ⇒ **整行 NaN** ✓（不许悄悄换一天 ✗）；
4. **买入价 NaN 或 ≤0** ⇒ 整行 NaN ✓（拿 0 做分母会出 inf ✗）；
5. **卖价越界（`q >= n_row`）** ⇒ 该 k 列 NaN ✓（不是用最后一天顶 ✗）；
6. `max_ret` / `min_ret` 只取**有限值** ✓；全 NaN ⇒ NaN ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.event_study import _align_returns

A = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]


def _px(codes=("A", "B"), n=8):
    idx = pd.bdate_range("2024-01-02", periods=n)
    data = {"A": A[:n]}
    if "B" in codes:
        data["B"] = [v * 2 for v in A[:n]]
    return pd.DataFrame(data, index=idx)[list(codes)]


def _events(rows):
    return pd.DataFrame(rows, columns=["code", "dt"])


def _d(i):
    return pd.bdate_range("2024-01-02", periods=8)[i]


def test_lag1_buys_next_day_and_sells_next_plus_k():
    """⭐ 默认 `lag=1`：买 `T+1`、`j=0` 卖 `T+2` ✓（差一格就整体平移 ✗）。"""
    px = _px()
    mat, mx, mn = _align_returns(px, _events([("A", _d(0))]), max_k=2, lag=1)
    assert mat.shape == (1, 2)
    assert mat[0, 0] == pytest.approx(12.0 / 11.0 - 1)
    assert mat[0, 1] == pytest.approx(13.0 / 11.0 - 1)
    assert mx[0] == pytest.approx(13.0 / 11.0 - 1)
    assert mn[0] == pytest.approx(12.0 / 11.0 - 1)


def test_lag0_buys_signal_day():
    px = _px()
    mat, _, _ = _align_returns(px, _events([("A", _d(0))]), max_k=1, lag=0)
    assert mat[0, 0] == pytest.approx(11.0 / 10.0 - 1)


def test_missing_code_gives_nan_row():
    px = _px()
    mat, mx, _ = _align_returns(px, _events([("ZZZ", _d(0))]), max_k=2, lag=1)
    assert np.isnan(mat[0]).all()
    assert np.isnan(mx[0])


def test_event_date_not_on_calendar_gives_nan_row():
    """⭐ 信号日必须**严格**落在交易日上 ✓（旧实现 `cal[p] != dt → skip` ✓）。"""
    px = _px()
    sat = pd.Timestamp("2024-01-06")            # 周六 ✓
    mat, _, _ = _align_returns(px, _events([("A", sat)]), max_k=2, lag=1)
    assert np.isnan(mat[0]).all()


def test_last_day_event_has_no_buy_row():
    """最后一天 + `lag=1` ⇒ 没有 T+1 ⇒ **整行 NaN** ✓（不许退化成当天 ✓）。"""
    px = _px()
    mat, _, _ = _align_returns(px, _events([("A", _d(7))]), max_k=2, lag=1)
    assert np.isnan(mat[0]).all()


def test_sell_beyond_end_gives_nan_for_that_k_only():
    """⭐ 卖价越界 ⇒ **该 k 列** NaN ✓，前面的列照常 ✓（不是整行丢 ✗）。"""
    px = _px()
    mat, mx, mn = _align_returns(px, _events([("A", _d(5))]), max_k=4, lag=1)
    # 买 px[6]=16；j=0 ⇒ px[7]=17 ⇒ 有效 ✓；j>=1 ⇒ 越界 ⇒ NaN ✓
    assert mat[0, 0] == pytest.approx(17.0 / 16.0 - 1)
    assert np.isnan(mat[0, 1]) and np.isnan(mat[0, 2]) and np.isnan(mat[0, 3])
    assert mx[0] == pytest.approx(17.0 / 16.0 - 1)


def test_nan_buy_price_gives_nan_row():
    px = _px()
    px.iloc[1, px.columns.get_loc("A")] = np.nan      # T+1 买入价缺失 ✓
    mat, _, _ = _align_returns(px, _events([("A", _d(0))]), max_k=2, lag=1)
    assert np.isnan(mat[0]).all()


def test_zero_or_negative_buy_price_gives_nan_row():
    px = _px()
    px.iloc[1, px.columns.get_loc("A")] = 0.0
    mat, _, _ = _align_returns(px, _events([("A", _d(0))]), max_k=1, lag=1)
    assert np.isnan(mat[0]).all()


def test_multiple_events_and_mixed_validity():
    """多事件时**行序必须与输入一致** ✓（错位 ⇒ 后续按行取因子名会张冠李戴 ✗）。"""
    px = _px()
    ev = _events([("A", _d(0)), ("ZZZ", _d(0)), ("B", _d(0))])
    mat, mx, _ = _align_returns(px, ev, max_k=1, lag=1)
    assert mat.shape == (3, 1)
    assert mat[0, 0] == pytest.approx(12.0 / 11.0 - 1)               # A ✓
    assert np.isnan(mat[1, 0])                                        # 缺失标的 ✓
    assert mat[2, 0] == pytest.approx(24.0 / 22.0 - 1)               # B（= 2×A）✓
    assert not np.isnan(mx[0]) and np.isnan(mx[1]) and not np.isnan(mx[2])


def test_empty_inputs_return_nan_shapes():
    px = _px()
    mat, mx, mn = _align_returns(px, _events([]), max_k=3, lag=1)
    assert mat.shape == (0, 3) and mx.shape == (0,) and mn.shape == (0,)
    mat2, _, _ = _align_returns(pd.DataFrame(), _events([("A", _d(0))]), max_k=2, lag=1)
    assert np.isnan(mat2[0]).all()
