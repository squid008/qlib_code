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
from ..factors.parser import (
    translate_formula, build_library, LexerError, ParseError, SemanticError, CodeGenError,
)
from ..services.custom_formulas import (
    list_custom_formulas as _list_custom_formulas,
    create_custom_formula as _create_custom_formula,
    update_custom_formula as _update_custom_formula,
    delete_custom_formula as _delete_custom_formula,
    find_live_by_name as _find_live_by_name,          # ★ v1.20.82：保存查重 ✓
    mark_dependents_stale as _mark_dependents_stale,  # ★ v1.20.87：依赖自动失效 ✓
    set_pinned as _set_pinned,                        # ★ v2.1.45：置顶/取消置顶 ✓
)
from .. import config
from ..engine.task_manager import get_task_manager
from ..factors.single_test import FactorTestCancelled, _bad_date_arg, run_single_factor_tests
from ..factors.event_study import run_event_study
from ..factors.holdings_index import HoldingsIndex      # v2.1.0：每期持仓索引（XLSX 导出用 ✓）
# ★ v2.7.0：含入场价（ENTRY_PRICE / ENTRY_FIRST）的离场公式 ⇒ 拆成「预计算列 + 骨架」✓
#   （与入场价无关的部分当普通字段列由面板算 ⇒ 任意字段/算子都能用 ✓，骨架回测逐日代入 ✓）
from ..factors.exit_rules import EntryRule, split_entry_rule, uses_entry

router = APIRouter(prefix="/api/factors", tags=["factors"])



# ---------- 单因子测试（不训练模型，快速诊断因子预测力） ----------





















    #   明细曲线要算的 K 列表：**≤1 视为「日均有效只数的百分比」**（0.1 = 10%），>1 视为只数；
    #   与默认档**同一取整口径**（`int(日均中位只数 × 百分比)`，截断）⇒ 0.1 就是默认档。
    #   None/空 ⇒ 只算默认档（10%）。默认档（= 十分位第 1 组）**恒算且排在 items[0]**。


# ---------- 单因子测试异步任务：POST 提交返回 task_id，GET 轮询进度/结果 ----------
# 进度存储为进程内内存 dict（本地单用户工具，无需持久化）；任务完成后保留最近 _SFT_MAX_TASKS 条。
from .factors_state import (  # noqa: E402  ★ P1-A-2：状态层已抽到 factors_state ✓
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
    _json_safe,
)

# ★ P1-A-2（2026-10-18 ✓）：事件研究组与请求模型已拆出 ✓ 这里**再导出**一次，
#   避免仓库里 `from .routers.factors import ...` 的老引用断掉 ✓（渐进迁移 ✓）。
# ⚠★ 必须**显式列出**、绝不能用 `import *` ✗ —— 星号导入会连 `router` 一起覆盖 ✓
#   ⇒ `factors.router` 变成事件模块的 router ⇒ 路由被注册两次
#     （实测：`Duplicate Operation ID event_study…` 12 条警告 ✗）
#   ⇒ 且本文件自己的端点会挂在**没人引用的旧 router 对象**上 ⇒ 静默消失 ✗。
from .factors_event import (  # noqa: F401,E402
    EventNavCancelRequest, EventNavRequest, EventStudyRequest, _untrig_nav_series,
    event_study, event_study_cancel, event_study_clear, event_study_nav,
    event_study_nav_cancel, event_study_progress,
)
from .factors_models import SingleFactorTestFactor, SingleFactorTestRequest  # noqa: F401,E402
from .factors_single import (  # noqa: F401,E402  ★ 显式列名（**绝不用 import \*** ✗）
    _period_roll_pick,
    _signal_exit_nav,
    _attach_signal_roll,
    _attach_period_roll,
    _exit_list,
    _apply_exit_validation,
    _exit_matrix,
    _exit_pre_mats,
    _panel_period_roll_nav,
    _sft_render_view,
    _sft_touch,
    single_factor_test,
    single_factor_test_progress,
    single_factor_test_cancel,
    single_factor_test_clear,
    single_factor_test_tasks,
)
# 内存治理：任务列表最多保留 _SFT_MAX_TASKS 条元信息；但完整 result（items 含全部因子的
# 大 dict）只保留最近 _SFT_RESULT_KEEP 个已完成任务，更早的 result 置 None 释放长驻内存
# （此前每条成功任务都完整驻留，多测几次单因子后 result 总量可占数百 MB，导致进程内存持续高位）。




























