# -*- coding: utf-8 -*-
"""**冲击成本倍数封顶**的守门单测（v2.8.5，2026-10-16 用户报的滚动净值垮塌根因 ✓）。

数字全部来自真实现场（`ai_test/probe_impact_cost_1016.py` ✓ —— 本金 1 亿的滚动回测，段 1 首笔建仓：
按 qlib 平方冲击公式复算 37 笔的成本 = **7577.7 万** ✓，与 recorder 的 `total_cost` = **7597.5 万** ✓
逐位吻合 ⇒ 现金被当成本扣光 ⇒ 净值一步砸到 0.2402 ✓）。

⚠ 本套**不需要数据** ⇒ 不打 `datareq` ✓。
"""
from __future__ import annotations

from qlib.backtest.exchange import Exchange

from app.engine.board_exchange import IMPACT_RATIO_CAP, BoardAwareExchange, cap_impact_cost

OPEN_COST = 0.0005
CLOSE_COST = 0.0015
IMPACT = 0.002
MIN_COST = 5.0


def test_normal_order_is_untouched():
    """★ 关键不变量：**单笔 ≤ 当日成交额 ⇒ 返回 None**（调用方保留父类原值 ⇒ 正常回测逐位不变 ✓）。"""
    assert cap_impact_cost(1.9e6, 1.0e8, OPEN_COST, IMPACT, MIN_COST) is None
    # 恰好等于当日成交额 ⇒ 也放行（倍数 = 1 ≤ cap ✓）
    assert cap_impact_cost(1.9e6, 1.9e6, OPEN_COST, IMPACT, MIN_COST) is None


def test_explosive_order_is_capped_to_real_numbers():
    """★ 现场那一笔：190 万的单子 / 当日成交额 2.78 万（倍数 68）⇒ 成本从 **1778 万** 降到 **4749 元** ✓。"""
    val, turn = 1899956.8, 27776.3
    got = cap_impact_cost(val, turn, OPEN_COST, IMPACT, MIN_COST)
    assert got is not None
    old = max(val * (OPEN_COST + IMPACT * (val / turn) ** 2), MIN_COST)   # qlib 原公式 ✓
    assert old > 1.7e7, "原公式确实爆到 1700 万以上 ✓（这是被修掉的现象 ✓）%.0f" % old
    assert abs(got - max(val * (OPEN_COST + IMPACT * 1.0), MIN_COST)) < 1e-6
    assert got < 5.0e3 and got > 4.0e3, got
    assert got < old / 1000, "量级要差三个数量级 ✓ %.0f vs %.0f" % (got, old)


def test_cap_parameter_is_respected():
    """封顶倍数可调：`cap=0.1`（=最多吃当日 10% 成交额 ✓）⇒ 成本更小、且仍是**线性**的 ✓。"""
    val, turn = 1.9e6, 1.0e6            # 倍数 1.9 > 1 ⇒ 命中封顶 ✓
    got = cap_impact_cost(val, turn, OPEN_COST, IMPACT, MIN_COST, cap=0.1)
    assert got is not None
    assert abs(got - max(val * (OPEN_COST + IMPACT * 0.01), MIN_COST)) < 1e-6
    # 倍数 > cap 但 < 1 时也应命中（本例 1.9 > 1 ✗ ⇒ 换个场景：倍数 0.5 > cap 0.1 ✓）
    got2 = cap_impact_cost(val, 2.0 * val, OPEN_COST, IMPACT, MIN_COST, cap=0.1)
    assert got2 is not None and got2 < val, got2


def test_sell_side_uses_close_cost():
    """卖出侧用 `close_cost`（费率不同 ✓）—— 这里只验"换了费率会得到不同结果" ✓。"""
    val, turn = 1.9e6, 1.0e5            # 倍数 19 ✓
    buy = cap_impact_cost(val, turn, OPEN_COST, IMPACT, MIN_COST)
    sell = cap_impact_cost(val, turn, CLOSE_COST, IMPACT, MIN_COST)
    assert buy is not None and sell is not None and sell > buy, (buy, sell)
    assert abs(sell - max(val * (CLOSE_COST + IMPACT), MIN_COST)) < 1e-6


def test_bad_inputs_return_none():
    """成交量缺失/0/NaN、金额≤0 ⇒ 一律 `None`（保持父类结果 ✓，绝不瞎算 ✗）。"""
    for turn in (0.0, None, float("nan")):
        assert cap_impact_cost(1.9e6, turn, OPEN_COST, IMPACT, MIN_COST) is None
    for val in (0.0, -1.0, None, float("nan")):
        assert cap_impact_cost(val, 1.0e5, OPEN_COST, IMPACT, MIN_COST) is None


def test_impact_cost_zero_degrades_to_base_rate():
    """`impact_cost=0`（不算冲击）⇒ 封顶后就是 `金额 × 方向费率` ✓（不会凭空多收钱 ✓）。"""
    got = cap_impact_cost(1.9e6, 1.0e4, OPEN_COST, 0.0, MIN_COST)
    assert got is not None and abs(got - max(1.9e6 * OPEN_COST, MIN_COST)) < 1e-6


def test_default_cap_constant():
    """默认封顶 = 1.0（= 最多吃掉当日全部成交额 ✓）—— 改了要一起改文档与测试 ✓。"""
    assert IMPACT_RATIO_CAP == 1.0


def test_exchange_overrides_the_hook():
    """接线自检：`BoardAwareExchange` **必须**覆盖 `_calc_trade_info_by_order` ✓
    （否则封顶根本没生效 ✗ —— 这类"写了函数但没接上"的坑我踩过 ✓）。"""
    assert BoardAwareExchange._calc_trade_info_by_order is not Exchange._calc_trade_info_by_order
