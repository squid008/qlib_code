# -*- coding: utf-8 -*-
"""**成交明细 → 每期持仓**（纯函数，供导出「每期持仓明细」用 ✓）。

★ 为什么单独一个模块：回测（`signals/engine.py`）只产出**逐笔成交**（`bt.trades` ✓），
  而"每期持仓"是它的**派生视图** —— 三个地方要用（事件研究导出、将来的回测导出、自查脚本 ✓）
  ⇒ 抽成纯函数：吃 DataFrame、吐 dict 列表，**不碰 qlib / 不读全局状态** ✓（好单测 ✓）。

口径（导出文件里要照写的）：
  · 同一 `mode`（资金方案）内按**成交日**推进 `持仓股数 += 买 − 卖` ✓；
  · 只输出**当日有成交的日期**（事件研究是"有信号就买、到期就卖" ⇒ 成交日 = 调仓日 ✓）；
  · 快照取**当日成交之后**的状态（该卖的已卖、该买的已买 ✓）；
  · `weight` = 该股 `股数 × 最近成交价` ÷ 当日同方案总市值（**用成交价估算**，不是收盘市值 ✗
    —— 成交价就在手边、且与曲线同源 ✓；口径差异写进导出文件的说明页 ✓）；
  · 卖出后股数归零的股票**不进快照**（已经不是持仓了 ✓）。

⚠ 已知上限：`engine._trades_frame` 只取**前 20000 笔**成交（防内存爆 ✗）⇒ 触发次数极多时
  本函数派生的持仓也会缺尾部 —— 导出文件的说明页会写清这一点 ✓（不静默 ✗）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, cast

import pandas as pd

REQUIRED_COLS = ("date", "code", "side", "shares", "price", "mode")
BUY_TOKENS = ("买", "buy", "B", "1")


def _is_buy(side: Any) -> bool:
    """买/卖判定：`engine._TRADE_KEYS` 的 `side` 是中文「买/卖」✓（也容忍 ±1 / en ✓）。"""
    s = str(side).strip()
    if s in BUY_TOKENS or s in ("+1", "1"):
        return True
    if s in ("卖", "sell", "S", "-1"):
        return False
    try:
        return float(s) > 0
    except (TypeError, ValueError):
        return False


def buy_signals_from_trades(trades: Optional[pd.DataFrame],
                            max_rows: int = 200_000) -> List[Dict[str, Any]]:
    """成交明细 → **真实买入事件** `[{date, code, mode}, …]`（v2.1.3 ✓）。

    ★ 为什么要有它：导出文件第 2 页是"每期持仓**快照**"，而回灌要的是"**买入信号**" ——
      同一只票在它持有的每个调仓日都会出现在快照里 ⇒ 把快照当信号会**重复买入**（实测
      event_even 放大 11.8 倍 ✗，用户报"为啥测试结果不一样" ✓）。这里直接取回测成交里的"买" ✓。
    """
    if trades is None or not isinstance(trades, pd.DataFrame) or trades.empty:
        return []
    if not {"date", "code", "side"} <= set(trades.columns):
        return []
    out: List[Dict[str, Any]] = []
    has_mode = "mode" in trades.columns
    for row in trades.itertuples(index=False):
        if not _is_buy(getattr(row, "side", None)):
            continue
        d = getattr(row, "date", None)
        out.append({
            # ★ v2.11.39：`cast(Any, d)` ✓ —— `d` 是 `getattr` 出来的 `Any | None` ✗，
            #   `hasattr` 只能给运行时兜底 ✓、mypy 不据此收窄 ✗（它认为可能是 None ✓）。
            "date": cast(Any, d).strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10],
            "code": str(getattr(row, "code", "")),
            "mode": str(getattr(row, "mode", "")) if has_mode else "",
        })
        if len(out) >= int(max_rows):
            break
    return out


def positions_from_trades(trades: Optional[pd.DataFrame],
                          modes: Optional[Sequence[str]] = None,
                          max_rows: int = 200_000) -> List[Dict[str, Any]]:
    """成交明细 → 每期持仓行（`[{rebalance_date, mode, code, shares, price, amount, weight}, …]` ✓）。

    `modes` 为 None ⇒ 按 `trades["mode"]` 的**出现顺序**分组（与回测里的方案顺序一致 ✓）；
    数据缺列 / 为空 ⇒ 返回 `[]`（调用方据此给可读提示，不抛 ✗）。
    """
    if trades is None or not isinstance(trades, pd.DataFrame) or trades.empty:
        return []
    if not set(REQUIRED_COLS) <= set(trades.columns):
        return []
    df = trades
    order: Iterable[str] = modes if modes else list(dict.fromkeys(df["mode"].astype(str)))
    out: List[Dict[str, Any]] = []
    for mode in order:
        g = df[df["mode"].astype(str) == str(mode)]
        if g.empty:
            continue
        held: Dict[str, int] = {}          # code → 股数
        last_px: Dict[str, float] = {}     # code → 最近成交价（估值用 ✓）
        for day, day_df in g.groupby("date", sort=True):
            for code, side, shares, price in zip(day_df["code"].astype(str),
                                                 day_df["side"], day_df["shares"],
                                                 day_df["price"]):
                qty = int(shares or 0)
                px = float(price or 0.0)
                if px > 0:
                    last_px[code] = px
                if _is_buy(side):
                    held[code] = held.get(code, 0) + qty
                else:
                    held[code] = held.get(code, 0) - qty
            snap = {c: q for c, q in held.items() if q > 0}
            if not snap:
                continue
            mvs = {c: q * float(last_px.get(c) or 0.0) for c, q in snap.items()}
            total = float(sum(mvs.values()))
            day_str = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)[:10]
            for code, qty in snap.items():
                mv = mvs[code]
                out.append({
                    "rebalance_date": day_str,
                    "mode": str(mode),
                    "code": code,
                    "shares": int(qty),
                    "price": (round(float(last_px.get(code) or 0.0), 4) or None),
                    "amount": (round(mv, 2) if total > 0 else None),
                    "weight": (round(mv / total, 6) if total > 0 else None),
                })
                if len(out) >= int(max_rows):
                    return out
    return out
