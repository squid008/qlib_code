# -*- coding: utf-8 -*-
"""**路由层共享状态**（缓存 / 锁 / 任务表 / 存取 helper）—— 从 `routers/factors.py` 抽出 ✓。

## 为什么单独成模块（P1-A-2，2026-10-18 ✓）

`factors.py` 曾经 2,489 行 ✗ 且**四组端点混居** ✓。拆分时撞到一个硬约束 ✗：

  事件研究组（~825 行）看似可整块搬 ✗，但它中间夹着 `_PR_BT_STASH` / `pr_bt_stash_put` /
  `clear_nav_caches` —— 这些是**单因子测试组在用**的状态 ✓ ⇒ 整块搬 ⇒ **循环依赖** ✗。

⇒ 把"状态"先独立出来 ✓：两组端点各自 `from .factors_state import ...` ✓，互不牵连 ✓。

## 约定（都很重要 ✓）

- 这些对象是**进程内**状态 ✗（没有落盘 ✓）⇒ **重启即丢** ✓；多 worker 不共享 ✓。
  （已知限制 ✓：所以接口会返回"任务不存在或结果已清理"✓ —— 用户侧要看到**可读原因**而不是 500 ✓。）
- 所有可变容器都配一把 `threading.Lock` ✓（`_SFT_LOCK` / `_EST_LOCK` / `_NAV_BT_LOCK` …）——
  **读写都要持锁** ✓，否则多线程下会读到"半更新"的字典 ✗。
- 容量上限（`_SFT_MAX_TASKS` / `_NAV_BT_CAP` / `_UNTRIG_CAP` …）超了**按插入顺序淘汰最旧** ✓
  （"够用就好、不做 LRU 统计"✓ —— 这里追求的是**不涨内存** ✓，不是命中率 ✓）。
- **删除即 tombstone 式的"清理"** ✓：清理端点只清已完成任务 ✓（跑着的不动 ✓）。
"""
import hashlib
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_SFT_MAX_TASKS = 50

_SFT_RESULT_KEEP = 5

_SFT_TASKS: dict[str, dict] = {}

_SFT_LOCK = threading.Lock()

def _sft_store(task_id: str, state: dict) -> None:
    with _SFT_LOCK:
        _SFT_TASKS[task_id] = state
        if len(_SFT_TASKS) > _SFT_MAX_TASKS:
            # 只清理已结束任务里最旧的，保留运行中的
            finished = [k for k, v in _SFT_TASKS.items() if v.get("status") in ("success", "failed")]
            for k in sorted(finished, key=lambda k: _SFT_TASKS[k].get("ts", 0))[: len(_SFT_TASKS) - _SFT_MAX_TASKS]:
                _SFT_TASKS.pop(k, None)

def _sft_trim_results() -> None:
    """内存治理：已完成任务只保留最近 _SFT_RESULT_KEEP 条的完整 result，更早的置 None。

    ⚠ v1.19.61：**同时释放 `_evs`**（0/1 因子的触发明细）—— 它是给"净值曲线"复用的事件表，
      一个任务可能有几万行；只清 `result` 不清它，跑几十次后内存白占（而净值端点里那条
      "按表达式在最近任务里找回事件"的兜底只扫最近几个任务，清掉更早的正好）。
    """
    with _SFT_LOCK:
        finished = sorted(
            (k for k, v in _SFT_TASKS.items() if v.get("status") in ("success", "failed")),
            key=lambda k: _SFT_TASKS[k].get("ts", 0),
            reverse=True,
        )
        for k in finished[_SFT_RESULT_KEEP:]:
            if _SFT_TASKS[k].get("result") is not None:
                _SFT_TASKS[k]["result"] = None
            if _SFT_TASKS[k].get("_evs") is not None:
                _SFT_TASKS[k]["_evs"] = None
            if _SFT_TASKS[k].get("_hk") is not None:      # v2.1.0：每期持仓索引同策略释放 ✓
                _SFT_TASKS[k]["_hk"] = None

def _sft_get(task_id: str):
    with _SFT_LOCK:
        return _SFT_TASKS.get(task_id)

def est_export_state(task_id: str) -> Optional[dict]:
    """**导出模块读取独立「事件研究」任务状态的唯一入口**（与 `sft_export_state` 对称 ✓）。"""
    return _est_get(task_id)

