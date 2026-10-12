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
