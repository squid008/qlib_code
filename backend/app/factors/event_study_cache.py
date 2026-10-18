# -*- coding: utf-8 -*-
"""事件研究结果的**内容寻址磁盘缓存**（v1.20.36）。

为什么要它
----------
事件研究的耗时对**同一参数**重复请求是纯浪费：实测同一因子同参数重跑 15.4s → 8.9s（0.58×），
但"更快"只来自**原始读盘层**（`panel_expr._BIN_CACHE` + OS page cache），**没有任何结果级复用**
（`/factorS/event-study` 每次都新建 task_id、`_EST_RESULT_KEEP` 只保留最近几个结果不做复用）。
⇒ 用户「变更周期后重算」时无法预期快慢，前端也就无法可靠地做「自动跟随 / 手工按钮」的自适应。

本模块给出**确定性**的复用：指纹 = 所有影响结果的参数 + 数据版本 ⇒ 命中即秒回，并回传 `cached=True`。
范式与 `app/signals/event.py`（`_EVENT_CACHE_VERSION` / `_event_fingerprint`）保持一致，
那边的做法已在信号测试里上线验证过。

⚠ **改动事件研究口径/算法时必须把 `CACHE_VERSION` +1**（或删目录），否则历史缓存会让新算法"看起来没生效"
  —— 本项目最忌讳的"口径漂移"就变成"口径漂移 + 看不见"。目录：`backend/workdir/cache/event_study/`。
"""
from __future__ import annotations

import hashlib
import os
import pickle
from typing import Optional

# ⚠ 口径/算法变更必须 +1（见模块 docstring）
CACHE_VERSION = "v1"

# 会改变结果的**全部**参数（漏一个就会出现"参数不同却命中同一结果"的静默错误）
_FINGERPRINT_KEYS = (
    "universe", "start_date", "end_date", "expr", "max_k",
    "exclude_limit_up_signal", "exclude_limit_up_trade", "exclude_suspended",
    "exclude_st_t1", "exclude_stock_gem", "exclude_stock_kcb",
    "price_adjust", "price_round", "suspend_remove", "freeze_suspended_price",
    "warmup_days",
)


def cache_dir() -> str:
    """缓存目录（`backend/workdir/cache/event_study`，与 `signals/event.py` 同层级）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "workdir", "cache", "event_study")


def _data_version() -> str:
    """qlib 数据版本（取不到就退化为不校验；与 `signals/event.py` 同源思路）。"""
    try:
        from ..engine.feature_cache import _data_version as _dv
        return str(_dv())
    except Exception as e:
        _log_swallowed("event_study_cache.py", e)
        return "na"


def fingerprint(params: dict) -> str:
    """参数指纹：**同参数必命中、任一参数变必失效**（含数据版本）。"""
    h = hashlib.md5()
    h.update(("cv=%s" % CACHE_VERSION).encode("utf-8"))
    for k in _FINGERPRINT_KEYS:
        h.update(("|%s=%r" % (k, params.get(k))).encode("utf-8"))
    h.update(("|dv=%s" % _data_version()).encode("utf-8"))
    # ★★ v2.1.61：**算法源码也进指纹** ✗→✓（用户 2026-10-12："没有加版本的哈希校验？" ✓）——
    #   真正的口径都在 `factors/event_study.py`（+ 本模块）里 ✓ ⇒ 任一改动 ⇒ 指纹变 ⇒
    #   旧缓存自动失效 ✓。此前只有手工 `CACHE_VERSION` ✗（docstring 自己也说"必须 +1" ✗
    #   ⇒ 靠人记，忘了就会让新算法"看起来没生效" ✗✗）。手工版本号**保留**（仍是显式开关 ✓）。
    try:
        from ..engine.code_fp import code_fingerprint
        h.update(("|src=%s" % code_fingerprint("app/factors/event_study.py",
                                               "app/factors/event_study_cache.py")).encode("utf-8"))
    except Exception as e:  # noqa: BLE001
        _log_swallowed("event_study_cache.py", e)
        h.update(b"|src=na")
    return h.hexdigest()[:32]


def path_for(params: dict) -> str:
    return os.path.join(cache_dir(), fingerprint(params) + ".pkl")


def load(fp: str) -> Optional[dict]:
    """读缓存；文件不存在/损坏/结构不对 ⇒ 返回 None（**缓存不能挡住主流程**）。"""
    try:
        if not os.path.exists(fp):
            return None
        with open(fp, "rb") as f:
            obj = pickle.load(f)
        if isinstance(obj, dict) and "curve" in obj:
            return obj
        return None
    except Exception as e:
        _log_swallowed("event_study_cache.py", e)
        return None


def save(fp: str, obj: dict) -> bool:
    """原子落盘（并发/中断不会留下半个文件）。失败静默返回 False。"""
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        tmp = fp + ".tmp%d" % os.getpid()
        with open(tmp, "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, fp)
        return True
    except Exception as e:
        _log_swallowed("event_study_cache.py", e)
        return False


def _log_swallowed(where, exc):
    """**留痕兜底** ✓（P2-A，2026-10-18）—— 吞异常后写一行 debug 日志。

    ⚠ logger 名在不同模块不一样（`logger` / `_LOGGER` / 没有 ✓），这里全兜住 ✓；
      全程 `contextlib.suppress` ✓：**日志自身失败绝不许抛** ✗（2.9.26 事故教训 ✓）。
    """
    import contextlib
    import logging as _logging

    with contextlib.suppress(Exception):
        lg = globals().get("logger") or globals().get("_LOGGER") or _logging.getLogger(__name__)
        lg.debug("[吞] %s: %r", where, exc)
