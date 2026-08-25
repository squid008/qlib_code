# -*- coding: utf-8 -*-
"""
数据源抽象层。

设计目标：
- 通过统一的抽象接口屏蔽不同数据源的差异（Qlib / rqalpha-h5 / 其他）
- 支持日线、分钟、财报、行业分类、指数成分等数据类型
- 新数据源只需继承 DataSource 基类并实现对应方法即可接入
"""
from .base import (
    DataSource,
    BarData,
    DailyBar,
    MinuteBar,
    FinancialData,
    IndustryData,
    IndexConstituent,
    DataSourceError,
    DataNotAvailableError,
)

__all__ = [
    "DataSource",
    "BarData",
    "DailyBar",
    "MinuteBar",
    "FinancialData",
    "IndustryData",
    "IndexConstituent",
    "DataSourceError",
    "DataNotAvailableError",
]
