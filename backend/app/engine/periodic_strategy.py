# -*- coding: utf-8 -*-
"""按持仓周期整体换仓的自定义策略（PeriodicTopKStrategy）。

实现语义：每 n_days_hold 个交易日，按当天最新信号把持仓整体重建为 TopK；
中间的非调仓日持仓完全不动（返回空决策，不产生任何交易）。

与 qlib 默认的 TopkDropoutStrategy（每天小步轮换，掉出 TopK 就卖）不同，
本策略强调"持仓周期"：低频整体换仓，降低换手率与交易成本。

用法：在 _build_port_config 的 strategy 配置里指定本类。
"""
import copy
from typing import Optional

import numpy as np

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
        rebalance_base=None,
        weight_col=None,
        topk_ratio=None,
        **kwargs,
    ):
        super().__init__(risk_degree=risk_degree, **kwargs)
        # 权重目标模式（S2 触发叠加）：signal 提供 target_w 列（>0 进入目标组合，值=资金占比，
        # 逐日已归一≈1）。None=旧路径（等权 topk）。weight_col 非 None 且 signal 含该列时启用。
        self.weight_col = weight_col
        self.topk = int(topk)
        # ★ v1.20.66（用户 2026-09-24 要求 ✓）：**按百分比选股**（0<p≤1 ✓），与固定只数二选一 ✓。
        #   ⚠⚠ v1.20.67 **口径校正**（用户 2026-09-24 明确 ✓）：分母 = 当日**全池**可交易只数 ✓
        #   （= 信号当天全部标的、已剔禁买/ST ✓，**不是**"闸门合成后还剩有限分的只数" ✗）。
        #   原口径的坑 ✗：开闸门时候选集被压到几十只（主分 topK=50 − 拒尾 25% ≈ 38 ✓）
        #   ⇒ 1% × 38 = 0.38 ⇒ 四舍五入 0 ⇒ 兜底 **1 只** ✗✓（实测：两次调仓各买 1 只、
        #   且都是买不进的连板新股 ⇒ **全程空仓、净值恒 1** ✗）。
        #   新口径：1% × 全池 ~5400 ≈ **54 只** ✓，再封顶到闸门后的可用只数 ⇒ 实际买 ~38 只 ✓
        #   （正是用户预期 ✓）。池子大小随时间变化时选股宽度**不会悄悄漂移** ✓
        #   （早年全 A ~2000 只时 50 只 = 2.5% ✓、现在 ~5400 只 = 0.9% ✗ ⇒ 性格全变了 ✓）。
        # ⚠ 与 `topk` **互斥**：给了 ratio 就按 ratio 算 ✓（`_k_of()` 里决定 ✓）；1% 也允许 ✓。
        self.topk_ratio = None if topk_ratio in (None, "") else float(topk_ratio)
        self.n_days_hold = max(1, int(n_days_hold))
        self.only_tradable = only_tradable
        self.forbid_all_trade_at_limit = forbid_all_trade_at_limit
        # 调仓节奏的全局基准：回测起点在 qlib 全局交易日历中的索引。
        # 调仓日 = (当前全局交易日序号 - rebalance_base) % n_days_hold == 0。
        # 用"全局交易日序号"而非"段内 step"判断 → 调仓节奏跨滚动段连续，
        # 与段长（test_win=1/2/3/5 天任意）无关，避免"段首从空账户只买不卖"的 BUG。
        # None 时回退旧逻辑（段内相对 step，仅兼容未传参场景）。
        self._rebalance_base = rebalance_base
        # 上一次调仓的 step（时间步），旧逻辑用；rebalance_base 传入时忽略
        self._last_rebalance_step = None

    def _k_of(self, n_pool: int, n_avail: Optional[int] = None) -> int:
        """当日**目标持仓只数** K：按百分比（`topk_ratio` ✓）或固定只数（`topk` ✓）二选一 ✓。

        ★★ v1.20.67（用户 2026-09-24 校正口径 ✓）：
          · `n_pool`  = 当日**全池**可交易只数 ✓（信号里当天的全部标的，已剔禁买/ST ✓，
                        **未**经闸门/合成把候选外置 `-inf` ✓）—— 这是**百分比的分母** ✓；
          · `n_avail` = **实际还能买的只数** ✓（合成后仍是有限分的那些 ✓，即闸门拒尾后的 ~38 只 ✓）
                        ⇒ 只用来**封顶** ✓。
        ⇒ 口径 = `比例 × n_pool`，再封顶到 `n_avail` ✓：
            1% × 全池 ~5400 ≈ **54 只** ✓；闸门剔 25% ⇒ 实际买 **~38 只** ✓（= 用户预期 ✓）。
        ⚠ 旧口径（v1.20.66 ✗）拿"信号里**分数有限**的只数"当分母 ✗ ⇒ 开闸门时该数被压到几十 ✗
          ⇒ `1% × 38 = 0.38` ⇒ 四舍五入 0 ⇒ 兜底 **1 只** ✗✓（实测正是如此：两次调仓各买 1 只 ✓，
          且都是买不进的连板新股 ⇒ **全程空仓、净值恒 1** ✗）。
        ⚠ 下限 1 只 ✓（避免 0 只 ⇒ 空仓 ✓）；上限 = `n_avail` ✓（不会越界 ✓）。
        """
        if n_pool <= 0 or (n_avail is not None and n_avail <= 0):
            return 0
        cap = n_pool if n_avail is None else n_avail
        if self.topk_ratio is not None:
            k = int(round(max(0.0, min(1.0, self.topk_ratio)) * n_pool))
            return max(1, min(cap, k))
        return max(1, min(cap, self.topk))

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)
        pred_start_time, pred_end_time = self.trade_calendar.get_step_time(trade_step, shift=1)

        # 调仓日判定（优先全局交易日序号，跨段连续；回退段内相对 step）
        if self._rebalance_base is not None:
            global_step = self.trade_calendar.start_index + trade_step
            if (global_step - self._rebalance_base) % self.n_days_hold != 0:
                return TradeDecisionWO([], self)
        else:
            if self._last_rebalance_step is not None:
                if trade_step - self._last_rebalance_step < self.n_days_hold:
                    return TradeDecisionWO([], self)

        # 调仓日：获取当前信号
        raw_sig = self.signal.get_signal(start_time=pred_start_time, end_time=pred_end_time)
        if raw_sig is None or (hasattr(raw_sig, "empty") and raw_sig.empty):
            logger.info("Rebalance %s: 空仓-raw_sig 为空（当日无信号）", trade_step)
            return TradeDecisionWO([], self)
        # 权重目标模式（S2）：signal 为多列 DataFrame 且含 weight_col → 按权重下单
        if (self.weight_col is not None and isinstance(raw_sig, pd.DataFrame)
                and self.weight_col in raw_sig.columns):
            return self._rebalance_weighted(raw_sig, trade_start_time, trade_end_time)
        pred_score = raw_sig.iloc[:, 0] if isinstance(raw_sig, pd.DataFrame) else raw_sig
        if pred_score is None or len(pred_score) == 0:
            logger.info("Rebalance %s: 空仓-pred_score 为空", trade_step)
            return TradeDecisionWO([], self)

        # 用 deepcopy 模拟调仓（不应直接修改真实账户；最终订单由 executor 实际执行）
        current_temp = copy.deepcopy(self.trade_position)
        cash = current_temp.get_cash()
        current_stock_list = current_temp.get_stock_list()

        # 日截面剔除（ST/退市整理/创业板/科创板，开关在 exchange 上）：TopK 候选先剔除
        # 调仓当日禁买股，避免 topk 位置被禁买股占据导致现金空转；已持有的这类股票
        # 因不在新 topk 而随调仓卖出 → 持仓永不包含被剔除股票。
        forbid_cnt = 0
        orig_cnt = len(pred_score)
        try:
            forbid = self.trade_exchange.get_forbidden_mask(
                pred_score.index, trade_start_time, trade_end_time
            )
            if forbid is not None and bool(forbid.any()):
                forbid_cnt = int(forbid.sum())
                pred_score = pred_score[~forbid]
        except Exception:
            # 旧 exchange/异常时退回原逻辑（不过滤），保证回测可跑
            pass
        if pred_score is None or len(pred_score) == 0:
            logger.info("Rebalance %s: 空仓-禁买剔除后无候选（原始%d，被禁%d）",
                        trade_step, orig_cnt, forbid_cnt)
            return TradeDecisionWO([], self)

        # ★ v1.20.67：先记下**当日全池可交易只数** = 禁买/ST 剔除之后的全部候选 ✓
        #   ⚠ 必须**早于**下面那句"丢弃非有限分"✗ —— 那一步会把闸门置 `-inf` 的候选取掉 ✓，
        #   而百分比的分母要的是**全池** ✓（见 `_k_of` ✓）；否则开闸门时 1% 会退化成 1 只 ✗✓。
        n_pool = len(pred_score)

        # ★ v1.20.66：丢弃**非有限分数**（闸门拒尾/候选外被置 `-inf` ✓）——
        #   ⚠ 否则 `-inf` 会参与排序并**占满 topk 名额** ✗（"拒尾后不补"就落不了地 ✓）。
        pred_score = pred_score.replace([np.inf, -np.inf], np.nan).dropna()
        if pred_score is None or len(pred_score) == 0:
            logger.info("Rebalance %s: 空仓-有效分数全为空（全池 %d 只 ⇒ 合成后一只不剩 ✗）",
                        trade_step, n_pool)
            return TradeDecisionWO([], self)

        # 目标组合：信号分数最高的 K 只（K 由固定只数或百分比决定 ✓）
        n_avail = len(pred_score)
        k = self._k_of(n_pool, n_avail)
        logger.info("Rebalance %s: 目标 %d 只（全池可交易 %d ｜合成后可用 %d ｜%s）",
                    trade_step, k, n_pool, n_avail,
                    ("比例 %.4g%%" % (self.topk_ratio * 100)) if self.topk_ratio is not None
                    else ("固定 %d 只" % self.topk))
        target_topk = list(pred_score.sort_values(ascending=False).index[:k])

        # 卖出：当前持仓中不在目标 topk 的（整体卖出）
        sell_order_list = []
        n_in_target = n_tradable_sell_skip = n_amount_sell_skip = n_check_sell_skip = 0
        for code in current_stock_list:
            if code in target_topk:
                n_in_target += 1
                continue
            if self.only_tradable and not self.trade_exchange.is_stock_tradable(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else Order.SELL,
            ):
                n_tradable_sell_skip += 1
                continue
            sell_amount = current_temp.get_stock_amount(code=code)
            if not sell_amount or sell_amount <= 0:
                n_amount_sell_skip += 1
                continue
            order = Order(
                stock_id=code,
                amount=sell_amount,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=Order.SELL,
            )
            # 可交易性预检交给 check_order（涨跌停/停牌判定）：
            # 注意：若预检把卖单拦下说明当日确不可卖；若放行但 executor 仍拒成 0 成交，
            # 则是卖单股数口径/整手问题（见 board 修复，勿删预检）。
            if not self.trade_exchange.check_order(order):
                n_check_sell_skip += 1
                continue
            sell_order_list.append(order)
            trade_val, trade_cost, _ = self.trade_exchange.deal_order(order, position=current_temp)
            cash += trade_val - trade_cost

        # 买入：目标 topk 中当前未持有的（整体买入）
        buy_order_list = []
        to_buy = [code for code in target_topk if code not in current_stock_list]
        n_price_skip = n_amount_skip = n_tradable_skip = 0
        if to_buy:
            value = cash * self.risk_degree / len(to_buy)
            for code in to_buy:
                if self.only_tradable and not self.trade_exchange.is_stock_tradable(
                    stock_id=code, start_time=trade_start_time, end_time=trade_end_time,
                    direction=None if self.forbid_all_trade_at_limit else Order.BUY,
                ):
                    n_tradable_skip += 1
                    continue
                buy_price = self.trade_exchange.get_deal_price(
                    stock_id=code, start_time=trade_start_time, end_time=trade_end_time, direction=Order.BUY
                )
                if not buy_price or buy_price <= 0:
                    n_price_skip += 1
                    continue
                buy_amount = self.trade_exchange.round_amount_by_trade_unit(
                    value / buy_price,
                    self.trade_exchange.get_factor(stock_id=code, start_time=trade_start_time, end_time=trade_end_time),
                )
                if not buy_amount or buy_amount <= 0:
                    n_amount_skip += 1
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
                "Rebalance %s: sell %d buy %d | 持仓%d 目标内%d "
                "卖出跳过(不可交易%d 无仓%d 拒单%d) 买入跳过(不可交易%d 无价%d 手数0=%d)",
                trade_step, len(sell_order_list), len(buy_order_list), len(current_stock_list),
                n_in_target, n_tradable_sell_skip, n_amount_sell_skip, n_check_sell_skip,
                n_tradable_skip, n_price_skip, n_amount_skip,
            )
        elif to_buy or current_stock_list:
            # 调仓日但零订单：打详细原因（候选/禁买/价格缺失/手数不足），便于诊断空仓净值=1
            logger.info(
                "Rebalance %s: 零订单 raw=%d 禁买=%d 过滤后=%d 目标=%d 待买=%d "
                "跳过(不可交易=%d 无价=%d 手数=0=%d) 当前持仓=%d",
                trade_step, orig_cnt, forbid_cnt, len(pred_score),
                len(target_topk), len(to_buy), n_tradable_skip, n_price_skip,
                n_amount_skip, len(current_stock_list),
            )
        return TradeDecisionWO(orders, self)

    def _rebalance_weighted(self, sig, trade_start_time, trade_end_time):
        """按 target_w 权重目标整体重建（S2 触发叠加）。

        sig: 当日 signal DataFrame（含 weight_col）。>0 的行 = 目标组合，权重=该列值
        （逐日已归一≈1，主层+触发层）。语义与等权路径一致：
        卖出不在目标的旧仓、买入目标内未持有的新仓（按权重预算资金），已持有的目标仓不动。
        """
        w = sig[self.weight_col].astype(float)
        try:
            forbid = self.trade_exchange.get_forbidden_mask(
                sig.index, trade_start_time, trade_end_time
            )
            if forbid is not None and bool(forbid.any()):
                w = w[~forbid]
        except Exception:
            pass
        w = w[w > 0].dropna()
        if w is None or len(w) == 0:
            return TradeDecisionWO([], self)
        total_w = float(w.sum())
        if total_w <= 0:
            return TradeDecisionWO([], self)

        current_temp = copy.deepcopy(self.trade_position)
        cash = current_temp.get_cash()
        current_stock_list = current_temp.get_stock_list()
        target_codes = set(w.index)

        # 卖出：当前持仓不在目标权重集合的（整体卖出）
        sell_order_list = []
        for code in current_stock_list:
            if code in target_codes:
                continue
            if self.only_tradable and not self.trade_exchange.is_stock_tradable(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else Order.SELL,
            ):
                continue
            sell_amount = current_temp.get_stock_amount(code=code)
            if not sell_amount or sell_amount <= 0:
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

        # 买入：目标集合中未持有的，按权重预算分配
        budget = cash * self.risk_degree
        buy_order_list = []
        for code, wt in w.items():
            if code in current_stock_list:
                continue
            if self.only_tradable and not self.trade_exchange.is_stock_tradable(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else Order.BUY,
            ):
                continue
            buy_price = self.trade_exchange.get_deal_price(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time, direction=Order.BUY
            )
            if not buy_price or buy_price <= 0:
                continue
            share = budget * (wt / total_w)
            if share <= 0:
                continue
            amount = self.trade_exchange.round_amount_by_trade_unit(
                share / buy_price,
                self.trade_exchange.get_factor(stock_id=code, start_time=trade_start_time,
                                               end_time=trade_end_time),
            )
            if not amount or amount <= 0:
                continue
            buy_order_list.append(
                Order(
                    stock_id=code,
                    amount=amount,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=Order.BUY,
                )
            )
        self._last_rebalance_step = self.trade_calendar.get_trade_step()
        orders = sell_order_list + buy_order_list
        if orders:
            logger.info(
                "Rebalance(weighted) at step %s: sell %d, buy %d",
                self._last_rebalance_step, len(sell_order_list), len(buy_order_list),
            )
        return TradeDecisionWO(orders, self)
