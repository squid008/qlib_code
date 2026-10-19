# -*- coding: utf-8 -*-
"""价格宽表与进程级缓存（★ v2.9.86，2026-10-19 ✓ —— `_full_wide_key` / `_full_px_wide` 等）。

## 为什么专测缓存键

`_FULL_WIDE_CACHE` 存的是**全样本 PX 宽表**（注释：单份约 50MB，全 A 重建一次 3.3s ✓）⇒
用它省时间 ✓。但缓存键写漏一项，就会**返回另一张表** ✗，而结果只是"数字不对"✓（最阴的一类 ✓）：

* **v2.1.56 的真实教训** ✗：键原来只看**首值** ✓，而「开盘表」与「收盘表」的**首值可能恰好相同**
  （同日同股 ✓）⇒ 把**收盘宽表当开盘表返回** ✗ —— 这类 bug 不报错、不算崩，只是**所有离场/事件
  收益都按错的价算** ✓。修法是**多取一个中位样本** ✓。
  ⇒ 本文件专门构造"**首值相同、中位值不同**"的两张表，断言键**必须不同** ✓（去掉中位采样立刻红 ✓）。

另钉：缓存命中返回**同一对象** ✓、`clear_full_wide_cache()` 真的清空 ✓、
`_FULL_WIDE_MAX = 1`**只留最近一份** ✓、`_event_px_from_full` 的**列名大写归一** ✓
（触发代码小写也能对上 ✓）、以及它与 `_event_px_wide` 的**数值等价** ✓（后者是性能优化前的实现 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import single_test as ST


def _full(px_rows) -> pd.DataFrame:
    """px_rows = [(code, day_index, px), ...] ⇒ index=[instrument, datetime]，列 PX ✓。"""
    days = pd.bdate_range("2024-01-02", periods=6)
    idx = pd.MultiIndex.from_tuples(
        [(c, days[i]) for c, i, _ in px_rows], names=["instrument", "datetime"],
    )
    return pd.DataFrame({"PX": [p for _, _, p in px_rows]}, index=idx)


def _grid(codes=("A", "B"), n=6, base=10.0):
    rows = []
    for j, c in enumerate(codes):
        for i in range(n):
            rows.append((c, i, base + j + 0.1 * i))
    return _full(rows)


@pytest.fixture(autouse=True)
def _clean_cache():
    ST.clear_full_wide_cache()
    yield
    ST.clear_full_wide_cache()


# ------------------------------------------------------------------ _full_wide_key
def test_key_is_stable_for_same_frame():
    df = _grid()
    assert ST._full_wide_key(df, 0) == ST._full_wide_key(df, 0)


def test_key_changes_when_mid_value_differs_even_if_first_same():
    """⭐⭐ v2.1.56：**首值相同、中位不同** ⇒ 键必须不同 ✓（否则开盘表/收盘表串用 ✗）。"""
    df1 = _grid()
    df2 = df1.copy()
    # 只改中位那个值（首值原样 ✓）
    mid = len(df2) // 2
    df2.iloc[mid, df2.columns.get_loc("PX")] = 999.0
    assert float(df1["PX"].iloc[0]) == float(df2["PX"].iloc[0])
    assert ST._full_wide_key(df1, 0) != ST._full_wide_key(df2, 0)


def test_key_changes_with_shape_and_inst_level():
    df = _grid()
    assert ST._full_wide_key(df, 0) != ST._full_wide_key(df, 1)
    assert ST._full_wide_key(df, 0) != ST._full_wide_key(
        _grid(codes=("A", "B", "C")), 0)


# ----------------------------------------------------------------- _full_px_wide 缓存
def test_cache_hit_returns_same_object():
    df = _grid()
    w1 = ST._full_px_wide(df, 0)
    w2 = ST._full_px_wide(df, 0)
    assert w1 is w2
    assert list(w1.columns) == ["A", "B"]
    assert list(w1.index) == list(pd.bdate_range("2024-01-02", periods=6))


def test_clear_cache_forces_rebuild():
    df = _grid()
    w1 = ST._full_px_wide(df, 0)
    ST.clear_full_wide_cache()
    w2 = ST._full_px_wide(df, 0)
    assert w1 is not w2          # 重建 ✓
    assert w1.equals(w2)         # 但数值一致 ✓


def test_data_change_invalidates_cache_automatically():
    """数据变了（中位价变）⇒ 键变了 ⇒ **自动重建** ✓（不会返回旧表 ✗）。"""
    df1 = _grid()
    _ = ST._full_px_wide(df1, 0)
    df2 = df1.copy()
    df2.iloc[len(df2) // 2, df2.columns.get_loc("PX")] = 123.0
    w2 = ST._full_px_wide(df2, 0)
    assert 123.0 in set(np.asarray(w2.to_numpy(), dtype=float).ravel())


def test_cache_keeps_only_latest_entry():
    """`_FULL_WIDE_MAX = 1` ✓ ⇒ 换一张不同形状的表后，缓存里只留一份 ✓。"""
    assert ST._FULL_WIDE_MAX == 1
    ST._full_px_wide(_grid(), 0)
    ST._full_px_wide(_grid(codes=("A", "B", "C")), 0)
    assert len(ST._FULL_WIDE_CACHE) == 1


# ------------------------------------------------------------- _event_px_wide / from_full
def test_event_px_wide_subsets_and_ffills():
    df = _grid()
    # 让 A 的第 2 天停牌（NaN）✓
    df.loc[("A", pd.Timestamp("2024-01-03")), "PX"] = np.nan
    trig_index = pd.MultiIndex.from_tuples(
        [("A", pd.Timestamp("2024-01-02"))], names=["instrument", "datetime"])
    w = ST._event_px_wide(df, trig_index, 0)
    assert list(w.columns) == ["A"]                       # 只留触发标的 ✓
    assert not w["A"].isna().any()                        # 停牌日被 ffill ✓
    # ⚠ 停牌那天是 **index 1**（`_grid` 里 A 的第 2 天 ✓）—— 我第一版写成 iloc[2] 是错的 ✗
    assert w["A"].iloc[1] == pytest.approx(w["A"].iloc[0]), "停牌日应被前值填充 ✓"
    assert w["A"].iloc[2] == pytest.approx(10.2), "次日原值不该被动 ✓"


def test_event_px_wide_none_when_no_match():
    df = _grid()
    trig_index = pd.MultiIndex.from_tuples(
        [("ZZZ", pd.Timestamp("2024-01-02"))], names=["instrument", "datetime"])
    assert ST._event_px_wide(df, trig_index, 0) is None


def test_from_full_uppercases_codes_and_intersects():
    """⭐ 触发代码小写也能对上 ✓（`_event_px_from_full` 会 `.upper()` 归一 ✓）。"""
    w = ST._full_px_wide(_grid(), 0)
    trig_index = pd.MultiIndex.from_tuples(
        [("a", pd.Timestamp("2024-01-02"))], names=["instrument", "datetime"])
    sub = ST._event_px_from_full(w, trig_index, 0)
    assert list(sub.columns) == ["A"]
    # 没有交集 ⇒ None ✓（不是空 DataFrame ✗）
    none_idx = pd.MultiIndex.from_tuples(
        [("ZZZ", pd.Timestamp("2024-01-02"))], names=["instrument", "datetime"])
    assert ST._event_px_from_full(w, none_idx, 0) is None
    assert ST._event_px_from_full(None, trig_index, 0) is None
    assert ST._event_px_from_full(w.iloc[0:0], trig_index, 0) is None


def test_from_full_equals_event_px_wide():
    """两条实现**数值等价** ✓（后者是性能优化前的版本 ✓，注释里承诺过 ✓）。"""
    df = _grid()
    trig_index = pd.MultiIndex.from_tuples(
        [("A", pd.Timestamp("2024-01-02")), ("B", pd.Timestamp("2024-01-02"))],
        names=["instrument", "datetime"])
    slow = ST._event_px_wide(df, trig_index, 0)
    fast = ST._event_px_from_full(ST._full_px_wide(df, 0), trig_index, 0)
    pd.testing.assert_frame_equal(slow, fast)
