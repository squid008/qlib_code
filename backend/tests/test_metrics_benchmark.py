# -*- coding: utf-8 -*-
"""`engine/metrics.py` 的基准归一与指标重算（★ v2.9.93，2026-10-19 ✓）。

## 为什么要归一 + 为什么要测

qlib 基准**日收益首行**含"窗口首日相对**前收**"的**段外收益** ✓（实测首日 −2.91% ⇒ 曲线从 `0.97` 起 ✗）。
而策略净值从 **1.0** 起 ✓ ⇒ 两条曲线**视觉错开** ✗（用户 2026-10 反馈过 ✓）。
`anchor_benchmark_start` / `normalize_benchmark_curve` 就是把这个起点拉齐 ✓。

但它**不只是观感** ✓：归一后必须**同步重算** `benchmark_return` 与 `annualized_excess_return` ✓
（否则"图上基准涨 5%、指标里基准涨 7.9%"✗ —— 同一页面两套口径，正是本项目最忌的 ✗）。

⇒ 本文件钉五条 ✓：
1. `anchor_benchmark_start` **返回新列表、不动入参** ✓（partial 曲线要复用它，不能污染原数据 ✗）；
2. 首个**有效** benchmark 归一为 1.0 ✓，缺失点保持 `None` ✓（不许填 0 ✗）；
3. 首点已 ≈1.0 ⇒ **原样返回**（不必复制 ✓）；
4. `normalize_benchmark_curve` 归一后 **`benchmark_return` = 末点 − 1** ✓
   （与展示曲线同口径 ✓）；
5. **`annualized_excess_return` 按 252 交易日年化重算** ✓（策略年化 − 基准年化 ✓）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engine.metrics import anchor_benchmark_start, normalize_benchmark_curve


def _nav(pairs):
    """pairs = [(value, benchmark|None), ...] ✓。"""
    out = []
    for i, (v, b) in enumerate(pairs):
        p = {"date": "2024-01-%02d" % (i + 2), "value": v}
        if b is not None:
            p["benchmark"] = b
        out.append(p)
    return out


# ------------------------------------------------------- anchor_benchmark_start
def test_anchor_empty_is_noop():
    assert anchor_benchmark_start([]) == []
    assert anchor_benchmark_start(None) is None


def test_anchor_returns_new_list_and_does_not_mutate_input():
    """⭐ partial（运行中）曲线要复用它 ⇒ **绝不能污染原 all_nav** ✗。"""
    nav = _nav([(1.0, 1.019), (1.01, 1.03)])
    out = anchor_benchmark_start(nav)
    assert out is not nav
    assert nav[0]["benchmark"] == 1.019, "入参被改了 ✗"
    assert out[0]["benchmark"] == pytest.approx(1.0, abs=1e-9)
    assert out[1]["benchmark"] == pytest.approx(1.03 / 1.019, abs=1e-6)
    # value 不动 ✓（只动 benchmark ✓）
    assert out[0]["value"] == 1.0


def test_anchor_skips_missing_benchmark_points():
    """首个**有效**点当锚 ✓；缺失点保持 `None` ✓（填 0 会把曲线拉到底 ✗）。"""
    nav = [{"date": "d0", "value": 1.0},                       # 无 benchmark ✓
           {"date": "d1", "value": 1.0, "benchmark": 0.98},
           {"date": "d2", "value": 1.0},
           {"date": "d3", "value": 1.0, "benchmark": 0.99}]
    out = anchor_benchmark_start(nav)
    assert "benchmark" not in out[0]
    assert out[1]["benchmark"] == pytest.approx(1.0, abs=1e-9)
    assert "benchmark" not in out[2]
    assert out[3]["benchmark"] == pytest.approx(0.99 / 0.98, abs=1e-6)


def test_anchor_early_returns_when_already_normalized_or_invalid():
    nav = _nav([(1.0, 1.0), (1.01, 1.02)])
    assert anchor_benchmark_start(nav) is nav, "已归一 ⇒ 原样返回（不复制）✓"
    bad = _nav([(1.0, 0.0), (1.01, 1.02)])
    assert anchor_benchmark_start(bad) is bad, "首点 <=0 ⇒ 原样返回 ✓"


# ---------------------------------------------------- normalize_benchmark_curve
def _result(nav, **over):
    r = SimpleNamespace(nav=nav, benchmark_return=None, annualized_excess_return=None,
                        annualized_return=None)
    for k, v in over.items():
        setattr(r, k, v)
    return r


def test_normalize_noop_on_empty():
    r = _result([])
    normalize_benchmark_curve(r)
    assert r.nav == []
    normalize_benchmark_curve(None)          # 不许抛 ✓


def test_normalize_rescales_in_place_and_recomputes_return():
    """⭐ 归一后 `benchmark_return` 必须 = **末点 − 1** ✓（图上曲线与指标同口径 ✓）。"""
    nav = _nav([(1.0, 0.98), (1.01, 0.99), (1.02, 1.029)])
    r = _result(nav)
    normalize_benchmark_curve(r)
    assert r.nav is nav, "原地改 ✓（不换列表 ✓）"
    assert r.nav[0]["benchmark"] == pytest.approx(1.0, abs=1e-9)
    assert r.benchmark_return == pytest.approx(1.029 / 0.98 - 1, abs=1e-9)


def test_normalize_recomputes_annualized_excess_with_252_days():
    """⭐ 年化超额按 **252 交易日**重算 ✓（策略年化 − 基准年化 ✓）。"""
    days = 252
    nav = [{"date": "d%03d" % i, "value": 1.0 + i * 0.0001,
            "benchmark": 0.98 * (1.0 + i * 0.00005)} for i in range(days)]
    r = _result(nav, annualized_return=0.20)
    normalize_benchmark_curve(r)
    bench_total = r.benchmark_return
    expect_ann_bench = (1 + bench_total) ** (1 / (days / 252.0)) - 1
    assert r.annualized_excess_return == pytest.approx(0.20 - expect_ann_bench, abs=1e-9)


def test_normalize_skips_excess_when_annualized_return_missing():
    nav = _nav([(1.0, 0.98), (1.02, 1.0)])
    r = _result(nav)          # annualized_return 为 None ✓
    normalize_benchmark_curve(r)
    assert r.benchmark_return is not None
    assert r.annualized_excess_return is None, "缺策略年化 ⇒ 不算超额 ✓（不许瞎算 ✗）"


def test_normalize_early_returns_when_already_normalized():
    nav = _nav([(1.0, 1.0), (1.02, 1.01)])
    r = _result(nav)
    normalize_benchmark_curve(r)
    assert r.nav[1]["benchmark"] == 1.01        # 没被缩放 ✓
    assert r.benchmark_return is None           # 没触发重算 ✓
