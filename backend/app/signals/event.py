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

from typing import Dict, List, Optional

import pandas as pd

from ..factors.event_study import build_event_stats, compute_baseline_curves


def run_event_study(events: pd.DataFrame, close_trig: pd.DataFrame,
                    close_pool: Optional[pd.DataFrame], max_k: int = 60,
                    cancel_check=None) -> Dict:
    """`events`（date, code）→ 事件研究结果 dict。

    · `close_trig` —— 触发标的的**后复权收盘**宽表（`signals/pricing.load_price_panel` 的 CLOSE）；
    · `close_pool` —— 基准池全样本宽表（"未触发组"的口径来源；None ⇒ 只出触发组，不出超额）。
    """
    if not len(events) or close_trig is None or not len(close_trig):
        return {"error": "无事件或无价格数据"}
    ev = pd.DataFrame({"code": events["code"].astype(str),
                       "dt": pd.to_datetime(events["date"])})
    max_k = max(1, min(int(max_k or 60), 250))
    es = build_event_stats(close_trig, ev, max_k, n_raw=int(len(events)),
                           cancel_check=cancel_check)
    base = None
    if close_pool is not None and len(close_pool):
        base = compute_baseline_curves(close_trig, close_pool, ev, max_k,
                                       cancel_check=cancel_check)
    es["baseline"] = base
    es["n_events_used"] = int(len(ev))
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
