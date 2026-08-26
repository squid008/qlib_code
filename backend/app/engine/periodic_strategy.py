# -*- coding: utf-8 -*-
"""按持仓周期整体换仓的自定义策略（PeriodicTopKStrategy）。

实现语义：每 n_days_hold 个交易日，按当天最新信号把持仓整体重建为 TopK；
中间的非调仓日持仓完全不动（返回空决策，不产生任何交易）。

与 qlib 默认的 TopkDropoutStrategy（每天小步轮换，掉出 TopK 就卖）不同，
本策略强调"持仓周期"：低频整体换仓，降低换手率与交易成本。

用法：在 _build_port_config 的 strategy 配置里指定本类。
"""
from qlib.backtest.decision import Order, TradeDecisionWO
from qlib.contrib.strategy.signal_strategy import BaseSignalStrategy
from qlib.log import get_module_logger

import pandas as pd

logger = get_module_logger(__name__)


class PeriodicTopKStrategy(BaseSignalStrategy):
    """每 N 天整体换仓一次：调仓日按信号重建 TopK，非调仓日持仓不动。

    参数：
      - topk: 组合中持有的股票数量
      - n_days_hold: 持仓周期（天），每 N 个交易日调仓一次。1=每日整体重建。
      - risk_degree: 仓位占比（默认 0.95）
    """

    def __init__(
        self,
        *,
        topk,
        n_days_hold=10,
        risk_degree=0.95,
        only_tradable=False,
        forbid_all_trade_at_limit=True,
        **kwargs,
    ):
        super().__init__(risk_degree=risk_degree, **kwargs)
        self.topk = int(topk)
        self.n_days_hold = max(1, int(n_days_hold))
        self.only_tradable = only_tradable
        self.forbid_all_trade_at_limit = forbid_all_trade_at_limit
        # 上一次调仓的 step（时间步），用于判断是否到了调仓日
        self._last_rebalance_step = None

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)
        pred_start_time, pred_end_time = self.trade_calendar.get_step_time(trade_step, shift=1)

        # 非调仓日：返回空决策，持仓不动
        if self._last_rebalance_step is not None:
            if trade_step - self._last_rebalance_step < self.n_days_hold:
                return TradeDecisionWO([], self)

        # 调仓日：获取当前信号
        pred_score = self.signal.get_signal(start_time=pred_start_time, end_time=pred_end_time)
        if isinstance(pred_score, pd.DataFrame):
            pred_score = pred_score.iloc[:, 0]
        if pred_score is None or len(pred_score) == 0:
            return TradeDecisionWO([], self)

        current_temp = self.trade_position
        cash = current_temp.get_cash()
        current_stock_list = current_temp.get_stock_list()

        # 目标组合：信号分数最高的 topk 只
        target_topk = list(pred_score.sort_values(ascending=False).index[: self.topk])

        # 卖出：当前持仓中不在目标 topk 的（整体卖出）
        sell_order_list = []
        for code in current_stock_list:
            if code in target_topk:
                continue
            if self.only_tradable and not self.trade_exchange.is_stock_tradable(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else Order.SELL,
            ):
                continue
            sell_amount = current_temp.get_stock_amount(code=code)
            if sell_amount <= 0:
                continue
            order = Order(
                stock_id=code,
                amount=sell_amount,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=Order.SELL,
            )
            if self.trade_exchange.check_order(order):
                sell_order_list.append(order)
                trade_val, trade_cost, _ = self.trade_exchange.deal_order(order, position=current_temp)
                cash += trade_val - trade_cost

        # 买入：目标 topk 中当前未持有的（整体买入）
        buy_order_list = []
        to_buy = [code for code in target_topk if code not in current_stock_list]
        if to_buy:
            value = cash * self.risk_degree / len(to_buy)
            for code in to_buy:
                if self.only_tradable and not self.trade_exchange.is_stock_tradable(
                    stock_id=code, start_time=trade_start_time, end_time=trade_end_time,
                    direction=None if self.forbid_all_trade_at_limit else Order.BUY,
                ):
                    continue
                buy_price = self.trade_exchange.get_deal_price(
                    stock_id=code, start_time=trade_start_time, end_time=trade_end_time, direction=Order.BUY
                )
                if buy_price <= 0:
                    continue
                buy_amount = self.trade_exchange.round_amount_by_trade_unit(
                    value / buy_price,
                    self.trade_exchange.get_factor(stock_id=code, start_time=trade_start_time, end_time=trade_end_time),
                )
                if buy_amount <= 0:
                    continue
                buy_order_list.append(
                    Order(
                        stock_id=code,
                        amount=buy_amount,
                        start_time=trade_start_time,
                        end_time=trade_end_time,
                        direction=Order.BUY,
                    )
                )

        # 记录本次调仓的 step
        self._last_rebalance_step = trade_step
        orders = sell_order_list + buy_order_list
        if orders:
            logger.info(
                "Rebalance at step %s: sell %d, buy %d",
                trade_step, len(sell_order_list), len(buy_order_list),
            )
        return TradeDecisionWO(orders, self)
