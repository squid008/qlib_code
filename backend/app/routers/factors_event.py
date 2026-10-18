# -*- coding: utf-8 -*-
"""**事件研究**接口（从 `routers/factors.py` 拆出 ✓ P1-A-2，2026-10-18）。

## 边界（实测 ✓）

本组只依赖：请求模型（`factors_models` ✓）、状态层（`factors_state` ✓）、
事件研究引擎（`factors.event_study` ✓）—— **不依赖**单因子测试组 ✓。

## ⚠ URL 不变 ✓

自带 `APIRouter(prefix="/api/factors")` ✓（与原同一 prefix ✓）；
净值曲线三类缓存、取消语义全在 `factors_state` ✓（`clear_nav_caches` 也在那儿 ✓）。
"""

# -*- coding: utf-8 -*-
"""因子库目录接口。

对外提供特征集的"因子名 + 表达式 + 分类 + 描述"目录，供前端勾选特征、
悬停查看公式。设计为可扩展：未来维护成千上万因子时，只需在
app/factors/catalog.py 的 FACTOR_PROVIDERS 注册新的 provider，接口无需改动。
"""
import threading
import time
import uuid
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..factors.catalog import get_catalog, FACTOR_PROVIDERS

from .factors_models import (  # noqa: F401
    SingleFactorTestFactor, SingleFactorTestRequest, EventStudyRequest, EventNavRequest,
    EventNavCancelRequest,
)
from .factors_state import (  # noqa: F401
    _SFT_MAX_TASKS, _SFT_RESULT_KEEP, _SFT_TASKS, _SFT_LOCK,
    _sft_store, _sft_trim_results, _sft_get, est_export_state,
    sft_export_state, _EST_TASKS, _EST_LOCK, _EST_RESULT_KEEP,
    _est_get, _est_store, _est_trim, _UNTRIG_CACHE,
    _UNTRIG_LOCK, _UNTRIG_CAP, _NAV_PANEL_CACHE, _NAV_PANEL_LOCK,
    _NAV_BT_CACHE, _NAV_BT_LOCK, _NAV_BT_CAP, _NAV_BT_STASH,
    _NAV_BT_STASH_CAP, _PR_BT_STASH, _PR_BT_CAP, _pr_key,
    pr_bt_stash_put, pr_bt_stash_get, clear_nav_caches, _nav_stash_key,
    nav_bt_stash_get, _NAV_REQ_SEQ, _NAV_REQ_LOCK, _NAV_REQ_KEEP,
    _NavCancelled, _ev_fingerprint, _nav_req_key, _nav_req_begin,
    _nav_req_current, _NAV_CANCEL, _NAV_CANCEL_MAX, _nav_bt_key,
    _nav_cache_key, _nav_panel_cached,
)
# ---- ★ P1-A-2 补遗（2026-10-18 ✓）：ruff F821 实测出来的 5 个依赖 ----
from .. import config                                              # noqa: E402
from ..engine.task_manager import get_task_manager                 # noqa: E402
from ..factors.event_study import run_event_study                  # noqa: E402
from ..factors.single_test import FactorTestCancelled, _bad_date_arg  # noqa: E402
from .factors_state import _json_safe                              # noqa: E402

router = APIRouter(prefix="/api/factors", tags=["factors"])  # noqa: E402

