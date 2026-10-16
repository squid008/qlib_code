# -*- coding: utf-8 -*-
"""★ T5（审计 ⑪ / v2.8.7，用户 2026-10-16 已拍 ✓）：`perf_metrics` 的**隐含契约显式化** ✗→✓。

审计原文：`nav_from_rets`（cumprod **不含**起点 1 ✗）、`max_drawdown`（第 0 点恒 0 ✗）、
`annualized_return`（以 `a[0]` 为基、`n = size - 1` ✗）⇒ 谁把**日收益数组**直接塞进来
就会**漏首日 / 天数差一天** ✗，且**不报错** ✓（静默差一点 ✗，最坏的一种 ✓）。

本文件钉住三条：
  ① 传"日收益数组"进 `max_drawdown`/`annualized_return` ⇒ **抛 ValueError**（不是静默算错 ✓）；
  ② `nav_with_start` 补上起点 1.0 ⇒ `len - 1` 恰好等于收益个数 ✓（年化口径不再差一天 ✓）；
  ③ 正常净值路径（`nav_from_rets` / `compute_perf`）**数字不变** ✓。
"""
import numpy as np
import pytest

from app.engine.perf_metrics import (
    annualized_return,
    compute_perf,
    max_drawdown,
    nav_from_rets,
    nav_with_start,
)

_RETS = [0.01, -0.02, 0.03, 0.005]


# ---------------------------- ① 传错类型必须报错 ----------------------------

def test_returns_array_into_max_drawdown_raises():
    """★★ 典型误用（把手里的日收益数组直接丢进来 ✗）⇒ **必须报错** ✓。"""
    with pytest.raises(ValueError) as ei:
        max_drawdown(_RETS)
    assert "日收益数组" in str(ei.value)


def test_returns_array_into_annualized_raises():
    with pytest.raises(ValueError) as ei:
        annualized_return(_RETS)
    assert "nav_with_start" in str(ei.value)


def test_nav_is_still_accepted():
    """正常净值（全正 ✓）不受影响 ✓ —— 改动不能把老路径拦掉 ✗。"""
    nav = nav_from_rets(_RETS)
    assert float(np.min(nav)) > 0
    assert max_drawdown(nav) < 0
    assert annualized_return(nav) is not None


# ---------------------------- ② 起点契约：天数 = len - 1 ----------------------------

def test_nav_with_start_prepends_one():
    """★ 含起点 ⇒ `len = 天数 + 1` ✓，且第 0 天恰好是 1.0 ✓。"""
    nav = nav_with_start(_RETS)
    assert len(nav) == len(_RETS) + 1 and nav[0] == 1.0
    # 末值 = 累计收益 ✓（与 nav_from_rets 的末值一致 ✓，只是多了个首点 ✓）
    assert nav[-1] == pytest.approx(nav_from_rets(_RETS)[-1])


def test_annualized_uses_len_minus_one():
    """★ 年化天数 = `len - 1` ✓ —— 含起点的 4 个收益 ⇒ 4 天 ✓（不是 5 ✗）。"""
    nav = nav_with_start(_RETS)
    want = float(np.prod([1.0 + r for r in _RETS]) ** (252.0 / len(_RETS)) - 1.0)
    assert annualized_return(nav) == pytest.approx(want, rel=1e-12)


def test_nav_with_start_empty_is_safe():
    assert list(nav_with_start([])) == [1.0]


# ---------------------------- ③ 老数字不变 ----------------------------

def test_compute_perf_numbers_unchanged():
    """★ `compute_perf`（引擎侧真正在用的入口 ✓）口径不动 ✓（现网所有调用方本来就补了首日 ✓）。"""
    r = compute_perf(_RETS)
    assert r["n_days"] == len(_RETS)
    assert r["nav_end"] == pytest.approx(float(np.prod([1 + x for x in _RETS])), rel=1e-6)
    assert r["total_return"] == pytest.approx(r["nav_end"] - 1.0, abs=1e-6)
    assert r["max_drawdown"] < 0 and r["sharpe"] is not None
