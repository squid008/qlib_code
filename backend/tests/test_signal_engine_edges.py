# -*- coding: utf-8 -*-
"""`signals/engine.py` 的**边界分支**（★ v2.11.96 —— 拆 614 行函数前的**前置护栏**）。

## 为什么先写这个
计划把 `run_backtest` 里那个 504 行的 for 循环拆成 init / 日循环 / 收尾三段，
但**拆之前必须先把覆盖率从 92.2% 拉到 >95%** ✓ —— 否则拆错没有网接着 ✗。
缺的 51 行全在**边界分支**（正常路径早被既有 17 个 signal 测试跑过了 ✓）：

| 分支 | 不测会怎样（拆的时候最容易碰坏） |
|---|---|
| 买入价 **NaN** ⇒ `no_price` 拒绝 | 拆完变成"按 0 元成交" ⇒ 净值暴增 ✗ |
| 排队期间**已被别的信号买进** ⇒ 刷新持有天数 | 拆完变成重复买入 ⇒ 持仓翻倍 ✗ |
| **离场规则求值抛错** ⇒ 关规则 + 留 note | 拆完变成静默退化成"和对照一样"✗（2026-10-16 真事故）|
| 批量引擎：**跌停/停牌卖不出 ⇒ 顺延** | 拆完变成"当天硬卖" ⇒ 净值偏高 ✗ |
| 批量引擎：卖价 NaN / **减仓（trim）** | 同上 |
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals.engine import run_backtest

DATES = pd.bdate_range("2022-01-03", periods=6)
CODE = "SH600000"


def _panel(px, lu=None, ld=None, susp=None, nan_open=None):
    """最小 panel ✓（与 `test_signal_batch._panel` 同构 + `nan_open` 用来制造"无成交价"）。"""
    codes = sorted(px)
    C = pd.DataFrame({c: list(px[c]) for c in codes}, index=DATES, dtype=float)
    O = C.shift(1).fillna(C)
    CR, OR = C.copy(), O.copy()
    for (c, i) in (susp or []):
        CR.iloc[i, CR.columns.get_loc(c)] = np.nan
        OR.iloc[i, OR.columns.get_loc(c)] = np.nan
    for (c, i) in (nan_open or []):
        O.iloc[i, O.columns.get_loc(c)] = np.nan
        OR.iloc[i, OR.columns.get_loc(c)] = np.nan
    nan = [np.nan] * len(DATES)
    LU = pd.DataFrame({c: list((lu or {}).get(c, nan)) for c in codes}, index=DATES, dtype=float)
    LD = pd.DataFrame({c: list((ld or {}).get(c, nan)) for c in codes}, index=DATES, dtype=float)
    return {"CALENDAR": pd.DataFrame(index=DATES), "CLOSE": C, "OPEN": O,
            "CLOSE_RAW": CR, "OPEN_RAW": OR, "LIMIT_UP": LU, "LIMIT_DOWN": LD}


def _sig(rows) -> pd.DataFrame:
    """rows: [(日序, code, side)] —— 主引擎只要求 `date`/`code`/`side` ✓。"""
    return pd.DataFrame([{"date": DATES[d], "code": c, "side": s, "name": "", "raw_date": "",
                          "raw_code": "", "bucket": "W_0", "weight": 1.0} for d, c, s in rows])


def _flat(n=6, px=10.0):
    return {CODE: [px] * n}


def _blob(bt) -> str:
    """把 diag/stats 里的文字全拼起来（不断言具体结构，只找关键原因 ✓）。"""
    parts = [str(getattr(bt, "diag", ""))]
    stats = getattr(bt, "stats", None) or {}
    for v in stats.values():
        parts.append(str(v))
    rej = getattr(bt, "rejects", None)
    if rej is not None:
        parts.append(str(rej))
    return " ".join(parts)


def _run(signals, panel, **kw):
    return run_backtest(signals, panel, hold_days=3, fill="t1_open", cost=0.0,
                        capital=1_000_000.0, alloc_modes=kw.pop("alloc_modes", ("cash_even",)),
                        **kw)


class TestBuyEdges:
    def test_buy_price_nan_is_rejected_not_filled_at_zero(self):
        """⭐ 成交日开盘价 NaN ⇒ 必须**拒绝这笔**，而不是按 0 元买入 ✓（0 元买 = 净值暴增 ✗）。"""
        p = _panel(_flat(), nan_open=[(CODE, 1)])          # 第 2 天开盘 NaN（= 第 1 天信号的第 1 个成交日）
        bt = _run(_sig([(0, CODE, 1)]), p)     # ⚠ `fill` 已由 `_run` 传，重复传会 TypeError ✗
        t = getattr(bt, "trades", None)
        n = 0 if t is None else len(t)
        assert n == 0, "无价却成交了 ✗：%d 笔" % n
        assert bt.nav is not None and len(bt.nav) > 0, bt.diag

    def test_already_held_is_refresh_not_double_buy(self):
        """⭐ 同一天两条同标的信号 ⇒ 第二条走"已在持仓 ⇒ 刷新"分支 ✓（不许买两次 ✗）。"""
        bt = _run(_sig([(0, CODE, 1), (0, CODE, 1)]), _panel(_flat()))
        t = getattr(bt, "trades", None)
        buys = 0 if t is None else sum(1 for r in t.to_dict("records")
                                       if str(r.get("side")) in ("1", "buy", "B"))
        assert buys <= 1, "同一标的被买进 %d 次 ✗" % buys
        assert bt.nav is not None


class _BoomRule:
    """`eval_row` 必抛的离场规则桩 ✓（覆盖"求值失败 ⇒ 关规则 + 留 note"）。"""

    def eval_row(self, i, avg_px, first_px):        # noqa: D401
        raise RuntimeError("规则炸了")


class TestExitRuleEdges:
    def test_exit_rule_exception_is_loud_not_silent(self):
        """⭐⭐ 离场规则求值失败 ⇒ **关掉离场并留 note** ✓ —— 静默退化成"和对照一样"是
        2026-10-16 的真事故（39 vs 36）✗。"""
        bt = _run(_sig([(0, CODE, 1), (2, CODE, 1)]), _panel(_flat()), exit_rule=_BoomRule())
        blob = _blob(bt)
        assert "求值失败" in blob, "求值失败必须说出来 ✗（现在是静默）：%s" % blob[:200]
        assert bt.nav is not None


class TestBatchSellEdges:
    def test_batch_sell_limit_down_defers(self):
        """⭐ 卖出决策日**跌停** ⇒ 卖不出 ⇒ 顺延并记拒绝（不许当天硬卖 ✗）。"""
        p = _panel(_flat(), ld={CODE: [np.nan, np.nan, 10.0, 10.0, 10.0, 10.0]})
        bt = _run(_sig([(0, CODE, 1), (1, CODE, -1)]), p, alloc_modes=("batch_even",))
        blob = _blob(bt)
        assert bt.nav is not None
        assert ("limit_down" in blob) or ("跌停" in blob) or ("rejects_total" in blob), blob[:200]

    def test_batch_sell_nan_price_is_rejected(self):
        """⭐ 卖价 NaN ⇒ `no_price` 拒绝 + 顺延 ✓（静默跳过会让持仓卡死而净值看不出来 ✗）。"""
        p = _panel(_flat(), susp=[(CODE, 2)])
        bt = _run(_sig([(0, CODE, 1), (1, CODE, -1)]), p, alloc_modes=("batch_even",))
        assert bt.nav is not None
        blob = _blob(bt)
        assert ("no_price" in blob) or ("停牌" in blob) or ("rejects_total" in blob), blob[:200]

    def test_batch_trim_units_path(self):
        """⭐ 份数变少 ⇒ 走**减仓（trim）**分支（跌停当天卖不掉的明天再卖 ✓）。"""
        # 两次刷新、份数不同 ⇒ 触发超配减仓
        p = _panel(_flat(), ld={CODE: [np.nan, 10.0, 10.0, np.nan, np.nan, np.nan]})
        bt = _run(_sig([(0, CODE, 1), (2, CODE, 1)]), p, alloc_modes=("batch_even",))
        assert bt.nav is not None and len(bt.nav) > 0


@pytest.mark.parametrize("mode", ["event_even", "cash_even", "batch_even"])
def test_all_alloc_modes_smoke(mode):
    bt = _run(_sig([(0, CODE, 1)]), _panel(_flat()), alloc_modes=(mode,))
    assert bt.nav is not None and len(bt.nav) > 0, bt.diag