def sft_export_state(task_id: str) -> Optional[dict]:
    """**导出模块读取单因子测试任务状态的唯一入口**（结果 / `_hk` 持仓索引 / `_req` 参数 ✓）。

    ⚠ 为什么走函数、而不是让导出路由直接摸 `_SFT_TASKS`：那张表是**本模块的内部结构**
      （键名会变、还带锁 ✓）⇒ 集中一处暴露，将来调整结构只动这里 ✓。
    """
    return _sft_get(task_id)

_EST_TASKS: dict = {}

_EST_LOCK = threading.Lock()

_EST_RESULT_KEEP = 3   # 内存治理：只保留最近 3 个完成任务的结果

def _est_get(task_id: str):
    with _EST_LOCK:
        return _EST_TASKS.get(task_id)

def _est_store(task_id: str, state: dict) -> None:
    with _EST_LOCK:
        _EST_TASKS[task_id] = state

def _est_trim() -> None:
    with _EST_LOCK:
        finished = sorted(
            (k for k, v in _EST_TASKS.items() if v.get("status") in ("success", "failed")),
            key=lambda k: _EST_TASKS[k].get("ts", 0),
            reverse=True,
        )
        for k in finished[_EST_RESULT_KEEP:]:
            if _EST_TASKS[k].get("result") is not None:
                _EST_TASKS[k]["result"] = None

_UNTRIG_CACHE: dict = {}

_UNTRIG_LOCK = threading.Lock()

_UNTRIG_CAP = 4

_NAV_PANEL_CACHE: dict = {}

_NAV_PANEL_LOCK = threading.Lock()

_NAV_BT_CACHE: dict = {}

_NAV_BT_LOCK = threading.Lock()

_NAV_BT_CAP = 4

_NAV_BT_STASH: dict = {}

_NAV_BT_STASH_CAP = 4

_PR_BT_STASH: dict = {}

_PR_BT_CAP = 8

def _pr_key(task_id, factor_key, horizon, ei) -> tuple:
    return (str(task_id or ""), str(factor_key or ""), int(horizon or 0),
            -1 if ei is None else int(ei))

def pr_bt_stash_put(task_id, row: dict, item: Optional[dict], with_exit: bool,
                    ei: int, bt) -> None:
    """登记"这次每期全仓滚动回测"的成交明细（**导出专用** ✓，绝不进响应 ✗）。

    ⚠ **周期必须取 `row["horizon"]`** ✓（不是 `item["horizon"]` ✗）—— 2026-10-16 第一版写成
      `item` 的 ⇒ 存进去是 `0`、导出侧按 `row` 的 `60` 去取 ⇒ **键对不上、调仓记录整页空白** ✗✓
      （冒烟当场抓到 ✓）。**存取两侧必须用同一个来源** ✓，本函数与 `export_xlsx` 那边都是 `row` ✓。
    """
    try:
        key = _pr_key(task_id, (row or {}).get("expression"), (row or {}).get("horizon"),
                      (int(ei) if with_exit else None))
        with _NAV_BT_LOCK:
            _PR_BT_STASH.pop(key, None)
            _PR_BT_STASH[key] = {
                "trades": getattr(bt, "trades", None),
                "diag": dict(getattr(bt, "diag", None) or {}),
                "stats": ((getattr(bt, "stats", None) or {}).get("period_roll") or {}),
            }
            while len(_PR_BT_STASH) > _PR_BT_CAP:
                _PR_BT_STASH.pop(next(iter(_PR_BT_STASH)))
    except Exception:                                  # noqa: BLE001 —— 登记失败**绝不能影响回测** ✓
        pass

def pr_bt_stash_get(task_id, factor_key, horizon, ei):
    """导出侧按**同一键**取回（取不到 ⇒ None ⇒ 导出如实写"没有这一页" ✓，不假装 ✓）。"""
    with _NAV_BT_LOCK:
        return _PR_BT_STASH.get(_pr_key(task_id, factor_key, horizon, ei))

