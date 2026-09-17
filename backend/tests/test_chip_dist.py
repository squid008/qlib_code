# -*- coding: utf-8 -*-
"""筹码分布内核（WINNER / COST）单测（v1.19.83）。

⚠ 先读 `app/factors/chip_dist.py` 的模块文档：这两个函数**没有官方公开算法**，
  本实现是"换手率衰减 + 当日区间三角分布"的主流复刻 ⇒ 不可能与通达信/益盟逐位一致。
  因此这里测的是**内在一致性与方向性**，不是"与软件对数值"：
    · 归一化（筹码总和恒为 1）；
    · COST 单调（COST(5) ≤ COST(30) ≤ COST(75) ≤ COST(95)）；
    · WINNER / COST 互为反函数（往返一致）；
    · 一字板/停牌不破坏状态；横盘 ⇒ 筹码集中（黏合度小）、趋势 ⇒ 筹码发散（黏合度大）。
"""
import numpy as np
import pandas as pd

from app.factors.chip_dist import chip_run, cost_levels, winner_at


def _flat(n_day: int, n_stk: int, price: float, turn: float = 0.05):
    """单调标价矩阵：close/high/low 全等（一字板），给定换手率。"""
    c = np.full((n_day, n_stk), float(price))
    t = np.full((n_day, n_stk), float(turn))
    return c, c.copy(), c.copy(), t


def test_costs_are_monotonic_and_ordered():
    c, h, l, t = _flat(60, 3, 10.0)
    res = cost_levels(c, h, l, t, qs=(5, 30, 75, 95))
    c5, c30, c75, c95 = (res["cost_%d" % q][-1] for q in (5, 30, 75, 95))
    assert np.all(c5 <= c30) and np.all(c30 <= c75) and np.all(c75 <= c95)


def test_winner_and_cost_are_inverse():
    """WINNER(COST(q)) ≈ q —— 两个函数必须互逆（同一份筹码分布）。"""
    rng = np.random.default_rng(7)
    n_day, n_stk = 120, 4
    drift = np.cumsum(rng.normal(0, 0.012, size=(n_day, n_stk)), axis=0)
    close = 20.0 * np.exp(drift)
    high = close * (1 + np.abs(rng.normal(0, 0.01, size=close.shape)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, size=close.shape)))
    turn = np.clip(rng.normal(0.04, 0.01, size=close.shape), 0.001, 0.5)
    q = 75.0
    cost = cost_levels(close, high, low, turn, qs=(q,))["cost_%g" % q]
    win = winner_at(close, high, low, turn, cost)
    ok = np.isfinite(cost) & np.isfinite(win)
    assert ok.any()
    assert np.nanmax(np.abs(win[ok] - q / 100.0)) < 0.02, "WINNER(COST(q)) 应≈ q"


def test_flat_market_concentrates_chips():
    """一字板横盘 ⇒ 95% 与 5% 成本几乎重合（黏合度≈1），符合"筹码高度集中"。"""
    c, h, l, t = _flat(100, 2, 10.0)
    res = cost_levels(c, h, l, t, qs=(5, 95))
    ratio = res["cost_95"][-1] / res["cost_5"][-1]
    assert np.allclose(ratio, 1.0, atol=1e-6), "全在一价成交 ⇒ 筹码应全部集中在该价"


def test_trend_widens_chip_spread():
    """持续上涨 ⇒ 成本分布被拉开（黏合度 > 1），这正是"筹码发散"。"""
    n_day = 120
    step = 1.008
    close = 10.0 * step ** np.arange(n_day)[:, None]
    high = close * 1.01
    low = close * 0.99
    turn = np.full_like(close, 0.05)
    res = cost_levels(close, high, low, turn, qs=(5, 95))
    ratio = res["cost_95"][-1] / res["cost_5"][-1]
    assert np.all(ratio > 1.05), "趋势行情下筹码应被拉开"


def test_suspended_days_freeze_state():
    """停牌（价格 NaN / 换手 NaN）⇒ 筹码冻结，COST 与前一天一致。"""
    c, h, l, t = _flat(40, 1, 12.0)
    c[20:25] = np.nan
    h[20:25] = np.nan
    l[20:25] = np.nan
    t[20:25] = np.nan
    res = cost_levels(c, h, l, t, qs=(50,))
    col = res["cost_50"][:, 0]
    assert np.all(np.isfinite(col))
    assert col[24] == col[19], "停牌期间筹码不应变化"


def test_degenerate_and_shape_options():
    """`peak` / `shape` / `decay` 参数可切换且结果仍合法（0/1 范围内的占比）。"""
    rng = np.random.default_rng(3)
    n_day, n_stk = 50, 2
    close = 15.0 + np.cumsum(rng.normal(0, 0.1, size=(n_day, n_stk)), axis=0)
    close = np.abs(close) + 1.0
    high = close * 1.02
    low = close * 0.98
    turn = np.full_like(close, 0.03)
    for shape in ("tri", "uni"):
        for peak in ("hl2", "hlc3", "close"):
            r = chip_run(close, high, low, turn, qs=(50,), shape=shape, peak=peak)
            w = r["winner_max"]
            assert np.all(np.isfinite(w[:, 0])), (shape, peak)
            assert np.all((w >= 0) & (w <= 1))


def test_matrix_matches_single_stock_call():
    """矩阵口径 == 逐股单独调用（向量化没有跨股票串味）——这条是性能改造的安全网。"""
    rng = np.random.default_rng(11)
    n_day, n_stk = 60, 3
    close = 10.0 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(n_day, n_stk)), axis=0))
    high = close * 1.015
    low = close * 0.985
    turn = np.clip(rng.normal(0.03, 0.008, size=close.shape), 0.001, 0.3)
    allres = cost_levels(close, high, low, turn, qs=(5, 30, 75, 95))
    for i in range(n_stk):
        one = cost_levels(close[:, [i]], high[:, [i]], low[:, [i]], turn[:, [i]],
                          qs=(5, 30, 75, 95))
        for q in (5, 30, 75, 95):
            key = "cost_%d" % q
            a = allres[key][:, i]
            b = one[key][:, 0]
            assert np.allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True), (key, i)


def test_pandas_input_is_accepted():
    """调用方常拿 DataFrame（日期 × 股票）⇒ 内核应能直接吃 .to_numpy() 的输入。"""
    c, h, l, t = _flat(30, 2, 8.0)
    df = pd.DataFrame(c, index=pd.bdate_range("2024-01-01", periods=30))
    res = cost_levels(df.to_numpy(), h, l, t, qs=(50,))
    assert res["cost_50"].shape == (30, 2)