# ---------- 事件研究（0/1 稀疏信号：触发事件对齐 T=0 的收益分布） ----------
# 动机：稀疏信号的"按日配对检验"在触发日样本=1 时退化为单票收益序列，均值/显著性
# 不可信（实测 CCCMA250 全 A 每日触发中位数 1 只，Top20 天贡献日差净和 100.1%）。
# 事件研究以"每次触发"为样本单位，给出真正的概率/赔率画像。














# ★★ 2026-10-14（用户：「加一条**未触发组等权**参照线，我看看到底咋样」✓）
#   为什么必须有它 ✗→✓：旧图里只有「沪深300」灰虚线 + 两条策略曲线 ✗，而**事件研究的真实基准
#   其实是「未触发组等权」**（超额曲线用的就是它 ✓）⇒ 同一张图上**没有可比参照** ✓，
#   实测用户把灰线当成"全 A 等权" ✗（沪深300 0.957 vs 池内等权 1.4125 ✓ 差 48% ✗ ⇒ 结论会看反 ✓）。
#   本函数产出一条**与超额曲线同源**的净值曲线 ✓：每日把「全池 − 当日触发股」等权持有一天的收益累积 ✓
#   （收盘对收盘 ✓、无成本 ✓ —— 与策略曲线的口径差异在曲线名里注明 ✓）。
#   缓存 ✓：key = (universe, 起止, 复权, 整分, 价字段, 事件数) ✓，容量 4 ✓（同一任务反复改 k 只算一次 ✓）。






