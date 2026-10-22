# -*- coding: utf-8 -*-
"""`batch_even` 的**结果装配**（★ v2.11.28 从 `signals/engine.py` 抽出）。

逐日循环跑完之后，这里把计数器拼成 `BtResult`（净值 / 成交 / 统计 / 诊断 ✓）。
抽出来的理由：**装配是纯函数** ✓（不碰行情、不改状态 ✓），而它原来占了 `run_batch_backtest`
尾部 40 多行 ✗ —— 混在 300 多行的逐日循环里，改一个统计字段要在循环里翻半天 ✓。

## ⚠ 两条口径（写在这儿，别在装配层\"顺手优化\"）
1. **`trades` / `rejects` 截断**（4000 / 1500 ✓）：全 A 跑一轮能有几万笔 ✓，
   全塞进响应会把浏览器撑爆 ✗。⚠ 但统计里的 `"trades"` 用的是**真实条数** ✓（不是截断后的 ✓）——
   两者混用的话，用户看到的\"成交笔数\"会**只有 4000** ✓ 而实际更多 ✗。
2. **`avg_hold_days` 可以是 `None`** ✓（一笔都没平仓时 ✓）—— **不是 0** ✗ ——
   0 会被读成\"持有 0 天\"✓，而真相是\"还没有平过仓\"✓（用户 2026-09-16 专门问过 ✓）。
   同理 `win_rate` 在 `wins+losses==0` 时是 `None` 而不是 0 ✓。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List

import pandas as pd

if TYPE_CHECKING:                     # 只为类型注解 ✓（运行时**不导入** ⇒ 无循环引用 ✓）
    from .engine import BtResult
# ⚠ **不能**在模块顶层 `from .engine import BtResult, _perf` ✗ ——
#   `engine.py` 顶层就 import 了本模块 ✓ ⇒ 会构成
#   **`engine` ↔ `batch_result` 循环引用** ✗（`BtResult` 那时还没定义完 ✓，导入期直接炸 ✓）。
#   ⇒ 在函数内懒导入 ✓（调用时 `engine` 必然已加载完 ✓）。


def build_batch_result(*, nav_dates: List, nav_vals: List,
                       buckets: List[str], share: float, codes: List[str],
                       prep: dict, panel: dict, capital: float, mode: str,
                       fill: str, cost: float, strict_limit: bool, lot: int,
                       n_mapped: int, defer: int, beyond: int,
                       trades: List[dict], rejects: List[dict],
                       cash: Dict[str, float], sh: Dict[str, Dict[int, int]],
                       fee_sum: float, wins: int, losses: int,
                       hold_sum: int, hold_n: int,
                       n_refresh: int, n_defer_sell: int,
                       min_cash: float) -> "BtResult":
    """把计数器拼成 `BtResult` ✓（见模块 docstring 的两条口径）。

    ⚠⚠ **计数器必须由调用方显式传进来** ✗→✓ —— 不能从 `batch_state` 那份 dict 里读 ✓：
    那份 dict 只是**初始快照** ✓，逐日循环里 `_log_trade` 用的是 `nonlocal hold_sum, hold_n, …` ✓
    ⇒ **改的是局部变量，不写回 dict** ✓。我第一版从 dict 读 ⇒ 拿到的是**初始值 0**
    ⇒ `avg_hold_days` 变成 `None`、`fees_paid` 变成 0 ✗
    （是 `test_signal_batch.py::test_avg_hold_days_is_reported` 当场抓到的 ✓）。
    """
    from .engine import BtResult, _perf        # 懒导入：避开循环引用 ✓（见文件头 ⚠）

    nav = pd.Series(nav_vals, index=pd.DatetimeIndex(nav_dates))
    out = BtResult()
    out.nav = pd.DataFrame({mode: nav})
    out.nav.index.name = "date"
    out.trades = pd.DataFrame(trades[:4000])
    out.rejects = pd.DataFrame(rejects[:1500])
    net = nav.pct_change().fillna(0.0).to_numpy()
    out.stats[mode] = {
        "alloc": mode, "rebal_band": 0.0, "min_cash": round(float(min_cash), 2),
        "final_nav": round(float(nav.iloc[-1]), 4) if len(nav) else None,
        "total_return": round(float(nav.iloc[-1] - 1), 4) if len(nav) else None,
        # ⚠ 真实条数（不是 `trades[:4000]` 的长度 ✓，见口径 1）
        "trades": len(trades),
        "buys": sum(1 for t in trades if t["side"] == "买"),
        "sells": sum(1 for t in trades if t["side"] == "卖"),
        "fees_paid": round(fee_sum, 2),
        "fee_ratio_of_capital": round(fee_sum / max(1.0, capital), 4),
        "hold_refreshes": n_refresh,
        "sell_deferrals": n_defer_sell,
        "closed_trades": hold_n,
        # 清单驱动：持有天数由名单决定（不是固定 N）——但仍**如实统计**已实现持有的交易日
        # （用户 2026-09-16 问「为啥会是 null 日」⇒ 不该是 null ✓）；没平过仓 ⇒ None ✓
        "avg_hold_days": round(hold_sum / hold_n, 1) if hold_n else None,
        "win_rate": round(wins / (wins + losses), 3) if (wins + losses) else None,
        "open_positions_end": sum(len(sh[b]) for b in buckets),
        "rejects_limit_up": sum(1 for r in rejects if r["reason"].startswith("limit_up")),
        "rejects_suspended": sum(1 for r in rejects if r["reason"] == "suspended"),
        "rejects_no_cash": sum(1 for r in rejects if r["reason"] == "no_cash"),
        "rejects_total": len(rejects),
        "perf": _perf(net),
    }
    out.diag = {
        "fill": fill, "cost": cost, "capital": capital, "hold_days": None,
        "strict_limit": bool(strict_limit), "lot": lot, "n_codes": len(codes),
        "missing_codes": prep["missing"][:20],
        "signals_mapped": n_mapped,
        "signals_deferred": defer, "signals_beyond_data": beyond,
        "limits_from": "inferred" if "_limits_inferred" in panel else "exchange_tag",
        "batch": {"mode": mode, "n_batches": len(buckets), "share_each": round(share, 4),
                  "batches": buckets[:12], "refreshes": n_refresh,
                  "cash_end": {b: round(float(cash[b]), 2) for b in buckets[:12]},
                  "holdings_end": {b: len(sh[b]) for b in buckets[:12]}},
    }
    return out


__all__ = ["build_batch_result"]
