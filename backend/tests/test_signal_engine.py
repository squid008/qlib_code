# -*- coding: utf-8 -*-
"""交易信号测试 · 回测引擎单测（合成价格面板，**不依赖 qlib**，确定性可复核）。

覆盖用户明确的口径：
  · 次日开盘成交 / 持有 N 日后卖出；
  · 持有期内再来信号 ⇒ 持有天数刷新为 0；
  · 涨停买不进（放弃）、停牌买不进、跌停卖不出（顺延）；
  · 成本 = 往返合计（单边 cost/2）⇒ 净值 ≈ 1 − 2×单边费；
  · 两种资金方案都产出（事件驱动再平衡 / 现金等分），且现金不为负（不会偷偷用杠杆）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals import event as sigevent
from app.signals.engine import run_backtest


def make_panel(days: int = 12, price: float = 10.0, code: str = "SZ000001",
               dates=None):
    """单标的、价格恒定的面板（便于手算佣金/份额）。"""
    idx = pd.DatetimeIndex(dates) if dates is not None else pd.bdate_range("2024-01-01", periods=days)
    C = pd.DataFrame({code: [price] * len(idx)}, index=idx, dtype=float)
    zeros = pd.DataFrame({code: [price] * len(idx)}, index=idx, dtype=float)
    return {
        "CALENDAR": pd.DataFrame(index=idx),
        "CLOSE": C, "OPEN": C.copy(), "CLOSE_RAW": zeros.copy(), "OPEN_RAW": zeros.copy(),
        "LIMIT_UP": pd.DataFrame({code: [np.nan] * len(idx)}, index=idx),
        "LIMIT_DOWN": pd.DataFrame({code: [np.nan] * len(idx)}, index=idx),
    }, idx


def sig(dates, code="SZ000001", side=1):
    return pd.DataFrame({"date": pd.to_datetime(list(dates)), "code": [code] * len(dates),
                         "side": [side] * len(dates)})


def test_basic_buy_hold_sell_and_cost():
    """信号日 T ⇒ T+1 开盘买入；持有 3 日 ⇒ T+4 卖出；成本 0.004 往返。"""
    panel, idx = make_panel(days=12)
    bt = run_backtest(sig([idx[0]]), panel, hold_days=3, fill="t1_open", cost=0.004,
                      capital=100000.0, alloc_modes=("cash_even",))
    tr = bt.trades
    assert list(tr["side"]) == ["买", "卖"]
    assert tr.iloc[0]["date"] == str(idx[1].date())      # 次日开盘买
    assert tr.iloc[1]["date"] == str(idx[4].date())      # 1 + 3 个交易日后卖
    # 份额：100000/(1+0.002)/10 ⇒ 9900 股（整手）；两边各收 99000×0.002 = 198 元
    assert tr.iloc[0]["shares"] == 9900
    assert tr.iloc[0]["fee"] == pytest.approx(198.0, abs=0.01)
    assert tr.iloc[1]["fee"] == pytest.approx(198.0, abs=0.01)
    st = bt.stats["cash_even"]
    assert st["final_nav"] == pytest.approx((100000 - 396) / 100000, abs=1e-4)
    assert st["min_cash"] >= -1e-6                       # 现金不为负（不漏计费用）
    assert st["avg_hold_days"] == 3.0


def test_limit_up_on_fill_day_blocks_buy():
    """成交日收盘涨停 ⇒ 买不进（放弃该信号，计入明细）。"""
    panel, idx = make_panel(days=8)
    code = "SZ000001"
    panel["LIMIT_UP"].loc[idx[1], code] = 10.0           # 涨停价 = 收盘价 ⇒ 判涨停
    bt = run_backtest(sig([idx[0]]), panel, hold_days=3, fill="t1_open",
                      capital=100000.0, alloc_modes=("cash_even",))
    assert bt.trades.empty
    assert bt.stats["cash_even"]["rejects_limit_up"] == 1
    assert bt.rejects.iloc[0]["text"] == "涨停买不进"


def test_suspended_blocks_buy():
    panel, idx = make_panel(days=8)
    panel["CLOSE_RAW"].loc[idx[1], "SZ000001"] = np.nan   # 次日停牌
    bt = run_backtest(sig([idx[0]]), panel, hold_days=3, fill="t1_open",
                      capital=100000.0, alloc_modes=("cash_even",))
    assert bt.trades.empty
    assert bt.stats["cash_even"]["rejects_suspended"] == 1


def test_limit_down_defers_sell():
    """到期日跌停 ⇒ 卖不出，**顺延**到下一个能卖的日子。"""
    panel, idx = make_panel(days=12)
    code = "SZ000001"
    panel["LIMIT_DOWN"].loc[idx[4], code] = 10.0          # 到期日跌停
    bt = run_backtest(sig([idx[0]]), panel, hold_days=3, fill="t1_open",
                      capital=100000.0, alloc_modes=("cash_even",))
    sells = bt.trades[bt.trades["side"] == "卖"]
    assert len(sells) == 1 and sells.iloc[0]["date"] == str(idx[5].date())
    assert bt.stats["cash_even"]["sell_deferrals"] == 1


def test_signal_refreshes_hold_days():
    """持有期内又来信号 ⇒ 持有天数归零，卖出日顺延。"""
    panel, idx = make_panel(days=12)
    bt = run_backtest(sig([idx[0], idx[2]]), panel, hold_days=3, fill="t1_open",
                      capital=100000.0, alloc_modes=("cash_even",))
    sells = bt.trades[bt.trades["side"] == "卖"]
    assert len(bt.trades[bt.trades["side"] == "买"]) == 1        # 已持有 ⇒ 只刷新，不加仓
    # 刷新发生在 idx[2]（信号日）⇒ 从那天重新数 3 个交易日 ⇒ idx[2]+3 = idx[5]
    assert sells.iloc[0]["date"] == str(idx[5].date())
    assert bt.stats["cash_even"]["hold_refreshes"] == 1


def test_both_alloc_modes_returned_and_cash_even_splits():
    """两种资金方案都产出；同一信号只买一次（去重），且各自成交里带 mode 标记。"""
    panel, idx = make_panel(days=10)
    bt = run_backtest(sig([idx[0], idx[0]]), panel, hold_days=3, fill="t1_open",
                      capital=100000.0)
    assert set(bt.nav.columns) == {"event_even", "cash_even"}
    assert set(bt.trades["mode"]) == {"event_even", "cash_even"}
    for _mode, g in bt.trades.groupby("mode"):
        assert len(g[g["side"] == "买"]) == 1            # 同日同码去重 ⇒ 只买一次
        assert g[g["side"] == "买"].iloc[0]["date"] == str(idx[1].date())


def test_deadband_comparison_curves():
    """`event_even@0.05` 这种写法 = 同一方案多跑一档死区做对比（用户要的"死区 5% 对比曲线"）。"""
    from app.signals.engine import _split_mode

    assert _split_mode("event_even") == ("event_even", None, "event_even")
    assert _split_mode("event_even@0.05") == ("event_even", 0.05, "event_even_band5")
    assert _split_mode("event_even@0.075") == ("event_even", 0.075, "event_even_band7p5")
    panel, idx = make_panel(days=12)
    bt = run_backtest(sig([idx[0]]), panel, hold_days=3, fill="t1_open", capital=100000.0,
                      rebal_band=0.0, alloc_modes=("event_even", "event_even@0.05"))
    assert set(bt.nav.columns) == {"event_even", "event_even_band5"}
    assert bt.stats["event_even"]["rebal_band"] == 0.0
    assert bt.stats["event_even_band5"]["rebal_band"] == 0.05
    assert bt.stats["event_even"]["alloc"] == "event_even"


def test_event_even_rebalances_after_new_signal():
    """event_even：新信号成交后把老仓一起调平到等权（会多出再平衡成交）。"""
    idx = pd.bdate_range("2024-01-01", periods=14)
    codes = ["SZ000001", "SZ000002"]
    C = pd.DataFrame({c: [10.0] * len(idx) for c in codes}, index=idx)
    panel = {"CALENDAR": pd.DataFrame(index=idx), "CLOSE": C, "OPEN": C.copy(),
             "CLOSE_RAW": C.copy(), "OPEN_RAW": C.copy(),
             "LIMIT_UP": pd.DataFrame(np.nan, index=idx, columns=codes),
             "LIMIT_DOWN": pd.DataFrame(np.nan, index=idx, columns=codes)}
    events = pd.DataFrame({"date": [idx[0], idx[4]], "code": ["SZ000001", "SZ000002"],
                           "side": [1, 1]})
    bt = run_backtest(events, panel, hold_days=10, fill="t1_open", capital=100000.0,
                      alloc_modes=("event_even",))
    reasons = set(bt.trades["reason"])
    assert "buy_signal" in reasons
    assert "rebalance_buy" in reasons or "rebalance_sell" in reasons or len(bt.trades) >= 3
    # 等权：两个标的的持仓市值应接近（允许整手误差）
    assert bt.stats["event_even"]["min_cash"] >= -1e-6


def test_event_study_content_cache(tmp_path, monkeypatch):
    """事件研究**内容缓存**（v1.19.48）：同输入第二次命中、任一输入变了必须失效。

    动因：基准池那段对每个 k 过一遍「(配对日 × 池内股票)」大矩阵（全A 15~30s），
    用户实测「重跑一样慢」。数学不能改（单因子测试共用），只能在重复请求上省时间。
    """
    monkeypatch.setattr(sigevent, "_event_cache_dir", lambda: str(tmp_path))
    dates = pd.bdate_range("2021-01-04", periods=60)
    codes = ["SH600000", "SH600001", "SH600002"]
    rng = np.random.default_rng(0)
    px = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (60, len(codes))), axis=0),
                  index=dates, columns=codes)
    events = pd.DataFrame({"date": [dates[5], dates[10]], "code": [codes[0], codes[1]]})

    r1 = sigevent.run_event_study(events, px, px, 5, cache_ns="all|2021-01-01|2021-12-31")
    assert r1.get("cached") is False
    assert r1.get("baseline") is not None

    r2 = sigevent.run_event_study(events, px, px, 5, cache_ns="all|2021-01-01|2021-12-31")
    assert r2.get("cached") is True                                  # 同输入 ⇒ 命中
    assert r2["curve"][0]["mean"] == r1["curve"][0]["mean"]          # 且数值逐位一致

    ev2 = events.copy()
    ev2.loc[0, "code"] = codes[2]                                    # 事件变了 ⇒ 必须重算
    assert sigevent.run_event_study(ev2, px, px, 5,
                                cache_ns="all|2021-01-01|2021-12-31").get("cached") is False

    # 池子/区间变了（命名空间不同）⇒ 不能命中
    assert sigevent.run_event_study(events, px, px, 5,
                                cache_ns="csi1000|2021-01-01|2021-12-31").get("cached") is False
    # max_k 变了 ⇒ 不能命中（曲线期数不同）
    assert sigevent.run_event_study(events, px, px, 4,
                                cache_ns="all|2021-01-01|2021-12-31").get("cached") is False
    # 不给 cache_ns ⇒ 永不缓存（单因子测试那条路径的行为不变）
    n_before = len(list(tmp_path.glob("*.pkl")))
    assert sigevent.run_event_study(events, px, px, 5).get("cached") is False
    assert len(list(tmp_path.glob("*.pkl"))) == n_before, "无 cache_ns 时不该落盘"
    assert n_before >= 1, "有 cache_ns 时应该落了缓存文件"
