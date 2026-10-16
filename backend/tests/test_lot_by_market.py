# -*- coding: utf-8 -*-
"""整手股数守门单测（v2.1.17~19）：**科创板 688/689 ⇒ 200 股**，其余 100 股 ✓。

为什么要有它（用户 2026-10-11 以为引擎本来就有 ✗）：引擎原来只有 `LOT_DEFAULT = 100` ✗
（`codes.py` 里的 `688` 只是**号段识别** ✓）⇒ 688 的票一直按 100 股在买 ✗。
现在 `_lot_of` 一处定义 ✓，`cash_even` / `event_even` / `period_roll` 都已切过去 ✓ ——
离线守门，防止将来改回统一手数 ✗。
"""
import numpy as np
import pandas as pd

from app.signals.engine import _lot_of, run_backtest
from app.signals.period_roll import run_period_roll

CODES = ["SH688001", "SH600519", "SZ300750"]      # 科创板 / 主板 / 创业板
CAL = pd.bdate_range("2024-01-01", periods=40)


def _panel() -> dict:
    rng = np.random.default_rng(20261011)
    ret = rng.normal(0.0003, 0.015, size=(len(CAL), len(CODES)))
    px = pd.DataFrame(10.0 * np.cumprod(1.0 + ret, axis=0), index=CAL, columns=CODES)
    halt = rng.random(px.shape) < 0.01
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": px.mask(halt),
            "OPEN": px.mask(halt), "CLOSE_RAW": px.mask(halt), "OPEN_RAW": px.mask(halt),
            "LIMIT_UP": px * 1.1, "LIMIT_DOWN": px * 0.9, "_limits_inferred": True}


def _signals() -> pd.DataFrame:
    days, codes = [], []
    for d in (0, 5, 10, 15, 20, 25):                # 6 期调仓 ✓
        for c in CODES:
            days.append(CAL[d])
            codes.append(c)
    return pd.DataFrame({"date": pd.to_datetime(days), "code": codes, "side": 1})


def _check(trades: pd.DataFrame, tag: str) -> None:
    buys = trades[trades["side"].astype(str) == "买"]
    assert len(buys), "%s：没有买入 ⇒ 测试无意义 ✗" % tag
    for code, mult in (("SH688001", 200), ("SH600519", 100), ("SZ300750", 100)):
        rows = buys[buys["code"] == code]
        assert len(rows), "%s：%s 没有买入 ✗" % (tag, code)
        bad = rows[rows["shares"].astype(int) % mult != 0]
        assert not len(bad), "%s：%s 的股数不是 %d 的倍数 ✗\n%s" % (tag, code, mult, bad)


def test_lot_of_kcb_is_200_and_others_100():
    assert _lot_of("SH688001") == 200 and _lot_of("688001.SH") == 200
    assert _lot_of("SH689009") == 200                 # 689 也属科创板 ✓
    assert _lot_of("SH600519") == 100 and _lot_of("SZ300750") == 100
    assert _lot_of("BJ430047") == 100 and _lot_of("SH600519", 50) == 50   # 默认值可覆盖 ✓


def test_cash_even_and_event_even_use_per_code_lot():
    panel, sig = _panel(), _signals()
    r = run_backtest(sig, panel, hold_days=5, fill="t1_open", cost=0.004, capital=1e8,
                     strict_limit=True, alloc_modes=("cash_even", "event_even"))
    _check(r.trades, "cash_even+event_even")


def test_period_roll_uses_per_code_lot():
    out = run_period_roll(_signals(), _panel(), fill="t1_open", cost=0.004,
                          capital=1e8, strict_limit=True)
    _check(out.trades, "period_roll")


# ---------------------------------------------------------------------------
# ★★ T7-b（v2.8.8，用户 2026-10-16："7 做" ✓）：**`batch_even` 也按"真实股数的一手"** ✗→✓
#
#   为什么必须造"复权价 ≠ 真实价"的盘面才能测出差别 ✗：
#     · v2.8.4 之前/漏改的口径是 `int(金额 / **复权价** / lot) * lot` ⇒ 取的是"**复权单位**整手" ✗
#       ⇒ 真实股数 = `q_adj × f` 常常**不是整手倍数** ✗（f = 复权价/真实价 ✓，非整数时必然 ✗）；
#     · 当 `f = 1`（`_panel()` 那样 CLOSE_RAW == CLOSE ✓）两种口径**完全一样** ✓
#       ⇒ 用原盘面写测试**永远测不出** ✗（这是我先踩过的坑 ✓）。
# ---------------------------------------------------------------------------

def _panel_scaled(f: float) -> dict:
    """复权价 = 真实价 × `f`（`CLOSE_RAW` 保持真实价 ✓ ⇒ `_prepare` 会给 `RAW` ✓）。

    `f < 1` ⇒ 复权价**更便宜** ⇒ 老口径会**多买**（真实股数不足整手 ✗）；`f ≥ 1` 则相反 ✓。
    """
    p = _panel()
    for k in ("CLOSE", "OPEN"):
        p[k] = p[k] * f
    return p


def test_batch_even_uses_real_lot_shares():
    """★ 真实价 ≠ 复权价时，`batch_even` 的成交股数仍必须**真实整手** ✓（老口径会给出 0.87 手的倍数 ✗）。"""
    r = run_backtest(_signals(), _panel_scaled(0.87), hold_days=5, fill="t1_open",
                     cost=0.004, capital=1e8, strict_limit=True,
                     alloc_modes=("batch_even",))
    _check(r.trades, "batch_even(真实≠复权)")


def test_batch_even_refuses_when_one_real_lot_unaffordable():
    """★ 真实一手买不起 ⇒ **不买** ✓（老口径按"复权单位一手"估 ⇒ 会比真金白银便宜得多 ⇒ 会错买 ✗）。

    盘面 `f=0.3`（复权价 3 元 = 真实价 10 元 × 0.3 ✓），三只票共 1200 元 ⇒ 每只预算 400 元 ✓：
      · 老口径：`int(400 / 3 / 100) * 100 = 100` 复权单位 ⇒ 真实只有 **30 股**（0.3 手 ✗）⇒ **会买** ✗；
      · 新口径：真实一手 = 100 × 10 = 1000 元 + 5 元佣金 > 400 ⇒ **拒单（no_cash）** ✓。
    """
    r = run_backtest(_signals(), _panel_scaled(0.3), hold_days=5, fill="t1_open",
                     cost=0.004, capital=1200, strict_limit=True,
                     alloc_modes=("batch_even",))
    trades = r.trades if r.trades is not None and len(r.trades) else None
    buys = trades[trades["side"].astype(str) == "买"] if trades is not None else None
    assert buys is None or not len(buys), (
        "真实一手买不起却成交了 ✗ ⇒ 说明又按「复权单位整手」在算（T7-b 回归）✗\n%s" % buys)
    # 反向护栏：拒单原因必须是"钱不够"（而不是根本没生成买单 ✗）
    rej = r.rejects if r.rejects is not None and len(r.rejects) else None
    if rej is not None:
        assert (rej["reason"] == "no_cash").any(), "应留下 no_cash 拒单痕迹 ✓"
