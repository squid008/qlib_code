# -*- coding: utf-8 -*-
"""事件研究封装（交易信号测试 · 模式①）—— 直接复用 `factors/event_study.py`，不重写。

要的就是单因子测试里那套"01 公式"的东西（用户原话："这就相当于 01 公式的信号了，
然后就是事件研究那一套"）：
  · 逐 k = 1..N：触发组的均值/中位数/胜率/分位（`build_event_stats`）；
  · 未触发组（基准池内当日剔除触发股）与**超额**曲线，含两套口径（日配对均值 / 事件级中位数）
    与逐 k 的 HAC t、日胜率（`compute_baseline_curves`）。

⚠ **有效 / 有效反向的锚点判定不在后端** —— 那是**前端** `components/verdictRules.ts` 的职责
（`isValidAt` / `isReverseValidAt` / `pairStabilityOf`，含 `max(0.5%, 0.025%×k)` 门槛）。
后端只给原始字段，前端用**同一份规则**算锚点 ⇒ 与单因子测试口径**逐位一致**，不会出现
"两个页面同一信号判定不同"的经典坑。
"""
from __future__ import annotations

import hashlib
import os
import pickle
from typing import Dict, List, Optional

import pandas as pd

from ..factors.event_study import build_event_stats, compute_baseline_curves


