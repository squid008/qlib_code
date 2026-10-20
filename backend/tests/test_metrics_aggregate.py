# -*- coding: utf-8 -*-
"""`_aggregate_from_nav`（★ v2.9.98，2026-10-20 ✓ —— 把分段净值拼成整条并汇总指标 ✓）。

## 它算的是"回测结束后那一眼看到的全部数字"

总收益 / 年化 / 最大回撤 / 夏普 / 胜率 / 基准收益 / 年化超额 —— 全部在这里一次算出 ✓。
口径错一处，界面上**只有数字变**、看不出异常 ✗，所以逐条钉 ✓。

## ★ 一条审计修复（T2 / v2.8.7，用户 2026-10-16 拍板）

旧实现 `result.nav = all_nav[:2000]` ✗ —— 长区间（10 年 ≈ **2500** 个交易日）被**截尾** ✗
⇒ 界面出现「**总收益 100%，但曲线终值只有 1.80、最后 500 天不见**」✗ ——
因为指标是用**完整序列**算的 ✓，而曲线被截断 ⇒ **指标与曲线自相矛盾** ✗。
现在必须**保留全程** ✓（10 年 2500 点、百 KB 级 ✓ 不是成本问题；绘图抽样交给前端 ✓）。
⇒ 本文件专门构造 **2500 点**，断言 `len(result.nav) == 2500` ✓（有人再写 `[:2000]` 立刻红 ✓）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.engine.metrics import _aggregate_from_nav


def _nav(closes, bench=None):
    out = []
    for i, v in enumerate(closes):
        p = {"date": "2024-%02d-%02d" % (1 + i // 28, 1 + i % 28), "value": float(v)}
        if bench is not None:
            p["benchmark"] = float(bench[i])
        out.append(p)
    return out


def test_empty_nav_returns_empty_result():
    r = _aggregate_from_nav([], [])
    # ⚠ `BacktestResult.nav` 默认是 **None** ✗ 不是 `[]` ✓（我第一版断言 `== []` 直接红 ✓）
    assert r.nav is None
    assert r.total_return is None
    assert r.annualized_return is None


def test_total_return_and_annualized_use_252_days():
    closes = [1.0] * 251 + [1.21]        # 252 个点 ⇒ 恰好 1 年 ✓
    r = _aggregate_from_nav(_nav(closes), [None])
    assert r.total_return == pytest.approx(0.21, abs=1e-12)
    assert r.annualized_return == pytest.approx(0.21, abs=1e-9)   # 1 年 ⇒ 年化 = 总收益 ✓


def test_max_drawdown_is_peak_to_trough():
    closes = [1.0, 1.2, 0.9, 1.1]        # 峰 1.2 → 谷 0.9 ⇒ −25% ✓
    r = _aggregate_from_nav(_nav(closes), [None])
    assert r.max_drawdown == pytest.approx(0.9 / 1.2 - 1, abs=1e-12)
    assert r.max_drawdown < 0


def test_sharpe_and_win_rate_from_daily_returns():
    closes = [1.0, 1.01, 1.0, 1.01, 1.0]     # 日收益：+1% / −0.99% / +1% / −0.99% ✓
    r = _aggregate_from_nav(_nav(closes), [None])
    ret = pd.Series(closes).pct_change().dropna()
    assert r.sharpe == pytest.approx(float(ret.mean() / ret.std() * (252 ** 0.5)), rel=1e-9)
    assert r.win_rate == pytest.approx(0.5, abs=1e-9)


def test_sharpe_is_none_when_std_is_zero():
    r = _aggregate_from_nav(_nav([1.0, 1.0, 1.0]), [None])
    assert r.sharpe is None, "零波动 ⇒ 夏普无定义 ✓（不许写成 inf ✗）"


def test_benchmark_return_uses_last_and_none_when_absent():
    closes = [1.0, 1.1]
    r = _aggregate_from_nav(_nav(closes, bench=[0.95, 0.98]), [None])
    # 归一首点后：末点 0.98 / 首点 0.95 − 1 ✓
    # ⚠ `normalize_benchmark_curve` 会把每点 **round 到 6 位** ✓ ⇒ 容差只能用 1e-6 ✗（1e-9 会红 ✓，实测抓到 ✓）
    assert r.benchmark_return == pytest.approx(0.98 / 0.95 - 1, abs=1e-6)
    r2 = _aggregate_from_nav(_nav(closes), [None])          # 没基准 ✓
    assert r2.benchmark_return is None
    assert r2.annualized_excess_return is None, "没基准就不许瞎算超额 ✓"


def test_annualized_excess_uses_benchmark_annualization():
    closes = [1.0] * 251 + [1.21]
    bench = [1.0] * 251 + [1.10]
    r = _aggregate_from_nav(_nav(closes, bench=bench), [None])
    assert r.annualized_excess_return == pytest.approx(0.21 - 0.10, abs=1e-9)


def test_curve_keeps_full_length_never_truncated():
    """⭐⭐ 审计 T2：**2500 点必须全在** ✓ —— 有人再写 `[:2000]` 立刻红 ✓。"""
    n = 2500
    closes = [1.0 + 0.0002 * i for i in range(n)]
    r = _aggregate_from_nav(_nav(closes), [None] * 5)
    assert len(r.nav) == n, "曲线被截断了 ✗（指标用全程算、曲线截尾 ⇒ 自相矛盾 ✓）"
    assert r.nav[-1]["value"] == pytest.approx(closes[-1])


def test_rolling_segments_reported_in_report_df():
    """段数记在 `report_df` 里 ✓（不加新模型字段 ✓ 避免影响序列化契约 ✓）。"""
    r = _aggregate_from_nav(_nav([1.0, 1.05]), [object(), object(), object()])
    assert r.report_df["rolling_segments"] == 3


def test_value_column_is_coerced_to_float():
    """`value` 可能是字符串/整数（来自 JSON ✓）⇒ 必须转 float ✓（否则 pct_change 会算成字符串拼接 ✗）。"""
    nav = [{"date": "2024-01-02", "value": "1.0"}, {"date": "2024-01-03", "value": 2}]
    r = _aggregate_from_nav(nav, [None])
    assert r.total_return == pytest.approx(1.0, abs=1e-12)
