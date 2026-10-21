# -*- coding: utf-8 -*-
"""物化字段缺失时的**自愈与告警**（★ v2.10.63，2026-10-21 ✓ —— P2-6 第二刀）。

## 这块为什么值得测

物化字段（`chip_*` / `pre*` / `prevwap*` / `mkt_*`）**不在 git 里** ✓ ——
每台机器要各跑一次 `backend/tools/materialize_*.py` ✓。漏跑的表现是：

> **不报错、不崩、公式照样跑** ✓ —— 只是结果**整列 NaN** ✗
> （`COST()/WINNER()` 静默失效、`BLOCKSETNUM()/INSUM()` 板内统计全 NaN、`forward` 前复权失效 ⇒
> 价格量纲因子被按**后复权价**排序 ✓）。

⇒ 这是典型的"**错了但界面正常**"✓，所以这段代码做了两件事：
① **`mkt_*` 先试现算回退** ✓（结果正确、只是慢 ✗）；② 仍失败才**出声告警**（每字段仅一次 ✓）。

⚠ 告警走 `_LOGGER.warning`；`_LOGGER` 为 `None` 时退 `warnings.warn` ✓ ——
本文件统一把 `_LOGGER` 置 `None` 走 `pytest.warns` ✓（比抓 logging 稳 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as P


@pytest.fixture(autouse=True)
def _reset_warned(monkeypatch):
    """清"已告警过"集合 + 强制走 `warnings` 分支 ✓（模块级全局状态会互相污染 ✗）。"""
    P._MISSING_WARNED.clear()
    monkeypatch.setattr(P, "_LOGGER", None)
    yield
    P._MISSING_WARNED.clear()


def _series(values, n_inst=2):
    """两层索引（`instrument, datetime`）✓ —— 代码里 `nlevels > 1` 时才取第 1 层日期 ✓。"""
    n = len(values)
    idx = pd.MultiIndex.from_product(
        [["A", "B"][:n_inst], pd.date_range("2024-01-02", periods=n // n_inst, freq="D")],
        names=["instrument", "datetime"])
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def test_non_materialized_field_is_ignored():
    """非物化前缀（普通行情字段）⇒ **完全不处理** ✓（别对着 `$close` 报物化缺失 ✗）。"""
    s = _series([np.nan] * 6)
    with pytest.warns(None) if False else _no_warn():
        P._warn_if_materialized_missing("$close", s)


class _no_warn:
    """上下文：断言**没有任何 warning** ✓（`pytest.warns(None)` 新版已移除 ✗）。"""

    def __enter__(self):
        import warnings
        self._ctx = warnings.catch_warnings(record=True)
        self._rec = self._ctx.__enter__()
        warnings.simplefilter("always")
        return self

    def __exit__(self, *exc):
        self._ctx.__exit__(*exc)
        assert not [w for w in self._rec if issubclass(w.category, UserWarning)], \
            "这里不该有任何告警 ✗：%r" % [str(w.message) for w in self._rec]
        return False


def test_has_any_value_is_ignored():
    """整列**只要有一个有限值** ⇒ 视为正常 ✓（物化文件在 ✓，无需告警）。"""
    s = _series([np.nan, np.nan, 1.5, np.nan, np.nan, np.nan])
    with _no_warn():
        P._warn_if_materialized_missing("chip_cost", s)


def test_chip_missing_warns_once_and_mentions_fix_command():
    """⭐⭐ `chip_*` 全 NaN ⇒ 告警**一次** ✓，且文案里必须给出**修复命令** ✓ ——
    只喊"缺了"而不指路 = 用户照样不知道怎么办 ✗（本项目 `md/deploy.md` 的清单要能对上 ✓）。"""
    s = _series([np.nan] * 6)
    with pytest.warns(UserWarning) as rec:
        P._warn_if_materialized_missing("chip_cost", s)
    msg = str(rec[0].message)
    assert "chip_cost" in msg
    assert "materialize_chip.py" in msg, "必须给出修复命令 ✓"
    assert "COST()" in msg or "WINNER()" in msg, "要说清**影响什么公式** ✓"

    # 第二次**不许再喊** ✓（每次评估都刷屏 ⇒ 日志没法看 ✗）
    with _no_warn():
        P._warn_if_materialized_missing("chip_cost", s)


def test_market_field_falls_back_inline_and_stays_silent(monkeypatch):
    """⭐⭐ `mkt_*` 缺失 ⇒ **先现算回退** ✓：填进 series、**且不告警** ✓
    （已不是失效状态 ⇒ 再喊"缺文件"是误导 ✗）。"""
    from app.factors import market_stat

    s = _series([np.nan] * 6)
    # ⚠ stub 必须返回**按日期索引**的 Series ✗→✓：代码会 `ser.reindex(DatetimeIndex(dates))` ✓，
    #   若不带 DatetimeIndex（或带**重复**索引 ✗，reindex 会抛 ✓）⇒ 回填全是 NaN
    #   ⇒ 测试会误判成"回退失败"✓（我第一版就是这样红的 ✓）。
    dates = pd.DatetimeIndex(s.index.get_level_values(1)).unique()
    stub = pd.Series(np.arange(1.0, len(dates) + 1.0), index=dates)
    monkeypatch.setattr(market_stat, "compute_field_inline", lambda key: stub)
    with _no_warn():
        P._warn_if_materialized_missing("mkt_insum_purity", s)
    assert np.isfinite(s.to_numpy(dtype=float)).all(), "回退成功后必须**就地填好** ✓"
    # ⚠ 实测：回退**成功**时**不**记入 `_MISSING_WARNED` ✗（只有失败才记 ✓）——
    #   我第一版以为"自愈了就记住"，实测是 `set()` 空 ✓。
    #   ⇒ 后果：**每次评估都会重新现算回退** ✓（结果正确 ✓，但走的是"明显更慢"那条路 ✗）。
    #   已记进 `md/技术债.md`（P2-#16），这里只钉**现状** ✓（不假装它是设计 ✗）。
    assert "mkt_insum_purity" not in P._MISSING_WARNED


def test_market_field_fallback_failure_warns(monkeypatch):
    """`mkt_*` 回退**失败** ⇒ 照旧出声 ✓（"慢是必然的，这是坏了才走的退路"✓）。"""
    from app.factors import market_stat

    def _boom(key):
        raise RuntimeError("no data")

    monkeypatch.setattr(market_stat, "compute_field_inline", _boom)
    s = _series([np.nan] * 6)
    with pytest.warns(UserWarning) as rec:
        P._warn_if_materialized_missing("mkt_insum_count", s)
    assert "materialize_market.py" in str(rec[0].message), "必须指路到 mkt_ 的物化脚本 ✓"


def test_market_fallback_returning_all_nan_still_warns(monkeypatch):
    """回退**返回全 NaN** ⇒ 等于没救 ✓ ⇒ 必须告警（不许静默通过 ✗）。"""
    from app.factors import market_stat

    monkeypatch.setattr(market_stat, "compute_field_inline",
                        lambda key: pd.Series([np.nan] * 6))
    s = _series([np.nan] * 6)
    with pytest.warns(UserWarning):
        P._warn_if_materialized_missing("mkt_insum_count", s)


def test_forward_prefix_warns_about_price_scale():
    """`pre*`（前复权）缺失 ⇒ 文案必须点明**后果**：价格量纲因子会按**后复权价**排序 ✓
    （那不是"稍微有点偏差"✗，是**选股顺序整体改变** ✗）。"""
    s = _series([np.nan] * 6)
    with pytest.warns(UserWarning) as rec:
        P._warn_if_materialized_missing("preclose", s)
    msg = str(rec[0].message)
    assert "build_preclose.py" in msg
    assert "后复权" in msg, "必须说清后果 ✓"


def test_empty_series_does_not_warn():
    """空列（size 0）⇒ 直接返回 ✓（空表不该刷"物化缺失"✗）。"""
    s = _series([], n_inst=2)
    with _no_warn():
        P._warn_if_materialized_missing("chip_turn", s)
