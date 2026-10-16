# -*- coding: utf-8 -*-
"""**审计 P1 修复**的守门单测（v2.8.3，2026-10-16 ✓）：

· **① 首日空仓**（用户："你都没有训练数据哪里来的调仓信号呢对吧"✓）——
  预测窗口不再前移 ⇒ 回测首日没有 T−1 预测 ⇒ 那天不调仓 ✓；
· **⑨ 现金绝不允许为负**（用户："肯定不能出现负数"✓）——
  三处买入统一走 `_fit_lots`（**按真实费用**、含最低 5 元 ✓）⇒ `cash ≥ 0` ✓。

⚠ 这批**不需要数据** ⇒ 不打 `datareq` ✓（2026-10-16 的教训：要数据的测试必须打标记，
  否则 CI 红 ✗；反之不打的**必须**真能在无数据环境跑 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals import engine as eng
from app.signals.period_roll import run_period_roll

CAL = pd.bdate_range("2024-01-01", periods=6)
CODES = ["S00", "S01"]


# ---------------------------------------------------------------------------
# ① 首日空仓
# ---------------------------------------------------------------------------

def test_test_segment_starts_at_test_start():
    """★ `test` 段必须**正好从 test_start 起** ✓（不再有 predict_start / 预热 ✓）。"""
    from app.engine.qlib_engine import _build_dataset
    from app.models.backtest import BacktestRequest

    req = BacktestRequest()
    cfg = _build_dataset(req, ["SH600000"], ("2023-01-01", "2023-12-31"),
                         ("2024-01-01", "2024-06-30"))
    segs = cfg["kwargs"]["segments"]
    assert segs["test"][0] == "2024-01-01", "首日空仓：测试段不得前移 ✓ %s" % (segs["test"],)
    assert segs["test"][1] == "2024-06-30"
    # 训练/验证段仍严格早于测试起点 ✓（purge 那条不变量，v2.8.2 ✓）
    assert segs["train"][1] < segs["test"][0]
    assert segs["valid"][1] < segs["test"][0]


def test_warm_start_helper_is_gone():
    """"首段预热"的计算函数应当**已被删除** ✓（否则有人会把它接回去 ✗）。"""
    import app.engine.qlib_engine as qe

    assert not hasattr(qe, "_first_segment_warm_start"), \
        "首日空仓口径下不该再留预热入口 ✓"


def test_train_semantics_stamp_covers_first_day_flat():
    """口径又变（首日空仓）⇒ **戳再升** ✓ —— 否则 2.8.2 的旧模型产物会被复用 ✗。"""
    from app.engine.qlib_engine import TRAIN_SEMANTICS

    assert TRAIN_SEMANTICS == "2.8.3", TRAIN_SEMANTICS


# ---------------------------------------------------------------------------
# ⑨ 禁负现金
# ---------------------------------------------------------------------------

def test_fit_lots_never_overdraws_with_min_fee():
    """★ 核心：`q*px + 真实费用 ≤ cash` ✓ —— 604 元买 6 元一手（100 股）**买不起** ✓。

    ⚠ 这正是审计 #9 的场景：老代码按"比例费"估 ⇒ 算出 q=100 ⇒ 花 600+5=605 > 604 ⇒
      **现金 −1** ✗（用户："肯定不能出现负数"✓）。
    """
    assert eng._fit_lots(604.0, 6.0, 100, 0.002, 604.0) == 0, "买不起就该 0 ✓"
    q = eng._fit_lots(605.0, 6.0, 100, 0.002, 605.0)
    assert q == 100 and q * 6.0 + eng._fee_of(q * 6.0, 0.002) <= 605.0 + 1e-9
    # 钱很够 ⇒ 与"朴素取整"一致 ✓（别把正常路径改坏 ✗）
    assert eng._fit_lots(10000.0, 6.0, 100, 0.002, 10000.0) == 1600
    # 无成本档（half=0 ⇒ 费用恒 0 ✓）⇒ 不做任何缩量 ✓
    assert eng._fit_lots(600.0, 6.0, 100, 0.0, 600.0) == 100


def test_fit_lots_normal_path_unchanged():
    """正常路径（钱够）**一位数字都不变** ✓ —— 只缩"原本会透支"的那些 ✓。"""
    for px, lot, cash in ((10.0, 100, 5000.0), (3.3, 200, 20000.0), (25.0, 100, 1000.0)):
        expect = int(cash / (1.0 + 0.002) / px / lot) * lot
        assert eng._fit_lots(cash / (1.0 + 0.002), px, lot, 0.002, cash) == expect


def _panel() -> dict:
    close = pd.DataFrame(6.0, index=CAL, columns=CODES)
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": close, "OPEN": close.copy(),
            "CLOSE_RAW": close.copy(), "OPEN_RAW": close.copy(),
            "LIMIT_UP": close * 1.1, "LIMIT_DOWN": close * 0.9, "_limits_inferred": True}


def test_engine_cash_never_negative_end_to_end():
    """★ 端到端：**604 元**本金（买不起一手 605 元）⇒ 引擎必须**拒单**且现金非负 ✓。

    ⚠ 这是审计 #9 的最小复现：老的 `cash_even` 会买下 100 股 ⇒ 现金 **−1** ✗。
    """
    sig = pd.DataFrame({"date": [CAL[0]], "code": ["S00"], "side": [1]})
    for modes in (("cash_even",), ("event_even",)):
        bt = eng.run_backtest(sig, _panel(), hold_days=3, fill="t1_open", cost=0.004,
                              capital=604.0, strict_limit=True, alloc_modes=list(modes))
        st = (bt.stats or {}).get(modes[0]) or {}
        assert st.get("min_cash_negative") is False, (modes, st)
        assert (st.get("min_cash") or 0) >= -1e-6, (modes, st)
        assert (st.get("buys") or 0) == 0, "买不起一手 ⇒ 不该成交 ✓：%s" % (st,)


def test_period_roll_cash_never_negative_end_to_end():
    """同一条不变量也要在「每期全仓滚动」上成立 ✓（它原来也有小额负值 ✗）。"""
    sig = pd.DataFrame({"date": [CAL[0]], "code": ["S00"], "side": [1]})
    out = run_period_roll(sig, _panel(), fill="t1_open", cost=0.004, capital=604.0,
                          strict_limit=True)
    st = out.stats["period_roll"]
    assert st["min_cash_negative"] is False, st
    assert st["min_cash"] >= -1e-6, st
    # ⚠ 没成交时 `trades` 是**空 DataFrame**（连列都没有 ✗）⇒ 别直接取 ["side"]（会 KeyError ✓）
    buys = 0 if out.trades.empty else int((out.trades["side"] == "买").sum())
    assert buys == 0, "买不起一手 ⇒ 不该有买单 ✓\n%s" % out.trades


def test_bigger_capital_still_buys():
    """对照（别把功能改死 ✗）：本金够 ⇒ 正常买入 ✓、现金仍非负 ✓。"""
    sig = pd.DataFrame({"date": [CAL[0]], "code": ["S00"], "side": [1]})
    out = run_period_roll(sig, _panel(), fill="t1_open", cost=0.004, capital=10000.0,
                          strict_limit=True)
    st = out.stats["period_roll"]
    assert (out.trades["side"] == "买").any(), out.trades
    assert st["min_cash_negative"] is False and st["min_cash"] >= -1e-6, st


@pytest.mark.parametrize("bad", [float("nan"), 0.0, -1.0])
def test_fit_lots_handles_bad_price(bad):
    """价格 NaN/非正 ⇒ 直接 0 ✓（不抛 ✗、不买 ✗）。"""
    assert eng._fit_lots(1000.0, bad, 100, 0.002, 1000.0) == 0
    assert isinstance(eng._fit_lots(1000.0, np.nan, 100, 0.002, 1000.0), int)


# ---------------------------------------------------------------------------
# ⑧ 整手取整按**真实股数**（v2.8.4 ✓ —— 用户 2026-10-16 举的就是下面第一个例子 ✓）
# ---------------------------------------------------------------------------

def test_real_lots_user_example_cannot_afford_one_lot():
    """★ 用户原例：现金 685、真实价 10、复权价 6 ⇒ **一手真实成本 1000 元 ⇒ 买不起** ✓。

    ⚠ 老代码会买"100 复权单位"= 600 元 = **60 真实股**（0.6 手 ✗）—— 这正是审计 #8 ✓。
    """
    q, n = eng._fit_real_lots(10.0, 6.0, 100, 0.002, 685.0)
    assert (q, n) == (0.0, 0), "买不起一手 ⇒ 不买 ✓（老代码会买 60 真实股 ✗）"


def test_real_lots_post_adjusted_buys_whole_real_lot():
    """★ 反方向（**默认后复权** f≥1 ✓）：现金 1010、真实 10、复权 20 ⇒ **买 100 真实股** ✓。

    ⚠ 老代码要 20×100 = 2000 元 ⇒ 买 **0**（少买 ✗）。
    """
    q, n = eng._fit_real_lots(10.0, 20.0, 100, 0.002, 1010.0)
    assert n == 100
    assert abs(q * 20.0 - 1000.0) < 1e-9, "折回复权单位后金额必须一致 ✓ %.6f" % (q * 20.0,)
    assert n * 10.0 + max(n * 10.0 * 0.002, 5.0) <= 1010.0, "含最低佣金也必须买得起 ✓"


def test_real_lots_budget_is_respected():
    """预算（等权目标那种 ✓）：给他 2500 元额度 ⇒ 最多 200 真实股 ⇒ `q_adj` 金额 = 2000 ✓。"""
    q, n = eng._fit_real_lots(10.0, 20.0, 100, 0.002, 1e9, budget=2500.0)
    assert n == 200 and abs(q * 20.0 - 2000.0) < 1e-9


def test_real_lots_degenerates_to_old_behavior_when_no_adjustment():
    """★ `真实价 == 复权价`（**不复权**模式 ✓）⇒ **逐位退化成老行为** ✓（那套数字完全不动 ✓）。"""
    for cash, px, lot in ((10000.0, 10.0, 100), (5000.0, 3.3, 200), (1010.0, 20.0, 100)):
        q, n = eng._fit_real_lots(px, px, lot, 0.002, cash)
        expect = eng._fit_lots(cash / (1.0 + 0.002), px, lot, 0.002, cash)
        assert q == expect and n == expect, (cash, px, q, expect)


def _panel_adj(adj_px: float, raw_px: float) -> dict:
    """复权价（`CLOSE` ✓）与真实价（`CLOSE_RAW` ✓）**不一样**的面板 ⇒ 专门测 ⑧ ✓。"""
    close = pd.DataFrame(adj_px, index=CAL, columns=CODES)
    raw = pd.DataFrame(raw_px, index=CAL, columns=CODES)
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": close, "OPEN": close.copy(),
            "CLOSE_RAW": raw, "OPEN_RAW": raw.copy(),
            "LIMIT_UP": raw * 1.1, "LIMIT_DOWN": raw * 0.9, "_limits_inferred": True}


def test_engine_buys_by_real_lots_end_to_end():
    """★ 端到端：后复权（复权 20 / 真实 10）⇒ 成交额应是**真实整手的钱**（1000 ✓）。"""
    sig = pd.DataFrame({"date": [CAL[0]], "code": ["S00"], "side": [1]})
    out = run_period_roll(sig, _panel_adj(20.0, 10.0), fill="t1_open", cost=0.004,
                          capital=1010.0, strict_limit=True)
    tr = out.trades
    buys = tr[tr["reason"] == "roll_in"] if not tr.empty else tr
    assert len(buys) == 1, out.trades
    amt = float(buys.iloc[0]["amount"])
    assert abs(amt - 1000.0) < 1e-6, "成交额 = 100 真实股 × 10 元 = 1000 ✓ 实际 %s" % amt
    assert abs((amt / 10.0) % 100) < 1e-9, "真实股数必须是整手 ✓"
    assert out.stats["period_roll"]["min_cash_negative"] is False, out.stats["period_roll"]


def test_engine_refuses_when_real_lot_unaffordable():
    """★ 端到端（用户原例）：现金 685、真实 10、复权 6 ⇒ **不成交** ✓（老代码会买 60 真实股 ✗）。"""
    sig = pd.DataFrame({"date": [CAL[0]], "code": ["S00"], "side": [1]})
    out = run_period_roll(sig, _panel_adj(6.0, 10.0), fill="t1_open", cost=0.004,
                          capital=685.0, strict_limit=True)
    assert out.trades.empty or not (out.trades["side"] == "买").any(), out.trades
    st = out.stats["period_roll"]
    assert st["min_cash_negative"] is False and st["min_cash"] >= -1e-6, st
