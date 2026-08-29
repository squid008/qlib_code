# -*- coding: utf-8 -*-
"""按板块区分涨跌停的自定义 Exchange（替代 qlib 全局 limit_threshold）。

qlib 原版 limit_threshold 是全局一个浮点数，用 $change 统一判断涨跌停，
不区分主板/创业板/科创板/北交所。这导致 20%/30% 涨跌幅板块的股票在回测中
被错误的涨跌停规则约束，收益与实盘严重失真（典型表现：策略持有大量创业板/
科创板股票，却按 10% 涨跌停判定，吃到实盘无法获得的极端收益）。

本类按股票代码前缀区分板块，涨跌停阈值按板块放大：
  - 主板（SH600/SH601/SH603/SH605、SZ000/SZ001/SZ002/SZ003）: limit_threshold（约 10%）
  - 创业板（SZ300/SZ301）与科创板（SH688）: limit_threshold * 2（约 20%）
  - 北交所（BJ 开头）: limit_threshold * 3（约 30%）

涨停（当日涨跌幅 >= 该股阈值）禁止买入；跌停（<= -阈值）禁止卖出。
"""
from typing import Union

import pandas as pd

from qlib.backtest.exchange import Exchange


def _board_limit_threshold(code, limit_threshold: float) -> float:
    """按股票代码返回该股的涨跌停阈值（涨跌幅比例，如 0.095 表示 9.5%）。"""
    c = str(code).upper()
    if c.startswith(("SH688", "SZ300", "SZ301")):
        # 创业板 / 科创板：20% 涨跌幅
        return limit_threshold * 2.0
    if c.startswith("BJ"):
        # 北交所：30% 涨跌幅
        return limit_threshold * 3.0
    # 主板：10% 涨跌幅
    return limit_threshold


class BoardAwareExchange(Exchange):
    """按板块区分涨跌停的 Exchange（主板 10% / 创业板、科创板 20% / 北交所 30%）。"""

    def _update_limit(self, limit_threshold: Union[tuple, float, None]) -> None:
        # 用户显式传表达式（tuple）或 None（不限涨跌停）时，维持父类行为
        if limit_threshold is None or isinstance(limit_threshold, tuple):
            return super()._update_limit(limit_threshold)

        # $close 为 NaN 表示停牌/无行情，不可交易
        suspended = self.quote_df["$close"].isna()
        if isinstance(self.quote_df.index, pd.MultiIndex):
            insts = self.quote_df.index.get_level_values(0)
        else:
            # 单标的场景：index 为 DatetimeIndex，无 instrument 信息，退化为全局阈值
            insts = [""] * len(self.quote_df)
        # 每行（股票 × 日期）对应一个按板块计算的阈值
        thr = pd.Series(
            [_board_limit_threshold(i, limit_threshold) for i in insts],
            index=self.quote_df.index,
        )
        change = self.quote_df["$change"]
        self.quote_df["limit_buy"] = change.ge(thr) | suspended
        self.quote_df["limit_sell"] = change.le(-thr) | suspended
