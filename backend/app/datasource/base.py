# -*- coding: utf-8 -*-
"""
数据源统一数据结构与抽象接口定义。

这是整个数据服务层的中枢：
1. 定义了所有数据类型的统一 Pydantic 模型（日线、分钟、财报、行业、指数成分）
2. 定义了 DataSource 抽象基类，任何数据源（Qlib / rqalpha-h5 / 其他）都必须实现它

后续接入 rqalpha 的 h5 数据时，只需：
    - 新增 rqalpha_source.py，继承 DataSource 并实现各方法
    - 在 DataSourceFactory 中注册该数据源
"""
from __future__ import annotations

import abc
import datetime as _dt
from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# =====================================================================
# 统一数据结构
# =====================================================================

class BarData(BaseModel):
    """行情K线基类（日线/分钟通用字段）"""
    instrument: str = Field(..., description="证券代码，统一格式如 sz000001")
    datetime: _dt.datetime = Field(..., description="K线时间（日线为当日，分钟为K线结束时刻）")
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    factor: float = 1.0  # 复权因子


class DailyBar(BarData):
    """日线数据"""


class MinuteBar(BarData):
    """分钟数据（预留：rqalpha 支持 1/5/15/30/60 分钟）"""
    freq: str = Field("1min", description="分钟频率，如 1min/5min/60min")


class FinancialData(BaseModel):
    """财报数据（预留：rqalpha 支持资产负债表/利润表/现金流量表）"""
    instrument: str
    report_date: date = Field(..., description="报告期")
    announce_date: Optional[date] = Field(None, description="公告日期")
    # 利润表
    revenue: Optional[float] = None          # 营业收入
    operating_profit: Optional[float] = None  # 营业利润
    net_profit: Optional[float] = None        # 净利润
    # 资产负债表
    total_assets: Optional[float] = None      # 总资产
    total_liabilities: Optional[float] = None # 总负债
    equity: Optional[float] = None            # 股东权益
    # 常用财务指标（可由上计算或直接提供）
    eps: Optional[float] = None               # 每股收益
    roe: Optional[float] = None               # 净资产收益率
    margin: Optional[float] = None            # 毛利率


class IndustryData(BaseModel):
    """行业分类数据（预留：rqalpha 支持申万/中信等分类）"""
    instrument: str
    industry: str = Field(..., description="行业名称")
    industry_type: str = Field("industry", description="行业分类体系，如 sw1/sw2/industry")
    effective_date: Optional[date] = None


class IndexConstituent(BaseModel):
    """指数成分股（预留：rqalpha 支持沪深300/中证500/上证50等）"""
    instrument: str
    index_code: str = Field(..., description="指数代码，如 000300.SH")
    weight: Optional[float] = None
    effective_date: Optional[date] = None


# =====================================================================
# 异常定义
# =====================================================================

class DataSourceError(Exception):
    """数据源通用错误"""


class DataNotAvailableError(DataSourceError):
    """请求的数据在当前数据源中不可用"""


# =====================================================================
# 数据源抽象基类
# =====================================================================

class DataSource(abc.ABC):
    """
    数据源抽象基类。

    任何数据源必须实现以下所有方法。对于不支持的数据类型，
    应抛 DataNotAvailableError 并说明原因，而不是静默返回空数据。
    """

    name: str = "base"

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_daily_bars(
        self,
        instrument: str,
        start_date: str,
        end_date: str,
        adjust: str = "none",
    ) -> List[DailyBar]:
        """获取日线数据。instrument 统一为如 sz000001 的格式。"""

    @abc.abstractmethod
    def get_minute_bars(
        self,
        instrument: str,
        start_dt: str,
        end_dt: str,
        freq: str = "1min",
        adjust: str = "none",
    ) -> List[MinuteBar]:
        """获取分钟数据。freq 支持 1min/5min/15min/30min/60min。"""

    # ------------------------------------------------------------------
    # 基本面 / 行业 / 指数
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_financial_data(
        self,
        instrument: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FinancialData]:
        """获取财报数据。"""

    @abc.abstractmethod
    def get_industry(self, instrument: str) -> List[IndustryData]:
        """获取行业分类。"""

    @abc.abstractmethod
    def get_index_constituents(self, index_code: str) -> List[IndexConstituent]:
        """获取指数成分股及其权重。"""

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_instruments(self, market: str = "all") -> List[str]:
        """列出可用的证券代码列表。"""

    @abc.abstractmethod
    def get_calendar(self, start_date: str, end_date: str) -> List[date]:
        """获取交易日历。"""

    # 以下为可选能力声明，供调度层判断数据源能力
    capabilities: Dict[str, bool] = {
        "daily": True,
        "minute": False,
        "financial": False,
        "industry": False,
        "index_constituent": False,
    }
