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
    except Exception:
        return "na"


def fingerprint(params: dict) -> str:
    """参数指纹：**同参数必命中、任一参数变必失效**（含数据版本）。"""
    h = hashlib.md5()
    h.update(("cv=%s" % CACHE_VERSION).encode("utf-8"))
    for k in _FINGERPRINT_KEYS:
        h.update(("|%s=%r" % (k, params.get(k))).encode("utf-8"))
    h.update(("|dv=%s" % _data_version()).encode("utf-8"))
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
    except Exception:
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
    except Exception:
        return False
