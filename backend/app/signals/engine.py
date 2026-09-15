# -*- coding: utf-8 -*-
"""信号驱动回测引擎（交易信号测试 · 模式①）—— 逐日事件驱动，不走 qlib PortAnaRecord。

规则（全部来自用户 2026-09-15 的口径确认）：
  · **出信号就买入**（当日多个信号等权分配资金）；**持有 N 个交易日后卖出**；
    持有时又来信号 ⇒ **持有天数刷新为 0**（又能拿 N 天）—— 与事件研究「T+1 买入、T+1+N 卖出」同口径；
  · **成交时点**三选一：信号日收盘 / 次日开盘 / 次日收盘；
  · **严格不可成交**（默认开）：成交日**收盘涨停**买不进；选"次日开盘"时**开盘一字涨停**也买不进；
    卖出侧镜像（跌停卖不出）；**停牌两边都做不了**。买不进 = **放弃该信号**（不追）；
    卖不出 = **顺延**到下一个能卖的日子（逐日重试）；
  · **成本** `cost` = 往返（买+卖）合计费率 ⇒ 单边 `cost/2`（与连续信号 0.004 同义）；
  · **两种资金方案都算**（用户选"两种都做、界面可切换"）：
      `cash_even`  —— 当日新买入在**可用现金**内等分，用完为止（不做任何再平衡）；
      `event_even` —— **只在"买入信号成交"或"到期卖出"当天**把全部持仓再平衡到等权
                     （用户明确：**绝不做每日再平衡**，否则手续费炸裂）。
  · 资金默认 10 亿（用户要求：1000 只同时触发也能等权买得起）；按**整手 100 股**撮合。

价格口径：`$close` 后复权（收益率与真实前复权逐位等价、含分红）；停牌/涨跌停判据用真实价
`$close/$factor`（NaN = 停牌；`close_raw >= 涨停价 − 1e-6 且涨停价 > 0` = 涨停）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

LOT_DEFAULT = 100
_FILL_LAG = {"t_close": 0, "t1_open": 1, "t1_close": 1}          # 决策日 → 成交日
_FILL_PRICE = {"t_close": "close", "t1_open": "open", "t1_close": "close"}

REJECT_TEXT = {
    "limit_up": "涨停买不进", "limit_up_open": "开盘一字涨停买不进",
    "limit_down": "跌停卖不出（顺延）", "limit_down_open": "开盘一字跌停卖不出（顺延）",
    "suspended": "停牌", "no_cash": "资金不足", "no_price": "该日无行情",
}


def _split_mode(spec: str) -> Tuple[str, Optional[float], str]:
    """`alloc_modes` 元素 → (基础方案, 该方案死区或 None, 净值列名)。

    允许 `"event_even@0.05"` 这种写法 ⇒ 同一个方案可以跑**多档再平衡死区**做对比
    （用户 2026-09-15：「给个"死区 5%"的对比曲线」）⇒ 列名 `event_even_band5`。
    不带 `@` 时死区取函数参数 `rebal_band`（= 界面上那一档），列名就是方案名（向后兼容）。
    """
    base, _, band_s = str(spec).partition("@")
    if not band_s:
        return base, None, base
    b = float(band_s)
    tag = ("%g" % (b * 100)).replace(".", "p")
    return base, b, "%s_band%s" % (base, tag)


@dataclass
class BtResult:
    nav: pd.DataFrame = field(default_factory=pd.DataFrame)      # index=date, 各方案净值
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    rejects: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: Dict = field(default_factory=dict)
    diag: Dict = field(default_factory=dict)


def _prepare(signals: pd.DataFrame, panel: Dict[str, pd.DataFrame]) -> dict:
    """把面板/信号整理成 numpy（列位置一次解析，主循环里只做整数索引）。"""
    cal = pd.DatetimeIndex(panel["CALENDAR"].index)
    close = panel["CLOSE"]
    codes = sorted(set(signals["code"]) & set(close.columns))
    missing = sorted(set(signals["code"]) - set(close.columns))
    if not codes:
        return {"cal": cal, "codes": [], "missing": missing}
    col_of = {c: i for i, c in enumerate(codes)}

    def _m(key, like):
        dfw = panel.get(key)
        if dfw is None:
            return like.copy()
        return dfw.reindex(index=cal, columns=codes).to_numpy(dtype=float)

    C = _m("CLOSE", np.full((len(cal), len(codes)), np.nan))
    O = _m("OPEN", C.copy())
    CR = _m("CLOSE_RAW", C.copy())
    OR = _m("OPEN_RAW", O.copy())
    LU = panel.get("LIMIT_UP")
    LD = panel.get("LIMIT_DOWN")
    LU = (LU.reindex(index=cal, columns=codes).to_numpy(dtype=float) if LU is not None
          else np.full_like(C, np.nan))
    LD = (LD.reindex(index=cal, columns=codes).to_numpy(dtype=float) if LD is not None
          else np.full_like(C, np.nan))
    pos_of = {d: i for i, d in enumerate(cal)}
    return {"cal": cal, "codes": codes, "col_of": col_of, "pos_of": pos_of,
            "C": C, "O": O, "CR": CR, "OR": OR, "LU": LU, "LD": LD, "missing": missing}


def _bucket_signals(signals: pd.DataFrame, prep: dict) -> dict:
    """信号 → 交易日位置分桶（非交易日**顺延**到下一交易日；数据范围外丢弃）。"""
    cal, pos_of, col_of = prep["cal"], prep["pos_of"], prep["col_of"]
    buys: Dict[int, List[int]] = {}
    exits: Dict[int, List[int]] = {}
    raw_days: Dict[int, int] = {}
    defer = beyond = 0
    n = len(cal)
    for dt, code, side in zip(signals["date"], signals["code"], signals["side"]):
        if code not in col_of:
            continue
        d = pd.Timestamp(dt)
        if n == 0 or d < cal[0]:
            beyond += 1                                 # 早于行情起点 ⇒ 丢弃（**不能**映射到首日）
            continue
        p = int(cal.searchsorted(d))
        if p >= n:
            beyond += 1                                 # 晚于行情末尾 ⇒ 丢弃
            continue
        if cal[p] != d:
            defer += 1                                  # 非交易日 ⇒ 顺延到下一交易日
        c = col_of[code]
        tgt = buys if side > 0 else exits
        bucket = tgt.setdefault(p, [])
        if c in bucket:
            continue
        bucket.append(c)
        if side > 0:
            raw_days[p] = raw_days.get(p, 0) + 1
    return {"buys": buys, "exits": exits, "deferred": defer, "beyond": beyond,
            "n_mapped": sum(len(v) for v in buys.values()) + sum(len(v) for v in exits.values())}


def run_backtest(signals: pd.DataFrame, panel: Dict[str, pd.DataFrame], *,
                 hold_days: int = 20, fill: str = "t1_open", cost: float = 0.004,
                 capital: float = 1e9, strict_limit: bool = True,
                 alloc_modes: Sequence[str] = ("event_even", "cash_even"),
                 lot: int = LOT_DEFAULT, rebal_band: float = 0.0) -> BtResult:
    """跑回测，返回**两种资金方案**的净值 + 成交/被拒明细 + 统计。"""
    if not len(signals) or "CALENDAR" not in panel or "CLOSE" not in panel:
        return BtResult(diag={"error": "无可用信号或价格面板"})
    fill = fill if fill in _FILL_LAG else "t1_open"
    lag, px_kind = _FILL_LAG[fill], _FILL_PRICE[fill]
    hold_days = max(1, int(hold_days))
    half = float(cost) / 2.0

    prep = _prepare(signals, panel)
    cal, codes, col_of = prep["cal"], prep["codes"], prep["col_of"]
    if not codes:
        return BtResult(diag={"error": "信号标的不在行情数据里", "missing": prep["missing"][:20]})
    C, O, CR, OR, LU, LD = (prep["C"], prep["O"], prep["CR"], prep["OR"],
                            prep["LU"], prep["LD"])
    bk = _bucket_signals(signals, prep)
    n_row = len(cal)

    def _price(i: int, c: int, kind: str = None) -> float:
        arr = O if (kind or px_kind) == "open" else C
        v = arr[i, c]
        return float(v) if np.isfinite(v) and v > 0 else float("nan")

    def _suspended(i: int, c: int) -> bool:
        return not np.isfinite(CR[i, c])

    def _limit(i: int, c: int, up: bool, level: str) -> bool:
        """成交日涨/跌停判定（level=close 用收盘价，open 用开盘价；标签是**价格**）。"""
        lv = LU[i, c] if up else LD[i, c]
        if not np.isfinite(lv) or lv <= 0:
            return False
        v = CR[i, c] if level == "close" else OR[i, c]
        if not np.isfinite(v):
            return False
        return (v >= lv - 1e-6) if up else (v <= lv + 1e-6)

    def can_buy(i: int, c: int) -> Optional[str]:
        if _suspended(i, c):
            return "suspended"
        if not strict_limit:
            return None
        # 成交日**收盘涨停** ⇒ 买不进（用户口径：次日涨停时无论开盘/收盘都不买）
        if _limit(i, c, True, "close"):
            return "limit_up"
        if px_kind == "open" and _limit(i, c, True, "open"):
            return "limit_up_open"                     # 开盘一字涨停
        return None

    def can_sell(i: int, c: int) -> Optional[str]:
        if _suspended(i, c):
            return "suspended"
        if not strict_limit:
            return None
        if _limit(i, c, False, "close"):
            return "limit_down"
        if px_kind == "open" and _limit(i, c, False, "open"):
            return "limit_down_open"
        return None

    out = BtResult(
        diag={"fill": fill, "cost": cost, "capital": capital, "hold_days": hold_days,
              "strict_limit": bool(strict_limit), "lot": lot,
              "n_codes": len(codes), "missing_codes": prep["missing"][:20],
              "signals_mapped": bk["n_mapped"], "signals_deferred": bk["deferred"],
              "signals_beyond_data": bk["beyond"],
              "limits_from": "inferred" if "_limits_inferred" in panel else "exchange_tag"})
    all_trades, all_rejects, navs = [], [], {}

    for mode_spec in alloc_modes:
        _base, _band, mode = _split_mode(mode_spec)
        band = float(rebal_band) if _band is None else float(_band)
        cash = float(capital)
        sh: Dict[int, int] = {}                       # 列号 → 股数
        dsz: Dict[int, int] = {}                      # 列号 → 已持有交易日数
        basis: Dict[int, float] = {}                  # 列号 → 持仓成本（用于胜率统计）
        buy_q: Dict[int, List[Tuple[int, int]]] = {}  # 成交日位置 → [(列号, 决策日), …]
        sell_q: Dict[int, Tuple[int, str]] = {}       # 列号 → (决策日, 原因)
        trades: List[dict] = []
        rejects: List[dict] = []
        nav_dates, nav_vals = [], []
        n_refresh = n_defer_sell = 0
        fee_sum = 0.0
        wins = losses = 0
        hold_sum = hold_n = 0
        min_cash = float(capital)          # 现金最低水位（<0 ⇒ 资金给少了，等效加杠杆）

        def _log_trade(i, c, side, qty, price, reason):
            nonlocal cash, fee_sum, wins, losses
            amt = qty * price
            fee = amt * half
            fee_sum += fee
            if side > 0:
                cash -= amt + fee
                sh[c] = sh.get(c, 0) + qty
                basis[c] = basis.get(c, 0.0) + amt + fee          # 含买入费的成本
            else:
                cash += amt - fee
                held = sh.get(c, 0)
                b = basis.get(c, 0.0)
                avg = (b / held) if held > 0 else 0.0              # 每股成本（含费）
                if avg > 0:
                    if (amt - fee) - avg * qty > 0:
                        wins += 1
                    else:
                        losses += 1
                sh[c] = held - qty
                basis[c] = max(0.0, b - avg * qty)
                if sh[c] <= 0:
                    sh.pop(c, None)
                    dsz.pop(c, None)
                    basis.pop(c, None)
            trades.append({"date": str(cal[i].date()), "code": codes[c], "side": "买" if side > 0 else "卖",
                           "shares": int(qty), "price": round(price, 4), "amount": round(amt, 2),
                           "fee": round(fee, 2), "reason": reason, "mode": mode})

        def _reject(i, c, reason, decision_pos=None):
            rejects.append({"date": str(cal[i].date()), "code": codes[c],
                            "reason": reason, "text": REJECT_TEXT.get(reason, reason),
                            "signal_date": str(cal[decision_pos].date()) if decision_pos is not None else None,
                            "mode": mode})

        i_start = min([max(0, min(bk["buys"], default=0) - 1)] +
                      [max(0, min(bk["exits"], default=0) - 1)] + [0])
        for i in range(i_start, n_row):
            # ---- 1) 到期/信号卖出 ----
            for c in list(sell_q.keys()):
                dec, reason = sell_q[c]
                if i < dec + lag or c not in sh:
                    continue
                bad = can_sell(i, c)
                if bad:
                    n_defer_sell += 1
                    _reject(i, c, bad, dec)
                    continue
                px = _price(i, c)
                if not np.isfinite(px):
                    _reject(i, c, "no_price", dec)
                    continue
                hold_sum += dsz.get(c, 0)
                hold_n += 1
                _log_trade(i, c, -1, sh[c], px, reason)
                sell_q.pop(c, None)
            # ---- 2) 信号日：刷新持有天数（已持有 ⇒ 归零，卖单撤回）----
            for c in bk["buys"].get(i, []):
                if c in sh:
                    dsz[c] = 0
                    sell_q.pop(c, None)
                    n_refresh += 1
                elif not any(c == cc for lst in buy_q.values() for cc, _ in lst):
                    buy_q.setdefault(i + lag, []).append((c, i))
            for c in bk["exits"].get(i, []):
                if c in sh and c not in sell_q:
                    sell_q[c] = (i, "sell_signal")
            # ---- 3) 买入成交（含再平衡）----
            pend = buy_q.pop(i, [])
            if pend:
                ok: List[Tuple[int, int]] = []
                for c, dec in pend:
                    bad = can_buy(i, c)
                    if bad:
                        _reject(i, c, bad, dec)
                        continue
                    if not np.isfinite(_price(i, c)):
                        _reject(i, c, "no_price", dec)
                        continue
                    if c in sh:                     # 排队期间已被别的信号买进/刷新
                        n_refresh += 1
                        dsz[c] = 0
                        sell_q.pop(c, None)
                        continue
                    ok.append((c, dec))
                if ok:
                    if mode == "cash_even":
                        # ⚠ 份额要按「价 ×(1+单边费)」算，否则把费漏掉会现金微负（单测抓到）
                        per = (cash / len(ok)) / (1.0 + half)
                        for c, _dec in ok:
                            px = _price(i, c)
                            q = int(per / px / lot) * lot
                            if q <= 0:
                                _reject(i, c, "no_cash")
                                continue
                            _log_trade(i, c, 1, q, px, "buy_signal")
                    else:                            # event_even：只在成交当日把全体再平衡到等权
                        mv = sum(sh[k] * _price(i, k) for k in list(sh) if np.isfinite(_price(i, k)))
                        n_tgt = len(sh) + len(ok)
                        tgt = (cash + mv) / max(1, n_tgt)
                        # ⚠⚠ 顺序必须是「先减仓腾现金 → 再买新信号 → 最后补低配」，否则：
                        #   新信号先花现金 ⇒ 现金被首日信号吃光 ⇒ 后续信号全被判 no_cash 拒掉，
                        #   而"有信号就调平到等权"的本意正是**卖掉超配的老仓**去接新信号
                        #   （2026-09-15 单测 `test_event_even_rebalances_after_new_signal` 抓到）。
                        # 死区：偏差小于「目标市值的 rebal_band」就不动（默认 0 = 每次事件完全调平，
                        # 即用户口径"有信号/到期就平衡"）。实测全调平的代价是 9 年 45% 本金的费用
                        # ⇒ 想让成本下来就把 band 调到 1%~10%（界面上可调）。
                        gap_of = lambda tv, px: max(band * tv, px * lot)   # 死区随方案（可 @ 指定）
                        # ① 减仓超配（腾出现金给新信号）
                        for k in list(sh.keys()):
                            px = _price(i, k)
                            if not np.isfinite(px) or k in [c for c, _ in ok]:
                                continue
                            dlt = sh[k] * px - tgt
                            if dlt > gap_of(tgt, px) and can_sell(i, k) is None:
                                q = min(int(dlt / px / lot) * lot, sh[k])
                                if q > 0:
                                    _log_trade(i, k, -1, q, px, "rebalance_sell")
                        # ② 买新信号（含费；现金不足则按比例缩减）
                        tgt_eff = tgt / (1.0 + half)
                        need = sum(int(tgt_eff / _price(i, c) / lot) * lot * _price(i, c) * (1 + half)
                                   for c, _ in ok)
                        scale = 1.0 if need <= cash * 1.0001 else max(0.0, cash / max(need, 1e-9))
                        for c, _dec in ok:
                            px = _price(i, c)
                            q = int(tgt_eff * scale / px / lot) * lot
                            if q <= 0:
                                _reject(i, c, "no_cash")
                                continue
                            _log_trade(i, c, 1, q, px, "buy_signal")
                        # ③ 补低配的老仓（先算新目标：新买入已计入 sh ⇒ tgt 用最新只数重算）
                        mv = sum(sh[k] * _price(i, k) for k in list(sh) if np.isfinite(_price(i, k)))
                        tgt = (cash + mv) / max(1, len(sh))
                        for k in list(sh.keys()):
                            px = _price(i, k)
                            if not np.isfinite(px):
                                continue
                            dlt = tgt - sh[k] * px
                            if dlt > gap_of(tgt, px) and can_buy(i, k) is None:
                                q = int(dlt / (px * (1 + half)) / lot) * lot
                                if q > 0 and cash >= q * px * (1 + half):
                                    _log_trade(i, k, 1, q, px, "rebalance_buy")
            # ---- 4) 到期卖出（在当日**开头状态**下判 days ≥ N − 成交延迟）----
            # ⚠ 决策日必须按成交延迟前移：`t1_open/t1_close` 的成交在决策日的**次日**，
            #   若仍写 `days >= N` 就会持有 N+1 个交易日（单测 test_basic_buy_hold_sell_and_cost
            #   抓到）。`t_close`（lag=0）不变 ⇒ 两种口径的**实际持有都正好 N 个交易日**，
            #   与事件研究「T+1 买入、T+1+N 卖出」一致。
            for c in list(sh.keys()):
                if c not in sell_q and dsz.get(c, 0) >= max(0, hold_days - lag):
                    sell_q[c] = (i, "sell_expiry")
            # ---- 5) 逐日盯市 ----
            equity = cash + sum(sh[k] * (_price(i, k) if np.isfinite(_price(i, k)) else 0.0)
                                for k in sh)
            if cash < min_cash:
                min_cash = cash
            nav_dates.append(cal[i])
            nav_vals.append(equity / capital)
            for c in list(sh.keys()):
                dsz[c] = dsz.get(c, 0) + 1
        navs[mode] = pd.Series(nav_vals, index=pd.DatetimeIndex(nav_dates))
        all_trades += trades
        all_rejects += rejects
        closed = hold_n
        net = navs[mode].pct_change().fillna(0.0).to_numpy()
        perf = _perf(net)
        out.stats[mode] = {
            "alloc": _base,
            "rebal_band": band,
            "min_cash": round(float(min_cash), 2),
            "final_nav": round(float(navs[mode].iloc[-1]), 4) if len(navs[mode]) else None,
            "total_return": (round(float(navs[mode].iloc[-1] - 1), 4) if len(navs[mode]) else None),
            "trades": len(trades), "buys": sum(1 for t in trades if t["side"] == "买"),
            "sells": sum(1 for t in trades if t["side"] == "卖"),
            "fees_paid": round(fee_sum, 2),
            "fee_ratio_of_capital": round(fee_sum / max(1.0, capital), 4),
            "hold_refreshes": n_refresh,
            "sell_deferrals": n_defer_sell,
            "closed_trades": closed,
            "avg_hold_days": round(hold_sum / closed, 1) if closed else None,
            "win_rate": round(wins / (wins + losses), 3) if (wins + losses) else None,
            "open_positions_end": len(sh),
            "rejects_limit_up": sum(1 for r in rejects if r["reason"].startswith("limit_up")),
            "rejects_suspended": sum(1 for r in rejects if r["reason"] == "suspended"),
            "rejects_no_cash": sum(1 for r in rejects if r["reason"] == "no_cash"),
            "perf": perf,
        }
    nav_df = pd.DataFrame(navs)
    nav_df.index.name = "date"
    out.nav = nav_df
    out.trades = pd.DataFrame(all_trades[:4000])
    out.rejects = pd.DataFrame(all_rejects[:1500])
    return out


def _perf(net: np.ndarray) -> dict:
    """年化/回撤/夏普等（复用项目 `engine/perf_metrics.compute_perf`，与回测页同口径）。"""
    try:
        from app.engine.perf_metrics import compute_perf

        p = compute_perf(net)
        if isinstance(p, dict):
            return {k: (round(float(v), 4) if isinstance(v, (int, float)) else v)
                    for k, v in p.items() if isinstance(v, (int, float, str, type(None)))}
    except Exception:
        pass
    return {}


def attach_benchmark(nav: pd.DataFrame, bench_wide: Optional[pd.DataFrame],
                     bench_code: str) -> pd.DataFrame:
    """把基准指数曲线**归一到与策略同一净值起点**（1.0）后并入 nav。"""
    if bench_wide is None or bench_code not in getattr(bench_wide, "columns", []) or not len(nav):
        return nav
    b = bench_wide[bench_code].reindex(nav.index).ffill()
    first = b.first_valid_index()
    if first is None:
        return nav
    base = float(b.loc[first])
    out = nav.copy()
    out["benchmark"] = (b / base).astype(float)
    return out


def nav_rows(nav: pd.DataFrame) -> list:
    """净值宽表 → Recharts 友好的行数组（列名即曲线名；NaN → None，前端断线不猜数）。

    ⚠ 由 `routers/signal_test.py` 移到这里（v1.19.60）：单因子「事件研究」面板也要画净值曲线
      ⇒ 序列化**只留一份**，避免两条路径各写一套（本项目最忌讳的格式/口径分叉）。
    """
    rows = []
    for d, row in nav.iterrows():
        rec = {"date": str(pd.Timestamp(d).date())}
        for c in nav.columns:
            v = row[c]
            rec[str(c)] = None if not pd.notna(v) else round(float(v), 5)
        rows.append(rec)
    return rows
