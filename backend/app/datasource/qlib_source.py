# -*- coding: utf-8 -*-
"""
Qlib 数据源实现。

基于本地 Qlib 环境读取 cn_data（日线）数据。
注意：Qlib 数据本身只有日线行情 + 复权因子，不含分钟/财报/行业/指数成分，
因此这些方法会抛 DataNotAvailableError，以提示需要切换到 rqalpha 等其他数据源。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from .base import (
    DataSource,
    DailyBar,
    FinancialData,
    IndustryData,
    IndexConstituent,
    MinuteBar,
    DataNotAvailableError,
)


class QlibDataSource(DataSource):
    """基于 Qlib 本地数据的日线行情数据源"""

    name = "qlib"

    def __init__(self, provider_uri: Optional[str] = None, region: str = "cn"):
        self.provider_uri = provider_uri
        self.region = region
        self._qlib = None
        # ★ 2026-10-20（mypy `attr-defined`）：这里先给 `None` ✓，lazy init 后才换成 `qlib.data.D` ✓
        #   ⇒ 不标注时 mypy 把它**冻结成 `None` 类型** ✗ ⇒ 下面 `D.features(...)` /
        #   `D.list_instruments(...)` / `D.calendar(...)` 全被报"`None` 没有该属性"✓。
        #   标 `Any` ✓：它的类型**依赖运行时是否装了 qlib** ✓，静态上无从确定 ✓。
        self._D: Any = None
        self._lazy_init()

    def _lazy_init(self):
        """惰性初始化 Qlib（首次使用时才加载，避免拖慢服务启动）。

        必须走统一入口（带 custom_ops）：否则 qlib.init() 的 reset 会把
        C.custom_ops 清空，导致 joblib worker 无法注册 DYN_* 等外挂算子，
        单因子/回测特征计算报 "operator [DYN_COUNT] is not registered"。
        """
        if self._qlib is None:
            from qlib.data import D

            from ..services.qlib_runtime import ensure_qlib_init

            ensure_qlib_init(self.provider_uri)
            self._qlib = __import__("qlib")
            self._D = D

    @property
    def capabilities(self):
        # Qlib 数据只有日线，其余数据需 rqalpha 提供
        return {
            "daily": True,
            "minute": False,
            "financial": False,
            "industry": False,
            "index_constituent": False,
        }

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def get_daily_bars(
        self,
        instrument: str,
        start_date: str,
        end_date: str,
        adjust: str = "none",
    ) -> List[DailyBar]:
        """日线（★ T4 / v2.8.7：`adjust` **真的生效** ✗→✓，口径与 `engine/adjust.py` 同一套 ✓）。

        审计 ⑩ 原文：旧实现 `if adjust in ("qfq", "hfq"): fields.append("$factor")` ✗ ——
        **只多取了个 `$factor` 字段**，价格原样返回 ✗ ⇒ `none`/`qfq`/`hfq` 三种取值
        返回的价格**完全相同** ✗✓（而调用方 `routers/data.py` 照原样透传 ⇒ 误导 ✓）。

        现在（数据事实见 `engine/adjust.py` 模块 docstring ✓）：
          · `none`（真实价 / 东财"不复权"口径 ✓）：OHLC = 原生价 / `$factor` ✓；
          · `hfq`（后复权 ✓）：OHLC = 原生价（qlib 原生即后复权 ✓，含分红送转 ✓）；
          · `qfq`（前复权 ✗）：**本数据源还不支持** ⇒ 抛 `DataNotAvailableError`（HTTP 400 ✓）——
            ⚠ 为什么不是"凑一个" ✗：真正的前复权要除以**每股最新因子**，而这一步在**引擎侧也还没落地**
            （`adjust_expr` 里写着 TODO ✗）⇒ 硬凑会和回测口径分叉 ✗（比报错更糟 ✗）。
        返回值里始终带 `factor`（= 当日因子 ✓），调用方想自己换算也有原料 ✓。
        """
        self._lazy_init()
        D = self._D
        mode = (adjust or "none").lower()
        if mode in ("forward", "qfq"):
            raise DataNotAvailableError(
                "本数据源暂不支持前复权（qfq）：引擎侧的『除以每股最新因子』还没落地 ✗ ⇒ "
                "请用 adjust=none（真实价 / 不复权口径 ✓）或 adjust=hfq（后复权 ✓），"
                "并用返回的 factor 自行换算 ✓。")
        if mode not in ("none", "hfq", "backward"):
            raise DataNotAvailableError(
                "未知的 adjust=%r：只支持 none（真实价 ✓）/ hfq（后复权 ✓）。" % adjust)
        divide = mode == "none"                      # ★ none ⇒ 真实价 = 原生价 / factor ✓
        fields = ["$open", "$high", "$low", "$close", "$volume", "$amount", "$factor"]
        df = D.features([instrument], fields, start_time=start_date, end_time=end_date)
        if df is None or df.empty:
            return []
        bars: List[DailyBar] = []
        for idx, row in df.iterrows():
            # idx 形如 (instrument, datetime)
            inst, ts = idx
            raw_factor = row.get("$factor")
            factor = float(raw_factor) if raw_factor == raw_factor else 1.0     # NaN ⇒ 1.0 ✓
            k = factor if (divide and factor > 0) else 1.0
            bars.append(
                DailyBar(
                    instrument=inst,
                    datetime=ts.to_pydatetime(),
                    open=float(row["$open"]) / k,
                    high=float(row["$high"]) / k,
                    low=float(row["$low"]) / k,
                    close=float(row["$close"]) / k,
                    volume=float(row["$volume"]),        # 量/额不随复权变化 ✓
                    amount=float(row["$amount"]),
                    factor=factor,
                )
            )
        return bars

    def get_minute_bars(
        self,
        instrument: str,
        start_dt: str,
        end_dt: str,
        freq: str = "1min",
        adjust: str = "none",
    ) -> List[MinuteBar]:
        raise DataNotAvailableError("Qlib 本地数据不包含分钟数据，请使用 rqalpha 数据源")

    # ------------------------------------------------------------------
    # 基本面 / 行业 / 指数（Qlib 不支持）
    # ------------------------------------------------------------------

    def get_financial_data(
        self, instrument: str, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> List[FinancialData]:
        raise DataNotAvailableError("Qlib 本地数据不包含财报数据，请使用 rqalpha 数据源")

    def get_industry(self, instrument: str) -> List[IndustryData]:
        raise DataNotAvailableError("Qlib 本地数据不包含行业分类，请使用 rqalpha 数据源")

    def get_index_constituents(self, index_code: str) -> List[IndexConstituent]:
        raise DataNotAvailableError("Qlib 本地数据不包含指数成分，请使用 rqalpha 数据源")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def list_instruments(self, market: str = "all") -> List[str]:
        self._lazy_init()
        D = self._D
        inst = D.list_instruments(D.instruments(market=market), as_list=True)
        # 转成小写统一格式 sz000001 / sh600000
        return [str(i).lower() for i in inst]

    def get_calendar(self, start_date: str, end_date: str) -> List[date]:
        self._lazy_init()
        cal = self._D.calendar(start_time=start_date, end_time=end_date)
        return [ts.date() for ts in cal]
