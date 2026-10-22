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

from .matching import (LOT_DEFAULT, REJECT_TEXT, FILL_LAG as _FILL_LAG,
                       FILL_PRICE as _FILL_PRICE, fee_of as _fee_of,
                       fit_lots as _fit_lots, fit_real_lots as _fit_real_lots,
                       lot_of as _lot_of)
from .engine import BtResult, _limit_ctx, _perf, _prepare, _trades_frame
# ★ v2.11.6（技术债 P1-#3 第十二刀）：离场输入准备（列轴对齐 / 整手股数 / 形状自检 ✓）
from .period_roll_exit_prep import align_exit_inputs
# ★ v2.11.7（第十三刀）：批次 → 调仓日 → 目标名单（保序去重 ✓）
from .period_roll_buckets import build_targets
# ★ v2.11.29（第十九刀）：逐日循环的初始状态（含"持有天数按票记"的口径 ✓）
from .period_roll_state import init_roll_state

MODE = "period_roll"
_LABEL = "每期全仓滚动（调仓日按当期名单等权重建）"


def run_period_roll(signals: pd.DataFrame, panel: Dict[str, pd.DataFrame], *,
                    fill: str = "t1_open", cost: float = 0.004, capital: float = 1e9,
                    strict_limit: bool = True, limit_judge: str = "legacy",
                    lot: int = LOT_DEFAULT,
                    cancel_cb=None, exit_mat=None, exit_rule=None) -> BtResult:
    """跑「每期全仓滚动」，返回 `BtResult`（`nav` / `trades` / `rejects` / `stats` ✓）。

    ★ T1（v2.8.6）：`limit_judge` = **涨停判定口径** ✓（`legacy` 默认 / `open_only` ✓）——
      语义见 `signals.engine._limit_ctx` ✓；⚠ `open_only` 只在 `fill` 为**开盘成交**时生效，
      否则引擎自动退回 `legacy` 并写进 `diag["limit_judge"]` ✓（不静默分叉 ✓）。

    `signals` 需含 `date` / `code` 列（买入信号 ✓）；`bucket` 列可选（= 批次/调仓日 ✓）。

    ★ v2.4.3（用户 2026-10-15 拍板 ✓）：`exit_mat` = **离场信号矩阵**
      （(T × C) 布尔 numpy 数组，行=日历、列=标的，与 `panel["CALENDAR"]`/本轮 `codes` **对齐** ✓；
       `None` = 不启用离场 ✓）。语义见下方"期中离场通道"注释 ✓。

    ★★ v2.6.0（用户 2026-10-15 拍板 ✓）：`exit_rule` = **含入场价的离场规则**
      （`factors/exit_rules.py::EntryRule` ✓）—— 它依赖**回测状态**（你要在哪根买、加没加仓，
      事先算不出来 ✗）⇒ 只能**逐日**用"当日 C/O 行 + 当前入场价向量"求值 ✓：
        · 每天收盘后算**今天**的信号行 ✓，`lag` 天后执行 ✓（与进场同一套滞后 ⇒ 不前视 ✓）；
        · **只有传了 `exit_rule` 才维护入场价向量** ✓ ⇒ 不用它的回测**零额外成本** ✓
          （用户 2026-10-15："不勾就不记录这样是不是可以加快回测性能" ✓ —— 对 ✓）。
      ⚠ `exit_mat` 与 `exit_rule` 二选一 ✓（同时传时 `exit_mat` 优先，规则被忽略 ✓）。
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
    _price, can_buy, can_sell, PX, RAW, _judge = _limit_ctx(prep, px_kind, strict_limit, limit_judge)
    _date_arr = np.asarray(cal.strftime("%Y-%m-%d"))
    pos = {d: i for i, d in enumerate(cal)}
    n_row = len(cal)
    # ★ v2.11.6（技术债 P1-#3 第十二刀 ✓）：**离场输入准备**整块搬去
    #   `signals/period_roll_exit_prep.align_exit_inputs` ✓ —— 列轴对齐（v2.8.8 那次
    #   "39 vs 36 ⇒ 五个离场计数全 0"的事故 ✓）、整手股数、形状自检都在那儿 ✓。
    exit_mat, lot_arr, _exit_off = align_exit_inputs(
        exit_mat=exit_mat, exit_rule=exit_rule,
        close_cols=list(panel["CLOSE"].columns), codes=codes,
        n_row=n_row, lot=lot, lot_of=_lot_of,
    )

    # ---------- ① 批次 → 调仓日 → 目标名单（列号，保序去重 ✓）----------
    # ★ v2.11.7（技术债 P1-#3 第十三刀 ✓）：整块搬去 `signals/period_roll_buckets.build_targets` ✓
    #   —— 那里写清了"同一执行日被多个批次命中必须去重合并"（不去重 ⇒ 同一只票被加两次仓 ✓，
    #   总仓位仍 100% ⇒ 别的票被挤少、净值看着正常但权重全错 ✗）。
    targets: Dict[int, List[int]] = build_targets(
        signals, pos=pos, lag=lag, n_row=n_row, col_of=col_of)

    # ---------- ② 逐日：调仓日重建全仓，期中不动 ----------
    # ★ v2.11.29（技术债 P1-#3 第十九刀 ✓）：初始状态搬去
    #   `signals/period_roll_state.init_roll_state` ✓（三条口径写在那儿 ✓）。
    #   ⚠⚠ 解包出来的是**局部变量** ✓ —— 循环里的 `nonlocal` 改的就是它们 ✓
    #     （**不要**回头读 `_RS[...]` ✗：那是初始值，v2.11.28 就这么错过一次 ✓）。
    _RS = init_roll_state(capital, len(codes), track_entry=exit_rule is not None)
    cash = _RS["cash"]
    sh, drop, tgt_now = _RS["sh"], _RS["drop"], _RS["tgt_now"]
    pending_exit = _RS["pending_exit"]
    exit_signals, exit_sells = _RS["exit_signals"], _RS["exit_sells"]
    exit_deferrals = _RS["exit_deferrals"]
    exit_ignored, exit_overridden = _RS["exit_ignored"], _RS["exit_overridden"]
    nav_vals, trades, rejects = _RS["nav_vals"], _RS["trades"], _RS["rejects"]
    fee_sum, min_cash = _RS["fee_sum"], _RS["min_cash"]
    _entry_i, _qty, _basis, _pnl = _RS["_entry_i"], _RS["_qty"], _RS["_basis"], _RS["_pnl"]
    hold_sum, hold_n = _RS["hold_sum"], _RS["hold_n"]
    wins, losses = _RS["wins"], _RS["losses"]
    _track_entry, _rule_provided = _RS["_track_entry"], _RS["_rule_provided"]
    avg_px, first_px = _RS["avg_px"], _RS["first_px"]

    def _acc(c: int, dq: float, gross: float, fee: float, i: int) -> None:
        """★ v2.1.50：平仓台账（移动平均成本法 ✓）。`dq > 0` = 买入、`dq < 0` = 卖出 ✓。

        ⚠ 只在**持仓归零**那一刻结算一笔（与主引擎"平仓交易"口径一致 ✓）——
          期间加/减仓只更新成本与数量，**不产生**新的一笔 ✓。
        """
        nonlocal hold_sum, hold_n, wins, losses
        if dq > 0:
            _fresh = _qty.get(c, 0.0) <= 0
            if _fresh:
                _entry_i[c] = i
                _pnl[c] = 0.0
                _basis[c] = 0.0
            _qty[c] = _qty.get(c, 0.0) + float(dq)
            _basis[c] = _basis.get(c, 0.0) + float(gross) + float(fee)
            if _track_entry:
                # 平均成本（含买入费 ✓）；首次入场价只在"从无到有"那一笔记 ✓（加仓不刷新 ✓）
                avg_px[c] = (_basis[c] / _qty[c]) if _qty[c] > 0 else np.nan
                if _fresh:
                    first_px[c] = (float(gross) + float(fee)) / float(dq)
            return
        part = min(float(-dq), _qty.get(c, 0.0))
        if part <= 0:
            return
        cur = _qty.get(c, 0.0)
        cost = _basis.get(c, 0.0) * (part / cur)
        _pnl[c] = _pnl.get(c, 0.0) + (float(gross) - float(fee)) - cost
        _basis[c] = _basis.get(c, 0.0) - cost
        _qty[c] = cur - part
        if _track_entry and _qty[c] > 1e-9:
            # ⚠ **卖出不改变"每股成本"** ✓ —— 按比例结转 ⇒ `(B−B·p/Q)/(Q−p) ≡ B/Q` ✓
            #   （券商口径也是这个 ✓；用户 2026-10-15 讨论确认 ✓）
            avg_px[c] = _basis[c] / _qty[c]
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
            if _track_entry:                     # 清仓 ⇒ 入场价失效 ✓（下次买回重新起算 ✓）
                avg_px[c] = np.nan
                first_px[c] = np.nan

    def _disp_row(i: int, c: int, q: float, px: float):
        """成交明细的**展示口径**：`(真实股数, 真实价)` ✓（v2.8.4，审计 #8 ✓）。

        账目仍按**复权单位**算（`q_adj × 复权价 = n_real × 真实价` ✓ 金额恒等 ✓）；这里只是把明细
        换成"读者能对上账"的口径 ✓（真实股数 × 真实价 = 真实金额 ✓，且真实股数是**整手倍数** ✓ ——
        `tests/test_lot_by_market.py` 一直守着这条 ✓）。真实价取不到 ⇒ 退回复权口径（不装懂 ✓）。
        """
        try:
            _pxr = float(RAW[i, c])
        except Exception:                                             # noqa: BLE001
            return int(q), px
        if np.isfinite(_pxr) and _pxr > 0:
            return int(round(float(q) * float(px) / _pxr)), _pxr
        return int(q), px

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
        _sh_d, _px_d = _disp_row(i, c, q, px)    # ★ v2.8.4：明细按真实口径 ✓
        trades.append((i, c, -1, _sh_d, _px_d, reason, MODE))
        return True

    def _buy(i: int, c: int, q: float, reason: str) -> bool:
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
            # ★ v2.8.3（审计 #9 ✓）：缩量也要用**真实费用**（含最低 5 元 ✓）——
            #   原来按 `cash/(px*(1+half))` 估费 ✗ ⇒ 小额单仍会被 5 元顶破 ⇒ 现金变负 ✗
            #   （用户 2026-10-16："肯定不能出现负数" ✓）。收敛到与主引擎**同一个** `_fit_lots` ✓。
            _lt = int(lot_arr[c])                   # v2.1.17：按标的自己的手数缩量 ✓
            # ★ v2.8.4（审计 #8 ✓）：缩量也按**真实股数的一手**（一手 = 真实 100/200 股 ✓）
            q, _n = _fit_real_lots(RAW[i, c], px, _lt, half, cash, budget=cash / (1.0 + half))
            if q <= 0:
                rejects.append((i, c, "no_cash", None))
                return False
            amt = q * px
            fee = _fee_of(amt, half)
        cash -= amt + fee
        fee_sum += fee
        sh[c] = sh.get(c, 0) + q
        _acc(c, q, amt, fee, i)                  # ★ v2.1.50：建仓/加仓台账 ✓
        _sh_d, _px_d = _disp_row(i, c, q, px)    # ★ v2.8.4：明细按真实口径 ✓
        trades.append((i, c, 1, _sh_d, _px_d, reason, MODE))
        return True

    # ★ v2.6.0 / v2.7.0：含入场价规则的**逐日信号行**（每天收盘后算一行 ✓、`lag` 天后执行 ✓）——
    #   ★ v2.7.0 起规则自带**预计算列**（`MA(C,5)` / 换手率 / 市值 / H/L/V… 都由面板算好 ✓，
    #   见 `factors/exit_rules.py` ⇒ 引擎这边只需喂**入场价向量** ✓，不再限制字段 ✓）。
    _rule_rows: List[Optional[np.ndarray]] = []
    for i in range(n_row):
        tgt = targets.get(i)
        tgt_set = set(tgt) if tgt is not None else None
        # ================= ★ v2.4.3：**期中离场通道**（用户 2026-10-15 拍板 ✓）=================
        # 语义（四条都是明确拍过板的 ✓）：
        #   · 离场信号 = `离场因子值 > 阈值`（面板侧已算成 (T×C) 布尔矩阵 ✓，这里只吃矩阵 ✓）；
        #   · **调仓日以名单为准、离场只在期中生效** ✓ —— 调仓日若该股仍在名单里 ⇒ 离场被**覆盖**
        #     （计数 `exit_overridden` ✓）。这正是用户那条"同日进+出 ⇒ 不交易（保持原状）"在
        #     连续信号引擎里的落地 ✓（名单 = 进场意愿 ⇒ "保持原状"就是继续持有 ✓；名单外照卖 ✓）；
        #   · **卖不掉（停牌/一字跌停）⇒ 顺延逐日重试** ✓（与主引擎 `engine.py` 同规矩 ✓）。
        #     ⚠ 这与 v2.1.14 特意删掉的"期中重试"**不是一回事** ✗ —— 那版是拿**过期名单**做决策 ✓
        #     （用户当时抓到"day6 卖了 A"✗）；这里按**离场信号**重试 ✓；
        #   · **持币期间的离场信号 = 空转**（忽略但计数 `exit_ignored` ✓，否则用户会以为信号没生效 ✓）。
        #   ⚠⚠ **成交滞后 **必须** 与进场同口径**（`lag` ✓，`fill='t1_open'` ⇒ lag=1）——
        #     矩阵第 t 行 = "用 t 日（含）数据算出的离场信号" ✓ ⇒ **执行日 = t + lag** ✓。
        #     （第一版忘了这一步，直接在信号当天卖 ⇒ **前视** ✗，被单测当场抓住 ✓。）
        _er = i - lag                                  # 今天要执行的"信号行" ✓
        # ★ v2.6.0：信号行有两个来源 —— ① 预先算好的矩阵（普通离场因子 ✓）；
        #   ② **含入场价的规则**（逐日现算 ✓，见文件末尾"给规则算今天的信号行" ✓）。
        _sig = None
        if _er >= 0:
            if exit_mat is not None:
                _sig = exit_mat[_er]
            elif exit_rule is not None and _er < len(_rule_rows):
                _sig = _rule_rows[_er]
        if _sig is not None:
            # ⚠ "空转计数"必须按**今天开盘时的持仓**判断 ✓ —— 按卖出**之后**算会把"今天刚被
            #   离场卖掉的那只"也算成空转 ✗（2026-10-15 单测抓到：4 天卖出、6 天再来信号，
            #   结果 ignored 记成 2 ✗ 应为 1 ✓）。
            _held_before = set(sh)
            for c in list(sh):
                if not _sig[c]:
                    continue
                exit_signals += 1
                if tgt_set is not None and c in tgt_set:
                    exit_overridden += 1
                    pending_exit.discard(c)
                    continue
                if _sell(i, c, "exit_signal"):
                    exit_sells += 1
                else:
                    pending_exit.add(c)              # 卖不掉 ⇒ 顺延 ✓
            for c in list(pending_exit):
                if c not in sh:
                    pending_exit.discard(c)
                    continue
                if tgt_set is not None and c in tgt_set:
                    pending_exit.discard(c)          # 名单又把它收回去 ⇒ 不再卖 ✓
                    exit_overridden += 1
                    continue
                if _sell(i, c, "exit_signal"):
                    pending_exit.discard(c)
                    exit_sells += 1
                else:
                    exit_deferrals += 1
            if len(_held_before) < len(codes):       # 持币期间触发 ⇒ 空转（计数 ✓）
                _hm = np.zeros(len(codes), dtype=bool)
                for _c in _held_before:
                    _hm[_c] = True
                exit_ignored += int(np.count_nonzero(_sig & ~_hm))
        # ====================================================================================
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
                need_buy: List[Tuple[int, float]] = []
                for c in tgt_now:
                    px = float(PX[i, c])
                    if px != px:
                        continue
                    _lt = int(lot_arr[c])                  # v2.1.17：按标的自己的手数取整 ✓
                    # ★ v2.8.4（审计 #8 ✓）：**目标手数也按"真实股数的一手"算** ✓
                    #   （`cash=inf` ⇒ 这里只管"一手"、不掺现金约束 ✓；真正的现金约束在 `_buy` ✓）
                    wq = _fit_real_lots(RAW[i, c], px, _lt, half, float("inf"),
                                        budget=want_val)[0]
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
                        _sh_d, _px_d = _disp_row(i, c, dq, px2)   # ★ v2.8.4：明细按真实口径 ✓
                        trades.append((i, c, -1, _sh_d, _px_d, "roll_trim", MODE))
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
        # ★★ v2.6.0：**给"含入场价"的离场规则算今天的信号行** ✓（在当天所有成交**之后**算 ✓
        #   ⇒ 用的是"今天收盘时的持仓 + 入场价" ✓，`lag` 天后执行 ⇒ 与进场同一套滞后、不前视 ✓）。
        if exit_rule is not None:
            try:
                _rule_rows.append(exit_rule.eval_row(i, avg_px, first_px))
            except Exception as _e:                              # noqa: BLE001
                _exit_off = _exit_off or "入场价离场规则求值失败：%r" % (_e,)
                exit_rule = None                                 # 关掉离场，绝不拖垮回测 ✓
                _rule_rows.append(None)
        if cancel_cb is not None and (i % 20 == 0):
            cancel_cb()

    nav = pd.Series(nav_vals, index=cal[:n_row], name=MODE)
    net = nav.pct_change().fillna(0.0).to_numpy()
    out = BtResult(
        nav=pd.DataFrame({MODE: nav}),
        diag={"fill": fill, "cost": cost, "capital": capital, "hold_days": None,
              "strict_limit": bool(strict_limit), "lot": lot, "n_codes": len(codes),
              # ★ T1：**生效**口径 + 请求口径都落盘 ✓（`open_only` 被守卫退回时这里看得见 ✓）
              "limit_judge": _judge, "limit_judge_requested": limit_judge,
              "missing_codes": prep["missing"][:20], "limits_from": "period_roll",
              "rolls": int(len(targets)),
              # ★ v2.4.3 / v2.6.0：离场通道是否生效 / 未生效的原因 ✓
              #   （形态不符或规则求值失败 ⇒ **关掉离场**而不崩 ✓）
              "exit_enabled": bool(exit_mat is not None or _rule_provided),
              "exit_rule": bool(_rule_provided), "exit_note": _exit_off},
    )
    out.trades = _trades_frame(trades[:20000], _date_arr, codes, half)
    out.rejects = pd.DataFrame(
        [{"date": _date_arr[_i], "code": codes[_c], "reason": _rs,
          "text": REJECT_TEXT.get(_rs, _rs), "signal_date": None, "mode": MODE}
         for (_i, _c, _rs, _dp) in rejects][:30000])
    out.stats[MODE] = {
        "alloc": MODE, "label": _LABEL, "rebal_band": 0.0,
        "min_cash": round(float(min_cash), 2),
        # ★ v2.8.3（审计 #9 ✓）：现金绝不允许为负 ✓（可断言字段，见 engine.py 同一字段 ✓）
        "min_cash_negative": bool(min_cash < -1e-6),
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
        # ★ v2.4.3：离场通道五个计数（口径见函数开头"期中离场通道"注释 ✓）——
        #   `exit_signals` 持仓中触发次数 ｜ `exit_sells` 实际卖出笔数 ｜
        #   `exit_deferrals` 卖不掉被顺延的**天数** ｜ `exit_ignored` 持币期间空转次数 ｜
        #   `exit_overridden` 调仓日被名单覆盖（"同日进出不交易"✓）的次数
        "exit_enabled": bool(exit_mat is not None or _rule_provided),
        # ★ v2.6.0：本条回测用的是"含入场价的规则"（逐日求值 ✓）还是"预计算矩阵" ✓
        "exit_rule": bool(_rule_provided),
        "exit_signals": int(exit_signals),
        "exit_sells": int(exit_sells),
        "exit_deferrals": int(exit_deferrals),
        "exit_ignored": int(exit_ignored),
        "exit_overridden": int(exit_overridden),
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
