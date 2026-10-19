# -*- coding: utf-8 -*-
"""`_trigger_split` 测试（★ v2.9.84，2026-10-19 ✓ —— 首次触发 vs 续期内再触发 ✓）。

## 它为什么存在（用户 2026-10-11 要的"分组显示" ✓）

两个子群收益差很大 ✓：实测 `黄金坑启动`（全A / 2022-01~2026-01 / h=60）
**首次 +4.956% vs 续期 +0.707%** ✓，条数比 **1 : 2.65** ✓ —— 而**组合**里同一个名字只持一轮 ✓
（≈ 首次口径 ✓）⇒ 不分组时"样本均值 +1.87%"与"组合 +27%/年"看着像**矛盾** ✗
（作者本人就误判过 ✓）⇒ 分组后一眼可辨 ✓。

## ⚠ 两个单位口径（都被用户截图抓到过 ✗）

1. **`LABEL` 本身就是百分数** ✓（与 `trigger.mean_ret` 同单位 ✓）⇒ `mean_ret` **不能再 ×100** ✗
   —— 实测 AR13 首次收益显示 **365.020** 而真实是 **3.650** ✓（正好 ×100 ✗）；
2. **`win_rate` 必须是小数（`0.466` ✓）** ✗ —— 曾返回 `46.6`（自以为是百分数 ✓）⇒ 前端再 ×100
   ⇒ 悬停显示 **4660%** ✗（用户 2026-10-12："tag 里胜率没改啊，还是 4660%" ✓）。

## 判据

同一标的相邻两次触发，**交易日间隔 ≤ 持有期 N** ⇒ 后一次记为"续期内再触发" ✓
（引擎语义：再触发 ⇒ 持有天数**重置为 N 天** ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import _trigger_split

# ⚠ 至少要 80 个交易日 ✓ —— 有用例要跨 61 天（> 持有期 60 ✓）；第一版只给 40 天，
#   直接 IndexError ✓（自己的夹具 bug ✓）。
DAYS = [str(d)[:10] for d in pd.bdate_range("2024-01-02", periods=80)]
CAL = pd.Index(pd.to_datetime(DAYS))


def _trig(rows) -> pd.DataFrame:
    """rows = [(code, day_index, label), ...] ✓（day_index 指向 DAYS ✓，交易日间隔可控 ✓）。"""
    idx = pd.MultiIndex.from_tuples(
        [(c, pd.Timestamp(DAYS[i])) for c, i, _ in rows], names=["instrument", "datetime"],
    )
    return pd.DataFrame({"LABEL": [lab for _, _, lab in rows]}, index=idx)


def test_none_or_empty_returns_none():
    assert _trigger_split(None, 60) is None
    assert _trigger_split(_trig([]), 60) is None


def test_oversized_input_is_skipped():
    """规模保护：> 20 万条直接跳过 ✓（Python 逐条循环 ✗，且该字段只用于展示 ✓）。"""
    n = 200_001
    idx = pd.MultiIndex.from_arrays(
        [np.array(["A%06d" % i for i in range(n)]), np.full(n, pd.Timestamp(DAYS[0]))],
        names=["instrument", "datetime"],
    )
    df = pd.DataFrame({"LABEL": np.zeros(n)}, index=idx)
    assert _trigger_split(df, 60) is None


def test_single_trigger_all_first():
    r = _trigger_split(_trig([("A", 0, 0.05), ("B", 0, -0.01)]), 60, CAL)
    assert r["hold_days"] == 60
    assert r["first"]["count"] == 2 and r["refresh"]["count"] == 0


def test_gap_within_hold_days_is_refresh():
    # A：第 0 天触发，第 10 天再触发（≤ 60 ⇒ 续期 ✓）
    r = _trigger_split(_trig([("A", 0, 0.05), ("A", 10, 0.01)]), 60, CAL)
    assert r["first"]["count"] == 1
    assert r["refresh"]["count"] == 1
    assert r["first"]["mean_ret"] == pytest.approx(0.05, abs=1e-9)
    assert r["refresh"]["mean_ret"] == pytest.approx(0.01, abs=1e-9)


def test_gap_longer_than_hold_days_is_new_first():
    # 第 0 天与第 61 天（> 60 ⇒ 新一轮 ✓）
    r = _trigger_split(_trig([("A", 0, 0.05), ("A", 61, 0.02)]), 60, CAL)
    assert r["first"]["count"] == 2 and r["refresh"]["count"] == 0


def test_hold_days_floor_is_one():
    """`hold_days <= 0` ⇒ 夹到 1 ✓（不许出现"窗口 0 ⇒ 全部算首次"的静默退化 ✗）。"""
    r = _trigger_split(_trig([("A", 0, 0.05), ("A", 0, 0.01)]), 0, CAL)
    assert r["hold_days"] == 1
    # 同一天两次触发 ⇒ 间隔 0 ≤ 1 ⇒ 第二次算续期 ✓
    assert r["first"]["count"] == 1 and r["refresh"]["count"] == 1


# --------------------------------------------------------------------- 单位口径（两个历史 bug）
def test_mean_ret_is_not_multiplied_by_100():
    """⭐ `LABEL` 已是百分数 ⇒ **不许再 ×100** ✗（0.0365 必须还是 0.0365，不是 3.65 ✓）。"""
    r = _trigger_split(_trig([("A", 0, 0.0365)]), 60, CAL)
    assert r["first"]["mean_ret"] == 0.0365
    assert r["first"]["mean_ret"] != pytest.approx(3.65, abs=1e-6)
    assert r["first"]["median_ret"] == 0.0365


def test_win_rate_is_fraction_not_percent():
    """⭐ `win_rate` 是**小数** ✓（0.5 而不是 50 ✗ —— 前端会再 ×100 ⇒ 50% ✓）。"""
    r = _trigger_split(_trig([("A", 0, 0.05), ("B", 0, -0.03)]), 60, CAL)
    assert r["first"]["win_rate"] == pytest.approx(0.5, abs=1e-9)
    assert r["first"]["win_rate"] != pytest.approx(50, abs=1e-6)


def test_nan_labels_are_excluded_from_stats():
    """NaN 标签不进 `count` / 均值 ✓（否则均值会被 NaN 污染成 NaN ✗）。"""
    r = _trigger_split(_trig([("A", 0, 0.05), ("B", 0, np.nan), ("C", 0, 0.01)]), 60, CAL)
    assert r["first"]["count"] == 2
    assert r["first"]["mean_ret"] == pytest.approx(0.03, abs=1e-9)


def test_calendar_override_controls_gaps():
    """给了 `cal` 就按它的**位置**算间隔 ✓（缺交易日时"自然日间隔"会算错 ✗）。"""
    # 只给 4 个交易日的日历 ⇒ 第 0 与第 1 个日历日相邻 ⇒ 10 天的自然日差被压成 1 ✓
    cal = pd.Index(pd.to_datetime([DAYS[0], DAYS[10], DAYS[20], DAYS[30]]))
    df = _trig([("A", 0, 0.05), ("A", 10, 0.01)])
    r_cal = _trigger_split(df, 60, cal)
    assert r_cal["refresh"]["count"] == 1            # 日历位置差 1 ≤ 60 ✓
    # 不给 cal ⇒ 用"出现过的日期"排序 ✓（同样相邻 ✓）
    r_auto = _trigger_split(df, 60)
    assert r_auto["refresh"]["count"] == 1
