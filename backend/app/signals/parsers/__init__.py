# -*- coding: utf-8 -*-
"""CSV 解析层：自动识别「信号清单」与「聚宽成交明细」两种格式，产出统一中间结构。"""
from __future__ import annotations

from .base import (                                     # noqa: F401
    ParseResult,
    decode_bytes,
    parse_csv,
    detect_kind,
    SIDE_ALIASES,
)
from .signal_list import parse_signal_list              # noqa: F401
from .jq_trades import parse_jq_trades                  # noqa: F401

__all__ = ["ParseResult", "decode_bytes", "parse_csv", "detect_kind",
           "SIDE_ALIASES", "parse_signal_list", "parse_jq_trades"]
