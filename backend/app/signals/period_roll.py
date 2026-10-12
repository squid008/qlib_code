# -*- coding: utf-8 -*-
"""`period_roll`：**每期全仓滚动**（"持仓曲线/每期组合"的实盘版口径）。

## 为什么要有它（用户 2026-10-11："按 2 来，开工"）

面板上的「持仓期净值曲线」是**组合收益模型**（每期等权收益累乘 ✓ 纯前端、零重算 ✓）：
调仓日取信号、T+1 买入、持有到下一个调仓日，期中不动 ✓。它**没有**资金约束、整手、涨跌停。

把这个"每期一批的组合"拖进交易信号测试后，现有两个方案都对不上 ✗：

· `event_even` —— "**只要当天有买入成交或到期卖出**，就把全部持仓再平衡到等权" ✗
  ⇒ 每期名单里**重复出现**的票会被当成"新买入"（实测 `hold_refreshes` 占比很高 ✗）
  ⇒ 多出大量调平交易、手续费吃掉可观本金 ✗；
· `batch_even` —— "**每批固定 1/N 份额**、批间互不挪用" ✗ ⇒ 旧批到期回款变成不再投入的现金 ✗
  ⇒ 复利大打折扣（实测与 `event_even` 差 53pp ✗）—— 它服务的是"一周 5 个并发批次"那种场景 ✓。

`period_roll` 的口径（与面板模型逐条对应 ✓）：

  · **批次 = 调仓日**（信号里的 `bucket` 列；缺列 ⇒ 用信号日 ✓）；
  · 每个批次**把当时的全部权益**按该批名单**等权重建**（先卖不在名单里的、再把在名单里的
    调到等权 ✓）—— 所以**不存在"每批固定 1/N"** ✓，回款立刻重新投入 ✓；
  · **期中不动**（不做任何再平衡 ✓，也不按持有天数自动卖 ✗ —— 下一批重建时自然换掉 ✓）；
  · 卖不掉（跌停/停牌）⇒ **顺延**到能卖的日子（逐日重试 ✓，与主引擎同规矩 ✓）；
  · 买不进（涨停）⇒ 该票这一期就少买（**不追** ✓，与主引擎同规矩 ✓）；
  · 整手 100 股、成本 `cost` = 往返合计（单边 `cost/2` ✓）。

⚠ **撮合/涨跌停/估值口径一律复用主引擎**（`engine._prepare` / `_limit_ctx` ✓）——
  本模块**只写资金分配策略** ✓，绝不复制一份撮合逻辑（复制必然漂移 ✗）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .engine import (LOT_DEFAULT, REJECT_TEXT, BtResult, _FILL_LAG, _FILL_PRICE, _fee_of,
                     _limit_ctx, _lot_of, _perf, _prepare, _trades_frame)

MODE = "period_roll"
_LABEL = "每期全仓滚动（调仓日按当期名单等权重建）"


def run_period_roll(signals: pd.DataFrame, panel: Dict[str, pd.DataFrame], *,
                    fill: str = "t1_open", cost: float = 0.004, capital: float = 1e9,
                    strict_limit: bool = True, lot: int = LOT_DEFAULT,
                    cancel_cb=None) -> BtResult:
    """跑「每期全仓滚动」，返回 `BtResult`（`nav` / `trades` / `rejects` / `stats` ✓）。

    `signals` 需含 `date` / `code` 列（买入信号 ✓）；`bucket` 列可选（= 批次/调仓日 ✓）。
    """
    half = float(cost) / 2.0
    if not len(signals) or "CALENDAR" not in panel or "CLOSE" not in panel:
        return BtResult(diag={"error": "无可用信号或价格面板"})
    fill = fill if fill in _FILL_LAG else "t1_open"
    lag, px_kind = _FILL_LAG[fill], _FILL_PRICE[fill]

    prep = _prepare(signals, panel)
    cal, codes, col_of = prep["cal"], prep["codes"], prep["col_of"]
    if not codes:
        return BtResult(diag={"error": "信号标的不在行情数据里", "missing": prep["missing"][:20]})
    _price, can_buy, can_sell, PX = _limit_ctx(prep, px_kind, strict_limit)
    # ★ v2.1.17：**每个标的的整手股数**（科创板 688/689 ⇒ 200 股 ✓，其余 100 ✓）——
    #   按列号预算一次 ✓（`_lot_of` 是字符串判断，别放进逐票循环里 ✗）。
    lot_arr = np.fromiter((_lot_of(c, lot) for c in codes), dtype=np.int64, count=len(codes))
    _date_arr = np.asarray(cal.strftime("%Y-%m-%d"))
    pos = {d: i for i, d in enumerate(cal)}
    n_row = len(cal)

    # ---------- ① 批次 → 调仓日 → 目标名单（列号，保序去重 ✓）----------
    sig = signals
    if "bucket" not in sig.columns or sig["bucket"].isna().all():
        # 没有批次列：**每个"有信号的日期"自成一个批次** ✓ ——
        # 我们导出的"每期组合"正是这种形状（只在调仓日有信号 ✓），语义完全对应 ✓。
        bucket_s = sig["date"].astype(str)
    else:
        bucket_s = sig["bucket"].astype(str)
    targets: Dict[int, List[int]] = {}          # 成交日位置 → 目标列号
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
            targets.setdefault(i_exec, []).extend([c for c in cols if c not in targets.get(i_exec, [])])

    # ---------- ② 逐日：调仓日重建全仓，期中不动 ----------
    cash = float(capital)
    sh: Dict[int, int] = {}                     # 列号 → 股数
    drop: set = set()                           # 该卖出但（跌停/停牌）没卖成的
    tgt_now: List[int] = []                      # 当前批次的目标（顺延卖出时要继续用 ✓）
    nav_vals: List[float] = []
    trades: List[tuple] = []
    rejects: List[tuple] = []
    fee_sum = 0.0
    min_cash = cash
    # ★★ v2.1.50：**已实现持有天数 / 胜率 / 平仓笔数** ✗→✓ —— 用户 2026-10-12 报：
    #   「每期全仓滚动」那张卡片显示 `平均持有 = null 日`、`已实现胜率 = -` ✗（"不是 60 天调仓么" ✓）。
    #   根因：本模块当初把这三个字段**写死为 None/0** ✗（见文件末尾的 stats ✓）—— 它是**附加统计**，
    #   没人先做 ✓。现在按**移动平均成本法**实时记账（与主引擎 `engine.py` 的
    #   `avg_hold_days` / `win_rate` / `closed_trades` **同义** ✓）：
    #     · 建仓（持仓 0 → >0）记 `_entry_i`；期内加仓（`roll_in`）/减仓（`roll_trim`）**不改起点** ✓；
    #     · 持仓**归零** ⇒ 视为平仓 ⇒ 计入 `hold_sum / hold_n`，并按该笔**净额**判胜负 ✓。
    #   ⚠ 天数用**交易日数**（`i − entry_i` ✓，与主引擎的 `60 日` 量纲一致 ✓）。
    _entry_i: Dict[int, int] = {}
    _qty: Dict[int, float] = {}
    _basis: Dict[int, float] = {}
    _pnl: Dict[int, float] = {}
    hold_sum = 0.0
    hold_n = 0
    wins = 0
    losses = 0

    def _acc(c: int, dq: int, gross: float, fee: float, i: int) -> None:
        """★ v2.1.50：平仓台账（移动平均成本法 ✓）。`dq > 0` = 买入、`dq < 0` = 卖出 ✓。

        ⚠ 只在**持仓归零**那一刻结算一笔（与主引擎"平仓交易"口径一致 ✓）——
          期间加/减仓只更新成本与数量，**不产生**新的一笔 ✓。
        """
        nonlocal hold_sum, hold_n, wins, losses
        if dq > 0:
            if _qty.get(c, 0.0) <= 0:
                _entry_i[c] = i
                _pnl[c] = 0.0
                _basis[c] = 0.0
            _qty[c] = _qty.get(c, 0.0) + float(dq)
            _basis[c] = _basis.get(c, 0.0) + float(gross) + float(fee)
            return
        part = min(float(-dq), _qty.get(c, 0.0))
        if part <= 0:
            return
        cur = _qty.get(c, 0.0)
        cost = _basis.get(c, 0.0) * (part / cur)
        _pnl[c] = _pnl.get(c, 0.0) + (float(gross) - float(fee)) - cost
        _basis[c] = _basis.get(c, 0.0) - cost
        _qty[c] = cur - part
        if _qty[c] <= 1e-9:                      # 清仓 ⇒ 结算一笔 ✓
            hold_sum += float(i - int(_entry_i.get(c, i)))
            hold_n += 1
            if _pnl.get(c, 0.0) > 0:
                wins += 1
            else:
                losses += 1
            _qty[c] = 0.0
            _basis[c] = 0.0
            _pnl[c] = 0.0

    def _sell(i: int, c: int, reason: str) -> bool:
        """卖出（含费用 ✓）；不可成交 ⇒ 记拒绝 + 返回 False ✓。"""
        nonlocal cash, fee_sum
        bad = can_sell(i, c)
        if bad:
            rejects.append((i, c, bad, None))
            return False
        px = _price(i, c)
        if px != px:
            rejects.append((i, c, "no_price", None))
            return False
        q = sh.pop(c)
        amt = q * px
        fee = _fee_of(amt, half)
        cash += amt - fee
        fee_sum += fee
        _acc(c, -q, amt, fee, i)                 # ★ v2.1.50：平仓台账 ✓
        trades.append((i, c, -1, q, px, reason, MODE))
        return True

    def _buy(i: int, c: int, q: int, reason: str) -> bool:
        nonlocal cash, fee_sum
        bad = can_buy(i, c)
        if bad:
            rejects.append((i, c, bad, None))
            return False
        px = _price(i, c)
        if px != px:
            rejects.append((i, c, "no_price", None))
            return False
        amt = q * px
        fee = _fee_of(amt, half)
        if amt + fee > cash + 1e-9:                 # 现金不够 ⇒ 按可用现金缩量（整手 ✓）
            _lt = int(lot_arr[c])                   # v2.1.17：按标的自己的手数缩量 ✓
            q = int((cash / (px * (1.0 + half))) / _lt) * _lt
            if q <= 0:
                rejects.append((i, c, "no_cash", None))
                return False
            amt = q * px
            fee = _fee_of(amt, half)
        cash -= amt + fee
        fee_sum += fee
        sh[c] = sh.get(c, 0) + q
        _acc(c, q, amt, fee, i)                  # ★ v2.1.50：建仓/加仓台账 ✓
        trades.append((i, c, 1, q, px, reason, MODE))
        return True

    for i in range(n_row):
        tgt = targets.get(i)
        # ★★ v2.1.14（用户 2026-10-11 指出，方向我改过一次才对 ✓）：
        #   **`period_roll` 只在调仓日交易** ✓ —— 期中**完全不交易**（含不顺延卖出 ✗）。
        #   卖单只在调仓日产生；当时卖不掉（跌停/停牌）就**留到下一个调仓日**，
        #   到那时**按新名单重新决定**：
        #     · 它回到名单 ⇒ 继续持有 ✓（用户原话："下次名单有它了，就不应该继续卖了呀" ✓）
        #     · 它仍然不在名单 ⇒ 再卖一次 ✓
        #   ⚠ 我上一版（v2.1.13）改成了"每次调仓日重算 drop"✗ 但**保留了期中逐日重试** ✗ ⇒
        #     仍会在期中把它卖掉（单测 `test_period_roll.py` 当场抓到：day6 卖了 A ✗）——
        #     因为"逐日重试"是从**已过时的名单**出发的，等于用旧名单做决策 ✗。
        #   ⇒ 现在删掉 `drop` 集合与期中重试：谁"本期不在名单"就当场卖；卖不掉就留着，
        #     等下个调仓日再说 ✓（这也正是"每期全仓滚动、期中不动"的定义 ✓）。
        if tgt is not None:
            tgt_set = set(tgt)
            for c in [c for c in list(sh) if c not in tgt_set]:
                _sell(i, c, "roll_out")            # 卖不掉 ⇒ 留着（下个调仓日再判 ✓）
        # 重建等权（只在调仓日 ✓）
        if tgt is not None:
            tgt_now = tgt
            keep = [c for c in tgt_now if c in sh]
            mv = 0.0
            for c in keep:
                v = float(PX[i, c])
                if v == v:
                    mv += sh[c] * v
            equity = cash + mv
            n = len(tgt_now)
            if n and equity > 0:
                want_val = equity / n
                need_buy: List[Tuple[int, int]] = []
                for c in tgt_now:
                    px = float(PX[i, c])
                    if px != px:
                        continue
                    _lt = int(lot_arr[c])                  # v2.1.17：按标的自己的手数取整 ✓
                    wq = int(want_val / px / _lt) * _lt
                    cur = sh.get(c, 0)
                    if wq < cur:
                        # 超配 ⇒ 减仓（卖不掉就留着，下一日再试 ✓）
                        bad = can_sell(i, c)
                        if bad:
                            rejects.append((i, c, bad, None))
                            continue
                        px2 = _price(i, c)
                        if px2 != px2:
                            continue
                        dq = cur - wq
                        amt = dq * px2
                        fee = _fee_of(amt, half)
                        cash += amt - fee
                        fee_sum += fee
                        sh[c] = wq
                        _acc(c, -dq, amt, fee, i)   # ★ v2.1.50：减仓台账 ✓
                        trades.append((i, c, -1, dq, px2, "roll_trim", MODE))
                    elif wq > cur:
                        need_buy.append((c, wq - cur))
                for c, dq in need_buy:             # 先卖后买：回款当天可用 ✓
                    _buy(i, c, dq, "roll_in")
            drop = {c for c in drop if c in sh}

        # ---- 逐日盯市（与主引擎同口径：用**成交价矩阵** ✓）----
        mv = 0.0
        for c, q in sh.items():
            v = float(PX[i, c])
            if v == v:
                mv += q * v
        equity = cash + mv
        min_cash = min(min_cash, cash)
        nav_vals.append(equity / float(capital))
        if cancel_cb is not None and (i % 20 == 0):
            cancel_cb()

    nav = pd.Series(nav_vals, index=cal[:n_row], name=MODE)
    net = nav.pct_change().fillna(0.0).to_numpy()
    out = BtResult(
        nav=pd.DataFrame({MODE: nav}),
        diag={"fill": fill, "cost": cost, "capital": capital, "hold_days": None,
              "strict_limit": bool(strict_limit), "lot": lot, "n_codes": len(codes),
              "missing_codes": prep["missing"][:20], "limits_from": "period_roll",
              "rolls": int(len(targets))},
    )
    out.trades = _trades_frame(trades[:20000], _date_arr, codes, half)
    out.rejects = pd.DataFrame(
        [{"date": _date_arr[_i], "code": codes[_c], "reason": _rs,
          "text": REJECT_TEXT.get(_rs, _rs), "signal_date": None, "mode": MODE}
         for (_i, _c, _rs, _dp) in rejects][:30000])
    out.stats[MODE] = {
        "alloc": MODE, "label": _LABEL, "rebal_band": 0.0,
        "min_cash": round(float(min_cash), 2),
        "final_nav": round(float(nav.iloc[-1]), 4) if len(nav) else None,
        "total_return": round(float(nav.iloc[-1] - 1), 4) if len(nav) else None,
        "trades": len(trades),
        "buys": sum(1 for t in trades if t[2] > 0),
        "sells": sum(1 for t in trades if t[2] < 0),
        "fees_paid": round(fee_sum, 2),
        "fee_ratio_of_capital": round(fee_sum / max(1.0, capital), 4),
        "hold_refreshes": 0, "sell_deferrals": 0,
        # ★ v2.1.50：不再写死 ✗ —— 用 `_acc` 的平仓台账如实统计 ✓（口径与主引擎同义 ✓：
        #   `avg_hold_days` = 已平仓交易的平均**交易日**持有数 ✓、`win_rate` = 其中净额为
        #   正的占比 ✓、`closed_trades` = 平仓笔数 ✓）。无平仓 ⇒ 仍为 None（前端显示 `-` ✓）。
        "closed_trades": int(hold_n),
        "avg_hold_days": (round(hold_sum / hold_n, 1) if hold_n else None),
        "win_rate": (round(wins / (wins + losses), 3) if (wins + losses) else None),
        "open_positions_end": len(sh),
        "rejects_limit_up": sum(1 for r in rejects if r[2].startswith("limit_up")),
        "rejects_suspended": sum(1 for r in rejects if r[2] == "suspended"),
        "rejects_no_cash": sum(1 for r in rejects if r[2] == "no_cash"),
        "rejects_total": len(rejects),
        "perf": _perf(net),
        "rolls": int(len(targets)),
    }
    return out


def merge_into(bt: BtResult, extra: Optional[BtResult]) -> BtResult:
    """把 `period_roll` 的净值列/统计**并进**主回测结果 ✓（列名不冲突 ✓，缺失也不炸 ✓）。"""
    if extra is None or extra.nav is None or not extra.nav.shape[1]:
        return bt
    if bt.nav is None or not bt.nav.shape[1]:
        return extra
    bt.nav = bt.nav.join(extra.nav, how="left")
    bt.stats.update(extra.stats)
    if len(extra.trades):
        bt.trades = pd.concat([bt.trades, extra.trades], ignore_index=True)
    if len(extra.rejects):
        bt.rejects = pd.concat([bt.rejects, extra.rejects], ignore_index=True)
    return bt
