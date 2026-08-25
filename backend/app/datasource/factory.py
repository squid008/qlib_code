# -*- coding: utf-8 -*-
"""
数据源工厂：统一管理数据源的创建与按需切换。

设计：
- 默认使用 Qlib 数据源（已可用）
- rqalpha 数据源预留，能力一旦实现即可通过配置开启
- 前端/其他模块通过 get_data_source(name) 获取指定数据源，
  通过 capabilities 判断该数据源是否支持某种数据类型
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from .base import DataSource
from .qlib_source import QlibDataSource
from .rqalpha_source import RQAlphaDataSource


# 默认 Qlib provider_uri，可通过环境变量覆盖
DEFAULT_QLIB_URI = os.environ.get(
    "QLIB_PROVIDER_URI",
    os.path.expanduser(r"~/.qlib/qlib_data/cn_data"),
)
DEFAULT_RQALPHA_BUNDLE = os.environ.get(
    "RQALPHA_BUNDLE_PATH", None
)


class DataSourceFactory:
    """数据源工厂（简单注册表模式）"""

    def __init__(self):
        self._registry: Dict[str, DataSource] = {}
        self._build()

    def _build(self):
        self._registry["qlib"] = QlibDataSource(provider_uri=DEFAULT_QLIB_URI)
        # rqalpha 预留：等实现接入后，把 lazy 初始化改为真正创建
        if DEFAULT_RQALPHA_BUNDLE:
            self._registry["rqalpha"] = RQAlphaDataSource(bundle_path=DEFAULT_RQALPHA_BUNDLE)

    def get(self, name: str = "qlib") -> DataSource:
        """获取数据源，默认 qlib。name 不存在时抛 KeyError。"""
        if name not in self._registry:
            raise KeyError(
                f"数据源 '{name}' 不存在或未启用。可用: {list(self._registry.keys())}"
            )
        return self._registry[name]

    def list(self) -> Dict[str, Dict[str, bool]]:
        """列出所有数据源及其能力声明"""
        return {name: ds.capabilities for name, ds in self._registry.items()}


# 全局单例
_factory: Optional[DataSourceFactory] = None


def get_data_source(name: str = "qlib") -> DataSource:
    global _factory
    if _factory is None:
        _factory = DataSourceFactory()
    return _factory.get(name)


def list_data_sources() -> Dict[str, Dict[str, bool]]:
    global _factory
    if _factory is None:
        _factory = DataSourceFactory()
    return _factory.list()
