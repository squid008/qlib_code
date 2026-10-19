# -*- coding: utf-8 -*-
"""`engine/analysis.py` 的 IC / 分层统计（★ v2.9.92，2026-10-19 ✓）。

## 这些数是"用户拿来下结论"的

IC / RankIC / ICIR 和分层曲线几乎出现在每一次因子评估里 ✓。它们的坑都在**口径**上 ✗
（界面永远画得出来 ✓，只是含义错了 ✗）：

1. **`_compute_layers` 有 `ret` 就必须用 `ret`** ✓ —— v1.20.43 的修正 ✗：
   原实现无条件用 `label` ✓，而 `label` 是 **N 日远期收益**（回测页 `label_horizon` 默认 20 ✓）
   ⇒ **逐日 `cumprod` 把重叠的 20 日收益反复复利 ⇒ 曲线虚高** ✗✗（同一个 20 日窗口被计入 20 次 ✓）。
   旧 docstring 那句"复利与累加粒度无关"**是错的** ✗（只对"同一组合的真实逐期收益"成立 ✓）。
2. **分层是复利** `Π(1+r)−1` ✓（v1.19.5 起 ✓，原为 `cumsum` ✗）——
   算术累加会**低估强策略、高估弱策略** ✓。
3. **`long_average` 是"最强组 − 池内等权"** ✓（不再是"最强组 vs 市值加权指数"✗ ——
   那会被口径差误导 ✓）。
4. **`_compute_ic` 必须成对 dropna 后再算秩** ✓ —— 只剔一列会改变秩位 ⇒ rank_ic 出错 ✓
   （docstring：脏面板实测 **20/20 天不同** ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.analysis import _compute_ic, _compute_layers, _group_corr


def _pl(rows, cols=("score", "label")):
    """rows = [(inst, date, score, label[, ret]), ...] ⇒ MultiIndex ✓。"""
    idx = pd.MultiIndex.from_tuples(
        [(r[0], pd.Timestamp(r[1])) for r in rows], names=["instrument", "datetime"])
    data = {c: [r[2 + i] for r in rows] for i, c in enumerate(cols)}
    return pd.DataFrame(data, index=idx)


D1, D2 = "2024-01-02", "2024-01-03"


# ------------------------------------------------------------------ _group_corr
def test_group_corr_matches_pandas_pearson():
    rng = np.random.default_rng(7)
    x = rng.normal(size=60)
    y = 0.6 * x + rng.normal(size=60) * 0.5
    codes = np.array([0] * 20 + [1] * 20 + [2] * 20)
    got = _group_corr(x, y, codes, 3)
    for g in range(3):
        exp = float(np.corrcoef(x[codes == g], y[codes == g])[0, 1])
        assert got[g] == pytest.approx(exp, abs=1e-12)


def test_group_corr_nan_for_small_group_and_zero_variance():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    y = np.array([1.0, 2.0, 3.0, 9.0])
    codes = np.array([0, 0, 1, 1])       # 每组 2 个 ✓
    got = _group_corr(x, y, codes, 3)
    assert np.isnan(got[2]), "空组 ⇒ NaN ✓"
    # 把第 1 组变成常数 ⇒ 零方差 ⇒ NaN ✓
    y2 = np.array([1.0, 2.0, 3.0, 3.0])
    got2 = _group_corr(x, y2, codes, 2)
    assert np.isnan(got2[1]), "零方差 ⇒ NaN ✓"


# ------------------------------------------------------------------- _compute_ic
def test_ic_none_for_empty():
    assert _compute_ic(None) is None
    assert _compute_ic(pd.DataFrame({"score": [], "label": []})) is None


def test_ic_perfect_positive_and_negative():
    rows = [(chr(65 + i), D1, float(i), float(i)) for i in range(6)]
    rows += [(chr(65 + i), D2, float(5 - i), float(i)) for i in range(6)]
    out = _compute_ic(_pl(rows))
    assert out is not None
    by_date = {p["date"]: p for p in out["points"]}
    assert by_date[D1]["ic"] == pytest.approx(1.0, abs=1e-9)
    assert by_date[D1]["rank_ic"] == pytest.approx(1.0, abs=1e-9)
    assert by_date[D2]["ic"] == pytest.approx(-1.0, abs=1e-9)
    # points 按日期**升序** ✓（factorize sort=True ✓）
    assert [p["date"] for p in out["points"]] == [D1, D2]


def test_icir_is_mean_over_std_and_none_when_single_day():
    rows = [(chr(65 + i), D1, float(i), float(i)) for i in range(5)]
    out = _compute_ic(_pl(rows))
    assert out["mean_ic"] == pytest.approx(1.0, abs=1e-9)
    assert out["icir"] is None, "只有一天 ⇒ std=NaN ⇒ icir 必须是 None ✓（不是 1.0 ✗）"


def test_ic_drops_nan_pairs_before_ranking():
    """⭐ 成对 dropna 后算秩 ✓ —— 只剔一列会让秩位变化、rank_ic 出错 ✓。"""
    rows = [
        ("A", D1, 1.0, 1.0), ("B", D1, 2.0, 2.0), ("C", D1, 3.0, 3.0),
        ("D", D1, np.nan, 4.0),          # score 缺 ⇒ 成对剔除 ✓
        ("E", D1, 5.0, np.nan),          # label 缺 ⇒ 成对剔除 ✓
    ]
    out = _compute_ic(_pl(rows))
    assert out["points"][0]["ic"] == pytest.approx(1.0, abs=1e-9)
    assert out["points"][0]["rank_ic"] == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------- _compute_layers
def _layers_rows(n_days=3, n_stock=6, with_ret=True, ret_scale=1.0):
    rows = []
    for di in range(n_days):
        day = "2024-01-%02d" % (di + 2)
        for i in range(n_stock):
            score = float(n_stock - i)                 # 降序 = 分数越高越靠前 ✓
            label = 0.10 * (i + 1)                     # N 日远期收益 ✓
            ret = 0.01 * (i + 1) * ret_scale           # 真实每日收益 ✓
            rows.append((chr(65 + i), day, score, label, ret))
    return rows


def test_layers_none_for_empty_and_all_nan_score():
    assert _compute_layers(None) is None
    df = _pl([("A", D1, np.nan, 0.1)])
    assert _compute_layers(df) is None


def test_layers_use_ret_not_label_when_available():
    """⭐ v1.20.43：**有 `ret` 就用 `ret`** ✓ —— 用 `label`（N 日远期）会被反复复利 ⇒ 曲线虚高 ✗✗。"""
    rows = _layers_rows(n_days=1, n_stock=4, with_ret=True)
    df = _pl(rows, cols=("score", "label", "ret"))
    out = _compute_layers(df, N=2, rebalance_period=1)
    g1 = out[0]           # 只有一天 ⇒ 累计 = 当日收益 ✓（复利首日 = 1+r-1 = r ✓）
    # 最强组（score 最高 = i=0,1）的当日 ret = 0.01 / 0.02 ⇒ 均值 0.015 ✓
    assert g1["Group1"] == pytest.approx(0.015, abs=1e-9)
    # 若错用 label（0.10/0.20 ⇒ 0.15）会差 10 倍 ✓
    assert g1["Group1"] != pytest.approx(0.15, abs=1e-6)


def test_layers_fall_back_to_label_without_ret():
    df = _pl(_layers_rows(n_days=1, n_stock=4, with_ret=False), cols=("score", "label"))
    out = _compute_layers(df, N=2, rebalance_period=1)
    assert out[0]["Group1"] == pytest.approx(0.15, abs=1e-9)   # label 均值 ✓


def test_layers_compounding_not_arithmetic_sum():
    """⭐ 复利 `Π(1+r)−1` ✓（算术累加会低估强策略 ✗）。"""
    df = _pl(_layers_rows(n_days=2, n_stock=4, with_ret=True), cols=("score", "label", "ret"))
    out = _compute_layers(df, N=2, rebalance_period=1)
    last = out[-1]
    # 两天都 +1.5% ⇒ 复利 = 1.015^2 - 1 ✓（算术 = 0.03 ✗）
    assert last["Group1"] == pytest.approx(1.015 ** 2 - 1, abs=1e-9)
    assert last["Group1"] != pytest.approx(0.03, abs=1e-6)


def test_layers_long_short_and_long_average_and_universe():
    """⭐ `long_short` = 最强 − 最弱 ✓；`long_average` = 最强 − **池内等权** ✓（且池内等权单独输出 ✓）。"""
    df = _pl(_layers_rows(n_days=1, n_stock=4, with_ret=True), cols=("score", "label", "ret"))
    out = _compute_layers(df, N=2, rebalance_period=1)
    g = out[0]
    assert g["long_short"] == pytest.approx(g["Group1"] - g["Group2"], abs=1e-12)
    uni = (0.01 + 0.02 + 0.03 + 0.04) / 4
    assert g["universe"] == pytest.approx(uni, abs=1e-9)
    assert g["long_average"] == pytest.approx(g["Group1"] - uni, abs=1e-12)
    # 键名齐全 ✓（前端按 Group1..GroupN 取曲线 ✓）
    for k in ("date", "Group1", "Group2", "long_short", "long_average", "universe"):
        assert k in g, k
