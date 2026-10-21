# -*- coding: utf-8 -*-
"""`compose_gate_tail`（闸门拒尾）与 `build_target_w`（S2 叠加权重）（★ v2.10.46，2026-10-21 ✓）。

## 为什么这两个值得测

它们决定**"到底买了哪些、各买多少"** ✓ —— 算错了**图和指标全都好看** ✗，
只有真下单时才会发现"怎么多买了 13 只"✓ 或"权重加起来 1.7" ✗。

## 两个函数各自最容易错的地方

* `compose_gate_tail` —— **候选集之外必须也置 `-inf`** ✓。
  旧实现只把"被拒的"置 `-inf` ✗，而第 51 名往后**分数原封不动** ✗
  ⇒ 策略按分数降序取 topk 时**必然从第 51 名往后补满** ✓
  （实测：买入排名中位 70、51~100 名连续 ✓ —— 正是补票行为 ✓）。
* `build_target_w` —— **主层与触发层的权重比例** ✓：主层合计 `1−w`、触发层合计 `w` ✓；
  写成"各占一半"或"触发层再乘一次"✗ 都会让 `target_w` 之和 ≠ 1 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.signal_compose import build_target_w, compose_gate_tail


def _idx(rows):
    return pd.MultiIndex.from_tuples(rows, names=["instrument", "datetime"])


D1, D2 = "2024-01-02", "2024-01-03"


# ---------------------------------------------------------- compose_gate_tail
def test_candidates_beyond_topk_are_killed():
    """⭐⭐ **候选集之外必须 `-inf`** ✓ —— 否则策略会从第 topk+1 名往后补票 ✗
    （历史上真的发生过：买入排名中位 70 ✓）。"""
    idx = _idx([("A", D1), ("B", D1), ("C", D1)])
    score = pd.Series([3.0, 2.0, 1.0], index=idx)
    z = pd.Series([0.1, 0.2, 0.3], index=idx)
    got = compose_gate_tail(score, z, topk=2, reject_ratio=0.0)
    assert got.loc[("A", D1)] == 3.0 and got.loc[("B", D1)] == 2.0
    assert got.loc[("C", D1)] == -np.inf, "第 3 名必须置 -inf ✓（否则会被补进来 ✗）"


def test_reject_ratio_drops_lowest_z_inside_candidates():
    """候选内按 **z 从低到高** 拒 `reject_ratio` ✓（z 越低越先被拒 ✓）。"""
    idx = _idx([("A", D1), ("B", D1), ("C", D1), ("D", D1)])
    score = pd.Series([4.0, 3.0, 2.0, 1.0], index=idx)
    z = pd.Series([0.9, 0.1, 0.5, 0.3], index=idx)
    got = compose_gate_tail(score, z, topk=4, reject_ratio=0.5)
    # 4 只候选 × (1-0.5) = 2 只存活 ⇒ z 最高的 A(0.9)、C(0.5) 存活 ✓
    assert got.loc[("A", D1)] == 4.0 and got.loc[("C", D1)] == 2.0
    assert got.loc[("B", D1)] == -np.inf and got.loc[("D", D1)] == -np.inf


def test_keep_at_least_one_even_with_full_reject():
    """`reject_ratio=1.0` ⇒ 仍然**至少留 1 只** ✓（`clip(lower=1)` ✓）——
    否则某天会"一只都不买"，与"拒尾后不补、剩多少买多少"的口径不符 ✗。"""
    idx = _idx([("A", D1), ("B", D1)])
    got = compose_gate_tail(pd.Series([2.0, 1.0], index=idx),
                            pd.Series([0.1, 0.9], index=idx), topk=2, reject_ratio=1.0)
    assert (got > -np.inf).sum() == 1


def test_each_day_is_ranked_independently():
    """**逐日独立排序** ✓（跨日混排 ⇒ 只有第一天能选到票 ✗）。"""
    idx = _idx([("A", D1), ("B", D1), ("C", D2), ("D", D2)])
    score = pd.Series([1.0, 2.0, 3.0, 4.0], index=idx)   # D2 的绝对值更大 ✓
    z = pd.Series([0.1, 0.2, 0.3, 0.4], index=idx)
    got = compose_gate_tail(score, z, topk=1, reject_ratio=0.0)
    assert got.loc[("B", D1)] == 2.0 and got.loc[("A", D1)] == -np.inf
    assert got.loc[("D", D2)] == 4.0 and got.loc[("C", D2)] == -np.inf


def test_missing_z_leaves_score_untouched():
    """⚠ **已知行为**（不是 bug，但要写下来 ✓）：`z` 缺失的行被 `dropna` 排除 ✓
    ⇒ 既不进候选、也不被置 `-inf` ✓ ⇒ **分数原封不动** ✓。
    谁要改成"z 缺失就剔"，会改掉"闸门不开时行为完全不变"的前提 ✗。"""
    idx = _idx([("A", D1), ("B", D1)])
    got = compose_gate_tail(pd.Series([2.0, 1.0], index=idx),
                            pd.Series([np.nan, np.nan], index=idx), topk=1, reject_ratio=0.5)
    assert list(got.values) == [2.0, 1.0]


def test_gate_tail_does_not_mutate_input():
    idx = _idx([("A", D1), ("B", D1)])
    score = pd.Series([2.0, 1.0], index=idx)
    compose_gate_tail(score, pd.Series([0.5, 0.5], index=idx), topk=1, reject_ratio=0.0)
    assert list(score.values) == [2.0, 1.0]


# ------------------------------------------------------------- build_target_w
def _panel(score_vals, z_vals, days=(D1, D2)):
    rows = []
    for d in days:
        for inst in ("A", "B", "C", "D"):
            rows.append((inst, d))
    idx = _idx(rows)
    return idx, pd.Series(score_vals, index=idx), pd.Series(z_vals, index=idx)


def test_main_layer_weight_ratio():
    """⭐⭐ 主层权重 = `(1−w)/只数` **再归一** ✓ —— 所以**当天没有触发层时就是等权 0.5** ✓。

    ⚠ 我第一版断言 `0.4` ✗，实测是 **0.5** ✓ —— 因为函数末段**逐日归一** ✓：
    主层 2 只各 0.8/2 = 0.4，合计 0.8 ⇒ 除以 0.8 ⇒ **各 0.5** ✓。
    ⇒ 这条语义要写清楚 ✓：`ovl_weight` 只决定"主层 vs 触发层"的**相对占比** ✓，
    **当天没有触发对象时，`w` 的效果会被归一化抹平** ✓（主层自动吃满 1.0 ✓ ——
    没有叠加对象，本来就该全给主层 ✓）。
    """
    idx, s, z = _panel([4.0, 3.0, 2.0, 1.0] * 2, [np.nan] * 8)
    got = build_target_w(s, z, topk_main=2, ovl_topk=1, ovl_weight=0.2)
    d1 = got.loc[(slice(None), D1)]
    assert d1.loc["A"] == pytest.approx(0.5) and d1.loc["B"] == pytest.approx(0.5)
    assert d1.loc["C"] == 0.0 and d1.loc["D"] == 0.0
    assert d1.sum() == pytest.approx(1.0)


def test_overlay_layer_adds_its_share():
    """⭐⭐ 触发层每只 = `w/触发只数` ✓（`w=0.2`、1 只 ⇒ 0.2 ✓）；主层降到 0.4 ✓。"""
    z = [np.nan, np.nan, np.nan, np.nan] + [0.9, 0.1, np.nan, np.nan]
    idx, s, zz = _panel([4.0, 3.0, 2.0, 1.0] * 2, z)
    got = build_target_w(s, zz, topk_main=2, ovl_topk=1, ovl_weight=0.2)
    d2 = got.loc[(slice(None), D2)]
    # D2 触发层只取 z 最高的 A ⇒ +0.2 ✓；A 同时是主层 ⇒ 0.4 + 0.2 = 0.6 ✓
    assert d2.loc["A"] == pytest.approx(0.6)
    assert d2.loc["B"] == pytest.approx(0.4)
    assert got.loc[(slice(None), D2)].sum() == pytest.approx(1.0)


def test_zero_overlay_weight_is_plain_equal_weight():
    """`ovl_weight=0` ⇒ 纯等权 ✓（与旧路径一致 ✓）。"""
    idx, s, z = _panel([4.0, 3.0, 2.0, 1.0] * 2, [np.nan] * 8)
    got = build_target_w(s, z, topk_main=4, ovl_topk=2, ovl_weight=0.0)
    assert got.loc[(slice(None), D1)].tolist() == [pytest.approx(0.25)] * 4


def test_inf_score_is_not_selectable():
    """`-inf`（被闸门拒掉的）**不可被选中** ✓ —— 只看 `notna` 会把它选进来 ✗。"""
    idx, s, z = _panel([4.0, -np.inf, 2.0, 1.0] * 2, [np.nan] * 8)
    got = build_target_w(s, z, topk_main=2, ovl_topk=1, ovl_weight=0.0)
    d1 = got.loc[(slice(None), D1)]
    assert d1.loc["B"] == 0.0, "-inf 被选进主层 ✗（权重会分给一只根本买不到的票 ✓）"
    assert d1.sum() == pytest.approx(1.0)