def clear_nav_caches() -> None:
    """★ T3（审计 ④ / v2.8.7，用户 2026-10-16 已拍 ✓）：切数据集时清本模块的**进程级缓存** ✓。

    为什么必须暴露出来 ✗：这三块缓存都按 **codes/区间(/k/cost)** 寻址 ✓，**不含数据集标识** ✗
    ⇒ 切到另一套数据集后，同样的 codes/区间会**命中旧数据的面板/回测** ✗✓
    （"看着切了、其实没切" —— 与 `panel_expr` / `feature_cache` 那几处同一类坑 ✓）。
    由 `services/datasets.clear_caches()` 调用 ✓（那边**失败会抛错** ✗，不再假成功 ✓）。
    """
    with _NAV_PANEL_LOCK:
        _NAV_PANEL_CACHE.clear()
    with _NAV_BT_LOCK:
        _NAV_BT_CACHE.clear()
        _NAV_BT_STASH.clear()
        _PR_BT_STASH.clear()          # ★ v2.8.8b：离场/对照的成交明细（键里也没有数据集 ✗）

def _nav_stash_key(task_id: str, factor_id: Optional[str], hold_days: int,
                   cost: float, capital: float) -> tuple:
    return (str(task_id or ""), str(factor_id or ""), int(hold_days), float(cost), float(capital))

def nav_bt_stash_get(task_id: str, factor_id: Optional[str], hold_days: int,
                     cost: float, capital: float):
    """导出用：取回 `/event-study/nav` **刚算过的那次**结果（含 `bt` / `signals` / 输入参数 ✓）。

    ★ v2.1.4：登记的东西从"只有 `bt`"扩成**"`bt` + 输入"** ✗→✓ —— 因为导出（XLSX）的意义
      是"把面板这次回测**原样搬走**"：信号（触发日/代码 ✓）、`fill`、`strict_limit`、
      `rebal_band`、`capital`、`cost`、`hold_days` 全都要跟着走 ✓，否则回灌方"看起来一样、
      结果不一样" ✗（用户 2026-10-11 报的就是这个：面板 `t1_open` + 触发日信号 + 死区 0，
      而文件里被我写成了 `t1_close` + **成交日**信号 + 界面死区 ⇒ 四个输入全不同 ✗）。

    返回 `{"bt":…, "signals":DataFrame, "fill":…, "strict_limit":…, "rebal_band":…, …}`；没有 ⇒ None ✓。
    ⚠ 调用方应**先调用一次** `event_study_nav(...)`（命中缓存 ⇒ 秒回 ✓）再取 ⇒ 保证有值 ✓。
    """
    with _NAV_BT_LOCK:
        return _NAV_BT_STASH.get(_nav_stash_key(task_id, factor_id, hold_days, cost, capital))

_NAV_REQ_SEQ: dict = {}

_NAV_REQ_LOCK = threading.Lock()

_NAV_REQ_KEEP = 32

class _NavCancelled(Exception):
    """同事件指纹下已有更新的请求 ⇒ 本次在途回测立即中止（由 `cancel_cb` 抛出）✓。"""

def _ev_fingerprint(ev) -> int:
    """**事件集合的内容指纹** ✓（v2.8.2 按审计 #3 修 ✓）。

    ⚠ 原来两处缓存键都只用「条数 + 首末触发日」代表一组事件 ✗ ——
      股票池相同、触发数相同、首末触发日相同的**两组不同信号**会算出同一个键 ⇒
      回测结果被**错误复用**（净值直接搬错的那份 ✓）。这里按 (code, dt) 逐行哈希 ✓，
      向量化（pandas hash ✓）⇒ 十万级事件也在毫秒量级 ✓。
    同时保留条数/首末日（便于人眼核对 ✓），内容指纹负责"**不同信号必然不同键**" ✓。
    """
    try:
        cols = [c for c in ("code", "dt") if c in getattr(ev, "columns", [])]
        if not cols:
            return 0
        h = pd.util.hash_pandas_object(ev[cols], index=False).to_numpy(dtype=np.uint64)
        return int(h.sum()) if h.size else 0
    except Exception:                                             # noqa: BLE001
        # 指纹算不出来 ⇒ 退回 0（等于旧行为 ✓）—— 宁可少命中缓存（重算一次 ✗ 慢一点 ✓），
        # 也**绝不能**因为指纹异常就误命中别人的结果 ✓。
        return 0

