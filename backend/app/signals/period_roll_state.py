# -*- coding: utf-8 -*-
"""`period_roll` 逐日循环的**初始状态**（★ v2.11.29 从 `signals/period_roll.py` 抽出）。

## ⚠⚠ 这份东西**只是初始值**，不是"活的状态"
逐日循环里的计数器（`fee_sum` / `hold_sum` / `hold_n` / `wins` / `losses` /
`exit_signals` …）都是**局部变量** ✓，内层闭包用 `nonlocal` 改它们 ✓
⇒ **不写回这里** ✗。所以调用方必须：
1. 从这里**解包成局部变量** ✓；
2. 循环结束后**从局部变量**读（**不要**回头读这个 dict ✗ —— 那是初始值 0 ✓）。

> 这条不是理论：v2.11.28 拆 `batch_even` 的装配时，我从同类 dict 里读计数器 ✓
> ⇒ `avg_hold_days` / `fees_paid` 全变 0 ✓，被 `test_avg_hold_days_is_reported` 当场抓到 ✗。

## ⭐ 三条口径（写在这儿，省得以后又"优化"回去）
1. **`cash` 从 `capital` 起**（本方案是**一个**组合 ✓，不分批次 ✓）；
2. ⭐⭐ **持有天数按「票」记**（`_entry_i` ✓，移动平均成本法）：
   建仓（0 → >0）记起点 ✓；期内 `roll_in` / `roll_trim` **不改起点** ✓；
   持仓**归零** ⇒ 视为平仓 ⇒ 计入 `hold_sum / hold_n` 并按该笔**净额**判胜负 ✓。
   ⚠ 天数用**交易日数**（`i − entry_i` ✓），与主引擎的 `60 日` **同量纲** ✓ ——
   换成自然日会让"平均持有 60 日"变成 ~85 ✓（用户 2026-10-12 报过 `平均持有 = null 日` ✓）。
3. **入场价向量（`ENTRY_PRICE` / `ENTRY_FIRST`）只在传了 `exit_rule` 时维护** ✓
   （不勾离场 ⇒ 连数组都不动 ⇒ 零成本 ✓）；未持有 ⇒ `NaN` ⇒ 规则里与它比较**恒为假** ✓
   （不会凭空触发 ✓）。
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np


def init_roll_state(capital: float, n_codes: int, *, track_entry: bool) -> Dict[str, object]:
    """→ 初始状态 dict ✓（键名与 `run_period_roll` 的局部变量一一对应 ✓）。"""
    cash = float(capital)
    return {
        "cash": cash,
        "sh": {},                         # 列号 → 股数
        "drop": set(),                    # 该卖但（跌停/停牌）没卖成的
        "tgt_now": [],                    # 当前批次目标（顺延卖出时继续用 ✓）
        # 离场通道：卖不掉 ⇒ 顺延队列 + 五类计数（统计走 stats ✓）
        "pending_exit": set(),
        "exit_signals": 0, "exit_sells": 0, "exit_deferrals": 0,
        "exit_ignored": 0, "exit_overridden": 0,
        "nav_vals": [],
        "trades": [],
        "rejects": [],
        "fee_sum": 0.0,
        "min_cash": cash,
        # 已实现持有天数 / 胜率 / 平仓笔数（移动平均成本法 ✓，见模块 docstring 口径 2）
        "_entry_i": {},
        "_qty": {},
        "_basis": {},
        "_pnl": {},
        "hold_sum": 0.0,
        "hold_n": 0,
        "wins": 0,
        "losses": 0,
        # 入场价向量（见口径 3）
        "_track_entry": bool(track_entry),
        "_rule_provided": bool(track_entry),
        "avg_px": np.full(n_codes, np.nan, dtype=np.float64),
        "first_px": np.full(n_codes, np.nan, dtype=np.float64),
    }


__all__ = ["init_roll_state"]
