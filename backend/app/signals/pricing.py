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

def _src_fp() -> str:
    """取价链路的**源码指纹**（v2.1.61 ✓）—— 拼进磁盘缓存 key ✓（改口径 ⇒ 自动失效 ✓）。

    为什么要它（用户 2026-10-12："信号测试是不是有缓存功能？没有加版本的哈希校验？" ✓）：
      原先这两处缓存只用**手工版本号**（`_POOL_WIDE_VERSION="v1"` / `"signals|v1"` ✗）
      ⇒ 改了取价口径但忘了 +1，就会把旧宽表当新的返回 ✗✗（"口径漂移 + 看不见" ✓）。
      与 `single_test.py` v1.19.97 的做法一致（源码 mtime+size ✓，不会忘 ✓）。
    """
    try:
        from app.engine.code_fp import code_fingerprint
        return code_fingerprint("app/signals/pricing.py", "app/engine/feature_cache.py")
    except Exception as e:  # noqa: BLE001
        _log_swallowed("pricing.py", e)
        return "na"


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

    ⚠ v1.19.48：这里**必须自己做 qlib 初始化** —— 原来依赖调用方先 init，单独调用时
      `D.list_instruments` 会**静默返回空池**（2026-09-15 我写校验脚本时踩到：
      池 = 0 只，还看不出原因）。
    """
    from qlib.data import D

    _ensure_init()

    try:
        insts = D.list_instruments(D.instruments(market=str(universe or "all")),
                                   start_time=start, end_time=end, as_list=True)
    except Exception as e:
        _log_swallowed("pricing.py", e)
        return []
    # ★★ v2.1.60：**与单因子测试面板同口径** ✗→✓（用户 2026-10-12，根因实测 ✓）
    #   事件研究的「基准（未触发组）」池在本函数取列 ✓，而单因子测试是 `single_test.py:117`
    #   的 `_resolve_instruments` 取的 ✓ —— 它**排除北交所 `BJ…` 与指数代码**
    #   （`SH000` / `SH88` / `SH89` / `SZ39` ✓ 见 126-134 行 ✓），本函数原来**不排** ✗
    #   ⇒ 同一份事件、同一个函数、同样的配对日（145 ✓），只有**池的列集**不同 ⇒
    #     基准均值 1.9466%（我 ✗）vs 1.5250%（弹窗/秒开 ✓）⇒ 超额随之 4.0369% vs 4.4585% ✗。
    #   ⇒ 这里按**同一套规则**过滤 ✓（口径必须一致，否则"两个页面两个数" ✓）。
    out: List[str] = []
    for i in insts:
        c = str(i).upper()
        if c.startswith("BJ"):
            continue
        if c.startswith(("SH000", "SH88", "SH89", "SZ39")):
            continue
        out.append(c)
    return sorted(out)


def _wide(pdf: pd.DataFrame, name: str, ffill: bool = True) -> Optional[pd.DataFrame]:
    """按**列名**取一列并转宽表（index=交易日升序 DatetimeIndex, columns=标的）。"""
    if pdf is None or not len(pdf) or name not in pdf.columns:
        return None
    try:
        s = pdf[name]
        lvl = s.index.names.index("instrument")
        w = s.unstack(level=lvl).sort_index()
    except Exception as e:
        _log_swallowed("pricing.py", e)
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
    # ★★ v2.1.76：**ST 标签也一起取** ✗→✓（用户 2026-10-12 的审计待办 ③ ✓）——
    #   回灌侧要执行「剔除成交日(T+1) ST」就必须有 `$is_st` ✓（此前本侧**完全取不到** ✗
    #   ⇒ 勾了也白勾、只给一句 warning ✓，与面板口径分叉 ✓）。
    #   ⚠ 只在**有 dump 标签**的机器上加 ✓（`tools/dump_states.py` 同步过才有 ✓），
    #     没有就干脆不加字段 —— 否则 `D.features` 会因缺 bin 直接崩 ✗（与 LIMIT_* 同一套降级逻辑 ✓）。
    #   ⚠ 语义用**当日**值（不做 ffill ✓）：面板的「剔除成交日 ST」本来就是取 T+1 **当日**状态 ✓。
    if field_bin_available("is_st"):
        fields = fields + [("$is_st", "IS_ST")]
    else:
        fields = [f for f in fields if f[1] != "IS_ST"]
    all_codes = want + cal_codes
    # ---- 磁盘缓存（"秒出"的关键：同池同区间第二次直接命中）----
    pdf = None
    cache_path = None
    try:
        from app.engine.feature_cache import _cache_path, _load_cache, _save_cache

        cache_path = _cache_path(all_codes, [e for e, _ in fields], [n for _, n in fields],
                                 start, end,
                                 # ★ v2.1.61：**取价源码进 key** ✗→✓（改口径 ⇒ 自动失效 ✓，
                                 #   不再靠手工 `signals|v1` 版本号 ✗）
                                 extra="signals|v1|%s|src=%s"
                                       % ("open" if need_open else "noopen", _src_fp()))
        pdf = _load_cache(cache_path)
    except Exception as e:
        _log_swallowed("pricing.py", e)
        cache_path = None
    if pdf is None:
        pdf = panel_features(all_codes, fields, start, end, cancel_cb=cancelled, warmup_days=0)
        if pdf is not None and len(pdf) and cache_path:
            try:
                _save_cache(cache_path, pdf)
            except Exception as e:
                _log_swallowed("pricing.py", e)
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


# ---------------------------------------------------------------------------
# 基准池「只要收盘宽表」的专用快路径（v1.19.48）
#
# 为什么单开一条：`load_price_panel` 会为 **6~7 个字段**各做一次 long→wide（unstack+ffill），
# 全A 实测 **12s/次**；而「未触发组」基准**只用到 CLOSE 一个字段**。
# 于是：① 只求 `$close`；② 把**宽表**（而不是 long 面板）落盘缓存 ⇒ 第二次读 ~0.3s。
# ⚠ 数值口径必须与 `load_price_panel` 的 CLOSE 完全一致 —— 因此复用同一个 `_wide(..., ffill=True)`。
#    改动本文件的取值口径时，记得 +1 `_POOL_WIDE_VERSION`（并在 `signals/event.py` 同步
#    `_EVENT_CACHE_VERSION`，否则旧的事件缓存会掩盖变化）。
# ---------------------------------------------------------------------------
_POOL_WIDE_VERSION = "v1"


def _pool_wide_cache_dir() -> str:
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "workdir", "cache", "signal_pool_wide")


def load_close_wide(codes: Sequence[str], start: str, end: str,
                    price: str = "$close") -> Optional[pd.DataFrame]:
    """基准池 → 后复权价格宽表（**只取一个字段**，带宽表磁盘缓存）。失败返回 None。

    ★ v2.1.55（修 v2.1.52 的回归 ✗→✓，用户 2026-10-12："你修坏了啊兄弟" ✓）：
      参数化 `price`（`"$close"` 默认 / `"$open"`）✓ —— 事件研究**跟随成交时点**后
      （`t1_open` ⇒ 触发组用开盘 ✓），基准池（未触发组）**也必须用开盘** ✓；
      而 v2.1.52 当时因为"本函数只有收盘" ✗ 就把开盘口径下的池置成了 `None` ✗
      ⇒ 用户看到的「基准/超额」整块消失 ✗。现在同一条快路径按 `price` 取 ✓
      （表达式与 `load_price_panel` 的 OPEN/CLOSE **逐字相同** ✓ ⇒ 不会混合口径 ✓）。
    ⚠ `price` **必须进缓存 key** ✗→✓（开/收盘宽表**形状完全相同** ✗，不进 key 就会把
      收盘表当开盘表返回 ✗✗ —— 与 `signals/event.py` 的指纹同一个坑 ✓）。
    """
    import hashlib
    import os
    import pickle

    from app.factors.panel_expr import panel_features

    _ensure_init()
    want = sorted({str(c).upper() for c in codes if str(c).strip()})
    if not want:
        return None
    h = hashlib.md5()
    _px = str(price or "$close")
    h.update(("%s|%s|%s|%s|%d" % (_POOL_WIDE_VERSION, _px, start, end, len(want))).encode("utf-8"))
    # ★★ v2.1.61：**取价源码也进 key** ✗→✓（用户 2026-10-12："没有加版本的哈希校验？" ✓）
    h.update(("|src=%s" % _src_fp()).encode("utf-8"))
    h.update(("|" + "|".join(want)).encode("utf-8"))          # 列集合进 key：池子变了必须失效
    try:
        from app.engine.feature_cache import _data_version
        h.update(("|dv=%s" % _data_version()).encode("utf-8"))
    except Exception as e:
        _log_swallowed("pricing.py", e)
        pass
    path = os.path.join(_pool_wide_cache_dir(), h.hexdigest()[:32] + ".pkl")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                w = pickle.load(f)
            if isinstance(w, pd.DataFrame) and len(w):
                return w
        except Exception as e:  # 坏缓存当未命中
            _log_swallowed("pricing.py", e)
            pass
    pdf = panel_features(want, [(_px, "PX")], start, end, warmup_days=0)
    if pdf is None or not len(pdf):
        return None
    w = _wide(pdf, "PX", ffill=True)
    if w is None or not len(w):
        return None
    try:
        os.makedirs(_pool_wide_cache_dir(), exist_ok=True)
        tmp = path + ".tmp%d" % os.getpid()
        with open(tmp, "wb") as f:
            pickle.dump(w, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)                              # 原子替换
    except Exception as e:
        _log_swallowed("pricing.py", e)
        pass
    return w


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


def _log_swallowed(where, exc):
    """**留痕兜底** ✓（P2-A，2026-10-18）—— 吞异常后写一行 debug 日志。

    ⚠ 本文件**没有 logger** ⇒ 这里惰性取 `logging.getLogger(__name__)` ✓；
      且全程 `contextlib.suppress` ✓：**日志自身失败绝不许抛** ✗
      （2.9.26 事故：把"静默"改成"崩溃" ✓，比不改更糟 ✗）。
    """
    import contextlib
    import logging as _logging

    with contextlib.suppress(Exception):
        lg = globals().get("logger") or globals().get("_LOGGER") or _logging.getLogger(__name__)
        lg.debug("[吞] %s: %r", where, exc)