@router.post("/event-study", summary="事件研究（0/1 信号触发后收益分布，异步提交）")
def event_study(req: EventStudyRequest):
    """提交事件研究任务，返回 task_id；随后轮询 /factors/event-study/progress/{task_id}。

    仅适用于 0/1 二值信号（触发 = 因子值 > 0.5）。口径与单因子测试完全一致
    （见 factors/event_study.py 模块 docstring）。
    """
    if not req.start_date or not req.end_date:
        raise HTTPException(status_code=400, detail="请填写测试区间")
    _bad = _bad_date_arg(req.start_date, req.end_date)
    if _bad:
        raise HTTPException(status_code=400, detail=_bad)
    if not (req.factor and req.factor.expression):
        raise HTTPException(status_code=400, detail="请提供因子表达式")
    max_k = int(req.max_k or 40)
    if not (1 <= max_k <= 120):
        raise HTTPException(status_code=400, detail="最长持有期 max_k 需在 1~120 之间")
    if req.exclude_st_t1:
        from ..engine.limits import field_bin_available
        if not field_bin_available("is_st"):
            raise HTTPException(
                status_code=400,
                detail='勾选了"剔除ST(T+1)"，但本机 Qlib 数据没有 is_st 标签，请取消勾选',
            )

    task_id = uuid.uuid4().hex[:12]
    state: dict = {
        "task_id": task_id,
        "status": "running",
        "progress": 0.0,
        "message": "已提交",
        "result": None,
        "error": None,
        "cancel_requested": False,
        "ts": time.time(),
    }
    _est_store(task_id, state)

    def _run() -> None:
        manager = get_task_manager(config.WORK_DIR)
        manager.register_external_queued(task_id)
        try:
            from ..factors.single_test import _ensure_qlib_init
            _ensure_qlib_init()
        except Exception as e:  # noqa: BLE001
            state.update(status="failed", progress=100.0, message=f"事件研究失败: {e}", error=str(e), ts=time.time())
            manager.unregister_external_queued(task_id)
            _est_trim()
            return

        got = manager.external_wait_slot(
            task_id, cancel_check=lambda: bool(state.get("cancel_requested"))
        )
        manager.unregister_external_queued(task_id)
        if not got:
            return   # 排队期间被取消（终态在 cancel 侧已标记，或由收尾标记）

        def _cb(h, p: float, m: str) -> None:
            state["progress"] = float(p)
            state["message"] = m
            if state.get("cancel_requested"):
                raise FactorTestCancelled()

        try:
            res = run_event_study(
                universe=req.universe,
                start_date=req.start_date,
                end_date=req.end_date,
                factor=req.factor.model_dump(),
                max_k=max_k,
                exclude_limit_up_signal=req.exclude_limit_up_signal,
                exclude_limit_up_trade=req.exclude_limit_up_trade,
                exclude_suspended=req.exclude_suspended,
                exclude_st_t1=req.exclude_st_t1,
                exclude_stock_gem=req.exclude_stock_gem,
                exclude_stock_kcb=req.exclude_stock_kcb,
                price_adjust=req.price_adjust,
                price_round=req.price_round,
                # ★ v2.1.52：口径跟着面板的「成交时点」✓（t1_open ⇒ 开盘买/卖 ✓）
                fill=req.fill,
                suspend_remove=req.suspend_remove,
                freeze_suspended_price=req.freeze_suspended_price,
                warmup_days=req.warmup_days,
                progress_cb=_cb,
                cancelled=lambda: bool(state.get("cancel_requested")),
            )
            if res.get("error"):
                state.update(status="failed", progress=100.0, message=res["error"],
                             error=res["error"], ts=time.time())
            else:
                # ⚠ v1.19.60：触发事件（`_ev`）**不进 HTTP 响应**（几万行会把结果撑大、前端也不用），
                #   存进任务状态供「净值曲线」端点复用（`/factors/event-study/nav`）；
                #   同时留一份请求参数（取价需要区间）。
                state["_ev"] = res.pop("_ev", None)
                state["_req"] = req.model_dump()
                # v1.20.36：把耗时明细/缓存命中透出给前端（`/event-study/progress` 会带上）
                state["timings"] = res.get("timings")
                state["cached"] = res.get("cached")
                state.update(status="success", progress=100.0, message="事件研究完成",
                             result=_json_safe(res), ts=time.time())
        except FactorTestCancelled:
            state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())
        except Exception as e:  # noqa: BLE001
            state.update(status="failed", progress=100.0, message=f"事件研究失败: {e}",
                         error=str(e), ts=time.time())
        finally:
            # ⚠⚠ v1.20.35 修复（用户 2026-09-20 报「事件研究卡死」）：
            #   `external_wait_slot` 拿到的并发配额**必须成对归还**（同 `factors.py` 单因子路径
            #   的 `release_slot`）。此前这里只调 `_est_trim()`、**漏了归还** ⇒ 每做一次事件研究
            #   就永久泄漏 1 个槽；泄漏数攒到并发上限（本机 `max_concurrent=3`）之后，
            #   **后续所有事件研究 / 单因子测试都会永远排队**（message 停在「已提交」、CPU 0、
            #   磁盘 0 —— 表现完全像"卡死"，且取消也未必能唤醒）。
            #   诊断特征（很好认）：`GET /api/backtest/capacity` 的 `running` **大于实际在跑的任务数**
            #   （`running=3 / queued=1` 但没有任何任务在跑）⇒ 就是配额泄漏。
            #   注：走到这里必然已持有配额（`if not got: return` 在 try 之前）⇒ 归还恒成对。
            manager.release_slot(task_id)
            _est_trim()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id}

