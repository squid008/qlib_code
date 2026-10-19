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
from typing import Any, Optional, Union

import pandas as pd

from qlib.backtest.decision import Order
from qlib.backtest.exchange import Exchange

from .limits import mark_limit_down, mark_limit_up
from .adjust import normalize_mode

def _log_swallowed(where, exc):
    """**留痕兜底** ✓（P2-A，2026-10-18）—— 吞异常后写一行 debug 日志。

    ⚠ logger 名在不同模块不一样（`logger` / `_LOGGER` / 没有 ✓），这里全兜住 ✓；
      全程 `contextlib.suppress` ✓：**日志自身失败绝不许抛** ✗（2.9.26 事故教训 ✓）。
    """
    import contextlib
    import logging as _logging

    with contextlib.suppress(Exception):
        lg = globals().get("logger") or globals().get("_LOGGER") or _logging.getLogger(__name__)
        lg.debug("[吞] %s: %r", where, exc)


#: 冲击成本的**倍数封顶**（v2.8.5 ✓）：本单金额 / 当日成交额 的上限 ✓
IMPACT_RATIO_CAP = 1.0


def cap_impact_cost(trade_val: float, turnover: float, base_cost_rate: float,
                    impact_cost: float, min_cost: float,
                    cap: float = IMPACT_RATIO_CAP) -> Optional[float]:
    """按"倍数封顶"重算交易成本 ✓（v2.8.5；2026-10-16 用户报的**滚动净值垮塌**根因 ✓）。

    qlib 的滑点是**平方冲击**：`adj_cost_ratio = impact_cost × (本单金额 / 当日成交额)²`
    （`qlib/backtest/exchange.py::_calc_trade_info_by_order` ✓）⇒ 单笔金额一旦**超过当日成交额**，
    平方项就 > 1 ⇒ **成本可以比成交金额还大** ✗。实测（`ai_test/probe_impact_cost_1016.py` ✓，
    本金 1 亿的回测）：190 万的单子打在当日成交额 **2.78 万**的票上 ⇒ 倍数 **68** ⇒ 那一笔成本
    **1778 万** ✗；37 笔按公式合计 **7577.7 万** ⇒ 与 qlib recorder 里的 `total_cost`
    （**7597.5 万** ✓）**逐位吻合** ⇒ 现金被当成本扣光（1e8 → **27 元** ✗）⇒ 净值一步砸到 **0.2402** ✓。

    ⚠ 平台「成交量限制」默认留空（`volume_threshold=None` ⇒ 不限量 ✓）⇒ "单子远大于当日成交额"
      的单**真会发生** ✓（要贴近实盘应显式给 0.1~0.25 ✓ —— 那是另一个**口径决定** ✓，
      本函数只管"**别爆炸**" ✓）。

    做法：把倍数**封顶到 `cap`（默认 1.0 ✓）**＝"最多吃掉当日全部成交额 ⇒ 冲击按 1 倍计" ✓。
    ⚠ **正常回测逐位不变** ✓ —— 只有"单笔 > 当日成交额"时才命中（那时原成本本来就荒谬 ✓）；
      因此本函数在不需要封顶时返回 `None` ✓，调用方保留父类原值 ✓。

    返回：需要封顶时返回新成本 ✓；不需要（或数据不足）返回 `None` ⇒ 调用方**保持原值** ✓。
    """
    try:
        tv = float(trade_val)
        turn = float(turnover)
    except (TypeError, ValueError):
        return None
    if not (tv > 0) or not (turn > 0) or tv != tv or turn != turn:
        return None
    if tv / turn <= float(cap):
        return None
    rate = float(base_cost_rate or 0.0) + float(impact_cost or 0.0) * float(cap) ** 2
    return max(tv * rate, float(min_cost or 0.0))


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
        exclude_st: bool = False,
        exclude_stock_gem: bool = False,
        exclude_stock_kcb: bool = False,
        **kwargs: Any,
    ) -> None:
        # 本项目扩展参数，父类不识别，需先取出
        self._avg_mode = avg_mode
        self._price_adjust = normalize_mode(price_adjust)
        # 日截面剔除（调仓当日状态，无未来函数）：ST/退市整理按当日 is_st，创/科按代码恒定
        self._exclude_st = bool(exclude_st)
        self._exclude_gem = bool(exclude_stock_gem)
        self._exclude_star = bool(exclude_stock_kcb)
        self._exclude_any = self._exclude_st or self._exclude_gem or self._exclude_star
        self._forbid_by_dt = None  # 调仓日 -> 该日各股是否禁买（由 _mark_forbidden 构建）
        super().__init__(*args, **kwargs)

    def _calc_trade_info_by_order(self, order, position, dealt_order_amount=None):
        """包一层父类：**把冲击成本的"倍数"封顶** ✓（v2.8.5，见 `cap_impact_cost` 的说明 ✓）。

        为什么必须在这一层做：qlib 的成本是**在父类内部**用"本单金额 / 当日成交额"的平方算出来的 ✗，
        外部拿不到中间量 ⇒ 只能"算完再封顶" ✓（只改成本、不动成交量裁剪/整手/涨跌停那些判定 ✓）。

        ⚠ 只在"单笔金额 > 当日成交额"时生效 ⇒ **正常回测逐位不变** ✓（这也让它成为"
          防爆炸"的安全网，而不是改口径 ✗）。
        ⚠ 取不到成交量/价格 ⇒ **保持父类结果** ✓（绝不为了"防爆炸"把回测搞崩 ✗）。
        """
        price, val, cost = super()._calc_trade_info_by_order(
            order, position, dealt_order_amount if dealt_order_amount is not None else {})
        try:
            turnover = float(self.get_volume(order.stock_id, order.start_time,
                                             order.end_time)) * float(price)
            base = float(self.close_cost if order.direction == Order.SELL else self.open_cost)
            new_cost = cap_impact_cost(val, turnover, base, float(self.impact_cost or 0.0),
                                       float(self.min_cost or 0.0))
            if new_cost is not None:
                cost = new_cost
        except Exception as e:  # noqa: BLE001
            _log_swallowed("board_exchange.py", e)
            pass
        return price, val, cost

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
        # 日截面剔除掩码（需在 quote 列就绪后构建；依赖订阅字段含 $is_st，见 qlib_engine）
        if self._exclude_any:
            self._mark_forbidden()

    def _update_limit(self, limit_threshold: Union[tuple, float, None]) -> None:
        # 用户显式传表达式（tuple）或 None（不限涨跌停）时，维持父类行为
        if limit_threshold is None or isinstance(limit_threshold, tuple):
            return super()._update_limit(limit_threshold)

        # 涨跌停判定用【真实价】列 $close_real（= $close/$factor）与 $change（真实价涨跌幅），
        # 两者同源，除权日也精确（涨停价由交易所按真实价确定，与复权无关）。
        # 注意：本方法在 qlib 父类 __init__ 里先于 BoardAwareExchange.get_quote_from_qlib()
        # 对 $close_real 的注入执行（super().get_quote_from_qlib() 内部触发），因此这里必须
        # **自足注入真实价列**，否则会回退用后复权 $close 去比真实价涨跌停标签，
        # 造成 limit_sell 近乎全 True（卖出全锁死）、limit_buy 近乎全 False（买入畅通）→ 只买不卖。
        if "$close_real" not in self.quote_df.columns:
            self.quote_df["$close_real"] = self.quote_df["$close"] / self.quote_df["$factor"].fillna(1.0)
        close_col = "$close_real"
        # $close_real 为 NaN 表示停牌/无行情，不可交易（涨跌停判定对 NaN 返回 False，单独并上）
        suspended = self.quote_df[close_col].isna()
        self.quote_df["limit_buy"] = mark_limit_up(
            self.quote_df, close_col, "$change", base=float(limit_threshold)
        ) | suspended
        self.quote_df["limit_sell"] = mark_limit_down(
            self.quote_df, close_col, "$change", base=float(limit_threshold)
        ) | suspended
        # 日截面剔除的股票买入侧直接禁（卖出不受限）：check_order BUY 会被拒
        if "forbidden" in self.quote_df.columns:
            self.quote_df["limit_buy"] = self.quote_df["limit_buy"] | self.quote_df["forbidden"]

    # ------------------------------------------------------------------
    # 日截面剔除：ST/退市整理（当日 is_st）+ 创业板 + 科创板
    # 语义与单因子测试的"剔除 ST(T+1)/创/科"一致；回测以调仓当日状态为准。
    # 除买入侧禁 buy 外，还为策略提供 get_forbidden_mask，让 TopK 候选直接剔除这些股，
    # 避免 topk 位置被禁买股占据导致现金空转；已持仓的这类股票也会随调仓卖出。
    # ------------------------------------------------------------------

    def _mark_forbidden(self) -> None:
        """在 quote 上构建 forbidden 列（当日是否被日截面剔除），并按调仓日分组缓存。"""
        df = self.quote_df
        forbid = pd.Series(False, index=df.index)
        if self._exclude_st and "$is_st" in df.columns:
            forbid = forbid | (df["$is_st"].fillna(0.0) > 0.5)
        if self._exclude_gem or self._exclude_star:
            codes = df.index.get_level_values(0).astype(str).str.upper()
            if self._exclude_gem:
                forbid = forbid | codes.str.startswith("SZ30")
            if self._exclude_star:
                forbid = forbid | codes.str.startswith("SH688")
        df["forbidden"] = forbid
        # 按调仓日分组：get_forbidden_mask 按日 O(1) 取当日禁买集合。
        # 注意分组后每组 Series 的 index 仍是 (instrument, datetime) 双层，须去掉 datetime 层，
        # 否则 get_forbidden_mask 用单层 instrument 代码 reindex 全部 miss → fill_value=True 全禁 → 空仓。
        lvl_dt = None
        for i, nm in enumerate(df.index.names):
            if nm == "datetime":
                lvl_dt = i
                break
        groups = df["forbidden"].groupby(level=lvl_dt if lvl_dt is not None else -1)
        self._forbid_by_dt = {
            k: (v.droplevel(lvl_dt) if lvl_dt is not None else v) for k, v in groups
        }

    def get_forbidden_mask(self, stock_ids, start_time, end_time=None):
        """给定候选股票与调仓时间，返回当日是否被日截面剔除的 Series(bool)。

        无剔除开关/无当日数据时返回全 False（不改变选股）；当日无该股行情视为禁（True），
        由策略在取 TopK 前过滤候选。stock_ids 需与 quote 的 instrument 代码格式一致。
        """
        ids = list(stock_ids)
        if not ids or not self._exclude_any or not self._forbid_by_dt:
            return pd.Series(False, index=ids)
        try:
            dt = pd.Timestamp(start_time)
        except Exception as e:
            _log_swallowed("board_exchange.py", e)
            return pd.Series(False, index=ids)
        col = self._forbid_by_dt.get(dt)
        if col is None:
            return pd.Series(False, index=ids)
        try:
            return col.reindex(ids, fill_value=True).astype(bool)
        except Exception as e:
            _log_swallowed("board_exchange.py", e)
            return pd.Series(False, index=ids)
