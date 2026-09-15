# -*- coding: utf-8 -*-
"""聚宽流水"三条净值曲线"（A/B/C）的成交价口径回归（2026-09-15）。

动因（用户按曲线起点对比时抓出来的真 bug）：
  A/B 原来**一律用"我们的收盘价"**成交，而 C 用它**自己的实际成交价**（09:30 ⇒ 开盘价）。
  ⇒ 2016-01-04 熔断日（官方首日 −9.15%）：A/B "买入价 = 当天收盘" ⇒ **当天不亏**，
    起点 0.9997 vs C/聚宽官方 0.906 ⇒ 起点整整差 9%，被误读成"费率档次"或"分红口径"。
  修法：A/B 也按**委托时间**选价（`09:30` → 开盘、午后 → 收盘），与 C 同口径。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.signals.replay import replay_trades


def _one_trade(time_str: str):
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    # 当天：开盘 10、收盘 9（盘中 −10%）；"它"的成交价也记 9 ⇒ 比值 = 1（不引入换算干扰）
    open_ = pd.DataFrame({"SZ000001": [10.0, 9.0]}, index=idx)
    close = pd.DataFrame({"SZ000001": [9.0, 9.0]}, index=idx)
    # ⚠ 必须有**两个不同成交日**，否则 `len(win) < 2` 会被判"无交集"直接返回（函数的前置约束）
    tr = pd.DataFrame([
        {"date": idx[0], "time": time_str, "code": "SZ000001", "side": 1,
         "qty": 1000.0, "price": 9.0, "amount": 9000.0, "fee": 2.7},
        {"date": idx[1], "time": "14:00:00", "code": "SZ000001", "side": -1,
         "qty": 1000.0, "price": 9.0, "amount": 9000.0, "fee": 11.7},
    ])
    return replay_trades(tr, close, capital=10000.0, capital_ref=10000.0,
                         my_cost=0.004, open_=open_)


def test_replay_ab_uses_open_price_for_morning_orders():
    """09:30 的买单 ⇒ A/B 必须按**开盘价**成交（当天就吃到盘中跌幅）。"""
    out = _one_trade("09:30:00")
    nav = out["nav"]["nav_sim_fee"]
    # 现金 = 10000 − 1000×10×(1+0.0003) ≈ −3；持仓市值 = 1000×9 = 9000 ⇒ 0.8997
    assert nav.iloc[0] == pytest.approx(0.8997, abs=2e-4), nav.iloc[0]
    assert nav.iloc[0] < 0.95, "按开盘价成交 ⇒ 当天应有约 −10% 的浮亏"


def test_replay_ab_uses_close_price_for_afternoon_orders():
    """14:00 的买单 ⇒ 按收盘价成交（与旧行为一致：当天不产生盈亏）。"""
    out = _one_trade("14:00:00")
    nav = out["nav"]["nav_sim_fee"]
    # 现金 = 10000 − 1000×9×(1+0.0003) = 997.3；市值 9000 ⇒ 0.99973
    assert nav.iloc[0] == pytest.approx(0.99973, abs=2e-4), nav.iloc[0]


def test_replay_ab_falls_back_to_close_without_time_column():
    """没有时间列（`time` 为空）⇒ 回退收盘价（向后兼容，不能因缺列而算错价）。"""
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    open_ = pd.DataFrame({"SZ000001": [10.0, 9.0]}, index=idx)
    close = pd.DataFrame({"SZ000001": [9.0, 9.0]}, index=idx)
    tr = pd.DataFrame([
        {"date": idx[0], "time": "", "code": "SZ000001", "side": 1,
         "qty": 1000.0, "price": 9.0, "amount": 9000.0, "fee": 2.7},
        {"date": idx[1], "time": "14:00:00", "code": "SZ000001", "side": -1,
         "qty": 1000.0, "price": 9.0, "amount": 9000.0, "fee": 11.7},
    ])
    out = replay_trades(tr, close, capital=10000.0, capital_ref=10000.0,
                        my_cost=0.004, open_=open_)
    assert out["nav"]["nav_sim_fee"].iloc[0] == pytest.approx(0.99973, abs=2e-4)


def test_replay_without_open_matrix_keeps_old_behavior():
    """不传 `open_` ⇒ 保持"一律收盘价"的旧行为（调用方忘了传也不会静默改变结果）。"""
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    close = pd.DataFrame({"SZ000001": [9.0, 9.0]}, index=idx)
    tr = pd.DataFrame([
        {"date": idx[0], "time": "09:30:00", "code": "SZ000001", "side": 1,
         "qty": 1000.0, "price": 9.0, "amount": 9000.0, "fee": 2.7},
        {"date": idx[1], "time": "14:00:00", "code": "SZ000001", "side": -1,
         "qty": 1000.0, "price": 9.0, "amount": 9000.0, "fee": 11.7},
    ])
    out = replay_trades(tr, close, capital=10000.0, capital_ref=10000.0, my_cost=0.004)
    assert out["nav"]["nav_sim_fee"].iloc[0] == pytest.approx(0.99973, abs=2e-4)