def _untrig_nav_series(state: dict, ev, start: str, end: str, hold: int = 1) -> List[tuple]:
    """未触发组等权净值序列 ✓（`[(日期, 净值), ...]`；起点 1.0 ✓）。

    ★★ v2.9.1（用户 2026-10-17 ✓）：**必须与策略同持有期** ✗→✓ ——
      用户原话：「那肯定是**同口径**才能比啊，未触发组也得是**同持有期**才行啊」✓。
      · 旧实现只持 **1 天**（每日等权、天天换仓 ✗）⇒ 而策略是**买入持有 N 天** ✓
        ⇒ 两条曲线**持有期差 N 倍** ✗ ⇒ 图上「策略 1.449 vs 参照 0.785」根本不是 alpha ✓
        （这是 2026-10-17 那轮"配对检验说没优势、净值却说有优势"的**真正来源** ✓）；
      · 现在 `hold` = 该次请求的 `hold_days`（= 界面上的「持仓周期」✓）：
        每个交易日把「当日未触发股」等权买入、**持有 hold 个交易日**、到期卖出 ✓
        （重叠批次按**每只票等权**合并 ✓，与策略 `event_even` 的等权口径一致 ✓）。
      ⚠ `hold <= 1` 时**保持旧行为**（逐日等权、天天换仓 ✓）⇒ 老调用/老测试逐位不变 ✓。
    """
    rq = (state or {}).get("_req") or {}
    universe = str(rq.get("universe") or "all")
    pa = str(rq.get("price_adjust") or "backward")
    price_round = bool(rq.get("price_round", True))
    price_field = "$open" if str(rq.get("fill") or "").startswith("t1_open") else "$close"
    hold = max(1, int(hold or 1))
    key = (universe, str(start), str(end), pa, price_round, price_field, int(len(ev)), hold)
    with _UNTRIG_LOCK:
        got = _UNTRIG_CACHE.get(key)
    if got is not None:
        return got
    # ⚠ `_resolve_instruments` 的**家在 `single_test`** ✓（`event_study` 只是把它 import 进来 ✓
    #   ⇒ 单测要 monkeypatch 时打 `app.factors.event_study._resolve_instruments` ✓ 也有效 ✓）
    from ..factors.event_study import load_px_wide
    from ..factors.single_test import _resolve_instruments

    full_codes = sorted({str(c).upper() for c in _resolve_instruments(universe, start)})
    piv = load_px_wide(full_codes, str(start), str(end), pa, price_round, price=price_field)
    if piv is None or not len(piv) or len(piv) < 3:
        return []
    arr = piv.to_numpy(dtype=np.float64, copy=False)
    arr = np.where(np.isfinite(arr) & (arr > 0.0), arr, np.nan)
    with np.errstate(all="ignore"):
        ret = arr[1:] / arr[:-1] - 1.0
    valid = np.isfinite(ret)
    # ---- 触发矩阵（同一份数据给「1 天口径」与「同持有期口径」两条路径用 ✓）----
    dates = list(piv.index)
    pos = {str(d)[:10]: i for i, d in enumerate(dates)}
    col = {str(c).upper(): j for j, c in enumerate(piv.columns)}
    _r: List[int] = []
    _c: List[int] = []
    for d, c in zip(list(ev["dt"]), list(ev["code"])):
        i = pos.get(str(d)[:10])
        j = col.get(str(c).upper())
        if i is None or j is None:
            continue
        _r.append(i)
        _c.append(j)
    trig = np.zeros(arr.shape, dtype=bool)
    if _r:
        trig[np.asarray(_r, dtype=np.intp), np.asarray(_c, dtype=np.intp)] = True

    if hold <= 1:
        # ---- 旧口径（持 1 天：逐日等权、天天换仓 ✓）—— 语义与 v2.9.0 **逐位不变** ✓ ----
        valid1 = valid.copy()
        if _r:
            rr = np.asarray(_r, dtype=np.intp)
            cc = np.asarray(_c, dtype=np.intp)
            keep = rr + 1 < len(dates)             # 信号日 T ⇒ 剔除 T+1 那一格收益 ✓
            valid1[rr[keep], cc[keep]] = False
        with np.errstate(all="ignore"):
            means = np.nanmean(np.where(valid1, ret, np.nan), axis=1)
        nav = np.cumprod(np.where(np.isfinite(means), 1.0 + means, 1.0))
    else:
        # ---- ★ 同持有期口径（用户 2026-10-17 ✓）：未触发股也**买入持有 hold 天、到期卖出** ✓ ----
        #   每个交易日 T：把「当日未触发股」等权买入（T+1 起持有 ✓）；持有满 hold 个交易日卖出 ✓；
        #   重叠批次**按每只票等权合并**（与 `event_even` 的等权口径一致 ✓）。
        n_ret, n_col = ret.shape
        ent = np.zeros((n_ret + hold + 2, n_col), dtype=np.float64)
        exi = np.zeros_like(ent)
        untrig = ~trig[:n_ret]                     # 第 t 行 = 当日**未触发**的票 ✓
        ent[1:n_ret + 1] = untrig.astype(np.float64)
        exi[1 + hold:n_ret + 1 + hold] = untrig.astype(np.float64)
        held = np.cumsum(ent - exi, axis=0)[:n_ret] > 0.5
        rr = np.where(held & np.isfinite(ret), ret, np.nan)
        cnt = np.sum(np.isfinite(rr), axis=1).astype(np.float64)
        with np.errstate(all="ignore"):
            sums = np.nansum(rr, axis=1)
        means = np.where(cnt > 0, sums / np.maximum(cnt, 1.0), 0.0)
        nav = np.cumprod(1.0 + means)
    nav = np.concatenate([[1.0], nav])             # ret 比 dates 少一行 ✓ ⇒ 首日补 1.0 ✓
    out = [(str(d)[:10], float(v)) for d, v in zip(dates, nav)]
    with _UNTRIG_LOCK:
        while len(_UNTRIG_CACHE) >= _UNTRIG_CAP:
            _UNTRIG_CACHE.pop(next(iter(_UNTRIG_CACHE)))
        _UNTRIG_CACHE[key] = out
    return out

