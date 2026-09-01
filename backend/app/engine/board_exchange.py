# -*- coding: utf-8 -*-
"""按板块区分涨跌停的自定义 Exchange（替代 qlib 全局 limit_threshold）。

qlib 原版 limit_threshold 是全局一个浮点数，用 $change 统一判断涨跌停，
不区分主板/创业板/科创板/北交所。这导致 20%/30% 涨跌幅板块的股票在回测中
被错误的涨跌停规则约束，收益与实盘严重失真（典型表现：策略持有大量创业板/
科创板股票，却按 10% 涨跌停判定，吃到实盘无法获得的极端收益）。

本类按股票代码前缀区分板块，涨跌停判定与单因子测试统一为**涨停价四舍五入口径**
（app/engine/limits.py）：
  - 主板（SH600/SH601/SH603/SH605、SZ000/SZ001/SZ002/SZ003）: base（limit_threshold，约 10%）
  - 创业板（SZ300/SZ301）与科创板（SH688）: base * 2（约 20%）
  - 北交所（BJ 开头）: base * 3（约 30%）

涨停（收盘 >= 涨停价）禁止买入；跌停（收盘 <= 跌停价）禁止卖出；停牌（$close 为 NaN）双向禁。
"""
from typing import Union

import pandas as pd

from qlib.backtest.exchange import Exchange

from .limits import mark_limit_down, mark_limit_up


class BoardAwareExchange(Exchange):
    """按板块区分涨跌停的 Exchange（主板 10% / 创业板、科创板 20% / 北交所 30%）。"""

    def _update_limit(self, limit_threshold: Union[tuple, float, None]) -> None:
        # 用户显式传表达式（tuple）或 None（不限涨跌停）时，维持父类行为
        if limit_threshold is None or isinstance(limit_threshold, tuple):
            return super()._update_limit(limit_threshold)

        # $close 为 NaN 表示停牌/无行情，不可交易（涨跌停判定对 NaN 返回 False，单独并上）
        suspended = self.quote_df["$close"].isna()
        self.quote_df["limit_buy"] = mark_limit_up(
            self.quote_df, "$close", "$change", base=float(limit_threshold)
        ) | suspended
        self.quote_df["limit_sell"] = mark_limit_down(
            self.quote_df, "$close", "$change", base=float(limit_threshold)
        ) | suspended
