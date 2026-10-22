# -*- coding: utf-8 -*-
"""`batch_even` 的**信号折叠**（★ v2.11.12 从 `signals/engine.py::run_batch_backtest` 抽出）。

## 这里做什么
把"每行一个信号"的表折叠成 **{执行日行号: [(批次, {列号: 权重}, 决策日行号)]}** ✓ ——
之后逐日循环只在命中执行日时按这份名单买卖 ✓。

## 四个必须写清的语义（都有测试 ✓）
1. ⭐ **卖出信号一律忽略** ✓ —— 这是**清单驱动**的策略 ✓：卖出由"移出名单"自动产生 ✓。
   若照单执行 `side<=0`，会和"移出名单 ⇒ 清仓"**重复卖** ✓（甚至卖成负仓 ✗）。
2. ⭐ **`defer` 是"统计"不是"丢弃"** ✓ —— 信号日不在交易日历时
   （`cal[p] != d` ✓，如周末/停牌 ✓）它**仍然按 `p` 记账** ✓，只是计一个 `defer` 便于排查 ✓。
   若这里 `continue` 掉 ⇒ **整批信号凭空消失** ✓，而用户只看到"某些调仓日没动" ✗。
3. ⭐⭐ **执行日钳到最后一根** ✓：`min(n_row - 1, p + lag)` ✓ ——
   最后一天的信号 + `lag=1` ⇒ 落在 **最后一个交易日** ✓ 而不是被丢掉 ✓
   （丢掉的话"最后一批永远不成交" ✓，回测尾部会缺一段 ✗）。
4. ⭐ **同一 (批次, 日) 的权重累加** ✓（同一只票出现多次 ⇒ 权重相加 ✓）；
   `weight` 缺失 ⇒ 按 **1.0** ✓；非法值 ⇒ 也按 **1.0** ✓（`fillna(1.0)` ✓）；权重下限 `1e-9` ✓
   （0 权重的票**仍要占一个位置** ✓ —— 名字在名单里 ✓，与"不在名单"语义不同 ✓）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

NO_SIGNAL_ERROR = "batch_even：没有可用的买入信号"


def build_batch_execs(signals, *, col_of: Dict, cal, n_row: int, lag: int):
    """→ `(buckets, execs, i_start, defer, beyond, n_mapped, n_refresh, 错误文案)` ✓。

    · `buckets`   = 出现过的批次名（**排序后** ✓，`share` 按它均分 ✓）
    · `execs`     = `{执行日行号: [(批次, {列号: 权重}, 决策日行号)]}` ✓
    · `defer`     = 信号日不在交易日历的条数 ✓（**仍然记账** ✓，见 docstring 第 2 点）
    · `beyond`    = 落在日历范围外的条数 ✓（这些**确实丢弃** ✓ —— 没有可对应的行 ✓）
    · `n_refresh` = **批次日**数（`(批次, 日)` 去重后 ✓，`diag["batch"]["refreshes"]` 用它 ✓）
    · `n_mapped`  = 成功映射的条数 ✓（`diag["signals_mapped"]` 用它 ✓ ——
      ⚠ 搬家时漏了它 ⇒ `ruff` 的 **F821 当场点名** ✗→✓，见 change_log v2.11.12）
    """
    bkt = (signals["bucket"].astype(str).fillna("") if "bucket" in signals.columns
           else pd.Series([""] * len(signals), index=signals.index))
    wts = (pd.to_numeric(signals["weight"], errors="coerce").fillna(1.0)
           if "weight" in signals.columns else pd.Series(1.0, index=signals.index))
    per: Dict[Tuple[str, int], Dict[int, float]] = {}
    defer = beyond = 0
    for dt, code, side, b, ww in zip(signals["date"], signals["code"], signals["side"], bkt, wts):
        if int(side) <= 0:
            continue            # 清单驱动：卖出由"移出名单"自动产生，入参里的卖出信号忽略
        c = col_of.get(code)
        if c is None:
            continue
        d = pd.Timestamp(dt)
        if n_row == 0 or d < cal[0]:
            beyond += 1
            continue
        p = int(cal.searchsorted(d))
        if p >= n_row:
            beyond += 1
            continue
        if cal[p] != d:
            defer += 1
        lst = per.setdefault((str(b), p), {})
        lst[c] = lst.get(c, 0.0) + max(1e-9, float(ww))
    if not per:
        return [], {}, 0, defer, beyond, 0, 0, NO_SIGNAL_ERROR

    buckets = sorted({b for (b, _p) in per})
    seq: Dict[str, List[Tuple[int, Dict[int, float]]]] = {}
    for (b, p), lst in per.items():
        seq.setdefault(b, []).append((p, lst))
    for b in seq:
        seq[b].sort(key=lambda x: x[0])
    execs: Dict[int, List[Tuple[str, Dict[int, float], int]]] = {}
    for b, items in seq.items():
        for p, lst in items:
            execs.setdefault(min(n_row - 1, p + lag), []).append((b, lst, p))
    i_start = min(execs)
    n_mapped = int(sum(len(v) for v in per.values()))
    n_refresh = len(per)
    return buckets, execs, i_start, defer, beyond, n_mapped, n_refresh, None