@router.post("/event-study/nav/cancel", summary="取消净值曲线计算（关弹窗时调用）")
def event_study_nav_cancel(req: EventNavCancelRequest):
    """把该 task 标记为"已放弃" ✓ ⇒ 在途回测的 `cancel_cb` 下一次检查即抛 ⇒ 立即停算 ✓。

    ⚠ 幂等 ✓、永远返回 ok ✓（前端卸载时调用，不该因任何原因报错 ✓）。
    ⚠ 集合上限 64（FIFO 清理 ✓）—— 只存 task_id，量极小 ✓。
    """
    tid = str(req.task_id or "")
    if tid:
        _NAV_CANCEL.add(tid)
        while len(_NAV_CANCEL) > _NAV_CANCEL_MAX:
            _NAV_CANCEL.pop()
    return {"ok": True}

@router.post("/event-study/nav", summary="事件研究的净值曲线（两种资金方案 + 基准）")
def event_study_nav(req: EventNavRequest):
    """复用事件研究任务里**已经算好的触发事件**与磁盘缓存的价格面板，只花"一次回测"的钱。

    ⚠ 为什么单开端点、不塞进事件研究任务：净值要**跟随持仓周期 k 变动** ⇒ 每次改 k 都得重跑一遍
      回测（实测 0.34~0.59s/次，见 `ai_test/bench_event_nav.py`），不能一次算死。
      触发事件与价格面板都复用现成的（事件存在任务状态、面板走 `feature_cache`）⇒ 单次请求 ~0.5~1s。
    """
    # 事件研究任务与单因子测试任务**共用这个 id 查询**（两类状态字典都认一下）
    state = _est_get(req.task_id) or _sft_get(req.task_id)
    if state is None and req.task_id:
        raise HTTPException(status_code=404, detail="任务不存在或未完成（结果可能已被清理）")
    rq = (state or {}).get("_req") or {}
    ev = None
    if req.factor_id:
        ev = ((state or {}).get("_evs") or {}).get(req.factor_id)
    if ev is None and state is not None:
        ev = state.get("_ev")
    if ev is None and req.factor_id:
        # 兜底（v1.19.61）：任务 id 失效/没传时，按**因子表达式**在最近的任务里找触发明细。
        # 只扫最近 _SFT_RESULT_KEEP 个"有事件"的任务，命中即用（单因子测试常连跑多次，同因子很容易找到）。
        for st in sorted(_SFT_TASKS.values(), key=lambda v: v.get("ts", 0), reverse=True):
            found = (st.get("_evs") or {}).get(req.factor_id)
            if found is not None and len(found):
                ev, state = found, st
                rq = st.get("_req") or rq
                break
        if ev is None:
            for st in sorted(_EST_TASKS.values(), key=lambda v: v.get("ts", 0), reverse=True):
                found = st.get("_ev")
                if found is not None and len(found):
                    ev, state = found, st
                    rq = st.get("_req") or rq
                    break
    if ev is None or not len(ev):
        raise HTTPException(status_code=400,
                            detail="找不到这次信号的触发明细（任务已清理或因子没触发过）—— "
                                   "点「重新计算」重跑一次事件研究即可看净值曲线")
    start, end = rq.get("start_date"), rq.get("end_date")
    k = max(1, min(int(req.hold_days or 20), 250))
    codes = sorted(ev["code"].astype(str).unique().tolist())
    # 单向依赖：factors → signals（signals 侧不反向依赖 factors，无环）
    from ..signals.engine import attach_benchmark, nav_rows, run_backtest
    from ..signals.pricing import load_bench_wide

    timings: dict = {}
    try:
        t0 = time.perf_counter()
        # ★ v1.19.93：取价面板 + 涨跌停标注**与 k 无关** ⇒ 走进程内缓存
        #   （过顶触发 14.88 万次、事件覆盖几千只股票时，这一步原来每改一次 k 就重算一遍 ✗）
        panel, _hit = _nav_panel_cached(codes, start, end, strict=True)
        timings["prices"] = round(time.perf_counter() - t0, 3)
        timings["prices_cached"] = bool(_hit)
        if panel is None or "CLOSE" not in panel:
            raise HTTPException(status_code=400, detail="取不到触发标的的行情数据")
        # ★★ v2.1.79（用户 2026-10-13 拍板 ✓）：**不复权时净值曲线也用不复权** ✗→✓
        #   为什么改（用户的规矩 ✓）：他要"**与聚宽对齐**" ⇒ **一个口径走全链** ✓
        #   （因子/事件研究/回测/净值/回灌 全都不复权 ✓），而不是我 v2.1.73 自己定的
        #   "金额类永远后复权" ✗ —— 那条虽然是"财务正确" ✓，但**破坏了对拍** ✗：
        #   实测（`ai_test/ab_fuquan.py` ✓，只改复权口径 ✓）：
        #     组合卡 Q10 年化 backward 24.18% → none 21.03%（−3.15pp ✗ 真实价的除权跳空 ✓）
        #     实盘口径    backward 22.87% → none 22.87%（0.00pp ✓ 因为当时它被写死后复权 ✗）
        #   ⇒ 两边基准不同 ⇒ "实盘 vs 模型"的结论会**随复权口径翻转** ✗（用户当场发现 ✓）。
        #   ⚠ **必须浅拷贝** ✓：`_nav_panel_cached` 返回的是**进程内缓存对象** ✗，
        #     就地改 `panel["CLOSE"]` 会污染后续所有请求 ✓✓（比 bug 本身更糟 ✗）。
        #   ⚠ `CLOSE_RAW`/`OPEN_RAW` **不动** ✓（停牌/涨跌停判定仍按真实价 ✓）。
        if str(rq.get("price_adjust") or "").strip().lower() == "none":
            panel = dict(panel)
            _cr = panel.get("CLOSE_RAW")
            if _cr is not None and len(_cr):
                panel["CLOSE"] = _cr.ffill().round(2)
                _orr = panel.get("OPEN_RAW")
                if _orr is not None and len(_orr):
                    panel["OPEN"] = _orr.ffill().round(2)
        t0 = time.perf_counter()
        _modes = ("event_even", "cash_even")
        # ★★ v2.1.66：**口径归一必须放在最外层** ✗→✓（用户 2026-10-12 实测报错：
        #   `UnboundLocalError: local variable '_fill_nav' referenced before assignment` ✗）
        #   —— v2.1.65 我把它定义在「回测**缓存未命中**」的 else 分支里 ✗，而下面
        #   「登记给导出用」那步**命中缓存时也会用它** ⇒ 第二次同口径请求（命中缓存 ✓）必炸 ✗。
        # ★★ v2.1.69：**兜底改成"跟随那次任务的请求"** ✗→✓（用户 2026-10-12 实测：
        #   面板选「次日收盘」✓，导出的 XLSX 参数页却写「次日开盘」✗）。
        #   根因：本函数要**三方**都同意口径 —— ① 本请求 `req.fill`（v2.1.65 起前端才发 ✗，
        #   老 bundle 不发 ⇒ `None` ✗）② 那次任务自己的 `rq["fill"]`（= 面板真正用的 ✓）
        #   ③ 默认 `t1_open` ✓。旧代码只认 ① ✗ ⇒ 老前端 ⇒ 退成 `t1_open` ✗ ⇒
        #   净值/导出登记都写错 ✗（而**事件研究本身**用的是 `rq["fill"]` = 次日收盘 ✓
        #   ⇒ 文件里"参数=开盘 ✗ / 曲线=收盘 ✓"自相矛盾 ✓，回灌必然对不上 ✓）。
        #   ⇒ 现在按 ① > ② > ③ 取 ✓（任何一条链传了口径都认 ✓，绝不会无声退回默认 ✗）。
        _fill_nav = str(req.fill or rq.get("fill") or "t1_open").strip().lower()
        if _fill_nav not in ("t1_open", "t1_close", "t_close"):
            _fill_nav = "t1_open"
        # ★★ v2.9.3：持仓上限进缓存键 ✓（改上限必须重算 ✗ 不能命中旧的 ✓）
        _maxpos = int(req.max_positions or 0)
        _btk = _nav_bt_key(codes, ev, start, end, k, req.cost, req.capital, _modes, _fill_nav,
                           max_positions=_maxpos)
        # 取消源（v1.20.5）：登记本次请求序号；同事件指纹来了更新的请求 ⇒ 本次立即自停 ✓
        _seq = _nav_req_begin(_nav_req_key(codes, ev, start, end))

        _tid_key = str(req.task_id or "")
        # ⚠⚠ 必须**清除旧的取消标记** ✗ —— 否则用户"关掉弹窗、再重新打开同一个公式"时
        #   会命中上次留下的标记 ⇒ **一开就被取消** ✓（v1.20.15 自查发现 ✓）。
        if _tid_key:
            _NAV_CANCEL.discard(_tid_key)

        def _cancel_cb():
            # ① 前端关了弹窗（v1.20.15 ✓）② 同事件指纹来了更新的请求（v1.20.5 ✓）⇒ 立即自停 ✓
            if _tid_key and _tid_key in _NAV_CANCEL:
                raise _NavCancelled()
            if _nav_req_current(_nav_req_key(codes, ev, start, end)) != _seq:
                raise _NavCancelled()
        # ⚠ 信号表要在**缓存判断之前**建好 ✗→✓：v2.1.4 起它还要进"给导出用的登记"（stash ✓），
        #   若只在"缓存未命中"分支里建 ⇒ 命中缓存时 `events` 未绑定 ⇒ UnboundLocalError ✗
        #   （实测被 ai_test/check_replay_identical.py 抓出来：HTTP 400 ✓）。建表本身是 O(事件数) ✓
        #   的浅拷贝级开销（几万行也就几毫秒 ✓），放在这里不亏 ✓。
        events = pd.DataFrame({"date": pd.to_datetime(ev["dt"]),
                               "code": ev["code"].astype(str), "side": 1})
        with _NAV_BT_LOCK:
            bt = _NAV_BT_CACHE.get(_btk)
        if bt is not None:
            timings["backtest_cached"] = True
        else:
            timings["backtest_cached"] = False
            # ★ v2.1.65：**fill 跟随请求** ✗→✓（原先写死 `"t1_open"` ✗ ⇒ 面板切"次日收盘"无效果 ✗）
            #   ⚠ `_fill_nav` 由外层统一算好（v2.1.66 修：别在分支里定义 ✗，命中缓存时下面还要用 ✓）
            bt = run_backtest(events, panel, hold_days=k, fill=_fill_nav, cost=float(req.cost),
                              capital=float(req.capital), strict_limit=True,
                              # ★ T1（v2.8.6）：判停口径跟随请求 ✓（`None` ⇒ 老口径 ✓ 数字不变 ✓）
                              limit_judge=(getattr(req, "limit_judge", None) or "legacy"),
                              # ★★ v2.9.3：`max_positions` 透传 ✓（让组合"有选择性" ✓）
                              max_positions=_maxpos,
                              alloc_modes=_modes, cancel_cb=_cancel_cb)
            with _NAV_BT_LOCK:
                while len(_NAV_BT_CACHE) >= _NAV_BT_CAP:
                    _NAV_BT_CACHE.pop(next(iter(_NAV_BT_CACHE)))
                _NAV_BT_CACHE[_btk] = bt
        timings["backtest"] = round(time.perf_counter() - t0, 3)
        if bt.nav is None:
            raise HTTPException(status_code=400, detail="回测没有产出净值：%s" % bt.diag.get("error"))
        # v2.1.0：登记给导出用（**不进响应** ✗）—— 导出侧据此从 `bt.trades` 派生每期持仓 ✓
        _stash_key = _nav_stash_key(req.task_id, req.factor_id, k, float(req.cost), float(req.capital))
        with _NAV_BT_LOCK:
            _NAV_BT_STASH.pop(_stash_key, None)
            # ★ v2.1.4：连同**这次回测的输入**一起登记（导出要原样搬走 ⇒ 回灌才能逐位一致 ✓）
            #   `signals` 是**信号日**（触发日 ✓）而不是成交日 —— 引擎会自己按 `fill` 成交 ✓
            _NAV_BT_STASH[_stash_key] = {
                "bt": bt, "signals": events,
                # ★ v2.1.65：登记**本次真实用的**口径（原来写死 `"t1_open"` ✗ ⇒ 导出文件跟着写错 ✗）
                "fill": _fill_nav, "strict_limit": True, "rebal_band": 0.0,
                # ★ T1（v2.8.6）：判停口径也要登记 ✓（导出据此写进文件 ⇒ 回灌才能逐位复现 ✓）
                "limit_judge": (getattr(req, "limit_judge", None) or "legacy"),
                "hold_days": int(k), "cost": float(req.cost), "capital": float(req.capital),
            }
            while len(_NAV_BT_STASH) > _NAV_BT_STASH_CAP:
                _NAV_BT_STASH.pop(next(iter(_NAV_BT_STASH)))
        t0 = time.perf_counter()
        bench = load_bench_wide([req.benchmark], start, end)
        nav = attach_benchmark(bt.nav, bench, req.benchmark)
        timings["benchmark"] = round(time.perf_counter() - t0, 3)
        stats = dict(bt.stats or {})
        # ★★ 2026-10-14（用户："加一条未触发组等权参照线" ✓）：
        #   为什么需要它 ✗→✓：面板上原来只有「沪深300」（灰虚线 ✓）与两条策略曲线 ✓ ——
        #   而**"事件研究"里的基准其实是「未触发组等权」**（超额曲线用的就是它 ✓），
        #   两者在同一张图上**没有可比参照** ⇒ 用户实测时把灰线当成了"全 A 等权" ✗
        #   （实测沪深300 = 0.957，而池内等权 = 1.4125 ✓ 差 48% ✗ ⇒ 结论会看反 ✓）。
        #   ∴ 把「未触发组等权」也画成一条曲线 ✓（同日剔除当日触发股 ✓、等权 ✓、无成本 ✓），
        #     与超额曲线**同源** ✓ ⇒ "事件到底跑没跑赢它自己的基准"一眼可见 ✓。
        # ★★ v2.9.3（用户 2026-10-17 ✓）：**「信号稀释」诊断** ✗→✓ ——
        #   用户当场质疑（完全正确 ✓）：「日配对每 60 日落后 0.45pp ⇒ 4 年该落后 7.5%，
        #   怎么净值只差 1.8%？」⇒ 实测根因：信号**每只票约 23 个交易日就重触发一次**，
        #   而持有期 60 天 ⇒ 任一时刻约 **93% 的票都在持仓** ⇒ 组合退化成「全市场等权」✗
        #   ⇒ 信号被完全稀释 ✓（实测 hold=60 时策略 1.4698 vs 参照 1.4747，只差 0.33% ✓）。
        #   ⇒ 这里把「平均重触发间隔 / 预估持仓覆盖率」算出来一起回传 ✓，
        #     前端在覆盖率过高时**标黄警告** ✓（否则这种图会被误读成"信号有效" ✗）。
        nav_diag: dict = {}
        try:
            _n_ev = int(len(ev))
            _n_code = int(pd.Series(ev["code"]).nunique()) if _n_ev else 0
            _n_day = int(len(pd.DatetimeIndex(nav.index).unique()))
            _gap = (float(_n_day) * float(_n_code) / float(_n_ev)) if (_n_ev and _n_code) else 0.0
            _cover = (1.0 - (1.0 - 1.0 / _gap) ** float(k)) if _gap > 0 else None
            _diluted = bool(_cover is not None and _cover >= 0.8)
            nav_diag = {"n_events": _n_ev, "n_codes": _n_code, "n_days": _n_day,
                        "retrigger_days": (round(_gap, 2) if _gap else None),
                        "hold_days": int(k), "max_positions": _maxpos,
                        "hold_coverage": (round(float(_cover), 4) if _cover is not None else None),
                        "diluted": _diluted,
                        "text": (("持有期 %d 天 ≥ 平均重触发间隔 %.1f 天 ⇒ 约 %.0f%% 的票在任一时刻都在持仓"
                                  " ⇒ 组合已退化为「全市场等权」，这条曲线基本反映的是 beta，不是信号 ✗"
                                  % (int(k), _gap, float(_cover) * 100.0))
                                 if _diluted else "")}
        except Exception:                                          # noqa: BLE001 —— 诊断失败不影响主结果 ✓
            nav_diag = {}
        # ★★ v2.9.5（用户 2026-10-17 ✓）：设了持仓上限**也照画参照线** ✗→✓ ——
        #   用户原话：「我改 50 只后**非触发组曲线没了**？」✓ —— 上一版（v2.9.3）为了"不给不可比对照"
        #   干脆**隐藏**了它 ✗，但用户要的是"能看见、心里有数"，不是"看不见" ✓。
        #   ⇒ 现在**照常画** ✓，只在下方如实标注：它**不限只数** ⇒ **仅作参照、不可直接比较** ✓
        #   （宁可给一条带警告的参照，也不让用户以为"曲线丢了"✗）。
        try:
            # ★★ v2.9.1（用户 2026-10-17 ✓）：**同持有期** ✓ —— 传当前 `k`（= 界面「持仓周期」✓）：
            #   旧的"只持 1 天"会让参照线天然吃亏（策略吃 60 天 beta、参照只吃 1 天 ✗）
            #   ⇒ 图上 1.449 vs 0.785 全是口径差 ✗。现在两条线持有期一致 ✓ 才叫可比 ✓。
            _un = _untrig_nav_series(state, ev, start, end, hold=int(k))
            if _un is not None and len(_un):
                _s = pd.Series({pd.Timestamp(d): float(v) for d, v in _un})
                nav = nav.copy()
                nav["bench_equal"] = _s.reindex(nav.index).ffill()
                try:
                    from ..engine.perf_metrics import compute_perf      # 与上面净值统计同一个 ✓
                    _ret = nav["bench_equal"].pct_change().fillna(0.0).to_numpy()
                    stats["bench_equal"] = compute_perf(_ret)
                except Exception:                                      # noqa: BLE001
                    pass
                if _maxpos:
                    nav_diag["bench_equal_note"] = (
                        "参照线（未触发组等权）**不限只数**，而本策略已设「最大同时持仓数 %d」"
                        " ⇒ 两者**口径不同、仅作参照**（不能拿它的高低直接判定信号好坏 ✓）"
                        % _maxpos)
        except Exception as _ue:                                      # noqa: BLE001
            # 参照线失败只影响展示（与基准失败同一策略 ✓）⇒ 不拖垮净值曲线 ✓
            timings["untrig_error"] = repr(_ue)[:160]
        return {"hold_days": k, "nav": nav_rows(nav), "stats": stats, "diag": bt.diag,
                "nav_columns": [str(c) for c in nav.columns], "nav_diag": nav_diag,
                "alloc_default": "event_even", "timings": timings}
    except _NavCancelled:
        # 被"更新的那次 k"顶掉 ⇒ 如实告知（前端此时通常已切到新的 key ✓，一般看不到这条 ✓）
        raise HTTPException(status_code=409, detail="已被更新的一次计算取代（旧请求自动取消）")
    except HTTPException:
        raise
    except Exception as e:                                      # noqa: BLE001
        # 这类失败基本都是数据侧问题（标的/区间/字段缺失）⇒ 给**可读原因**而不是裸 500
        raise HTTPException(status_code=400, detail="净值计算失败：%s: %s" % (type(e).__name__, e))

