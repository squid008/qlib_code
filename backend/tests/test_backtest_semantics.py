# -*- coding: utf-8 -*-
"""`run_backtest` **语义基准**（v1.20.3，2026-09-18）。

为什么：下一步要把主循环（`engine.py:303-424`，逐日 Python 循环 × 两种资金方案 ×
遍历全部持仓 ✗）**向量化** ⇒ 这类改动**只许改性能、不许改语义** ✗，而语义错了通常
**不报错、只是数值悄悄变了** ✓ ⇒ 必须先把当前实现的输出**逐位固化**成回归 ✓。

做法：**完全离线**（自造面板 + 事件，固定 seed ✓ 不依赖 qlib/真实数据 ✓），
把 3 组参数下的**成交数 / 被拒数 / 净值末值 / 关键统计**写死为期望值 ✓
（数值来自 `ai_test/check_bt_ref.py` 生成的 `bt_ref.json`，且已验证"同实现跑两次逐位一致" ✓）。
⚠ 向量化后若本文件失败 ⇒ **回退向量化**，不要去改期望值 ✗。
"""
import numpy as np
import pandas as pd
import pytest

from app.signals.engine import run_backtest

N_DAY, N_STK = 300, 6
CODES = ["S%02d" % i for i in range(N_STK)]


def _panel():
    rng = np.random.default_rng(20260918)
    cal = pd.bdate_range("2024-01-01", periods=N_DAY)
    ret = rng.normal(0.0004, 0.02, size=(N_DAY, N_STK))
    px = 10.0 * np.cumprod(1.0 + ret, axis=0)
    close = pd.DataFrame(np.round(px, 2), index=cal, columns=CODES)
    open_ = pd.DataFrame(np.round(close.to_numpy() * (1 + rng.normal(0, 0.004, close.shape)), 2),
                         index=cal, columns=CODES)
    halt = rng.random(close.shape) < 0.02
    close, open_ = close.mask(halt), open_.mask(halt)
    return {"CALENDAR": pd.DataFrame(index=cal),
            "CLOSE": close, "OPEN": open_,
            "CLOSE_RAW": close.copy(), "OPEN_RAW": open_.copy(),
            "LIMIT_UP": close * 1.1, "LIMIT_DOWN": close * 0.9,
            "_limits_inferred": True}


def _events():
    rng = np.random.default_rng(7)
    n = 120
    d0 = pd.Timestamp("2024-02-01")
    dts = [d0 + pd.Timedelta(days=int(x)) for x in rng.integers(0, 400, n)]
    dts += [pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31")]   # 早于起点 / 晚于末尾
    codes = [CODES[int(x)] for x in rng.integers(0, N_STK, len(dts))]
    return pd.DataFrame({"date": pd.to_datetime(dts), "code": codes, "side": 1})


# 期望值 = 基准实现的逐位输出 ✓（生成脚本 `ai_test/check_bt_ref.py`）
# ★ v2.1.16 **有意的语义变更**（用户 2026-10-11："手续费最小 5 块加上不就行了" ✓）：
#   手续费改为 `max(成交额 × 单边费率, 最低 5 元)` ✓ ⇒ **有成本**的用例里 `cash_even` 末值变了 ✓
#   （`event_even` 不变 ⇒ 它的成交额都远超 5 元、最低佣金没触发 ✓；`cash_even` 的小额买入会触发 ✓）；
#   `k5_cost0`（**无成本**档）**逐位不变** ✓ —— 最低佣金不会出现在"无成本"里 ✓（见 `_fee_of` ✓）。
#   ⇒ 基准已用 `ai_test/check_bt_ref.py` 重新生成 ✓。
# ★★ v2.1.30 **又一次有意的语义变更**（用户 2026-10-11："资金负也查" ✓）：
#   三个买入点（`event_even` 买新信号 / 补低仓 / `rebalance_buy`）原来按
#   **估费** `成交额 × (1+半费)` 查现金 ✗，而真实费用是 `max(成交额×半费, 最低 5 元)` ✓
#   ⇒ 存在**现金透支**（实测用户数据 `event_even` 到 **−3216.71 元** ✗；`period_roll`、
#   `cash_even` 也有小额负值 ✓）。现改为**成交前按真实费用硬校验**（买得起的最大手数 ✓）
#   ⇒ 原本会透支的买单被缩量 ⇒ `trades`/净值末值**微变** ✓。
#   ⚠ 与 v2.1.16 那次不同：**这次"无成本"档也会变** ✗（因为透支与费用无关 ✓，
#     `cost=0` 时实测负得更多 −8078 ✓）—— 故 `k5_cost0` 的 `event_even` 变了 ✓，
#     而三个用例的 `cash_even` 末值**逐位不变** ✓（它的买入本来就都买得起 ✓）。
#   ⇒ 基准已用 `ai_test/regen_bt_ref_v2130.py` 重新生成 ✓。
EXPECT = {
    "k5_cost0": dict(hold_days=5, cost=0.0, n_trades=330, n_rejects=47,
                     nav={"event_even": 1.12178984, "cash_even": 0.81812352}),
    "k20_cost004": dict(hold_days=20, cost=0.004, n_trades=214, n_rejects=59,
                        nav={"event_even": 1.0895006776, "cash_even": 1.0087133918}),
    "k20_band5pct": dict(hold_days=20, cost=0.004, n_trades=176, n_rejects=59,
                         nav={"event_even": 1.0907490539, "cash_even": 1.0087133918}),
}


@pytest.mark.parametrize("name", list(EXPECT))
def test_backtest_matches_frozen_baseline(name):
    """向量化前/后必须**逐位一致**（成交 / 被拒 / 净值末值 ✓）。"""
    exp = EXPECT[name]
    kw = {k: v for k, v in exp.items() if k in ("hold_days", "cost")}
    if name == "k20_band5pct":
        kw["rebal_band"] = 0.05
    r = run_backtest(_events(), _panel(), fill="t1_open", cost=kw["cost"],
                     capital=1e8, strict_limit=True, hold_days=kw["hold_days"],
                     rebal_band=kw.get("rebal_band", 0.0),
                     alloc_modes=("event_even", "cash_even"))
    assert len(r.trades) == exp["n_trades"], "成交笔数变了 ⇒ 语义被改坏 ✗"
    assert len(r.rejects) == exp["n_rejects"], "被拒条数变了 ⇒ 语义被改坏 ✗"
    for m, want in exp["nav"].items():
        got = float(r.nav[m].iloc[-1])
        assert abs(got - want) < 1e-9, "%s 净值末值 %.10f ≠ %.10f ✗" % (m, got, want)


def test_backtest_is_deterministic():
    """同一输入跑两次必须逐位一致（DICT 遍历顺序等隐患的看门狗 ✓）。"""
    args = dict(fill="t1_open", cost=0.004, capital=1e8, strict_limit=True,
                hold_days=20, alloc_modes=("event_even", "cash_even"))
    a = run_backtest(_events(), _panel(), **args)
    b = run_backtest(_events(), _panel(), **args)
    assert a.trades.to_dict("records") == b.trades.to_dict("records")
    assert np.array_equal(a.nav.to_numpy(), b.nav.to_numpy())
