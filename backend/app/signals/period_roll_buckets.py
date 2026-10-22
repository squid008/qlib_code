# -*- coding: utf-8 -*-
"""「每期全仓滚动」的**批次 → 调仓日 → 目标名单**（★ v2.11.7 从 `signals/period_roll.py` 抽出）。

## 这一段在算什么
信号表里每一行是「某天、某只股票有信号」✓。这里把它折叠成
**{执行日行号: [目标列号, …]}** ✓ —— 之后逐日循环只在「命中执行日」时按这份名单重建全仓 ✓。

## 三个容易写错的点（都有对应测试 ✓）
1. ⭐ **没有 `bucket` 列时，每个"有信号的日期"自成一个批次** ✓ ——
   我们导出的"每期组合"正是这种形状（只在调仓日有信号 ✓），语义完全对应 ✓；
   若强行"今天所有信号算一批"而不看日期，会把不同调仓日的名单混成一锅 ✗。
2. ⭐⭐ **同一执行日可能被多个批次命中**（`lag` 相同、日期不同 ⇒ 执行日相同 ✓，
   或同一批次里重复出现 ✓）⇒ 必须 `setdefault(...).extend(去重后)` ✓。
   直接 `extend(cols)` 不去重 ⇒ 名单里同一只票出现两次 ✓ ⇒ 全仓等权时**它被加了两次仓**
   ✗（而且总仓位仍是 100% ⇒ 别的票被挤少 ✓，净值看着"正常"但权重全错 ✗ 极难发现）。
3. ⭐ **目标存的是列号、不是代码** ✓（逐日循环里按下标取值 ✓）；代码不在行情里 ⇒ 跳过 ✓
   （`col_of` 缺这个代码 ✓），执行日越界/日期不在日历 ⇒ 整批跳过 ✓。

## 为什么单独成文
它是**分批语义的唯一实现** ✓：改这里会同时影响"每期重建"的口径与持仓明细导出 ✓；
埋在 438 行的逐日循环前面时，谁也不敢动 ✗。
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd


def build_targets(signals, *, pos: Dict, lag: int, n_row: int, col_of: Dict) -> Dict[int, List[int]]:
    """→ `{执行日行号: [目标列号, …]}`（**保序去重** ✓，见模块 docstring）。

    · `pos`  = `{交易日: 行号}`（来自 `panel["CALENDAR"]` ✓）
    · `lag`  = 成交滞后（`t1_open`/`t1_close` ⇒ 1、`t_close` ⇒ 0 ✓）
    · `col_of` = `{代码: 列号}`
    """
    sig = signals
    if "bucket" not in sig.columns or sig["bucket"].isna().all():
        # 没有批次列：**每个"有信号的日期"自成一个批次** ✓
        bucket_s = sig["date"].astype(str)
    else:
        bucket_s = sig["bucket"].astype(str)

    targets: Dict[int, List[int]] = {}
    for _b, grp in sig.groupby(bucket_s, sort=True):
        day = pd.Timestamp(grp["date"].min())
        if day not in pos:
            continue
        i_exec = pos[day] + lag
        if i_exec >= n_row:
            continue
        seen: set = set()
        cols: List[int] = []
        for code in grp["code"].astype(str):
            c = col_of.get(code)
            if c is None or c in seen:
                continue
            seen.add(c)
            cols.append(c)
        if cols:
            # ⚠ 必须**去重合并**（同一执行日可能被多个批次命中 ✓）—— 见 docstring 第 2 点
            targets.setdefault(i_exec, []).extend(
                [c for c in cols if c not in targets.get(i_exec, [])])
    return targets