@router.get("/event-study/progress/{task_id}", summary="查询事件研究任务进度")
def event_study_progress(task_id: str):
    state = _est_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {
        "task_id": task_id,
        "status": state["status"],
        "progress": float(state.get("progress", 0.0)),
        "message": state.get("message", ""),
        "result": state.get("result"),
        "error": state.get("error"),
        # v1.20.36：耗时明细 + 缓存命中标记（前端据此自适应"自动跟随 / 手工按钮"，不显示耗时数字也用它做判定）
        "timings": state.get("timings"),
        "cached": state.get("cached"),
    }

@router.post("/event-study/cancel/{task_id}", summary="取消事件研究任务")
def event_study_cancel(task_id: str):
    state = _est_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if state.get("status") != "running":
        return {"ok": False, "message": "任务已结束，无需取消"}
    state["cancel_requested"] = True
    try:
        manager = get_task_manager(config.WORK_DIR)
        manager.wake_external_waiters()
    except Exception:
        pass
    return {"ok": True, "message": "已请求取消，正在终止..."}

@router.post("/event-study/clear", summary="清理已结束的事件研究任务与结果")
def event_study_clear():
    with _EST_LOCK:
        done_keys = [k for k, v in _EST_TASKS.items()
                     if v.get("status") in ("success", "failed", "cancelled")]
        for k in done_keys:
            _EST_TASKS.pop(k, None)
    return {"ok": True, "cleared": len(done_keys)}

