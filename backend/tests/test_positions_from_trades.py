# -*- coding: utf-8 -*-
"""「成交明细 → 每期持仓」守卫（v2.1.0 ✓）。

**为什么锁**：事件研究导出第 2 页的每期持仓**完全由这个派生视图决定** ⇒ 一旦口径错了，
用户看到的是"和成交明细对不上的持仓表"（表面合理、实际错 ✗ 最难发现）。
这里用**手算样例**钉住三件事：① 买卖推进的股数；② 卖出归零的股票要退出快照；
③ `weight` 是当日同方案内按"股数×最近成交价"的市值占比 ✓。
"""
import pandas as pd

from app.signals.positions import positions_from_trades


def _trades() -> pd.DataFrame:
    return pd.DataFrame([
        # 第 1 次调仓：买 A 100、B 200（事件方案按可用现金等分 ⇒ 这里只验派生逻辑 ✓）
        {"date": pd.Timestamp("2024-01-02"), "code": "A", "side": "买", "shares": 100, "price": 10.0,
         "amount": 1000.0, "mode": "event_even"},
        {"date": pd.Timestamp("2024-01-02"), "code": "B", "side": "买", "shares": 200, "price": 5.0,
         "amount": 1000.0, "mode": "event_even"},
        # 第 2 次调仓：卖光 A、买 C 50（A 必须从快照里消失 ✓）
        {"date": pd.Timestamp("2024-02-01"), "code": "A", "side": "卖", "shares": 100, "price": 12.0,
         "amount": 1200.0, "mode": "event_even"},
        {"date": pd.Timestamp("2024-02-01"), "code": "C", "side": "买", "shares": 50, "price": 20.0,
         "amount": 1000.0, "mode": "event_even"},
        # 另一资金方案：只买过一次（两个方案必须各自独立推进 ✓）
        {"date": pd.Timestamp("2024-01-02"), "code": "A", "side": "买", "shares": 10, "price": 10.0,
         "amount": 100.0, "mode": "cash_even"},
    ])


def test_positions_follow_buys_and_sells():
    rows = positions_from_trades(_trades())
    got = {(r["rebalance_date"], r["mode"], r["code"]): (r["shares"], r["weight"]) for r in rows}

    # 第 1 次调仓：A 100 股(1000)、B 200 股(1000) ⇒ 各占一半 ✓
    assert got[("2024-01-02", "event_even", "A")][0] == 100
    assert abs(got[("2024-01-02", "event_even", "A")][1] - 0.5) < 1e-9
    assert abs(got[("2024-01-02", "event_even", "B")][1] - 0.5) < 1e-9
    # 第 2 次调仓：A 已清仓 ⇒ 不在快照里 ✓；B 200×5=1000、C 50×20=1000 ⇒ 各半 ✓
    assert ("2024-02-01", "event_even", "A") not in got
    assert got[("2024-02-01", "event_even", "B")][0] == 200
    assert abs(got[("2024-02-01", "event_even", "C")][1] - 0.5) < 1e-9
    # 两个方案独立：cash_even 只有 A，占 100% ✓
    assert got[("2024-01-02", "cash_even", "A")][1] == 1.0
    assert ("2024-02-01", "cash_even", "A") not in got    # 该方案没有成交日 ⇒ 不出快照 ✓


def test_positions_tolerates_bad_input():
    assert positions_from_trades(None) == []
    assert positions_from_trades(pd.DataFrame()) == []
    assert positions_from_trades(pd.DataFrame([{"date": 1, "code": "A"}])) == []   # 缺列 ⇒ []（不抛 ✓)


def test_positions_respects_mode_order_and_cap():
    rows = positions_from_trades(_trades(), modes=("cash_even", "event_even"))
    assert rows[0]["mode"] == "cash_even"                 # 显式给的顺序优先 ✓
    assert len(positions_from_trades(_trades(), max_rows=2)) == 2
