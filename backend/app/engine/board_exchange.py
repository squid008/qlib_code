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
from typing import Any, Union

import pandas as pd

from qlib.backtest.exchange import Exchange

from .limits import mark_limit_down, mark_limit_up
from .adjust import normalize_mode


class BoardAwareExchange(Exchange):
    """按板块区分涨跌停的 Exchange（主板 10% / 创业板、科创板 20% / 北交所 30%）。

    扩展参数：
    - avg_mode：复合均价成交价基准（avg_co / avg_ohlc），本项目自定义，父类不识别。
    - price_adjust：复权方式 none/forward/backward。在 quote 层对价格列做复权
      （不修改 qlib 内核）。
      【数据事实】qlib quote 的 $close/$open/$high/$low 原生为【后复权价】= 真实价 × factor
      （见 engine/adjust.py docstring），因此：
        none      → 真实价 = price / factor（东财"不复权"口径）
        backward  → 后复权价 = price（原生，不处理）
        forward   → 前复权价 = price / 每股最新因子（归一到实盘价观感）
      涨跌停判定恒用【真实价】（$close_real = $close/$factor）与 $change（真实价涨跌幅），
      因涨停价由交易所按真实价确定、与复权无关。
    """

    def __init__(
        self,
        *args: Any,
        avg_mode: Union[str, None] = None,
        price_adjust: str = "none",
        **kwargs: Any,
    ) -> None:
        # 本项目扩展参数，父类不识别，需先取出
        self._avg_mode = avg_mode
        self._price_adjust = normalize_mode(price_adjust)
        super().__init__(*args, **kwargs)

    def _adjust_quote_prices(self) -> None:
        """按复权模式调整 quote 价格列（$close 等原生为后复权价，见模块 docstring）：

        - none（真实价）   ：price / factor
        - backward（后复权）：price（保持原生）
        - forward（前复权） ：price / 每股区间最新因子（归一到实盘价观感）

        只调整 $open/$high/$low/$close/$vwap 五个价格列；$volume/$amount/$change 不变。
        factor 缺失（NaN）时按 1.0 处理（该股无复权数据，真实价=原生价）。
        同时写入 $close_real（真实价），供涨跌停判定使用（与复权无关）。
        """
        df = self.quote_df
        if "$factor" not in df.columns:
            return
        factor = df["$factor"].fillna(1.0)
        price_cols = [f for f in ("$open", "$high", "$low", "$close", "$vwap") if f in df.columns]
        if self._price_adjust == "none":
            for f in price_cols:
                df[f] = df[f] / factor
        elif self._price_adjust == "forward":
            # 前复权：以每只股票最新有行情日的因子为基准归一化（价格观感接近实盘价；
            # 收益率与后复权等价，因为整体常数缩放被约掉）
            last_factor = df["$factor"].groupby(level=0).transform(lambda s: s.ffill().iloc[-1])
            last_factor = last_factor.fillna(1.0)
            for f in price_cols:
                df[f] = df[f] / last_factor
        # backward：价格列保持原生后复权价，无需处理
        # 真实价供涨跌停判定（交易所按真实价定价，与复权无关）
        df["$close_real"] = df["$close"] / factor

    def get_quote_from_qlib(self) -> None:
        """加载行情后：按需复权价格列，再注入自定义均价列。

        父类 __init__ 在 get_quote_from_qlib() 返回后才构造 self.quote（NumpyQuote），
        因此这里注入的列能被 quote 正确读取。qlib 的 deal_price 只支持 quote 中已有的
        字段名（如 $close / $vwap），复合均价（如 (open+close)/2）需先算成列。
        """
        super().get_quote_from_qlib()
        # 任何模式都要调（none 也需把后复权价转真实价并注入 $close_real 供涨跌停判定）
        self._adjust_quote_prices()
        mode = self._avg_mode
        if mode == "avg_co":
            self.quote_df["$avg_co"] = (self.quote_df["$open"] + self.quote_df["$close"]) / 2.0
            self.buy_price = self.sell_price = "$avg_co"
        elif mode == "avg_ohlc":
            self.quote_df["$avg_ohlc"] = (
                self.quote_df["$open"] + self.quote_df["$high"] + self.quote_df["$low"] + self.quote_df["$close"]
            ) / 4.0
            self.buy_price = self.sell_price = "$avg_ohlc"

    def _update_limit(self, limit_threshold: Union[tuple, float, None]) -> None:
        # 用户显式传表达式（tuple）或 None（不限涨跌停）时，维持父类行为
        if limit_threshold is None or isinstance(limit_threshold, tuple):
            return super()._update_limit(limit_threshold)

        # 涨跌停判定用【真实价】列 $close_real（= $close/$factor）与 $change（真实价涨跌幅），
        # 两者同源，除权日也精确（涨停价由交易所按真实价确定，与复权无关）。
        # $close_real 缺失（该股无 factor）时回退用 $close。
        close_col = "$close_real" if "$close_real" in self.quote_df.columns else "$close"
        # $close_real 为 NaN 表示停牌/无行情，不可交易（涨跌停判定对 NaN 返回 False，单独并上）
        suspended = self.quote_df[close_col].isna()
        self.quote_df["limit_buy"] = mark_limit_up(
            self.quote_df, close_col, "$change", base=float(limit_threshold)
        ) | suspended
        self.quote_df["limit_sell"] = mark_limit_down(
            self.quote_df, close_col, "$change", base=float(limit_threshold)
        ) | suspended
