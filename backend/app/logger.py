# -*- coding: utf-8 -*-
"""
统一日志模块。

为整个后端提供统一的 logger 工厂，避免各模块各自裸打 print/裸吞异常。
用法：
    from ..logger import get_logger
    logger = get_logger(__name__)
    logger.warning("...", exc_info=True)
"""
from __future__ import annotations

import logging
import os
import sys

_LEVEL = os.environ.get("QLIB_LOG_LEVEL", "INFO").upper()


def _build_default_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, _LEVEL, logging.INFO))
        # 避免重复打日志到父 logger
        logger.propagate = False
    return logger


def get_logger(name: str = "qlib") -> logging.Logger:
    """获取统一 logger。name 传模块名（如 __name__），用于定位来源。"""
    return _build_default_logger(name)
