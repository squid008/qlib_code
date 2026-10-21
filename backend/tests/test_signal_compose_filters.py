# -*- coding: utf-8 -*-
"""`engine/signal_compose.py` 的**无需行情**部分（★ v2.10.45，2026-10-21 ✓）。

## 这一轮的真正收获：`apply_hard_filters` **从来没跑通过**

它原来是这么调内部函数的 ✓：

```python
_daily_series(None, score.index, ["$market_cap", "$close", "$factor"])   # 3 个参数 ✗
def _daily_series(score_idx, fields):                                   # 只收 2 个 ✗
```

⇒ 只要用户配了**任一**硬过滤（市值上下限 / 最低股价）⇒ **`TypeError`** ✓
⇒ 「确定性硬规则闸门」这个功能**一次都没成功执行过** ✓。
它要 qlib 行情 ⇒ 常规测试碰不到 ✗ ⇒ **是 mypy 的 `call-arg` 把它揪出来的** ✓。

⇒ 这里的测试用**严格两参数的桩**替换 `_daily_series` ✓：
谁再传三个参数，`TypeError` 立刻让用例红 ✓（**签名不匹配也算被测项** ✓）。
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.engine import signal_compose as SC


# ------------------------------------------------------------------ align_trig
def _idx(pairs):
    return pd.MultiIndex.from_tuples(pairs, names=["instrument", "datetime"])


def test_align_trig_reindexes_and_fills_nan():
    """未命中的行必须 **NaN** ✓（不是 0 ✗ —— 0 会被当成"真的没有市值"而误剔 ✓）。"""
    src = pd.DataFrame({"trig": [1.0, 0.0]},
                       index=pd.MultiIndex.from_tuples(
                           [("A", pd.Timestamp("2024-01-02")), ("A", pd.Timestamp("2024-01-03"))],
                           names=["instrument", "datetime"]))
    tgt = _idx([("A", "2024-01-02"), ("A", "2024-01-04"), ("B", "2024-01-02")])
    got = SC.align_trig(src, tgt)
    assert list(got.index) == list(tgt)
    assert got["trig"].iloc[0] == 1.0
    assert np.isnan(got["trig"].iloc[1]) and np.isnan(got["trig"].iloc[2])


# ------------------------------------------------------------ apply_hard_filters
def _score() -> pd.Series:
    return pd.Series([1.0, 2.0, 3.0, 4.0],
                     index=_idx([("A", "2024-01-02"), ("B", "2024-01-02"),
                                 ("C", "2024-01-02"), ("D", "2024-01-02")]))


def _req(**hf) -> SimpleNamespace:
    return SimpleNamespace(hard_filters=hf)


@pytest.fixture
def patch_daily(monkeypatch):
    """⚠ **严格两参数**的桩 ✓ —— 这正是要钉的东西 ✓（多一个参数就 TypeError ✓）。"""
    seen = {}

    def _fake(score_idx, fields):
        seen["n_args"] = 2
        seen["fields"] = list(fields)
        return pd.DataFrame({
            "$market_cap": [1.0e10, 5.0e11, np.nan, 1.0e10],   # B 太大 ✓ C 缺失 ✓
            "$close": [10.0, 20.0, 30.0, 8.0],
            "$factor": [1.0, 1.0, 1.0, 2.0],                   # D 的真实价 = 8/2 = 4 ✓
        }, index=_score().index)

    monkeypatch.setattr(SC, "_daily_series", _fake)
    return seen


def test_no_filters_returns_same_object():
    s = _score()
    assert SC.apply_hard_filters(s, _req()) is s, "没配过滤 ⇒ 必须原样返回（别复制、别动值 ✓）"
    assert SC.apply_hard_filters(s, _req(min_mktcap_bn=None, max_mktcap_bn=None,
                                         min_price=None)) is s


def test_market_cap_band_excludes_outliers_and_missing(patch_daily):
    """⭐ 市值上下限：超出 ⇒ `-inf` ✓；**缺失（停牌）⇒ 也 `-inf`** ✓（"视为不满足"✓）。"""
    got = SC.apply_hard_filters(_score(), _req(min_mktcap_bn=100, max_mktcap_bn=2000))
    # 1e10 = 100 亿 ✓ 在下限内；5e11 = 5000 亿 ✓ 超出上限；NaN ⇒ 剔
    assert got.iloc[0] == 1.0, "100 亿正好等于下限 ⇒ 保留 ✓（>= 不是 > ✓）"
    assert got.iloc[1] == -np.inf
    assert got.iloc[2] == -np.inf, "市值缺失（停牌）必须剔 ✓（把 NaN 当 0 会变成「很小」✗）"
    assert got.iloc[3] == 4.0


def test_min_price_uses_real_price(patch_daily):
    """⭐⭐ 最低股价比的是**真实价** = `$close / $factor` ✓ ——
    直接拿后复权价比 ⇒ **历史越久越容易过** ✓（复权因子越老越小 ✗）⇒ 等于没过滤 ✓。"""
    got = SC.apply_hard_filters(_score(), _req(min_price=9))
    assert got.iloc[0] == 1.0            # 10/1 = 10 ✓ 过
    assert got.iloc[3] == -np.inf        # 8/2 = 4 ✗ 不过（**后复权价 8 会误过** ✓ 所以这条很重要）
    assert patch_daily["fields"] == ["$market_cap", "$close", "$factor"]


def test_daily_series_is_called_with_two_args(patch_daily):
    """⭐⭐ 直接把"签名契约"钉住 ✓：`_daily_series` 只收 **2 个** 参数 ✓。
    原实现多传了一个 `None` ✗ ⇒ 只要配了过滤就 `TypeError` ✓（真 bug，已修 ✓）。
    """
    SC.apply_hard_filters(_score(), _req(min_mktcap_bn=1))
    assert patch_daily.get("n_args") == 2


def test_filters_do_not_mutate_input(patch_daily):
    s = _score()
    SC.apply_hard_filters(s, _req(min_price=9))
    assert list(s.values) == [1.0, 2.0, 3.0, 4.0], "不许原地改调用方的 score ✗"
