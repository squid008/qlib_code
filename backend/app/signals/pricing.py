# -*- coding: utf-8 -*-
"""价格 / 涨跌停 / 停牌 面板的取数层（交易信号测试专用）。

三条硬约束（都由项目既有实现与实测经验定死）：
  ① **必须走 `factors/panel_expr.panel_features`**，不要用 `qlib D.features` —— 后者每次调用
     有 ~2.2s/只的固定开销（`benchmark_curves.load_bench_close` 的注释里有实测表：48ms vs 11.3s）；
  ② 结果按**内容**缓存到 `engine/feature_cache`（key 含股票池/表达式/区间）⇒ 同参数第二次秒开；
  ③ **不按位置贴列名** —— `panel_features` 返回的列顺序不保证与入参一致（2026-09-15 踩过），
     一律按名字取。

价格口径（用户要"前复权"）：`$close` 是数据原生**后复权**价 —— 同一序列后/前复权只差常数比例，
**收益率逐位相同**且天然含分红；`$close/$factor` 是**不复权真实价**，用来判停牌与涨跌停
（停牌 = 真实价 NaN；涨跌停由交易所按真实价确定，与复权无关）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

FIELDS = (
    ("$close", "CLOSE"),            # 后复权收盘（= 前复权收益率口径）
    ("$open", "OPEN"),              # 后复权开盘
    ("$close/$factor", "CLOSE_RAW"),  # 不复权真实价（停牌 = NaN）
    ("$open/$factor", "OPEN_RAW"),
    ("$change", "CHANGE"),
    ("$limit_up", "LIMIT_UP"),      # ⚠ 是**涨停价**（价格），不是 0/1 标志
    ("$limit_down", "LIMIT_DOWN"),
)


def _ensure_init() -> None:
    """qlib 全局初始化（**必须走统一入口**：进程内只 init 一次 + 每次都校验 custom_ops）。

    ⚠ 不初始化直接调 `panel_features` 会抛 `AttributeError: Please run qlib.init() first`
      （本模块 2026-09-15 端到端自检时踩到）。
    """
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()


def resolve_universe(universe: str, start: str, end: str) -> List[str]:
    """股票池 → 成分股**并集**（与单因子测试同口径：区间内曾在池中的都算）。

    `universe` 支持 qlib 市场名：`all` / `csi300` / `csi500` / `csi800` / `csi1000` / `csiall`。
    """
    from qlib.data import D

    try:
        insts = D.list_instruments(D.instruments(market=str(universe or "all")),
                                   start_time=start, end_time=end, as_list=True)
    except Exception:
        return []
    return sorted(str(i).upper() for i in insts)


def _wide(pdf: pd.DataFrame, name: str, ffill: bool = True) -> Optional[pd.DataFrame]:
    """按**列名**取一列并转宽表（index=交易日升序 DatetimeIndex, columns=标的）。"""
    if pdf is None or not len(pdf) or name not in pdf.columns:
        return None
    try:
        s = pdf[name]
        lvl = s.index.names.index("instrument")
        w = s.unstack(level=lvl).sort_index()
    except Exception:
        return None
    w.index = pd.DatetimeIndex(w.index)
    return w.ffill() if ffill else w


def load_price_panel(codes: Sequence[str], start: str, end: str,
                     need_open: bool = True, cancelled=None) -> Dict[str, pd.DataFrame]:
    """取「后复权收盘/开盘 + 真实价 + 涨跌停价」宽表。

    返回 dict：`CLOSE`/`OPEN`（ffill，停牌冻结）、`CLOSE_RAW`/`OPEN_RAW`（**不 ffill**，
    NaN 即停牌）、`LIMIT_UP`/`LIMIT_DOWN`（可能缺失 ⇒ 调用方回退用 `limit_ratio` 推算）、
    `CALENDAR`（交易日历，含基准指数的完整日历）。
    """
    from app.engine.limits import field_bin_available
    from app.factors.panel_expr import panel_features

    _ensure_init()
    want = [str(c).upper() for c in codes]
    if not want:
        return {}
    # 日历：借基准指数（沪深300）拿到完整交易日，避免"当日所有信号股都停牌 ⇒ 日历缺日"
    cal_codes = ["SH000300"] if "SH000300" not in want else []
    fields = list(FIELDS if need_open else [f for f in FIELDS if "OPEN" not in f[1]])
    if not field_bin_available("limit_up"):
        fields = [f for f in fields if not f[1].startswith("LIMIT_")]
    all_codes = want + cal_codes
    # ---- 磁盘缓存（"秒出"的关键：同池同区间第二次直接命中）----
    pdf = None
    cache_path = None
    try:
        from app.engine.feature_cache import _cache_path, _load_cache, _save_cache

        cache_path = _cache_path(all_codes, [e for e, _ in fields], [n for _, n in fields],
                                 start, end,
                                 extra="signals|v1|%s" % ("open" if need_open else "noopen"))
        pdf = _load_cache(cache_path)
    except Exception:
        cache_path = None
    if pdf is None:
        pdf = panel_features(all_codes, fields, start, end, cancel_cb=cancelled, warmup_days=0)
        if pdf is not None and len(pdf) and cache_path:
            try:
                _save_cache(cache_path, pdf)
            except Exception:
                pass                                   # 缓存写失败不影响主流程
    if pdf is None or not len(pdf):
        return {}
    out: Dict[str, pd.DataFrame] = {}
    for _, name in fields:
        if name in ("CLOSE_RAW", "OPEN_RAW"):
            continue
        w = _wide(pdf, name, ffill=(name in ("CLOSE", "OPEN", "CHANGE")))
        if w is not None:
            out[name] = w
    for _, name in fields:
        if name in ("CLOSE_RAW", "OPEN_RAW"):
            w = _wide(pdf, name, ffill=False)
            if w is not None:
                out[name] = w
    if "CLOSE_RAW" not in out and "CLOSE" in out:
        out["CLOSE_RAW"] = out["CLOSE"].copy()      # 无真实价列时退化为复权价（停牌判定变弱）
        out["_raw_fallback"] = pd.DataFrame()
    # ⚠ 借来的日历（SH000300）**不是**池成员：留在宽表里会污染「未触发组」基准
    #   （实测 @signals 池显示 336 只 = 335 只信号股 + 1 个指数，2026-09-15 抓到）。
    extra = set(cal_codes)
    if extra:
        for k in ("CLOSE", "OPEN", "CLOSE_RAW", "OPEN_RAW", "CHANGE", "LIMIT_UP", "LIMIT_DOWN"):
            w = out.get(k)
            if w is not None:
                keep = [c for c in w.columns if c not in extra]
                if len(keep) != w.shape[1]:
                    out[k] = w[keep]
    # 日历：优先基准指数那一列（SH000300），否则用全体收盘的并集
    cal = None
    if "CLOSE" in out and "SH000300" in out["CLOSE"].columns:
        cal = out["CLOSE"].index
    if cal is None:
        cal = out["CLOSE"].index if "CLOSE" in out else None
    if cal is not None:
        out["CALENDAR"] = pd.DataFrame(index=pd.DatetimeIndex(cal))
    return out


def fill_limits(panel: Dict[str, pd.DataFrame], strict: bool = True) -> Dict[str, pd.DataFrame]:
    """涨跌停价兜底：数据无交易所标签时，用 `limit_ratio` × 昨收（真实价）推算。

    ⚠ 推算版不识别 ST 5% 涨跌停（与 `engine/limits.py` 的文档一致），仅作无标签源的兜底；
    有标签时一律用标签（金额级差异：`$limit_up` 是价格 ⇒ 判涨停 = `close_raw >= limit_up − 1e-6`
    且 `limit_up > 0`）。
    """
    if not strict or ("LIMIT_UP" in panel and "LIMIT_DOWN" in panel):
        return panel
    from app.engine.limits import limit_ratio

    raw = panel.get("CLOSE_RAW")
    if raw is None:
        return panel
    prev = raw.shift(1)
    for code in raw.columns:
        r = float(limit_ratio(code, 0.10))
        up, dn = prev[code] * (1.0 + r), prev[code] * (1.0 - r)
        if "LIMIT_UP" not in panel:
            panel["LIMIT_UP"] = pd.DataFrame(index=raw.index)
        if "LIMIT_DOWN" not in panel:
            panel["LIMIT_DOWN"] = pd.DataFrame(index=raw.index)
        panel["LIMIT_UP"][code] = np.round(up, 2)
        panel["LIMIT_DOWN"][code] = np.round(dn, 2)
    panel["_limits_inferred"] = pd.DataFrame()
    return panel


def load_bench_wide(codes: Sequence[str], start: str, end: str, cancelled=None) -> Optional[pd.DataFrame]:
    """基准指数收盘宽表（复用 `factors/benchmark_curves.load_bench_close`，与单因子测试同源）。"""
    from app.factors.benchmark_curves import load_bench_close

    _ensure_init()
    return load_bench_close([str(c) for c in codes], start, end, cancelled)