# ---------------------------------------------------------------------------
# 净值曲线的「价格面板 + 涨跌停标注」缓存（v1.19.93）
#
# 为什么（用户 2026-09-17：过顶**触发 14.88 万次** ⇒ 净值曲线长挂"计算中"✗）：
#   `/event-study/nav` 每次请求都要 `load_price_panel(事件涉及的所有股票, 起止)` +
#   `fill_limits(...)` —— 对"事件覆盖几千只股票 × 十几年"的公式，这两步**与 k 完全无关**
#   （只有回测那部分随 k 变 ✓），却每改一次 k 就重算一遍 ✗ ⇒ 拖动持仓周期像卡死 ✗。
#   ⇒ 按 (codes, start, end, strict) **单条缓存**（只留最近 1 条：一块 5000 列 × 4000 行的
#     面板已是数百 MB 量级，多留会吃内存 ✗）。
#   ⚠ 这是"治本（C）"的第 1 步；第 2 步（把每日估值/持仓市值向量化）改动在
#     `signals/engine.run_backtest` 内，必须带**新旧对拍**（成交/被拒/净值逐位一致）才敢上 ✓。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 回测结果缓存（v1.20.0；用户 2026-09-18：「事件研究里净值曲线（两种方案 + 基准）计算很慢」）
#
# 病根：`run_backtest` **与 k 强相关** ⇒ 改一次持仓周期就整条重跑 ✗，且它内部是
#   **逐日 Python 循环 × 两种资金方案 × 遍历全部持仓** ⇒ 过顶（14.88 万触发）下单次数十秒 ✓。
#   而"同一 k 重复点开"（前端缓存失效后重算 / 切换基准 / 关掉弹窗再开）尤其浪费 ✗。
# 做法：按 (codes 指纹 + 区间 + **事件指纹** + k + cost + capital + modes + 口径) 缓存 `BtResult` ✓。
#   结果对象很小（净值/成交/统计 ✓）⇒ 容量 4 的 LRU 足够覆盖"来回切 2~3 个 k/成本" ✓。
#   ⚠ key **必须含事件指纹**（同一批 codes 但触发集合不同 ⇒ 结果不同 ✗）。
#   ⚠ `BtResult` 只读复用（调用方不改 ✓）⇒ 直接返回同一对象 ✓ 省一次深拷贝 ✓。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 「净值曲线那次回测」的**导出旁路**（v2.1.0；用户 2026-10-11 要"事件研究导出也要每期持仓"✓）
#
# 为什么需要它：每期持仓只能从 `bt.trades` 派生（回测产物 ✓），而 `bt` 对象**不进 HTTP 响应**
#   （几万笔成交会撑爆 JSON ✗）⇒ 导出侧没法直接拿到 ✗。重新算一遍又要重跑回测（白烧 CPU ✗）、
#   复制一份"取事件 + 取价面板 + 回测"的逻辑又是两处漂移 ✗（最坏的一种）。
# ⇒ 这里让 `/event-study/nav` 把**刚算好的** `bt` 顺手按"调用方可复算的键"登记一份 ✓，
#   导出端点先调同一个端点（命中缓存 ⇒ 秒回 ✓）再按同一个键取 `bt` ✓ —— 两侧**同一份实现** ✓。
#   ⚠ 键里必须含 task/factor/k/cost/capital（不同参数是不同回测 ✗）；容量 4、FIFO ✓。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ★★ v2.8.8b（用户 2026-10-16 ✓）：**「每期全仓滚动」成交明细**的登记（导出「调仓记录」用 ✓）
#
# 为什么要它：用户要用「调仓记录」**检查离场逻辑**（哪天、因为什么原因卖出 ✓ ——
#   成交里的 `reason` 正是 `exit_signal` / `sell_expiry` / `sell_trim_units` ✓）。
#   而成交明细只在 `BtResult` 里 ✗、**不进 HTTP 响应**（几万笔会撑爆 JSON ✓）。
# ⇒ 跑完顺手登记一份**精简版**（只留 trades + diag + period_roll stats ✓；不留 nav/rejects ✓ 省内存）；
#   键 = (任务, 因子表达式, 周期, 离场下标 ✓ —— `-1` = "对照（不离场）"那一条 ✓)；
#   ⚠ 键里**必须含离场下标**（每条离场卖的东西不一样 ✗）；容量 8、FIFO ✓；
#   ⚠ 切数据集要清（T3 纪律 ✓，见 `clear_nav_caches` ✓）。













# ---------------------------------------------------------------------------
# 取消源（v1.20.5；用户 2026-09-18：「先把取消源接通」✓）
#
# 需求：用户在净值曲线上反复改持仓周期（60 → 6 → …）时，**旧的那次回测应当立刻停** ✗
#   —— 否则每改一次就多堆一个"逐日循环 × 两资金方案"的任务在后台空烧 CPU ✓。
# 做法：按 **回测指纹**（= `_nav_bt_key`，含事件指纹 + k + 成本 ✓）维护一个**单调递增序号** ✓。
#   每次请求开始时把自己的序号记下来 ✓，`run_backtest` 主循环每 20 个交易日回查一次
#   （`cancel_cb`，见 `signals/engine.py` ✓）⇒ **序号被后来者顶掉 ⇒ 抛 `_NavCancelled`** ✓
#   ⇒ 立即中止（不再继续算完 ✓）。响应 409 ⚠ 前端此时早已 abort/换了 key ⇒ 通常看不到它 ✓。
# ⚠ 用**回测指纹**而不是任务 id：改 k 时 `_nav_bt_key` 里 k 不同 ⇒ **不会互相取消** ✗
#   ⇒ 所以这里额外**只按事件指纹分组**（见 `_nav_req_key` ✓），同一批事件下"最新那次 k 才算数" ✓。
# ---------------------------------------------------------------------------












# ⚠ v1.20.15（用户 2026-09-18：「关弹窗即取消」✓）：前端**卸载弹窗**时会调
#   `POST /event-study/nav/cancel` ✓ ⇒ 这里记下 task_id ✓；在途回测的 `cancel_cb`
#   每次检查命中即抛 ⇒ **关掉弹窗后后端立刻停算** ✓（此前是"自己跑完为止、没人收结果"✗，
#   大公式一次白烧几十秒 ✓）。


















