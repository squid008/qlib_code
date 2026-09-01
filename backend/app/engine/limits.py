# -*- coding: utf-8 -*-
"""涨跌停判定公共工具：涨停价四舍五入口径，单因子测试与回测 Exchange 共用。

判定方法（A 股涨停价规则）：
  涨停价 = round(昨收 * (1 + 板块涨停幅度), 2)   （四舍五入到分）
  收盘价 >= 涨停价 - 1e-6  →  封住涨停（买不到）
  跌停价 = round(昨收 * (1 - 板块涨停幅度), 2)
  收盘价 <= 跌停价 + 1e-6  →  封死跌停（卖不出）
昨收由 close / (1 + change) 反推。

板块幅度（base 为阈值基准，默认 10%）：
  主板 base（10%）、创业板/科创板 base*2（20%）、北交所 base*3（30%）
"""
from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd


def limit_ratio(code: Union[str, object], base: float = 0.10) -> float:
    """按股票代码返回该股的涨跌停幅度（比例，如 0.20 = 20%）。"""
    c = str(code).upper()
    if c.startswith(("SH688", "SZ300", "SZ301")):
        # 创业板 / 科创板：20% 涨跌幅
        return base * 2.0
    if c.startswith("BJ"):
        # 北交所：30% 涨跌幅
        return base * 3.0
    # 主板：10% 涨跌幅
    return base


def _codes(s: pd.DataFrame) -> pd.Series:
    """从 index 提取每行的股票代码；单标的（DatetimeIndex）视为 "" → 主板 base。"""
    if isinstance(s.index, pd.MultiIndex):
        return s.index.get_level_values(0).astype(str)
    return pd.Series([""] * len(s), index=s.index)


def _limit_price(prev_close, ratio) -> np.ndarray:
    """涨停/跌停价：昨收 * (1 ± 幅度) 四舍五入到分（floor(x*100+0.5)/100 避免 Python round 银行家舍入）。"""
    return np.floor(prev_close * (1 + ratio) * 100 + 0.5) / 100


def mark_limit_up(
    s: pd.DataFrame,
    close_col: str = "CLOSE",
    change_col: str = "CHANGE",
    base: float = 0.10,
) -> pd.Series:
    """收盘是否封住涨停（涨停价四舍五入口径）。NaN（停牌/无行情）返回 False。"""
    if s is None or len(s) == 0:
        return pd.Series(dtype=bool)
    codes = _codes(s)
    ratios = pd.Series([limit_ratio(c, base) for c in codes], index=s.index)
    prev = s[close_col] / (1 + s[change_col])
    limit_price = _limit_price(prev, ratios)
    return (s[close_col] >= limit_price - 1e-6).fillna(False)


def mark_limit_down(
    s: pd.DataFrame,
    close_col: str = "CLOSE",
    change_col: str = "CHANGE",
    base: float = 0.10,
) -> pd.Series:
    """收盘是否封死跌停（跌停价四舍五入口径）。NaN（停牌/无行情）返回 False。"""
    if s is None or len(s) == 0:
        return pd.Series(dtype=bool)
    codes = _codes(s)
    ratios = pd.Series([limit_ratio(c, base) for c in codes], index=s.index)
    prev = s[close_col] / (1 + s[change_col])
    limit_price = _limit_price(prev, -ratios)
    return (s[close_col] <= limit_price + 1e-6).fillna(False)
