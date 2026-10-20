# -*- coding: utf-8 -*-
"""`event_study.build_event_stats`（★ v2.10.9，2026-10-20 ✓ —— 事件研究"用户看到的那些表" ✓）。

## 它产出什么

`curve`（每个持有期 k 的均值/中位数/胜率/分位数 ✓）、`prob`（收益与**亏损侧镜像**档位 ✓）、
`upside`（期内最高/最低 ✓）、`top_by_k` / `worst_by_k`（**每个 k 各一份榜单** ✓）、
以及兼容用的顶层 `top_events` / `worst_events`（= max_k 口径 ✓）。

## 三条最容易坏、也最难靠肉眼发现的

1. **直方图不丢样本** ✓：`np.clip` 把超出端点的极端值并入端桶 ✓ ⇒
   **各桶计数之和必须恒等于 `n`** ✓（丢了就是"分布图看起来总有人凭空消失"✗）；
2. **榜单必须"按当前 k"** ✓ —— 旧实现固定用 `mat[:, -1]`（max_k 期）✗ ⇒
   **表头写"持有 k 日"、数值却是 max_k 期** ✗（还漏掉"没持满 max_k 但在短 k 上有效"的事件 ✗）；
3. **亏损侧是收益侧的镜像** ✓：`lt0/lt10/…` 与 `gt0/gt10/…` 同一套档位 ✓，
   且 `gt0 + lt0 <= 1` ✓（收益为 0 的事件两边都不算 ✓）；`lt100` 在 A 股**结构性恒 0** ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.event_study import _DIST_EDGES, build_event_stats


def _px(n=14):
    """A 每天 +10% / B 每天 −5% / C 不动 ✓（注意：A 的 k 期收益随 k 单调变大 ✓）。"""
    days = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({
        "A": [10.0 * (1.1 ** i) for i in range(n)],
        "B": [20.0 * (0.95 ** i) for i in range(n)],
        "C": [5.0] * n,
    }, index=days)


def _events(codes=("A", "B", "C"), day=0):
    days = pd.bdate_range("2024-01-02", periods=14)
    return pd.DataFrame({"code": list(codes), "dt": [days[day]] * len(codes)})


def test_curve_and_prob_shapes():
    out = build_event_stats(_px(), _events(), max_k=3)
    assert [c["k"] for c in out["curve"]] == [1, 2, 3]
    for c in out["curve"]:
        assert c["n"] == 3, "三只票都有价格 ⇒ 每期 3 个样本 ✓"
        assert set(c) >= {"mean", "median", "win", "max", "min", "counts"}
    assert [p["k"] for p in out["prob"]] == [1, 2, 3]


def test_histogram_counts_sum_to_n():
    """⭐⭐ 核心不变量：**各桶计数之和 == n** ✓（`np.clip` 保证极端值并进端桶 ✓）。"""
    out = build_event_stats(_px(), _events(), max_k=3)
    for c in out["curve"]:
        assert len(c["counts"]) == len(_DIST_EDGES) - 1
        # ⚠ 断言消息里**不许**嵌 ASCII 双引号 ✓（本次已是第 4 次 SyntaxError ✗，统一用「」✓）
        assert sum(c["counts"]) == c["n"], "丢样本了 ✗（分布图会「凭空少人」✓）"


def test_mean_matches_manual_computation():
    """`lag=1`：买 `T+1`、`k=1` 卖 `T+2` ✓ ⇒ 收益 = `px[T+2]/px[T+1] − 1` ✓。

    ⚠ 我第一版写成 `1.1² − 1` ✗（那是"相对信号日"的口径 ✓）⇒ 实测 0.016667 vs 0.0375 差一倍 ✓。
    """
    out = build_event_stats(_px(), _events(), max_k=1, lag=1)
    exp = np.mean([1.1 - 1, 0.95 - 1, 0.0])       # A: +10% ｜ B: −5% ｜ C: 0 ✓
    assert out["curve"][0]["mean"] == pytest.approx(exp, abs=1e-6)


def test_prob_mirror_and_structural_bounds():
    out = build_event_stats(_px(), _events(), max_k=2)
    for p in out["prob"]:
        assert p["gt0"] + p["lt0"] <= 1.0 + 1e-12, "零收益不该被两边同时计入 ✓"
        assert p["lt100"] == 0.0, "A 股多头最多亏 100% ⇒ `<−100%` 结构性恒 0 ✓"
        assert 0.0 <= p["gt0"] <= 1.0 and 0.0 <= p["lt0"] <= 1.0


def test_upside_has_both_sides():
    out = build_event_stats(_px(), _events(), max_k=3)
    up = out["upside"]
    assert up["mean"] is not None and up["dn_mean"] is not None
    assert up["max"] >= up["median"] >= up["dn_median"] >= up["dn_min"] - 1e-12


def test_top_by_k_uses_that_k_not_max_k():
    """⭐⭐ 旧 bug：榜单固定用 `mat[:, -1]`（max_k 期）✗ ⇒
    "k=1 的榜单里装的是 k=3 的收益" ✓。这里断言 **k=1 与 k=3 的冠军收益不同** ✓，
    且与 `curve` 的 `max` 对得上 ✓。"""
    out = build_event_stats(_px(), _events(), max_k=3)
    top1 = out["top_by_k"]["1"][0]
    top3 = out["top_by_k"]["3"][0]
    assert top1["code"] == top3["code"] == "A", "涨得最多的都是 A ✓"
    assert top3["ret"] > top1["ret"], "3 期收益必须大于 1 期 ✓（相等 = 榜单用错了 k ✗）"
    assert top1["ret"] == pytest.approx(out["curve"][0]["max"], abs=1e-6)
    assert top3["ret"] == pytest.approx(out["curve"][2]["max"], abs=1e-6)


def test_worst_by_k_mirrors():
    out = build_event_stats(_px(), _events(), max_k=3)
    w1 = out["worst_by_k"]["1"][0]
    assert w1["code"] == "B", "跌得最多的是 B ✓"
    assert w1["ret"] < 0
    assert w1["min_ret"] <= w1["ret"] + 1e-12, "期内最低必须不高于该期收益 ✓"


def test_lists_are_capped_at_20():
    days = pd.bdate_range("2024-01-02", periods=14)
    codes = ["A"] * 30
    ev = pd.DataFrame({"code": codes, "dt": [days[0]] * 30})
    out = build_event_stats(_px(), ev, max_k=1)
    assert len(out["top_by_k"]["1"]) <= 20
    assert len(out["worst_by_k"]["1"]) <= 20
    assert len(out["top_events"]) <= 20 and len(out["worst_events"]) <= 10


def test_empty_events_returns_empty_sections():
    """无事件 ⇒ 各段为空 / `upside` 全 `None` ✓（不许抛 ✗，也不许塞 0 ✗）。"""
    ev = pd.DataFrame({"code": [], "dt": []})
    out = build_event_stats(_px(), ev, max_k=3)
    assert out["curve"] == [] and out["prob"] == []
    assert out["top_by_k"] == {} and out["worst_by_k"] == {}
    assert out["upside"]["mean"] is None and out["upside"]["dn_mean"] is None


def test_max_k_zero_means_default_40_and_negative_clamps_to_1():
    """⭐ 实测怪癖 ✓：`max(1, int(max_k or 40))` 里 **`0` 是 falsy** ✗
    ⇒ `max_k=0` 走的是"**默认 40**"✓，**不是**夹到 1 ✗；负数才会被夹到 1 ✓。
    （这条写清楚，免得下一个人以为 `0` 等于 `1` ✓。）
    """
    zero = build_event_stats(_px(), _events(), max_k=0)
    assert [c["k"] for c in zero["curve"]][0] == 1
    assert len(zero["curve"]) > 1, "0 ⇒ 默认 40 期 ✓（只出 1 期就说明被夹到 1 了 ✗）"
    neg = build_event_stats(_px(), _events(), max_k=-3)
    assert [c["k"] for c in neg["curve"]] == [1]


def test_lag0_uses_signal_day_price():
    """`lag=0`（信号日成交）✓ ⇒ 收益 = `px[T+1]/px[T] − 1` ✓。"""
    out = build_event_stats(_px(), _events(), max_k=1, lag=0)
    exp = np.mean([1.1 - 1, 0.95 - 1, 0.0])
    assert out["curve"][0]["mean"] == pytest.approx(exp, abs=1e-6)
