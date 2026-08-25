# -*- coding: utf-8 -*-
"""
Qlib 数据源实现。

基于本地 Qlib 环境读取 cn_data（日线）数据。
注意：Qlib 数据本身只有日线行情 + 复权因子，不含分钟/财报/行业/指数成分，
因此这些方法会抛 DataNotAvailableError，以提示需要切换到 rqalpha 等其他数据源。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

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
        self._D = None
        self._lazy_init()

    def _lazy_init(self):
        """惰性初始化 Qlib（首次使用时才加载，避免拖慢服务启动）"""
        if self._qlib is None:
            import qlib
            from qlib.constant import REG_CN
            from qlib.data import D

            region_map = {"cn": REG_CN}
            qlib.init(
                provider_uri=self.provider_uri,
                region=region_map.get(self.region, REG_CN),
            )
            self._qlib = qlib
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
        self._lazy_init()
        D = self._D
        fields = ["$open", "$high", "$low", "$close", "$volume", "$amount"]
        if adjust in ("qfq", "hfq"):
            fields.append("$factor")
        df = D.features([instrument], fields, start_time=start_date, end_time=end_date)
        if df is None or df.empty:
            return []
        bars: List[DailyBar] = []
        for idx, row in df.iterrows():
            # idx 形如 (instrument, datetime)
            inst, ts = idx
            factor = float(row.get("$factor", 1.0)) if "$factor" in df.columns else 1.0
            bars.append(
                DailyBar(
                    instrument=inst,
                    datetime=ts.to_pydatetime(),
                    open=float(row["$open"]),
                    high=float(row["$high"]),
                    low=float(row["$low"]),
                    close=float(row["$close"]),
                    volume=float(row["$volume"]),
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