def _nav_req_key(codes, ev, start, end):
    """取消分组键：**只含事件与区间**（不含 k/cost ✓ ⇒ 同批事件下"最新的 k 才算数" ✓）。

    ⚠ v2.8.2：这里也要**事件内容指纹** ✗→✓ —— 否则两组不同信号会被当成"同一批"，互相取消 ✗
      （表现为点开曲线偶尔莫名 409/重算 ✓）。
    """
    return (len(codes), hash(tuple(codes)), str(start), str(end),
            len(ev), str(ev["dt"].iloc[0]), str(ev["dt"].iloc[-1]), _ev_fingerprint(ev))

def _nav_req_begin(key) -> int:
    with _NAV_REQ_LOCK:
        _NAV_REQ_SEQ[key] = _NAV_REQ_SEQ.get(key, 0) + 1
        while len(_NAV_REQ_SEQ) > _NAV_REQ_KEEP:      # 防字典无限增长 ✓
            _NAV_REQ_SEQ.pop(next(iter(_NAV_REQ_SEQ)))
        return _NAV_REQ_SEQ[key]

def _nav_req_current(key) -> int:
    with _NAV_REQ_LOCK:
        return _NAV_REQ_SEQ.get(key, 0)

_NAV_CANCEL: set = set()

_NAV_CANCEL_MAX = 64

def _nav_bt_key(codes, ev, start, end, k, cost, capital, modes, fill: str = "t1_open",
                max_positions: int = 0):
    # ★ v2.1.65：**`fill` 必须进回测缓存键** ✗→✓ —— 否则面板从"次日开盘"切到"次日收盘"时
    #   会**命中同一份缓存** ✗ ⇒ 净值不动 ⇒ 用户看到"两个数一模一样" ✗✓（就是这个现象 ✓）。
    # ★★ v2.8.2（**审计 #3** ✓）：**事件内容指纹**也必须进键 ✗→✓ —— 原来只用
    #   「条数 + 首末触发日」代表一组事件 ✗（连 `hash(ev.code)` 都没有 ✓）⇒ 同股票池 +
    #   同触发数 + 同首末触发日的两组不同信号会命中同一份 `BtResult` ✗（净值直接搬错 ✓）。
    # ★★ v2.9.3：`max_positions` 也必须进键 ✗→✓ —— 否则改了上限会**命中旧缓存** ✗
    #   ⇒ 净值纹丝不动 ⇒ 又是一个"选了没生效" ✓（本项目最贵的一类 bug ✓）。
    return (len(codes), hash(tuple(codes)), str(start), str(end),
            len(ev), str(ev["dt"].iloc[0]), str(ev["dt"].iloc[-1]), _ev_fingerprint(ev),
            int(k), float(cost), float(capital), tuple(modes), str(fill),
            int(max_positions or 0))

def _nav_cache_key(codes, start, end, strict: bool = True):
    return (len(codes), hash(tuple(codes)), str(start), str(end), bool(strict))

def _nav_panel_cached(codes, start, end, *, strict: bool = True):
    """取「价格面板 + 涨跌停标注」，按 (codes, 区间, strict) 缓存（见上方说明，v1.19.93）。"""
    from ..signals.pricing import fill_limits, load_price_panel

    key = _nav_cache_key(codes, start, end, strict)
    with _NAV_PANEL_LOCK:
        hit = _NAV_PANEL_CACHE.get(key)
    if hit is not None:
        return hit, True
    # ⚠ 必须与端点原调用一致：`need_open=True`（回测要开盘价 ✓）
    panel = load_price_panel(codes, start, end, need_open=True)
    if panel is None or "CLOSE" not in panel:
        return panel, False
    panel = fill_limits(panel, strict=strict)
    with _NAV_PANEL_LOCK:
        # ⚠ v1.20.0：原为 `clear()` **只留 1 条** ✗ ⇒ 换个因子/池（或先点 K=5 再点 K=20 触发
        #   不同 codes 集合）就整段重算（全A 面板 ≈12s ✓）⇒ 改为**容量 2 的 LRU**：一块
        #   5000×4000 的宽表面板是数百 MB 量级 ✓，容 2 条即可覆盖"来回切两个池/因子"的常见操作 ✓。
        while len(_NAV_PANEL_CACHE) >= 2:
            _NAV_PANEL_CACHE.pop(next(iter(_NAV_PANEL_CACHE)))
        _NAV_PANEL_CACHE[key] = panel
    return panel, False
