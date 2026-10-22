# -*- coding: utf-8 -*-
"""`batch_even` 逐日循环的**初始状态**（★ v2.11.13 从 `signals/engine.py` 抽出）。

## 为什么单独一块
这 20 多行是"逐日循环要用到的**全部可变状态**" ✓ —— 放在一个地方，读循环的人不用回头翻 ✗；
而且它**纯数据**（不碰行情、不碰信号 ✓）⇒ 可以单独构造与断言 ✓。

## ⭐⭐ 两条口径必须记住（写在这儿，省得以后又"优化"回去）
1. **`cash` 按批次均分** ✓（`capital * share` ✓，`share = 1/n_buckets` ✓）——
   写成"每批各拿一份完整资金"✗ ⇒ 总仓位变 N 倍 ✓，净值曲线**放大 N 倍而形状不变** ✗
   （用户只会觉得"这策略好猛" ✓）。
2. ⭐⭐ **持有天数按「票」记，不按「(批次, 票)」记** ✓（v2.1.51，用户 2026-10-12
   「统一成连续持有」✓）：
   · `entry_c` = **首次建仓日**（所有批次合计从 0 → >0 那一刻 ✓）；
   · `nhold`   = 有**几个批次**还持有它 ✓（**归零才结算一笔** ✓）；
   · `pnl_c`   = 该笔的**现金净额**累计 ✓（判胜负用 ✓）。
   按 `(批, 票)` 记的话，同一只票被多批连续持有时**每批各算一次** ✗
   ⇒ "平均持有"恒 ≈ 调仓周期 ✓（读起来像"只持 60 天"✗，其实是"每次调仓重新起算" ✗）。

## 另两组队列（别混）
· `sell_q` = **移出名单 ⇒ 清仓** ✓；
· `trim_q` = **份数变少 ⇒ 卖掉多出来的部分** ✓（跌停/停牌当天卖不出就登记在此逐日重试 ✓，
  且每次按**当前权益**重算目标 ✓ —— 价格在变 ✓）。

## ⚠ 为什么用 `TypedDict` 而不是普通 `dict`
第一版写成 `Dict[str, object]` ✗ ⇒ 调用处 `_S["cash"]` 全是 `object` ✗
⇒ **`mypy` 当场 64 条错误**（`engine.py` 整体失去类型 ✓）。
`TypedDict` 保留每个键的类型 ✓ ⇒ 调用处**不用改一行引用方式** ✓（`cash[b]` 仍可索引 ✓）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple, TypedDict


class BatchState(TypedDict):
    """逐日循环的状态（键名与 `run_batch_backtest` 的局部变量一一对应 ✓）。"""

    share: float
    cash: Dict[str, float]
    sh: Dict[str, Dict[int, int]]
    basis: Dict[str, Dict[int, float]]
    entry_c: Dict[int, int]
    nhold: Dict[int, int]
    pnl_c: Dict[int, float]
    sell_q: Dict[Tuple[str, int], Tuple[int, str]]
    trim_q: Dict[Tuple[str, int], Tuple[int, float, float]]
    trades: List[dict]
    rejects: List[dict]
    nav_dates: list
    nav_vals: list
    fee_sum: float
    wins: int
    losses: int
    n_refresh: int
    n_defer_sell: int
    hold_sum: int
    hold_n: int
    min_cash: float


def init_batch_state(capital: float, buckets: List[str]) -> BatchState:
    """构建逐日循环的初始状态 ✓（见模块 docstring 的两条口径 ✓）。"""
    share = 1.0 / max(1, len(buckets))
    return {
        "share": share,
        # 每个批次一份**均分后**的现金 ✓（不是一整份 ✗）
        "cash": {b: float(capital) * share for b in buckets},
        "sh": {b: {} for b in buckets},        # 批次 -> {列号: 份数}
        "basis": {b: {} for b in buckets},     # 批次 -> {列号: 成本}
        # 连续持有口径（按**票** ✓）
        "entry_c": {},
        "nhold": {},
        "pnl_c": {},
        "sell_q": {},                          # 移出名单 ⇒ 清仓
        "trim_q": {},                          # 份数变少 ⇒ 卖掉多的
        "trades": [],
        "rejects": [],
        "nav_dates": [],
        "nav_vals": [],
        "fee_sum": 0.0,
        "wins": 0,
        "losses": 0,
        "n_refresh": 0,
        "n_defer_sell": 0,
        "hold_sum": 0,                         # 已实现持有的交易日合计
        "hold_n": 0,                           # 笔数（avg_hold_days = hold_sum/hold_n）
        "min_cash": float(capital),
    }
