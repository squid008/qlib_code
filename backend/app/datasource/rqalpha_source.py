# -*- coding: utf-8 -*-
"""
rqalpha 数据源（预留实现）。

背景：
- rqalpha 的历史数据以 h5 文件形式存储（默认位于 ~/.rqalpha/bundle，由 rqdatac/quantos 导出）。
- h5 数据包含：日线、分钟线（1/5/15/30/60min）、财报（资产负债表/利润表/现金流量表）、
  行业分类（申万/中信）、指数成分及权重等。
- 读取方式通常有两种：
    1. 使用 rqalpha 自带的 `rqalpha.data.H5FileProvider` / `BundleData`
    2. 直接使用 `h5py`/`pandas.HDFStore` 读取 h5 文件

本文件仅定义接口契约和骨架。TODO 标记处需要根据你的实际 h5 文件路径和
表结构补齐实现。

注意：rqalpha h5 的代码格式通常是大写 "SZ000001"/"SH600000"，本层负责统一
转成小写 "sz000001"。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from .base import (
    DataSource,
    DailyBar,
    FinancialData,
    IndustryData,
    IndexConstituent,
    MinuteBar,
)


class RQAlphaDataSource(DataSource):
    """基于 rqalpha h5 数据文件的完整数据源（预留）"""

    name = "rqalpha"

    def __init__(self, bundle_path: Optional[str] = None):
        self.bundle_path = Path(bundle_path) if bundle_path else Path.home() / ".rqalpha" / "bundle"
        self._store = None  # TODO: 后续初始化 h5 数据 store

    @property
    def capabilities(self):
        return {
            "daily": True,
            "minute": True,
            "financial": True,
            "industry": True,
            "index_constituent": True,
        }

    # ------------------------------------------------------------------
    # 内部工具：代码格式转换
    # ------------------------------------------------------------------

    @staticmethod
    def _to_h5_code(instrument: str) -> str:
        """sz000001 -> SZ000001 (rqalpha 大写格式)"""
        code = instrument.upper()
        if code.startswith("SZ"):
            return "SZ" + code[2:]
        if code.startswith("SH"):
            return "SH" + code[2:]
        if code.startswith("BJ"):
            return "BJ" + code[2:]
        return code

    @staticmethod
    def _from_h5_code(code: str) -> str:
        """SZ000001 -> sz000001 (统一小写格式)"""
        return code.lower()

    def _ensure_store(self):
        """惰性初始化 h5 数据连接。TODO: 根据实际 h5 结构实现。"""
        if self._store is None:
            # ------------------------------------------------------------------
            # TODO(rqalpha): 在这里接入你的 h5 数据。
            # 示例：
            #   import pandas as pd
            #   df = pd.read_hdf(self.bundle_path / "stock_day.h5", "stock")
            #   self._store = df
            # 如果是 rqalpha 的 BundleData：
            #   from rqalpha.data.base_data_source import H5FileDataProxy
            #   self._store = H5FileDataProxy(...)
            # ------------------------------------------------------------------
            raise NotImplementedError(
                "rqalpha h5 数据源尚未接入。请在 rqalpha_source.py 的 _ensure_store() "
                "中根据你的 h5 文件结构实现数据读取。"
            )

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def get_daily_bars(self, instrument, start_date, end_date, adjust="none") -> List[DailyBar]:
        self._ensure_store()
        # TODO: 从 h5 读取日线
        raise NotImplementedError("rqalpha 日线读取待实现")

    def get_minute_bars(self, instrument, start_dt, end_dt, freq="1min", adjust="none") -> List[MinuteBar]:
        self._ensure_store()
        # TODO: 从 h5 读取分钟线
        raise NotImplementedError("rqalpha 分钟线读取待实现")

    # ------------------------------------------------------------------
    # 基本面 / 行业 / 指数
    # ------------------------------------------------------------------

    def get_financial_data(self, instrument, start_date=None, end_date=None) -> List[FinancialData]:
        self._ensure_store()
        # TODO: 从 h5 读取财报（资产负债表/利润表/现金流量表）
        raise NotImplementedError("rqalpha 财报读取待实现")

    def get_industry(self, instrument) -> List[IndustryData]:
        self._ensure_store()
        # TODO: 从 h5 读取行业分类
        raise NotImplementedError("rqalpha 行业分类读取待实现")

    def get_index_constituents(self, index_code) -> List[IndexConstituent]:
        self._ensure_store()
        # TODO: 从 h5 读取指数成分及权重
        raise NotImplementedError("rqalpha 指数成分读取待实现")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def list_instruments(self, market="all") -> List[str]:
        self._ensure_store()
        # TODO: 列出 h5 中所有证券代码
        raise NotImplementedError("rqalpha 证券列表读取待实现")

    def get_calendar(self, start_date, end_date) -> List[date]:
        self._ensure_store()
        # TODO: 读取交易日历
        raise NotImplementedError("rqalpha 交易日历读取待实现")
