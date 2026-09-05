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
  【创业板号段】SZ300/SZ301/SZ302…全部 20%：深交所注册制扩容后号段沿 30 段后延
  （301 之后启用 302/303/…），不能只匹配 SZ300/SZ301，否则新号段被当主板 10%
  ——用前缀 SZ30 覆盖整段（SZ300000 起的创业板；SZ39 为深证指数，非股票池标的）。
  科创板 SH688（唯一启用号段）；北交所 qlib 代码统一 BJ 前缀。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd


def limit_ratio(code: Union[str, object], base: float = 0.10) -> float:
    """按股票代码返回该股的涨跌停幅度（比例，如 0.20 = 20%）。"""
    c = str(code).upper()
    if c.startswith(("SH688", "SZ30")):
        # 创业板（SZ300/SZ301/SZ302…整段 30 前缀）/ 科创板：20% 涨跌幅
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
    """涨停/跌停价：昨收 * (1 ± 幅度) 四舍五入到分（floor(x*100+0.5)/100 避免 Python round 银行家舍入）。

    交易所与聚宽的涨跌停价都基于【昨收整分价】（round(昨收×幅度,2)）。
    调用前 prev_close 需已 round 到分（见 mark_* 函数），保证与行情软件口径一致。
    """
    return np.floor(prev_close * (1 + ratio) * 100 + 0.5) / 100


def _as_price(s: pd.Series) -> pd.Series:
    """价格按"分"取整（真实价 round 到 2 位）。

    数据源真实价 = $close/$factor 的 float 除法有尾差（如 27.710001 本应 27.71），
    直接与涨停价比较会在整分边界误判；round 到分后与交易所/聚宽的整分价对齐。
    NaN（停牌/无行情）保持 NaN，由调用方 fillna。
    """
    return s.round(2)


_tag_field_cache: dict = {}


def field_bin_available(field: str) -> bool:
    """探测 qlib 数据里是否存在 {field}.day.bin（如 limit_up/limit_down/is_st）。

    用于无 dump 标签的机器（如同事 pull 后没有 E:/rq 同步数据）自动降级：
    缺标签时不再加载这些字段（涨停/跌停回退倒推、ST 剔除提示），避免 D.features/quote 崩。
    结果缓存；探测 qlib 数据根下任一股票目录存在该 bin 即认为全量可用（dump 是全量写入）。
    """
    if field in _tag_field_cache:
        return _tag_field_cache[field]
    ok = False
    try:
        from ..config import QLIB_PROVIDER_URI
        root = QLIB_PROVIDER_URI or ""
        feat = Path(root) / "features" if root else None
        if feat is not None and feat.is_dir():
            for d in os.scandir(feat):
                if d.is_dir() and (Path(d.path) / f"{field}.day.bin").exists():
                    ok = True
                    break
    except Exception:
        ok = False
    _tag_field_cache[field] = ok
    return ok


def _limit_tag(s: pd.DataFrame, close_col: str, tag_kind: str):
    """返回与 close_col 同口径的交易所涨跌停标签列（不存在返回 None）。

    tag_kind: 'LIMIT_UP'/'LIMIT_DOWN'。支持的列名（按优先级）：
      - 单因子当日报表列：LIMIT_UP / LIMIT_DOWN
      - qlib 订阅原始名（回测 quote）：$limit_up / $limit_down
    close_col 以 T1_ 开头时优先取 T1_<kind>（成交日 T+1 的标签）。
    """
    if str(close_col).startswith("T1"):
        candidates = [f"T1_{tag_kind}", f"${tag_kind.lower()}", tag_kind]
    else:
        candidates = [tag_kind, f"${tag_kind.lower()}"]
    for col in candidates:
        if col in s.columns:
            return col
    return None


def mark_limit_up(
    s: pd.DataFrame,
    close_col: str = "CLOSE",
    change_col: str = "CHANGE",
    base: float = 0.10,
) -> pd.Series:
    """收盘是否封住涨停。NaN（停牌/无行情）返回 False。

    优先用交易所标签列（close_col 对应 T1_LIMIT_UP/LIMIT_UP）：close >= limit_up - 1e-6，
    天然覆盖 ST/退市整理/创业科创/北交全部涨跌停规则；无标签列时回退"涨停价四舍五入"倒推：
    昨收 = round(真实价/(1+涨跌幅), 2)，涨停价 = round(昨收×(1+板块幅度), 2)（该口径不识别
    ST 5% 涨停，仅作无标签源的兜底/对账）。
    """
    if s is None or len(s) == 0:
        return pd.Series(dtype=bool)
    tag = _limit_tag(s, close_col, "LIMIT_UP")
    if tag is not None:
        close = _as_price(s[close_col])
        return (close >= s[tag] - 1e-6).fillna(False)
    codes = _codes(s)
    ratios = pd.Series([limit_ratio(c, base) for c in codes], index=s.index)
    close = _as_price(s[close_col])
    prev = _as_price(close / (1 + s[change_col]))
    limit_price = _limit_price(prev, ratios)
    return (close >= limit_price - 1e-6).fillna(False)


def mark_limit_down(
    s: pd.DataFrame,
    close_col: str = "CLOSE",
    change_col: str = "CHANGE",
    base: float = 0.10,
) -> pd.Series:
    """收盘是否封死跌停（优先交易所标签列，口径与 mark_limit_up 对称）。NaN 返回 False。"""
    if s is None or len(s) == 0:
        return pd.Series(dtype=bool)
    tag = _limit_tag(s, close_col, "LIMIT_DOWN")
    if tag is not None:
        close = _as_price(s[close_col])
        return (close <= s[tag] + 1e-6).fillna(False)
    codes = _codes(s)
    ratios = pd.Series([limit_ratio(c, base) for c in codes], index=s.index)
    close = _as_price(s[close_col])
    prev = _as_price(close / (1 + s[change_col]))
    limit_price = _limit_price(prev, -ratios)
    return (close <= limit_price + 1e-6).fillna(False)
