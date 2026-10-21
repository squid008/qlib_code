# -*- coding: utf-8 -*-
"""**单因子测试**接口（从 `routers/factors.py` 拆出 ✓ P1-A-2，2026-10-18）。

## 内容

· 请求模型在 `factors_models` ✓（`SingleFactorTestFactor` / `SingleFactorTestRequest` ✓）；
· 期初滚动与离场曲线 helper：`_attach_period_roll` / `_attach_signal_roll` /
  `_panel_period_roll_nav` / `_exit_matrix` / `_exit_pre_mats` / `_exit_list` /
  `_apply_exit_validation` / `_period_roll_pick` / `_signal_exit_nav` ✓；
· 结果渲染 `_sft_render_view` ✓；5 个端点（提交 / 进度 / 取消 / 清理 / 列表 ✓）。

## ⚠ URL 不变 ✓

自带 `APIRouter(prefix="/api/factors")` ✓（与原同一 prefix ✓）；
任务表与锁在 `factors_state` ✓（`_sft_get` / `_SFT_TASKS` … ✓）。
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
    _SFT_MAX_TASKS, _SFT_RESULT_KEEP, _SFT_TASKS, _SFT_LOCK, _sft_store,
    _sft_trim_results, _sft_get, est_export_state, sft_export_state, _EST_TASKS,
    _EST_LOCK, _EST_RESULT_KEEP, _est_get, _est_store, _est_trim,
    _UNTRIG_CACHE, _UNTRIG_LOCK, _UNTRIG_CAP, _NAV_PANEL_CACHE, _NAV_PANEL_LOCK,
    _NAV_BT_CACHE, _NAV_BT_LOCK, _NAV_BT_CAP, _NAV_BT_STASH, _NAV_BT_STASH_CAP,
    _PR_BT_STASH, _PR_BT_CAP, _pr_key, pr_bt_stash_put, pr_bt_stash_get,
    clear_nav_caches, _nav_stash_key, nav_bt_stash_get, _NAV_REQ_SEQ, _NAV_REQ_LOCK,
    _NAV_REQ_KEEP, _NavCancelled, _ev_fingerprint, _nav_req_key, _nav_req_begin,
    _nav_req_current, _NAV_CANCEL, _NAV_CANCEL_MAX, _nav_bt_key, _nav_cache_key,
    _nav_panel_cached, _json_safe,
)
# ---- ★ P1-A-2 补遗（2026-10-18 ✓）：ruff F821 实测出来的 9 个依赖 ----
from .. import config                                                    # noqa: E402
from ..engine.task_manager import get_task_manager                        # noqa: E402
from ..factors.exit_rules import EntryRule, split_entry_rule, uses_entry  # noqa: E402
from ..factors.holdings_index import HoldingsIndex                        # noqa: E402
from ..factors.single_test import (FactorTestCancelled, _bad_date_arg,     # noqa: E402
                                   run_single_factor_tests)

def _log_swallowed(where, exc):
    """**留痕兜底** ✓（P2-A，2026-10-18）—— 吞异常后写一行 debug 日志。

    ⚠ 本模块**没有模块级 logger** ✗（`factors_single` 是内联 `get_logger("factors")` 用法 ✓）
      ⇒ 这里用 `logging.getLogger(__name__)` ✓（= `app.routers.xxx` ✓，能与其它日志一起看 ✓）。
    ⚠ 全程 `contextlib.suppress` ✓：**日志自身失败绝不许抛** ✗（2.9.26 事故教训 ✓）。
    """
    import contextlib
    import logging as _logging

    with contextlib.suppress(Exception):
        lg = globals().get("logger") or _logging.getLogger(__name__)
        lg.debug("[吞] %s: %r", where, exc)


router = APIRouter(prefix="/api/factors", tags=["factors"])  # noqa: E402

def _period_roll_pick(hk_state: dict, row: dict, item: dict) -> tuple:
    """解析**某一档**（`item`）的每期名单（v2.1.38 ✓）。返回 `(err, dates, lists)`。

    ⚠ `HoldingsIndex` 的键 = `(kind, k, quantile)`，而**分位档在结果里的 `k` 被改写成了
      "中位持有只数"** ✗（`single_test.py` 组装 item 时 `int(np.median(...))`）⇒ 用 item 的 k
      去查必然 miss ✗（实测就是这条：报"没有该档位的每期持仓"）⇒ 多键**依次尝试** ✓。
    ⚠ 本函数**只解析名单、不取价** ✓ —— 取价是全流程唯一的"慢"来源（全 A 10~20s ✗），
      必须由调用方**一次**取好（= 所有档位的并集 ✓）再喂进来（见 `_attach_period_roll` ✓）。
    """
    from ..factors.holdings_index import HoldingsIndex

    tc = row.get("topk_curves") or {}
    expr = str(row.get("expression") or row.get("name") or "")
    h = row.get("horizon")
    idx = HoldingsIndex.from_state(hk_state)
    cands = [(item.get("kind"), item.get("k"), item.get("quantile")),
             (item.get("kind"), 0, item.get("quantile")),
             ("decile", 0, tc.get("default_quantile")),
             ("decile", 0, None)]
    lists = None
    for _c in cands:
        lists = idx.get(expr, h, _c[0], _c[1], _c[2])
        if lists:
            break
    if not lists:
        # 表达式对不上时（例如结果里是别名）⇒ 按 horizon 找唯一的那个桶 ✓
        for _e, _v in (idx.factors or {}).items():
            if str(h) in (_v or {}):
                for _c in cands:
                    lists = idx.get(_e, h, _c[0], _c[1], _c[2])
                    if lists:
                        expr = _e
                        break
            if lists:
                break
    dates = idx.dates(expr, h)
    if not lists or not dates:
        return ("没有该档位的每期持仓（HoldingsIndex 未记录 ⇒ 请确认已跑连续因子档位）", [], [])
    return (None, dates, lists)

def _signal_exit_nav(req, row: dict, evs: dict, item: dict, panel: dict, *,
                     with_exit: bool, exit_mask=None, exit_cache: Optional[dict] = None,
                     exit_index: int = 0, exit_name: str = "", exit_rule=None) -> dict:
    """**0/1 信号因子**的含离场回测（**事件驱动口径** ✓，v2.8.0）—— 返回与 `period_roll` 同构的 dict ✓。

    **为什么单开这一条**（用户 2026-10-15 实测发现 ✓）：0/1 信号因子在单因子面板里**没有"每期名单"**
    （`topk_curves` 为空 ✗，它走的是事件研究 ✓）⇒ `period_roll` 只会报"该行没有持仓曲线档位"✗
    ⇒ **含离场回测根本跑不起来** ✗（界面看着像"勾了没生效" ✗）。
    这里改用**平台本来处理 0/1 信号的那个引擎**（`signals/engine.py` ✓，与「交易信号测试」同源 ✓）：

      · **进场** = 该因子的**触发事件**（T 日触发 ⇒ T+1 按面板「成交时点」买入 ✓，持有 `预测周期` 个交易日 ✓）；
      · **离场** = 离场信号触发的 (日期, 股票) ⇒ 作为**卖出信号**喂进同一个引擎 ✓
        （提前平仓 ✓；跌停/停牌卖不掉 ⇒ 逐日顺延 ✓ —— 引擎既有规则 ✓）；
      · **对照** = **同一批进场信号、不带离场** ✓（两条同源 ⇒ Δ 才解释得清 ✓）。

    ⚠ 它与"每期全仓滚动"**不是同一口径**（那条是连续因子的 ✓）：结果里带 `mode="signal"` 标注 ✓，
      前端据此写明"信号驱动（事件）"✓（不混着叫"实盘口径"✗）。
    """
    from ..signals.engine import run_backtest

    ev = evs.get(str(row.get("expression") or ""))
    if ev is None or not len(ev):
        return {"error": "该 0/1 因子的触发事件没找到 ⇒ 无法跑信号驱动回测（请重跑该因子）"}
    try:
        ent = pd.DataFrame({"date": pd.to_datetime(ev["dt"]),
                            "code": ev["code"].astype(str)})
        ent["side"] = 1
        frames = [ent]
        # ⚠ 两种取法都认（`exit_mask` 形参 或 `item["mask"]` ✓）—— 2026-10-16 我自己踩过：
        #   调用方把 mask 放进 `item` 而没传形参 ⇒ 离场那一支**被静默跳过** ✗（两条曲线一模一样 ✗，
        #   看起来像"离场没生效"✓）⇒ 这里统一兜住 ✓。
        _mask = exit_mask if exit_mask is not None else (item or {}).get("mask")
        if with_exit and _mask is not None:
            mat = _exit_matrix(_mask, panel, exit_cache, ei=exit_index)
            if mat is None:
                return {"error": "离场信号矩阵不可用（形状与行情面板不一致）"}
            cl = panel["CLOSE"]
            _ii, _cc = np.nonzero(mat)
            if len(_ii):
                frames.append(pd.DataFrame({
                    "date": pd.to_datetime(cl.index[_ii]),
                    "code": [str(cl.columns[k]) for k in _cc], "side": -1}))
        sig = pd.concat(frames, ignore_index=True)
        hold = max(1, int(row.get("horizon") or 20))
        bt = run_backtest(sig, panel, hold_days=hold, fill=str(req.fill), cost=float(req.cost),
                          capital=float(req.capital), strict_limit=bool(req.strict_limit),
                          # ★ T1（v2.8.6）：涨停判定口径透传 ✓（`None` ⇒ 老口径 ✓ 逐位不变 ✓）
                          limit_judge=(req.limit_judge or "legacy"),
                          # ★★ v2.8.8b（用户 2026-10-16 "接着做真算出来" ✓）：**含入场价的离场规则** ✓
                          #   —— 事件驱动引擎现在也维护"逐日入场价" ✓（`run_backtest(exit_rule=…)` ✓，
                          #   与 `period_roll` 同一套语义 ✓）⇒ 0/1 信号也能真算"入场价止损" ✓。
                          #   ⚠ 只有 `with_exit` 才启用 ✓（"对照"那条必须干净 ✓）。
                          exit_rule=(exit_rule if with_exit else None),
                          alloc_modes=["event_even"])
        if bt.nav is None or "event_even" not in getattr(bt.nav, "columns", []):
            _d = bt.diag or {}
            return {"error": "信号驱动回测无净值：%s"
                             % ("; ".join(str(v) for v in _d.values())[:200] or "未知原因")}
        nav = bt.nav["event_even"]
        st = (bt.stats or {}).get("event_even") or {}
        return {"dates": [str(d)[:10] for d in nav.index],
                "nav": [round(float(x), 9) for x in nav.to_numpy()],
                "label": ("信号驱动（含离场「%s」）" % exit_name if with_exit else "信号驱动（对照）"),
                "mode": "signal", "hold_days": hold, "with_exit": bool(with_exit),
                "exit_index": (int(exit_index) if with_exit else None),
                "exit_name": (str(exit_name) if with_exit else ""),
                # ★★ v2.8.8b（用户 2026-10-16 ✓）：把离场通道的**诊断**带出来 ✓（与
                #   `_panel_period_roll_nav` 的形状对齐 ✓ ⇒ 前端 `ExitCurveModal` 同一套读法 ✓）：
                #   `exit_note` = 未生效原因 ✓ / `exit_signals`·`exit_sells` = 触发与成交笔数 ✓
                #   ⚠ 键名取自引擎 stats（v2.8.8b 新增 ✓）；老任务没有 ⇒ 全为 None ⇒ 前端不误报 ✓。
                "diag": {"exit_note": str(st.get("exit_note") or ""),
                         "exit_enabled": st.get("exit_enabled"),
                         "exit_rule": st.get("exit_rule"),
                         "exit_signals": st.get("exit_signals"),
                         "exit_sells": st.get("exit_sells")},
                "stats": {k: v for k, v in st.items() if k != "perf"},
                "perf": st.get("perf")}
    except Exception as e:                                            # noqa: BLE001
        return {"error": "信号驱动回测失败：%s: %s" % (type(e).__name__, e)}

def _attach_signal_roll(req, row: dict, evs: dict, progress_cb=None,
                        exit_items: Optional[list] = None,
                        exit_cache: Optional[dict] = None, hk_state: Optional[dict] = None) -> dict:
    """0/1 信号因子的「信号驱动（对照 + 逐个离场）」整套回测 ✓（形状与 `period_roll` 一致 ✓）。"""
    from ..signals.pricing import load_price_panel

    def _np(frac: float, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(frac, msg)
            except Exception as e:  # noqa: BLE001
                _log_swallowed("factors_single.py", e)
                pass

    _exits = list(getattr(req, "exit_factors", None) or [])
    # 0/1 信号的触发事件只覆盖**有信号的股票** ⇒ 取价范围照它来（比全池小得多 ✓ 快 ✓）
    ev = evs.get(str(row.get("expression") or ""))
    codes = sorted({str(c) for c in (ev["code"].tolist() if ev is not None and len(ev) else [])})
    if not codes:
        row["period_roll"] = {"error": "该 0/1 因子的触发事件为空 ⇒ 无法回测"}
        row["period_roll_items"] = []
        return row["period_roll"]
    _np(0.10, "信号驱动（事件口径）：取行情面板（%d 只，较慢请稍等）…" % len(codes))
    try:
        panel = load_price_panel(codes, str(req.start_date), str(req.end_date), need_open=True)
    except Exception as e:                                            # noqa: BLE001
        row["period_roll"] = {"error": "取价失败：%r" % (e,)}
        row["period_roll_items"] = []
        return row["period_roll"]
    # 对照（不带离场 ✓ —— 用户定的"对照列必算" ✓）
    _np(0.35, "信号驱动（事件口径）：对照回测…")
    cmp_one = _signal_exit_nav(req, row, evs, {}, panel, with_exit=False)
    out = [cmp_one]
    if _exits:
        rows_exit: list = []
        n = len(_exits)
        for _ei, _ex in enumerate(_exits):
            _ename = str(getattr(_ex, "name", "") or getattr(_ex, "id", "") or "")
            _it0 = ((exit_items or [])[_ei]
                    if (exit_items and _ei < len(exit_items)) else {}) or {}
            # ★★ v2.8.8b（用户 2026-10-16 报障 ✓）：**0/1 信号 + "含入场价"离场 ⇒ 明确报"本档未计算"** ✗→✓
            #   为什么必须拦：含入场价的离场是**回测状态量**（`ENTRY_PRICE`/`ENTRY_FIRST` ✓），
            #   上面这条事件驱动路径**没有**逐日入场价通道 ✗ ⇒ `_mask` 为 `None` ⇒ 离场那一支
            #   **被静默跳过** ✗ ⇒ 含离场曲线与对照**逐位相同**、Δ 全 0 ✗，而 `error=None` ✓
            #   —— 用户原话：「**事件研究的离场曲线肯定也没算对**」✓，实测（`AR13_ID60`，
            #   `is_binary=True` ✓）三个统计键全是 `None` ✓ ⇒ **判断完全正确** ✓。
            #   ⇒ 这里**大声说**：这一档**未计算** ✓（不是"算了但没差异" ✗ —— 那会让人白读一条 Δ0 的表 ✓）。
            # ★★ v2.8.8b（用户 2026-10-16「接着做真算出来」✓）：**含入场价的离场 ⇒ 现在真的算** ✗→✓
            #   引擎侧已补上"逐日入场价"通道 ✓（`signals/engine.run_backtest(exit_rule=…)` ✓，
            #   与 `period_roll` 同一套滞后与顺延规则 ✓）⇒ 0/1 信号也能真算「入场价止损」✓。
            #   装配（骨架 `skel` + 预计算列对齐 ✓）与 `period_roll` 那条**逐行同一套** ✓；
            #   ⚠ 装不上 ⇒ **明确报"未计算"** ✗（绝不再给一条"看起来和对照一样"的假曲线 ✓）。
            _rule = None
            if _it0.get("mask") is None:
                _err_msg = ""
                if _it0.get("skel") is not None:
                    try:
                        _rule = EntryRule(_it0["skel"], _it0.get("pre_exprs") or [],
                                          float(req.exit_threshold),
                                          _exit_pre_mats(_it0.get("pre_series") or [], panel,
                                                         exit_cache, ei=_ei))
                    except Exception as _e:                          # noqa: BLE001
                        _rule = None
                        _err_msg = "%s: %s" % (type(_e).__name__, _e)
                else:
                    _err_msg = str(_it0.get("error") or "该离场项缺骨架（含入场价的公式没装配 ✓）")
                if _rule is None:
                    _one = {"mode": "signal",
                            "label": "信号驱动（含离场「%s」· 未计算）" % (_ename or "?"),
                            "with_exit": True, "exit_index": _ei, "exit_name": _ename,
                            "error": ("含入场价的离场规则**没能装配** ⇒ 本档**未计算** ✗（%s）。"
                                      "请把这条公式反馈给开发 ✓" % (_err_msg or "未知原因"))}
                    rows_exit.append({"exit": {"index": _ei, "name": _ename,
                                               "id": str(getattr(_ex, "id", "") or ""),
                                               "expression": str(getattr(_ex, "expression", "") or "")},
                                      "items": [_one], "first": _one})
                    continue
            _np(0.45 + 0.5 * _ei / max(1, n),
                "信号驱动（事件口径 · 含离场「%s」）：回测中…" % (_ename or "?"))
            _one = _signal_exit_nav(req, row, evs, _it0, panel, with_exit=True,
                                    exit_mask=_it0.get("mask"), exit_cache=exit_cache,
                                    # ★ v2.8.8b：含入场价的离场 ⇒ 把装配好的规则交下去 ✓
                                    #   （非入场价型 ⇒ `_rule` 为 None ✓，走既有的 `exit_mask` 通道 ✓）
                                    exit_rule=_rule,
                                    exit_index=_ei, exit_name=_ename)
            rows_exit.append({"exit": {"index": _ei, "name": _ename,
                                       "id": str(getattr(_ex, "id", "") or ""),
                                       "expression": str(getattr(_ex, "expression", "") or "")},
                              "items": [_one], "first": _one})
        row["exit_rolls"] = rows_exit
        row["exit_roll_items"] = rows_exit[0]["items"]     # 兼容 v2.4.3 的单数取法 ✓
        row["exit_roll"] = rows_exit[0]["first"]
    row["period_roll_items"] = out
    row["period_roll"] = out[0]
    _np(1.0, "信号驱动（事件口径）完成")
    return row["period_roll"]

def _attach_period_roll(req, hk_state: dict, row: dict, progress_cb=None,
                        exit_cache: Optional[dict] = None,
                        exit_items: Optional[list] = None) -> dict:
    """给**本行的每个档位**各算一条实盘口径净值（v2.1.38 ✓）。

    ★ 为什么改（用户 2026-10-12："应该是我测试的时候选了哪些档位就算哪些档位" ✓）：
      v2.1.27 只算了 `items[0]`（= 默认档 = 最强分位组 ✗）⇒ 弹窗里切到「固定 K=967」时，
      红线与那行指标**仍是默认档的** ✗（用户实测发现 ✓ —— 因为结果只挂在 row 级 ✓）。
      现在：`row["period_roll_items"] = [与 `items` **同序**的每档结果 ✓]`，
      前端按**当前选中档的下标**取 ✓ ⇒ 切档时曲线与指标一起变 ✓；
      并保留 `row["period_roll"] = period_roll_items[0]` ✓（= 默认档 ✓，兼容旧取法/旧脚本 ✓）。

    ⚠ **行情面板只取一次** ✓（这是关键 ✗→✓）：先解析出所有档位的名单、取**并集**代码去
      `load_price_panel` ✓（10 档若各取一次 = 10~20s × 10 ✗ 不可接受 ✓）；
      而 `run_period_roll` 只按**信号里出现的**标的建矩阵（`period_roll.py::_prepare` ✓
      ⇒ `prep["codes"]` 来自 signals ✓）⇒ 面板里多出来的代码**不增加回测开销** ✓。
    ⚠ 任何失败一律落**各自档位**的 `error` 字段 ✓ —— 它是附加信息，绝不拖垮统计（与 `perf` 同规矩 ✓）。

    ★ v2.4.2（用户 2026-10-15 反馈 ✓）：加 `progress_cb(frac 0~1, msg)` —— 本函数是"统计完成之后
      还要跑十几秒~几分钟"的那一段（先取全池行情面板、再逐档逐日回测 ✗），必须能报进度，
      否则进度条会顶在 100% 干等 ✗（详见 `_sft_render_view` 的 95% 封顶 ✓）。
    """
    from ..signals.pricing import load_price_panel

    def _np(frac: float, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(frac, msg)
            except Exception as e:  # noqa: BLE001
                _log_swallowed("factors_single.py", e)
                pass                              # 报进度失败绝不影响回测 ✓

    tc = row.get("topk_curves") or {}
    its = tc.get("items") or []
    if not its:
        row["period_roll"] = {"error": "该行没有持仓曲线档位（topk_curves.items 为空）"}
        row["period_roll_items"] = []
        return row["period_roll"]
    _np(0.02, "每期全仓滚动（实盘口径）：解析各档名单…")
    picks: list = []                              # [(item, (err, dates, lists)), ...]
    codes: set = set()
    for _it in its:
        try:
            _p = _period_roll_pick(hk_state, row, _it)
        except Exception as e:                    # noqa: BLE001
            _p = (repr(e), [], [])
        picks.append((_it, _p))
        if not _p[0]:
            for _lst in (_p[2] or []):
                codes.update(str(c) for c in _lst)
    panel, perr = None, None
    if codes:
        try:
            # ⚠ 这一步是全池取价（全 A 十几秒 ✗）⇒ 文案里点明"较慢"以免被当卡死 ✓
            _np(0.10, "每期全仓滚动（实盘口径）：取全池行情面板（%d 只，约十几秒，请勿刷新）…"
                % len(codes))
            panel = load_price_panel(sorted(codes), str(req.start_date), str(req.end_date),
                                     need_open=True)
        except Exception as e:                    # noqa: BLE001
            perr = repr(e)
    out: list = []
    n_it = max(1, len(picks))
    for _k, (_it, _p) in enumerate(picks, start=1):
        if _p[0] or not panel:
            out.append({"error": _p[0] or perr or "行情面板为空（取价失败）"})
            continue
        _np(0.10 + 0.88 * (_k - 1) / n_it,
            "每期全仓滚动（实盘口径）：第 %d/%d 档回测中…" % (_k, n_it))
        out.append(_panel_period_roll_nav(req, hk_state, row, item=_it, panel=panel))
    row["period_roll_items"] = out
    row["period_roll"] = out[0] if out else {}
    # ★★ v2.4.3（用户 2026-10-15 拍板 ✓）：勾了离场因子 ⇒ **同口径再跑一遍"含离场"**
    #   —— 上面那份就是**必算的对照列**（"不离场"✓，口径与数字**逐位不变** ✓）。
    #   两条同源（同一份名单、同一份价格面板、同一个引擎 ✓）⇒ 差值才解释得清 ✓
    #   （这也是我们自己的老教训：**没有可比参照 ⇒ 结论会看反** ✗）。
    # ★★ v2.5.0（用户 2026-10-15：\"能不能搞成多选啊，要矩阵化\" ✓）：**逐个离场各跑一遍**
    #   ⇒ 一行结果里就有 `1 + N离` 条同源同口径曲线 ✓（第 0 条永远是"不离场"对照 ✓）。
    #   ⚠ 离场列表来自 `req.exit_factors`（路由已规范化 ✓：丢空、去重、同公式拦截 ✓）。
    _exits = list(getattr(req, "exit_factors", None) or [])
    if _exits:
        # 进度：对照那一轮占 0.10→0.55，各离场一起分 0.55→0.98 ✓（总回测数 = (1+N)×档数 ✓）
        _n_pass = 1 + len(_exits)
        _exits_err: dict = {}                     # 离场下标 → 编译/求值失败原因 ✓（落各自档位 ✓）
        _rows_exit: list = []
        for _ei, _ex in enumerate(_exits):
            _ename = str(getattr(_ex, "name", "") or getattr(_ex, "id", "") or "")
            _it0 = ((exit_items or [])[_ei]
                    if (exit_items and _ei < len(exit_items)) else {}) or {}
            _mask = _it0.get("mask")
            # ★ v2.7.0：**含入场价**的离场公式 ⇒ 用单因子侧拆好的「骨架 + 预计算列」✓
            #   （与入场价无关的部分由面板算 ✓ ⇒ 任意字段/算子都能用 ✓；这里只把列对齐到
            #    本轮回测面板 ✓ + 绑定入场价 ✓）。失败 ⇒ 落到档位 error ✓（绝不拖垮统计 ✓）
            _rule = None
            if _it0.get("skel") is not None:
                try:
                    _rule = EntryRule(_it0["skel"], _it0.get("pre_exprs") or [],
                                      float(req.exit_threshold),
                                      _exit_pre_mats(_it0.get("pre_series") or [], panel,
                                                     exit_cache, ei=_ei))
                except Exception as _e:                          # noqa: BLE001
                    _rule = None
                    _exits_err[_ei] = str(_e)
            elif _it0.get("entry_dependent") and _it0.get("error"):
                _exits_err[_ei] = str(_it0["error"])
            elif _mask is None:
                # ★★ v2.8.8b **修正我自己前一版的错** ✗→✓（用户 2026-10-16 截图当场抓到 ✓）：
                #   这个"装配失败"兜底**只能**在"**既没骨架、也没 mask**"时触发 ✗ ——
                #   我第一版写成**无条件 `else`** ✗ ⇒ 把**普通离场**（`换手率离场` 这类**不含入场价**的 ✓，
                #   它们靠 `mask` 走"整列预计算"路径 ✓）也当失败拦掉了 ✗✗ ⇒ 那一档直接报
                #   "没能装配"、**根本没算** ✓（截图里 `负市值对数+换手率离场` 就是这个 ✓）。
                #   ⇒ 判据改成"**没有任何可用的离场输入**"才对 ✓：有 `mask` ⇒ 照常跑 ✓。
                _exits_err[_ei] = (
                    "这档离场**没有任何可用输入**（既没预计算 mask、也没装出规则骨架）⇒ 本档已按"
                    "**不离场**跑 ✗（所以曲线会与对照逐位相同 ✓）。请把这条公式反馈给开发 ✓")
            out_exit: list = []
            for _k, (_it, _p) in enumerate(picks, start=1):
                if _p[0] or not panel:
                    out_exit.append({"error": _p[0] or perr or "行情面板为空（取价失败）"})
                    continue
                _np(0.55 + 0.43 * ((_ei * n_it + (_k - 1)) / max(1, _n_pass * n_it)),
                    "含离场「%s」：第 %d/%d 档回测中…" % (_ename or "?", _k, n_it))
                if _exits_err.get(_ei):
                    out_exit.append({"error": _exits_err[_ei]})
                    continue
                out_exit.append(_panel_period_roll_nav(
                    req, hk_state, row, item=_it, panel=panel, with_exit=True,
                    exit_mask=_mask, exit_cache=exit_cache,
                    exit_index=_ei, exit_name=_ename, exit_rule=_rule))
            _rows_exit.append({
                "exit": {"index": _ei, "name": _ename,
                         "id": str(getattr(_ex, "id", "") or ""),
                         "expression": str(getattr(_ex, "expression", "") or "")},
                "items": out_exit,
                "first": (out_exit[0] if out_exit else {}),
            })
        row["exit_rolls"] = _rows_exit
        # 兼容 v2.4.3 的**单数**取法 ✓（= 第一个离场 ✓）
        row["exit_roll_items"] = _rows_exit[0]["items"]
        row["exit_roll"] = _rows_exit[0]["first"]
    _np(1.0, "每期全仓滚动完成")
    return row["period_roll"]

def _exit_list(req) -> list:
    """规范化后的**离场列表**（v2.5.0 多选 ✓）：丢空表达式、**按表达式去重** ✓、保序 ✓。"""
    out, seen = [], set()
    for e in list(getattr(req, "exit_factors", None) or []) + \
            ([req.exit_factor] if getattr(req, "exit_factor", None) is not None else []):
        expr = ((e.expression or "").strip() if e is not None else "")
        if not expr or expr in seen:
            continue
        seen.add(expr)
        out.append(e)
    return out

def _apply_exit_validation(req) -> None:
    """离场因子的**前置校验**（v2.4.3 / v2.5.0 多选 ✓）—— 纯函数、**在入队前**跑 ✓。

    ① 空表达式 ⇒ 丢掉 ✓（前端清理选择时常见 ✓）；同一公式选两次 ⇒ **只算一遍** ✓；
    ② **同公式既进又出 ⇒ 直接拦** ✗（用户选的"直接拦"✓）：同日进+出时，按"同日不交易"规则
       会**永远开不了仓**（净值恒 1 ✓）；若照做 ⇒ 买完就卖、被手续费一层层磨 ✗ —— 两种都没意义 ✓；
       ⚠ 多选下要**逐个**查 ✓（v2.4.3 只查了一个 ✗）；
    ③ 勾了离场 ⇒ **自动打开 `period_roll`** ✓（离场只能作用在"真金白银"那条回测上 ✓）；
    ④ 规范化结果**写回 `req.exit_factors`** ✓（下游只看这一个字段 ✓；单数形态归一后置 None ✓）。
    """
    items = _exit_list(req)
    req.exit_factors = items
    req.exit_factor = None

    # 入场价是回测状态量 ⇒ 只能出现在离场公式里 ✓；⚠ 本段必须在 `if not items: return` **之前**
    #（v2.10.44 ✗→✓：原在之后 ⇒ 没勾离场时这道闸根本不跑 ✗，详见 change_log）
    for f in (req.factors or []):
        if uses_entry(str(f.expression or "")):
            raise HTTPException(
                status_code=400,
                detail="「进场因子」里不能使用 ENTRY_PRICE / ENTRY_FIRST（入场价）✗ —— "
                       "它们是**回测状态量**（取决于你实际在哪一根买入 ✓），只有当**离场条件**才有意义 ✓；"
                       "请把它们写在离场公式里（例：`止损: CLOSE < ENTRY_PRICE*0.95;`）✓")

    if not items:
        return
    entries = {(f.expression or "").strip(): f for f in (req.factors or [])}
    for e in items:
        expr = (e.expression or "").strip()
        # ★ v2.6.0 / v2.7.0：含入场价的离场公式 ⇒ **入队前就拆一遍做校验** ✓
        #   （不支持的写法立刻 400 ✓，别等回测跑一半才炸 ✗）
        if uses_entry(expr):
            try:
                split_entry_rule(expr, float(getattr(req, "exit_threshold", 0.0) or 0.0))
            except Exception as _e:                               # noqa: BLE001
                raise HTTPException(
                    status_code=400,
                    detail="离场公式「%s」不支持 ✗：%s" % (e.name or e.id or expr[:30], _e))
        if expr in entries:
            raise HTTPException(
                status_code=400,
                detail="「离场因子」与某个「进场因子」是**同一个公式**（%s）✗ ⇒ 会在进场当天就离场："
                       "按『同日进出不交易』规则 ⇒ 永远开不了仓（净值恒 1）；照做 ⇒ 买完就卖、"
                       "被手续费一层层磨掉 ⇒ 两种都没有意义 ✓ 请换一个公式 ✓"
                       % (e.name or e.id or expr[:40]))
    req.period_roll = True

def _exit_matrix(mask, panel: dict, cache: Optional[dict] = None, ei: int = 0):
    """把单因子侧算好的离场布尔 Series 对齐成 **(T×C) 布尔矩阵** ✓（v2.4.3 / v2.5.0 ✓）。

    · 输入 = `run_single_factor_tests(exit_out=…)` 的 `items[ei]["mask"]`（索引 (instrument, datetime) ✓）；
    · 输出与 `panel["CLOSE"]` 的**日历/标的逐位对齐** ✓（`reindex` + `fillna(False)` ✓）——
      这是 `run_period_roll` 能吃的前提 ✓（形状不符它会自己**关掉离场**并记 diag ✓，绝不崩 ✗）；
    · `cache` = 调用方持有的小字典 ✓，键含 `(离场下标, 面板特征)` ✓（多选下每个离场各自一份 ✓；
      `_attach_period_roll` 是**逐行**调用的 ⇒ 不缓存会把 "unstack + reindex" 重复做很多遍 ✗）；
      面板换了（区间/标的变了）⇒ 旧键清掉 ✓（内存安全 ✓）。
    """
    if mask is None or not panel:
        return None
    cl = panel.get("CLOSE")
    if cl is None or not len(cl):
        return None
    pkey = (str(cl.index[0])[:10], str(cl.index[-1])[:10], len(cl.columns))
    key = (int(ei), pkey)
    if cache is not None and key in cache:
        return cache[key]
    try:
        w = mask.unstack(level="instrument")
        w = w.reindex(index=cl.index, columns=list(cl.columns)).fillna(False)
        mat = w.to_numpy(dtype=bool)
    except Exception as e:  # noqa: BLE001
        _log_swallowed("factors_single.py", e)
        mat = None
    if cache is not None:
        for k in [k for k in cache if k[1] != pkey]:                # 面板变了 ⇒ 旧的全丢 ✓
            cache.pop(k, None)
        cache[key] = mat
    return mat

def _exit_pre_mats(pre_series, panel: dict, cache: Optional[dict] = None, ei: int = 0):
    """把"入场价离场规则"的**预计算列**（面板侧算好的 Series ✓）对齐成 **(T×C) 矩阵** ✓。

    ★ v2.7.0（用户 2026-10-15 指出"不能只支持 C/O"✓）：含入场价的离场公式里，**与入场价无关的
      子树**（`MA(C,5)` / `TURNOVERRATE` / `MARKET_CAP` / H/L/V…）由**面板**当普通字段列算出来 ✓
      ⇒ 这里只需按回测面板的日历/标的 **reindex** ✓（`fillna(NaN)` ⇒ 与它比较恒为假 ✓，
      不会凭空触发 ✓）。**没取到 ⇒ 明确报错**，绝不静默算错 ✗（差值会骗人 ✓）。
    """
    if not pre_series or not panel:
        return []
    cl = panel.get("CLOSE")
    if cl is None or not len(cl):
        raise ValueError("行情面板为空（取价失败）")
    pkey = (str(cl.index[0])[:10], str(cl.index[-1])[:10], len(cl.columns))
    key = (int(ei), pkey)
    if cache is not None and key in cache:
        return cache[key]
    out = []
    for s in pre_series:
        try:
            w = s.unstack(level="instrument")
            w = w.reindex(index=cl.index, columns=list(cl.columns))
            out.append(w.to_numpy(dtype=np.float64))
        except Exception as e:                                    # noqa: BLE001
            raise ValueError("离场预计算列对齐失败：%s: %s" % (type(e).__name__, e)) from e
    if cache is not None:
        for k in [k for k in cache if k[1] != pkey]:              # 面板变了 ⇒ 旧的全丢 ✓
            cache.pop(k, None)
        cache[key] = out
    return out

def _panel_period_roll_nav(req, hk_state: dict, row: dict, item: Optional[dict] = None,
                           panel: Optional[dict] = None, with_exit: bool = False,
                           exit_mask=None, exit_cache: Optional[dict] = None,
                           exit_index: int = 0, exit_name: str = "",
                           exit_rule=None) -> dict:
    """面板侧「每期全仓滚动」净值（v2.1.27 ✓，v2.1.38 加 `item`/`panel` ✓）。

    ★ 为什么非要有它（用户 2026-10-11 追问"难道不该逐位一致" ✓）：
      面板那条**持仓曲线**是**组合收益模型**（每期等权、按换手扣成本、复利累乘；
      **无资金约束 / 无手数取整 / 无涨跌停** ✗），而「交易信号测试」拖文件回灌跑的是
      **真金白银引擎** ⇒ 两者的差**不是 bug、也不可能靠补丁消掉** ✗。
      要对拍就必须**两边同口径**：这里直接调**回灌侧同一个** `signals.period_roll.run_period_roll`
      （含资金约束/整手/涨跌停 ✓），喂**同一批每期名单**（= 导出文件 `_signals` 的来源 ✓）
      ⇒ 这两条才该**逐位一致** ✓。

    `item` = 要算**哪一档** ✓（缺省 = `items[0]` = 默认档 ✓）；
    `panel` = 调用方**预先取好**的面板 ✓（缺省 ⇒ 本函数自己取价，= 单档的老口径 ✓）。
    ⚠ 任何异常一律吞进 `error` 字段 ✓ —— 它是**附加信息**，绝不拖垮统计（与 `perf` 同规矩 ✓）。
    """
    try:
        from ..signals.period_roll import run_period_roll
        from ..signals.pricing import load_price_panel

        tc = row.get("topk_curves") or {}
        it0 = item if item is not None else (tc.get("items") or [{}])[0]
        _err, dates, lists = _period_roll_pick(hk_state, row, it0)
        if _err:
            return {"error": _err}
        recs = [{"date": d, "code": c, "bucket": d}      # bucket = 调仓日 ⇒ 每期一批 ✓
                for d, codes in zip(dates, lists) for c in codes]
        # ★★ v2.1.27：**必须按 (日期, 代码) 排序** ✗→✓ —— 回灌侧的解析器就是这么给的
        #   （`parsers/qlib_export.py` 用 `sorted({(date, code)})` ✓）。不排序的话：
        #   现金在"快用完"时**按什么顺序买**就不同 ⇒ 同一批信号会跑出
        #   buys 959 vs 956、min_cash 5.31 vs 3.6 ✗（实测：trades 总数一样、只是买卖拆分不同 ✓）
        #   ⇒ 逐点差 2.6e-3 ✗。排序后两边喂给引擎的表**逐行相同** ✓。
        recs.sort(key=lambda r: (r["date"], r["code"]))
        if not recs:
            return {"error": "每期持仓为空"}
        sig = pd.DataFrame(recs)
        if panel is None:                            # v2.1.38：调用方没预取 ⇒ 自己取（单档口径 ✓）
            panel = load_price_panel(sorted(sig["code"].astype(str).unique().tolist()),
                                     str(req.start_date), str(req.end_date), need_open=True)
        if not panel:
            return {"error": "行情面板为空（取价失败）"}
        # ★★ v2.1.79（用户 2026-10-13 拍板 ✓）：**「每期全仓滚动（实盘口径）」也跟随复权口径** ✗→✓
        #   —— 他要与聚宽对齐 ⇒ **一个口径走全链** ✓；否则"模型曲线走真实价、实盘走复权价" ✗
        #   ⇒ 「实盘 vs 模型」的结论会**随复权口径翻转** ✗（实测：backward 下实盘 −1.31pp ✓、
        #     none 下反而 +1.84pp ✗，用户当场发现 ✓）。
        #   口径来源：**那次任务自己算出来的** 事件研究参数 `row["event_study"]["params"]` ✓
        #   （它就是"面板真正用过的那个" ✓）；⚠ 浅拷贝 ✓（`panel` 由 `_attach_period_roll`
        #   在**多档之间共享** ✓，就地改会串档 ✓）。
        # ⚠⚠ v2.1.80：**优先读那次任务请求自己的 `price_adjust`** ✗→✓ —— 实测
        #   （`ai_test/ab_fuquan.py` ✓）：只读 `row["event_study"]["params"]` 时**拿不到值** ✗
        #   （该字段在装配顺序上**晚于**本函数 ✓）⇒ 换价静默失效 ⇒ 「实盘」在两种复权口径下
        #   **一动不动** ✗（与用户"不复权 → 实盘也要用不复权"的要求正好相反 ✓）。
        #   `req` 就是那次单因子测试请求 ✓（同一对象的 `fill`/`cost`/`capital` 本函数已在用 ✓）。
        _pa_row = str(getattr(req, "price_adjust", None)
                      or ((row.get("event_study") or {}).get("params") or {})
                      .get("price_adjust") or "").strip().lower()
        if _pa_row == "none":
            panel = dict(panel)
            _cr = panel.get("CLOSE_RAW")
            if _cr is not None and len(_cr):
                panel["CLOSE"] = _cr.ffill().round(2)
                _orr = panel.get("OPEN_RAW")
                if _orr is not None and len(_orr):
                    panel["OPEN"] = _orr.ffill().round(2)
        # ★ v2.4.3：`with_exit=True` ⇒ 同一次回测**再带上离场通道** ✓（离场矩阵＝面板侧算好的
        #   布尔信号对齐到本面板 ✓；对不上 ⇒ 引擎自己关掉离场并记 diag ✓，绝不崩 ✗）
        _ex = (_exit_matrix(exit_mask, panel, exit_cache, ei=exit_index)
               if with_exit else None)
        bt = run_period_roll(sig, panel, fill=str(req.fill), cost=float(req.cost),
                             capital=float(req.capital), strict_limit=bool(req.strict_limit),
                             # ★ T1（v2.8.6）：涨停判定口径透传 ✓（与净值曲线同口径 ✓ 不分叉 ✗）
                             limit_judge=(req.limit_judge or "legacy"),
                             exit_mat=_ex, exit_rule=(exit_rule if with_exit else None))
        # ★★ v2.8.8b（用户 2026-10-16 ✓）：**登记这次回测的成交明细** ✗→✓
        #   —— 导出「离场曲线」那一页的**调仓记录**靠它 ✓（用户要用它检查离场逻辑 ✓：
        #     `reason` 会明写 `exit_signal`（离场卖出 ✓）/ `sell_expiry`（到期 ✓）/ `sell_trim_units`（减仓 ✓））。
        #   ⚠ 只登记精简版、且**失败不影响回测** ✓（见 `pr_bt_stash_put` ✓）。
        pr_bt_stash_put((hk_state or {}).get("_task_id") or getattr(req, "task_id", "") or "",
                        row, item, with_exit, exit_index, bt)
        nav = getattr(bt, "nav", None)
        if nav is None or not len(nav):
            return {"error": "每期全仓滚动回测无净值"}
        col = str(nav.columns[0])
        stats = dict((getattr(bt, "stats", None) or {}).get(col) or {})
        # ★★ v2.1.41：**截断到"组合曲线的最后一个完整调仓日"** ✗→✓ —— 用户 2026-10-12 拍板：
        #   "实盘口径曲线只出到跟组合时间一样，年化计算也只算到 12-19，这样才是都对齐、有可比性" ✓。
        #   为什么必须截：组合那条日频序列在**最后一个完整持有期末**收口 ✓（未走完的一期
        #   不平铺进样本 ✗），而本函数原本跑满整段（例如到 2026-01-31 ✓）⇒ 一边算到 1-31、
        #   一边算到 12-19 ⇒ **窗口不同、不可比** ✗（用户截图里"实盘年化反而高"正是这么来的 ✗）。
        #   ⚠ 截断后必须**用截断序列重算指标** ✓（引擎的 `stats` 是全段的 ⇒ 不能用 ✗），
        #     并把 `final_nav` / `total_return` 一起校正 ✓，保证"看到的曲线 = 看到的指标" ✓。
        _cd = [str(x)[:10] for x in ((row.get("topk_curves") or {}).get("dates") or [])]
        if _cd:
            _last = _cd[-1]
            try:
                nav = nav.loc[[i for i in nav.index if str(i)[:10] <= _last]]
            except Exception as e:  # noqa: BLE001
                _log_swallowed("factors_single.py", e)
                pass
            if not len(nav):
                return {"error": "截断到调仓日（%s）后无净值" % _last}
            try:
                from ..engine.perf_metrics import compute_perf
                _trim = compute_perf(nav[col].pct_change().fillna(0.0).to_numpy())
                if _trim.get("annual_return") is not None:
                    stats["perf"] = _trim
                    stats["final_nav"] = _trim.get("nav_end")
                    stats["total_return"] = _trim.get("total_return")
                    stats["trimmed_to"] = _last          # 自查用：截断到哪个调仓日 ✓
            except Exception as e:                       # noqa: BLE001 —— 附加信息，绝不拖垮
                try:
                    from ..logger import get_logger
                    get_logger("factors").warning("period_roll 截断后重算指标失败: %r", e)
                except Exception as e:
                    _log_swallowed("factors_single.py", e)
                    pass
        return {"dates": [str(x)[:10] for x in nav.index],
                # ⚠ 精度 9 位（不是 6 ✓）：对拍脚本要求"逐位一致"，取到 6 位会留下
                #   ≤5e-6 的**纯取整**残差 ✗（实测 max|Δ| = 5e-6 ✓ 就是它）⇒ 提到 9 位 ✓。
                "nav": [round(float(x), 9) for x in nav[col].to_numpy()],
                # ★ v2.4.3 / v2.5.0：同口径的多条曲线必须**一眼可分** ✓（对照 vs 各离场）
                "label": ("每期全仓滚动（实盘口径 · 含离场%s）"
                          % (("「%s」" % exit_name) if exit_name else "")
                          if with_exit else "每期全仓滚动（实盘口径）"),
                "with_exit": bool(with_exit),
                "exit_index": (int(exit_index) if with_exit else None),
                "exit_name": (str(exit_name) if with_exit else ""),
                # ★★ v2.8.8：把引擎的 `diag` **带出来** ✗→✓ —— 离场没生效时这里直接看到原因
                #   （`exit_note` = 未生效原因 ✓ ｜ `exit_enabled` / `exit_rule` = 挂没挂上 ✓）；
                #   旧实现把 diag 丢掉 ✗ ⇒ 用户只能看到"曲线和对照一样"却无从知道为什么 ✗
                #   （2026-10-16 报障 ✓）。
                "diag": dict(getattr(bt, "diag", None) or {}),
                "stats": {k: v for k, v in stats.items() if k != "perf"},
                "perf": stats.get("perf")}
    except Exception as e:                                   # noqa: BLE001
        try:
            from ..logger import get_logger
            get_logger("factors").warning("period_roll 面板侧计算失败: %r", e)
        except Exception as e:
            _log_swallowed("factors_single.py", e)
            pass
        return {"error": repr(e)}

def _sft_render_view(state: dict) -> tuple:
    """读侧统一渲染 (progress, message)：消息只在这里合成，杜绝多线程抢写 message。

    进度模型（共享特征加载 → 逐周期统计，保证单调不回跳）：
      - 排队等待并发单元：0
      - 共享加载/解析：0-30（load_prog）
      - 周期统计：30 + 70×(已完成周期数 + 当前周期内部进度/100) / 总周期数
      message 按状态分层（排队中 → 共享加载中 → 正在统计哪些周期及其内部进度）。
    - 终态：直接使用存储的终态 message。
    """
    status = state.get("status")
    if status in ("success", "failed", "cancelled"):
        return float(state.get("progress", 100.0)), state.get("message", status)
    with _SFT_LOCK:
        horizons = list((state.get("config") or {}).get("horizons") or [])
        running = sorted(state.get("running_h") or [])
        queued = sorted(state.get("queued_h") or [])
        done_n = int(state.get("done_n") or 0)
        per_prog = dict(state.get("per_h_prog") or {})
        per_msg = dict(state.get("per_h_msg") or {})
        cancelled = bool(state.get("cancel_requested"))
        load_prog = float(state.get("load_prog") or 0.0)
        load_msg = state.get("load_msg") or ""
        ts = state.get("ts", 0.0)
    n_h = max(1, len(horizons))

    if cancelled:
        return 0.0, "正在取消..."  # 终态由 worker 主线程统一收尾写 "已取消"

    # 排队中（尚未获得并发单元）
    if queued and not running and done_n == 0:
        qs = "、".join(str(x) for x in queued[:6])
        tail = "等" if len(queued) > 6 else ""
        return 0.0, f"排队等待并发单元（预测周期 {qs} 日{tail}，与回测/训练共用配额）..."

    # 共享加载/解析阶段（尚无周期进入统计）：直接用加载进度 0-30
    if not running and done_n == 0:
        if load_msg:
            return round(load_prog, 1), load_msg
        return 0.0, "等待调度..."

    # 统计阶段：30 + 70 × 完成占比（已完成周期计 100%，运行中按内部进度）
    running_prog = sum(float(per_prog.get(h, 0.0)) for h in running) / 100.0
    done_frac = (done_n + running_prog) / n_h
    overall = round(30 + 70 * done_frac, 1)

    # ★ v2.4.2（用户 2026-10-15 反馈 ✓）：**统计全部完成后**仍有附加阶段
    #   （「每期全仓滚动」取全池行情 + 逐档逐日回测，全 A 十秒~分钟级 ✗）——
    #   原来这里直接给 100% ✗ ⇒ 进度条顶在 100% 干等；
    #   单周期时 `parts` 为空 ⇒ `msg` 也是空串 ✗ ⇒ 界面只剩"进度 100%"✗。
    #   现在：统计封顶 95 ✓、尾巴 95→99.5 交给 `extra_prog` ✓、文案用 `extra_msg` ✓。
    if done_n >= n_h and not running:
        _ep = float(state.get("extra_prog") or 0.0)
        _overall = min(99.5, 95.0 + 4.5 * max(0.0, min(100.0, _ep)) / 100.0)
        _msg = str(state.get("extra_msg") or "").strip() or "正在整理结果..."
        return round(_overall, 1), _msg

    parts = []
    for h in running:
        inner = per_msg.get(h, "")
        parts.append(f"[{h}日] {inner}" if inner else f"[{h}日] 统计中")
    msg = "；".join(parts)
    if n_h > 1:
        # 单执行体串行统计：running 恒为正在统计的那 1 个周期（parts 已带 [h日]），
        # 不再显示旧并发语义的"运行 k/n"（多周期并行取消后该数字恒 1，误导）；
        # 改为进度维度的"已完成 k/n 周期"，与 overall 进度条口径一致。
        msg += f"（已完成 {done_n}/{n_h} 周期"
        if queued:
            msg += f"，{len(queued)} 个排队"
        msg += "）"
    return overall, msg

def _sft_touch(state: dict) -> None:
    """写侧标记时间戳（供 tasks 列表按活动排序；不写 message——消息统一读侧渲染）。"""
    with _SFT_LOCK:
        state["ts"] = time.time()

@router.post("/single-factor-test", summary="单因子测试（不训练模型，异步提交）")
def single_factor_test(req: SingleFactorTestRequest):
    """提交单因子测试任务，后台线程逐个因子快速诊断，返回 task_id。

    批量预测周期：
      - label_horizons=[1,2,5,...] 一次测多个持有周期（结果为"因子分组、组内周期升序"，
        每行带 horizon 字段）；单周期（默认 label_horizon=2）行为与历史完全一致。
      - parallel：已弃用不再生效（单执行体共享一次特征加载，逐周期串行统计，见实现）。
    完成后通过 GET /factors/single-factor-test/progress/{task_id} 轮询进度并获取结果。
    """
    if not req.factors:
        raise HTTPException(status_code=400, detail="请至少勾选一个因子")
    # ★ v2.4.3：离场因子的两条前置校验（用户 2026-10-15 拍板 ✓）—— 抽成纯函数便于单测 ✓
    _apply_exit_validation(req)
    if not req.start_date or not req.end_date:
        raise HTTPException(status_code=400, detail="请填写测试区间")
    # 日期合法性兜底（前端已校验，此处防 API 直连）：如 2026-06-31 非法，
    # 放行会在 pandas 解析处报成「特征计算失败: day is out of range for month」。
    _bad = _bad_date_arg(req.start_date, req.end_date)
    if _bad:
        raise HTTPException(status_code=400, detail=_bad)

    # 归并预测周期：label_horizons 优先；否则退化为 label_horizon（单周期，历史行为）
    raw = req.label_horizons if req.label_horizons else ([req.label_horizon] if req.label_horizon else [])
    if not raw:
        raise HTTPException(status_code=400, detail="预测周期不能为空")
    horizons: list[int] = []
    for h in raw:
        try:
            hi = int(h)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"预测周期必须为正整数：{h!r}")
        if not (1 <= hi <= 250):
            raise HTTPException(status_code=400, detail=f"预测周期需在 1~250 之间：{hi}")
        if hi not in horizons:
            horizons.append(hi)
    horizons.sort()  # 结果按因子分组、组内周期升序
    parallel = bool(req.parallel) and len(horizons) > 1
    n_h = len(horizons)

    # 分位/曲线参数校验（v1.18.45：由 API 传入，不再硬编码；默认值与历史行为一致）
    # ⚠ 判空必须用 `is None`，**不能写 `x or 默认`** —— 那样 `0` 会被静默当成默认值，
    #   非法参数便绕过校验（实测 API 层抓到：rebalance_period=0 被当成 5 放行）。
    try:
        quantiles = int(10 if req.quantiles is None else req.quantiles)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"分位组数必须为整数：{req.quantiles!r}")
    if not (2 <= quantiles <= 20):
        raise HTTPException(status_code=400, detail=f"分位组数需在 2~20 之间：{quantiles}")
    try:
        # None 表示"跟随预测周期 h"（默认口径），原样透传；给了值才校验范围
        rebalance_period = (None if req.rebalance_period is None
                            else int(req.rebalance_period))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"调仓期必须为整数：{req.rebalance_period!r}")
    if rebalance_period is not None and not (1 <= rebalance_period <= 250):
        raise HTTPException(status_code=400, detail=f"调仓期需在 1~250 个交易日之间：{rebalance_period}")
    topk_list: List[float] = []
    if req.topk_list:
        # 上限 12 = 默认档(10% 固定档) + 前端全部 10 个预设，与计算层 `_ks[:12]`
        # （factors/single_test.py）保持一致。**原为 8**，而前端预设本就有 10 个
        # ⇒ 用户勾到第 9 个必报 400（"最多点选 8 个"），且计算层其实早就为"全选"预留了 12 的
        # 容量（其注释即写明"默认档 + 前端预设 10 个也要能全选"）—— 两处口径不一致，v1.18.64 统一。
        if len(req.topk_list) > 12:
            raise HTTPException(
                status_code=400,
                detail=f"明细曲线最多点选 12 个 K（默认档 + 10 个预设）：{len(req.topk_list)}")
        for v in req.topk_list:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"K 必须为数值：{v!r}")
            if not (fv > 0):
                raise HTTPException(status_code=400, detail=f"K 必须为正数：{fv}")
            topk_list.append(fv)

    # ST 剔除依赖 is_st 标签（tools/dump_states.py 从 E:/rq bundle 同步）：本机无 dump 时明确报错，
    # 避免"勾了但没剔除"的静默错误（涨停/跌停判定无标签时自动回退倒推，不受影响）
    if req.exclude_st_t1:
        from ..engine.limits import field_bin_available
        if not field_bin_available("is_st"):
            raise HTTPException(
                status_code=400,
                detail='勾选了"剔除ST(T+1)"，但本机 Qlib 数据没有 is_st 标签'
                "（需先执行 tools/dump_states.py 从 E:/rq bundle 同步）。"
                "取消勾选即可继续（涨停/跌停判定会自动回退倒推口径）",
            )

    task_id = uuid.uuid4().hex[:12]
    # 结构化运行态（读侧渲染 message/progress，杜绝多线程抢写 message 造成文案来回跳）：
    #   running_h / queued_h：正在统计运行 / 排队等并发单元的预测周期
    #   per_h_prog / per_h_msg：各周期统计内部进度 0-100 / 内部最新进度文案
    #   done_n：已完成统计的周期数
    #   load_prog / load_msg：共享特征加载阶段整体进度 0-30 / 文案（读侧映射：加载 0-30 → 统计 30-100）
    state: dict = {
        "task_id": task_id,
        "status": "running",
        "progress": 0.0,
        "message": "已提交",
        "result": None,
        "error": None,
        "cancel_requested": False,
        "ts": time.time(),
        "config": {"horizons": horizons, "parallel": parallel},
        "running_h": [],
        "queued_h": [],
        "done_n": 0,
        "per_h_prog": {},
        "per_h_msg": {},
        "load_prog": 0.0,
        "load_msg": "",
        # ★ v2.4.2（用户 2026-10-15 截图反馈 ✓）：统计**之后**还有一段附加阶段 ——
        #   「每期全仓滚动（实盘口径，逐点对拍）」要**先取全池行情面板**再逐档逐日回测
        #   （全 A 约 10~20 秒/档 ✗），而统计一完成进度就已经是 100% ✗ ⇒ 进度条顶在 100%
        #   干等（单周期时 `message` 还是空串 ✗ ⇒ 界面只剩"进度 100%"✗）。
        #   ⇒ 统计阶段封顶 **95%**、尾巴 95→99.5 留给这一阶段，并把文案实时报出来 ✓。
        "extra_prog": 0.0,
        "extra_msg": "",
    }
    _sft_store(task_id, state)

    def _set_cancel(state: dict) -> None:
        """标记任务已取消（仅当仍在运行中；避免覆盖 failed/success）。"""
        if state.get("status") == "running":
            state.update(status="cancelled", progress=100.0, message="已取消", ts=time.time())

    def _run() -> None:
        manager = get_task_manager(config.WORK_DIR)
        # 进入排队状态（计入并发统计的 queued）
        manager.register_external_queued(task_id)
        # 并行 worker 前先确保 qlib 只初始化一次（引擎 init 自带锁；先 init 完再开线程）
        try:
            from ..factors.single_test import _ensure_qlib_init
            _ensure_qlib_init()
        except Exception as e:
            state.update(status="failed", progress=100.0, message=f"单因子测试失败: {e}", error=str(e), ts=time.time())
            manager.unregister_external_queued(task_id)
            _sft_trim_results()
            return

        lock = threading.Lock()
        per_h: dict[int, list] = {}       # 周期 -> 该周期全部因子的结果
        fatal_all: str = ""               # 致命错误（共享执行体整体异常）
        done_set: set = set()             # 已完成的预测周期
        # ★ v2.4.3：离场因子的**跨作用域箱**（`_run_shared` 是内层函数 ⇒ 它里头的局部变量
        #   外层看不见 ✗）。⚠ 这里必须有：装配段（`t.join()` 之后）要用 mask/stats/cache ✓ ——
        #   第一版把它们定义在 `_run_shared` 里 ⇒ **CI 的 ruff F821 当场点名** ✗
        #   （若不修，运行时会 NameError ⇒ **每个**单因子测试的装配段都会炸 ✗）。
        #   ★ v2.5.0：多选 ⇒ 箱里放 `items`（每个离场一份：name/stats/effect/mask ✓）与
        #   `masks`（对齐用 ✓，**不进 HTTP 响应** ✗ —— pandas Series 不序列化 ✓）。
        _exit_box: dict = {"mask": None, "stats": None, "cache": {}, "err": "",
                           "items": [], "masks": []}

        def _run_shared() -> None:
            """多周期共享一次特征加载的单执行体（根治"并行多周期各自重复加载"）。

            占 1 个并发槽（而不是每周期各占一个）：特征加载只做一次，逐周期统计在
            内存 DataFrame 上进行（不再触发 qlib D.features）。排队/取消语义与原来一致：
            拿不到槽则阻塞排队，cancel_requested 时立即退出。
            """
            nonlocal fatal_all
            # 排队中：所有周期登记为 queued（读侧渲染"排队等待并发单元"）
            with lock:
                state["queued_h"] = list(horizons)
            got = manager.external_wait_slot(
                task_id, cancel_check=lambda: bool(state.get("cancel_requested"))
            )
            if not got:
                with lock:
                    state["queued_h"] = []
                return  # 排队期间被取消：终态由主线程统一收尾
            with lock:
                state["queued_h"] = []
            manager.unregister_external_queued(task_id)
            try:
                def _cb(h, p: float, m: str) -> None:
                    # h 维度进度：结构化解耦，读侧合成整体 progress/message。
                    # h=None → 共享加载/解析阶段整体进度（0-30）；h=int → 该周期统计内部进度。
                    with lock:
                        p = float(p)
                        if h is None:
                            state["load_prog"] = p
                            state["load_msg"] = m
                        elif p >= 100.0 and h not in done_set:
                            done_set.add(h)
                            state["done_n"] = len(done_set)
                            state["running_h"] = [x for x in state["running_h"] if x != h]
                            state["per_h_prog"].pop(h, None)
                            state["per_h_msg"].pop(h, None)
                        else:
                            if h not in state["running_h"]:
                                state["running_h"] = sorted(state["running_h"] + [h])
                            state["per_h_prog"][h] = p
                            state["per_h_msg"][h] = m
                    if state.get("cancel_requested"):
                        raise FactorTestCancelled()

                _evs: dict = {}          # 出参：0/1 因子的触发事件（键=因子表达式）
                # v2.1.0：出参 —— **每期持仓索引**（导出 XLSX 的「持仓明细」页用 ✓）。
                # 持仓在测试进程内算完即丢 ✗ ⇒ 顺手存一份（`has()` 为假时任务状态里不落键 ✓）
                _hks = HoldingsIndex()
                # ★ v2.4.3：离场因子的出参（布尔信号 + 触发率预检 ✓）—— 结果写进**外层**的
                #   `_exit_box` ✓（本函数是嵌套函数，写自己局部变量外层看不见 ✗）
                _exit_out: dict = {}
                res = run_single_factor_tests(
                    label_horizons=horizons,
                    fill=req.fill,      # v2.1.23（B ①）：成交时点（t1_close 默认 / t1_open ✓）
                    universe=req.universe,
                    start_date=req.start_date,
                    end_date=req.end_date,
                    factors=[f.model_dump() for f in req.factors],
                    progress_cb=_cb,
                    cancelled=lambda: bool(state.get("cancel_requested")),
                    exclude_limit_up_signal=req.exclude_limit_up_signal,
                    exclude_limit_up_trade=req.exclude_limit_up_trade,
                    exclude_suspended=req.exclude_suspended,
                    exclude_st_t1=req.exclude_st_t1,
                    exclude_stock_gem=req.exclude_stock_gem,
                    exclude_stock_kcb=req.exclude_stock_kcb,
                    price_adjust=req.price_adjust,
                    freeze_suspended_price=req.freeze_suspended_price,
                    suspend_remove=req.suspend_remove,
                    price_round=req.price_round,
                    warmup_days=req.warmup_days,
                    quantiles=quantiles,
                    rebalance_period=rebalance_period,
                    topk_list=topk_list,
                    events_out=_evs,     # v1.19.60：0/1 因子的触发事件（键=表达式）⇒ 供净值曲线复用
                    holdings_out=_hks,   # v2.1.0：每期持仓索引 ⇒ 供 XLSX 导出复用
                    # ★ v2.4.3 / v2.5.0：离场因子（用户 2026-10-15 ✓，**多选** ✓）——
                    #   每个只多求值一个 `EXIT_i` 列 ✓（不动既有列序/数字 ✓），
                    #   出参给布尔信号 + 触发率预检 + **离场事件研究** ✓
                    exit_factors=[e.model_dump() for e in (req.exit_factors or [])],
                    exit_threshold=float(req.exit_threshold),
                    exit_out=_exit_out,
                )
                # ★ v2.4.3 / v2.5.0：离场信号（布尔 Series ✓）+ 触发率预检 + 事件研究
                #   ⚠ 写进外层 `_exit_box`（见它的注释 ✓ —— 内层函数的局部变量外层看不见 ✗）
                _exit_box["items"] = list(_exit_out.get("items") or [])
                _exit_box["masks"] = [it.get("mask") for it in _exit_box["items"]]
                _exit_box["mask"] = _exit_out.get("mask")
                _exit_box["stats"] = (_exit_out.get("stats")
                                      or ({"error": _exit_out["error"]}
                                          if _exit_out.get("error") else None))
                if _exit_out.get("error"):
                    state["_exit_error"] = _exit_out["error"]
                with lock:
                    if _evs:
                        # ⚠ 不进 HTTP 响应（几万行会撑大结果）：存任务状态，供 /factors/event-study/nav 复用
                        state["_evs"] = dict(_evs)
                    # ⚠ v2.1.0：`_req` 由"仅在有事件时存"改为**无条件存** —— 导出（XLSX）要用它
                    #   回显参数（池子/区间/成本口径…），而**连续因子没有触发事件** ⇒ 原实现下
                    #   持仓曲线导不出来（参数全空 ✗）。它只是个几百字节的 dict ✓。
                    state["_req"] = req.model_dump()
                    if _hks.has():
                        # 同 `_evs`：**不进 HTTP 响应**（几千~几万个代码会白白撑大每次轮询 ✗）
                        state["_hk"] = _hks.as_state()
                    for h, rows in (res or {}).items():
                        per_h[h] = rows
                        if h not in done_set:
                            done_set.add(h)
                    state["done_n"] = len(done_set)
                    state["running_h"] = []
            except FactorTestCancelled as _e:
                _log_swallowed('factors_single.py', _e)
                pass  # 终态由主线程统一收尾
            except Exception as e:
                import traceback
                from ..logger import get_logger
                get_logger("factors").error("单因子共享执行失败: %s\n%s", e, traceback.format_exc())
                with lock:
                    fatal_all = str(e)
            finally:
                manager.release_slot(task_id)

        # 单执行体占 1 槽完成全部预测周期（共享一次特征加载）。
        # parallel 仅保留前端语义（不再每周期各占一并发单元），加载共享后无需多槽。
        t = threading.Thread(target=_run_shared, daemon=True)
        t.start()
        t.join()

        with lock:
            # 取消优先（排队中或运行中被取消都汇聚到这里统一收尾，保证只写一次终态）
            if state.get("cancel_requested") and state.get("status") == "running":
                _set_cancel(state)
            if state.get("status") == "cancelled":
                manager.unregister_external_queued(task_id)
                _sft_trim_results()
                return
            if fatal_all:
                state.update(status="failed", progress=100.0, message=f"单因子测试失败: {fatal_all}",
                             error=fatal_all, ts=time.time())
                manager.unregister_external_queued(task_id)
                _sft_trim_results()
                return
            # 结果按"因子分组、组内周期升序"组织：对每个因子，遍历所有周期取同一下标的行
            items: list[dict] = []
            # ★ v2.1.27：每期持仓索引（导出/逐点对拍复用 ✓；没落键 ⇒ 空 dict ✓）
            hk_state = state.get("_hk") or {}
            # ★★ v2.8.8b（用户 2026-10-16 ✓）：**把真实任务 id 顺路带进 `hk_state`** ✗→✓ ——
            #   为什么不能直接用 `req.task_id` ✗：**单因子测试的 task_id 是路由生成**的 ✓，
            #   请求体里根本没有它 ✗ ⇒ 登记成交明细时键里是**空串** ✗ ⇒ 导出按真 tid 去取
            #   ⇒ **取不到 ⇒ 调仓记录页整页空白** ✓✓（冒烟第一次就抓到这个 ✓）。
            #   `hk_state` 是**每个任务自己的**dict ✓、且一路传到 `_panel_period_roll_nav` ✓
            #   ⇒ 借它带一趟最省事 ✓（⚠ 加的是 `_` 前缀的私有键，`HoldingsIndex.from_state`
            #     只读它认得的键 ✓ 不受影响 ✓）。
            hk_state["_task_id"] = task_id
            n_factor = len(req.factors)
            # ★★ v2.4.2（用户 2026-10-15 截图反馈 ✓）：**统计之后**这一整段（整理结果 +
            #   「每期全仓滚动」取全池行情 + 逐档逐日回测）原来完全不报进度 ✗ ⇒ 进度条顶在
            #   100% 干等、单周期时连文案都是空的 ✗。现在按"组合格"计数上报 ✓（统计封顶 95% ✓）。
            _cells = sum(1 for _fi in range(n_factor) for _h in horizons
                         if (per_h.get(_h) is not None and _fi < len(per_h[_h])))
            _cell = 0
            state["extra_prog"] = 0.0
            state["extra_msg"] = (
                "整理结果：正在计算「每期全仓滚动（实盘口径%s）」…"
                % (" · 对照 1 条 + 离场 %d 条" % len(req.exit_factors or [])
                   if req.exit_factors else "")
                if req.period_roll else "整理结果…")
            for fi in range(n_factor):
                for h in horizons:
                    rows = per_h.get(h)
                    if rows is None or fi >= len(rows):
                        continue
                    row = dict(rows[fi])
                    row["horizon"] = h
                    _cell += 1
                    # ★ v2.1.27：**每期全仓滚动**（默认关 ✓，慢 ✓）—— 逐点对拍专用 ✓
                    # ★ v2.1.38：**用户选了哪些档就都算哪些** ✓ —— 结果落
                    #   `row["period_roll_items"]`（与 `topk_curves.items` **同序** ✓），
                    #   前端按当前选中档的下标取 ✓（面板取一次价喂所有档 ✓）。
                    if req.period_roll:
                        _base = 100.0 * (_cell - 1) / max(1, _cells)
                        _span = 100.0 / max(1, _cells)

                        def _pc(frac, msg, _b=_base, _s=_span, _c=_cell, _n=_cells):
                            # ⚠ 默认参数绑定当轮的值 ✓（闭包晚绑定会把所有轮次指向最后一轮 ✗）
                            state["extra_prog"] = _b + _s * max(0.0, min(1.0, float(frac)))
                            state["extra_msg"] = "整理结果（%d/%d）：%s" % (_c, _n, msg)

                        if (row.get("topk_curves") or {}).get("items"):
                            # 连续因子 ⇒ 「每期全仓滚动」（有每期名单 ✓）
                            row["period_roll"] = _attach_period_roll(
                                req, hk_state, row, progress_cb=_pc,
                                exit_cache=_exit_box["cache"], exit_items=_exit_box["items"])
                        else:
                            # ★★ v2.8.0（用户 2026-10-15 实测发现 ✓）：**0/1 信号因子没有"每期名单"**
                            #   ✗（它走事件研究 ✓）⇒ 原来这里只会得到"没有持仓曲线档位"✗、
                            #   含离场回测**根本跑不起来** ✗（界面看着像"勾了没生效" ✗）
                            #   ⇒ 改走**事件驱动引擎**（平台本来处理 0/1 信号那条 ✓）：
                            #   出信号买入、持有预测周期、离场信号提前平仓 ✓（与「交易信号测试」同源 ✓）
                            row["period_roll"] = _attach_signal_roll(
                                req, row, state.get("_evs") or {}, progress_cb=_pc,
                                exit_cache=_exit_box["cache"], exit_items=_exit_box["items"])
                    else:
                        state["extra_prog"] = 100.0 * _cell / max(1, _cells)
                    items.append(row)
            state.update(
                status="success",
                progress=100.0,
                message=f"完成（{n_h} 个预测周期）" if n_h > 1 else "完成",
                result={"items": items, "total": len(items),
                        # ★ v2.4.3：离场因子回显（含**触发率预检** ✓ —— 用户定的 >50% 前端黄字 ✓）
                        # ★ v2.4.3 / v2.5.0：离场回显 —— `exit_factors` 是**列表**（多选 ✓），
                        #   每项带触发率预检 `stats` 与**离场事件研究** `effect` ✓；
                        #   `exit_stats`/`exit_factor` 保留为**第一项**（v2.4.3 前端兼容 ✓）。
                        #   ⚠ `mask`（pandas Series）**绝不进响应** ✗（不序列化 ✓）。
                        "exit_stats": _exit_box["stats"],
                        "exit_factor": ({"name": _exit_box["items"][0].get("name"),
                                         "id": _exit_box["items"][0].get("id")}
                                        if _exit_box["items"] else None),
                        "exit_factors": [{"index": it.get("index"),
                                          "id": it.get("id"), "name": it.get("name"),
                                          "source": it.get("source"),
                                          "expression": it.get("expression"),
                                          "stats": it.get("stats"),
                                          "effect": it.get("effect"),
                                          # ⚠ v2.8.0 修：上一版漏了这个字段 ✗ ⇒ 前端没法显示
                                          #   "含入场价 ⇒ 触发率/事件研究不适用" 的提示 ✓
                                          "entry_dependent": bool(it.get("entry_dependent")),
                                          "error": it.get("error")}
                                         for it in _exit_box["items"]],
                        "exit_threshold": float(req.exit_threshold)},
                ts=time.time(),
            )
        manager.unregister_external_queued(task_id)
        # 内存治理：任务结束立即释放超龄任务的完整结果
        _sft_trim_results()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id}

@router.get("/single-factor-test/progress/{task_id}", summary="查询单因子测试任务进度")
def single_factor_test_progress(task_id: str):
    """轮询单因子测试任务：status running/success/failed/cancelled，progress 0-100，success 时附带 result。

    progress/message 由 _sft_render_view 读侧统一合成（多 worker 不写 message）。
    """
    state = _sft_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    progress, message = _sft_render_view(state)
    result = state.get("result")
    # 防御：统计结果可能含 NaN/Inf（极端样本），清洗为 null 避免整个接口 500
    result = _json_safe(result) if result is not None else None
    return {
        "task_id": task_id,
        "status": state["status"],
        "progress": progress,
        "message": message,
        "result": result,
        "error": state.get("error"),
    }

@router.post("/single-factor-test/cancel/{task_id}", summary="取消单因子测试任务")
def single_factor_test_cancel(task_id: str):
    """请求取消正在运行的单因子测试任务：设置取消标记，并唤醒排队等待配额的任务线程
    （否则 worker 阻塞等槽时无法及时感知取消），后台线程在下一进度点/下次唤醒终止。

    返回 {ok, message}；任务已结束（success/failed/cancelled）时 ok=False 且不改变状态。
    """
    state = _sft_get(task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if state.get("status") != "running":
        return {"ok": False, "message": f"任务已{state.get('message', '结束')}，无需取消"}
    state["cancel_requested"] = True
    # 唤醒阻塞在 external_wait_slot 的排队 worker（无槽可释放时也能立即响应取消）
    try:
        manager = get_task_manager(config.WORK_DIR)
        manager.wake_external_waiters()
    except Exception as e:
        _log_swallowed("factors_single.py", e)
        pass
    return {"ok": True, "message": "已请求取消，正在终止..."}

@router.post("/single-factor-test/clear", summary="清理已结束的单因子测试任务与结果（释放内存）")
def single_factor_test_clear():
    """删除全部已结束（success/failed/cancelled）任务的记录并释放其 result 内存。

    运行中 / 排队中 / 已请求取消但未结束的任务保留（前端仍轮询其进度）；之后新任务照常记录。
    供前端"清理结果（释放内存）"按钮调用——展示保留到用户主动清理，不随收起丢失。
    """
    with _SFT_LOCK:
        done_keys = [
            k for k, v in _SFT_TASKS.items()
            if v.get("status") in ("success", "failed", "cancelled")
        ]
        for k in done_keys:
            _SFT_TASKS.pop(k, None)
    return {"ok": True, "cleared": len(done_keys)}

@router.get("/single-factor-test/tasks", summary="列出最近的单因子测试任务")
def single_factor_test_tasks(limit: int = 20):
    """列出最近提交的单因子测试任务（含 running，按时间倒序）。

    用于前端刷新页面后恢复"未取消完成"的任务：找到 running 任务即可重新轮询进度/继续取消。
    """
    with _SFT_LOCK:
        tasks = sorted(_SFT_TASKS.values(), key=lambda v: v.get("ts", 0), reverse=True)
    out = []
    for t in tasks[: max(1, min(int(limit or 20), 100))]:
        progress, message = _sft_render_view(t)
        out.append(
            {
                "task_id": t["task_id"],
                "status": t["status"],
                "progress": progress,
                "message": message,
                "ts": t.get("ts", 0),
                "cancel_requested": t.get("cancel_requested", False),
            }
        )
    return {"tasks": out}