def _event_cache_dir() -> str:
    """事件研究结果缓存目录（`backend/workdir/cache/signal_event`）。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "workdir", "cache", "signal_event")


# ⚠ **改动事件研究算法的口径/实现时，必须把这个版本号 +1** —— 否则历史缓存会让"新算法"
#   返回旧结果（本项目最忌讳的"口径漂移"变成"口径漂移 + 看不见"）。清缓存也可删目录。
_EVENT_CACHE_VERSION = "v2"          # v2：基准池改走 `pricing.load_close_wide`（只取 CLOSE）


def _event_fingerprint(cache_ns: str, events: pd.DataFrame, close_trig: pd.DataFrame,
                       close_pool: Optional[pd.DataFrame], max_k: int) -> str:
    """内容指纹：**同参数重跑必须命中、任一输入变了必须失效**。

    含：池/区间前缀、max_k、**事件集合**（信号文件内容）、触发组与基准池的**形状+日期范围+列集合**
    （列集合决定"哪些股票在池里"）、以及 `feature_cache._data_version()`（数据更新后自动失效）。
    不含数值本身 —— 数值由"数据版本 + 列集合 + 事件"唯一决定（宽表是行情派生物）。
    """
    h = hashlib.md5()
    h.update(("cv=%s|ns=%s|k=%d" % (_EVENT_CACHE_VERSION, cache_ns, max_k)).encode("utf-8"))
    try:
        from ..engine.feature_cache import _data_version
        h.update(("|dv=%s" % _data_version()).encode("utf-8"))
    except Exception:                                   # 数据版本取不到就退化为不校验
        pass
    pair = events[["dt", "code"]].astype(str)
    h.update(("|ev=%d" % len(pair)).encode("utf-8"))
    h.update(("|" + "|".join(sorted(set(pair["dt"] + " " + pair["code"])))).encode("utf-8"))
    for tag, df in (("T", close_trig), ("P", close_pool)):
        if df is None or not len(df):
            h.update(("|%s=none" % tag).encode("utf-8"))
            continue
        h.update(("|%s=%d,%d,%s,%s" % (tag, df.shape[0], df.shape[1],
                                       str(df.index[0])[:10], str(df.index[-1])[:10])).encode("utf-8"))
        h.update(("|" + ",".join(str(c) for c in df.columns)).encode("utf-8"))
    return h.hexdigest()[:32]


def _event_cache_path(cache_ns: str, events: pd.DataFrame, close_trig: pd.DataFrame,
                      close_pool: Optional[pd.DataFrame], max_k: int) -> str:
    return os.path.join(_event_cache_dir(),
                        _event_fingerprint(cache_ns, events, close_trig, close_pool, max_k) + ".pkl")


def run_event_study(events: pd.DataFrame, close_trig: pd.DataFrame,
                    close_pool: Optional[pd.DataFrame], max_k: int = 60,
                    cancel_check=None, cache_ns: Optional[str] = None) -> Dict:
    """`events`（date, code）→ 事件研究结果 dict。

    · `close_trig` —— 触发标的的**后复权收盘**宽表（`signals/pricing.load_price_panel` 的 CLOSE）；
    · `close_pool` —— 基准池全样本宽表（"未触发组"的口径来源；None ⇒ 只出触发组，不出超额）；
    · `cache_ns`（v1.19.48）—— **内容缓存**命名空间（如 `"all|2016-01-01|2024-12-30"`）。
      给定时把结果落盘到 `workdir/cache/signal_event/<指纹>.pkl`，**同参数重跑直接返回**。

      ⚠ 为什么要缓存：基准池那段对**每个 k** 都要过一遍「(配对日 × 池内股票)」的大矩阵
      （全A 实测 2000×5000、k=60 ⇒ 15~30s），而且**每次重跑都重算**（2026-09-15 用户：
      "这次怎么测试速度感觉慢了很多呢，纯信号也算这么慢吗"）。**这段数学是单因子测试共用的
      已验证实现，不能改**（改了等于动已验证口径）⇒ 只在"同一份输入的重复请求"上省时间。
      返回里带 `cached` 字段，前端据此显示「缓存命中」。
    """
    if not len(events) or close_trig is None or not len(close_trig):
        return {"error": "无事件或无价格数据"}
    ev = pd.DataFrame({"code": events["code"].astype(str),
                       "dt": pd.to_datetime(events["date"])})
    max_k = max(1, min(int(max_k or 60), 250))

    path = None
    if cache_ns:
        try:
            path = _event_cache_path(cache_ns, ev, close_trig, close_pool, max_k)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    hit = pickle.load(f)
                if isinstance(hit, dict) and "curve" in hit:
                    hit["cached"] = True
                    return hit
        except Exception:                               # 缓存坏了当未命中（不能因缓存挡住主流程）
            path = None

    es = build_event_stats(close_trig, ev, max_k, n_raw=int(len(events)),
                           cancel_check=cancel_check)
    base = None
    if close_pool is not None and len(close_pool):
        base = compute_baseline_curves(close_trig, close_pool, ev, max_k,
                                       cancel_check=cancel_check)
    es["baseline"] = base
    es["n_events_used"] = int(len(ev))
    es["cached"] = False
    if path:
        try:
            os.makedirs(_event_cache_dir(), exist_ok=True)
            tmp = path + ".tmp%d" % os.getpid()
            with open(tmp, "wb") as f:
                pickle.dump(es, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)                       # 原子替换：并发/中断不会留下半个文件
        except Exception:
            pass
    return es


def coverage(events: pd.DataFrame, close_trig: pd.DataFrame) -> Dict:
    """信号覆盖率自检：多少事件能对齐到价格（对齐不上 = 代码/日期/复权口径有问题）。"""
    if not len(events) or close_trig is None or not len(close_trig):
        return {}
    codes = set(close_trig.columns)
    n_code_bad = int((~events["code"].astype(str).isin(codes)).sum())
    cal = pd.DatetimeIndex(close_trig.index)
    dts = pd.to_datetime(events["date"])
    n_cal_bad = int((~dts.isin(cal)).sum())
    return {"n_events": int(len(events)), "code_not_in_data": n_code_bad,
            "date_not_in_calendar": n_cal_bad,
            "n_codes": int(events["code"].nunique())}


def pool_close_for(panel_full: Optional[Dict[str, pd.DataFrame]]) -> Optional[pd.DataFrame]:
    """基准池宽表（直接取面板 CLOSE；列 = 池内全部标的）。"""
    if not panel_full:
        return None
    return panel_full.get("CLOSE")


def normalize_events(df: pd.DataFrame) -> pd.DataFrame:
    """事件表规范化（去重、按日期排序）。"""
    if df is None or not len(df):
        return pd.DataFrame(columns=["date", "code"])
    ev = df[["date", "code"]].copy()
    ev["date"] = pd.to_datetime(ev["date"])
    ev = ev.drop_duplicates().sort_values(["date", "code"]).reset_index(drop=True)
    return ev
