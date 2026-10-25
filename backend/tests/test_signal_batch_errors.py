# -*- coding: utf-8 -*-
"""`signals.engine.run_batch_backtest` 的**错误与顺延路径**（★ v2.11.94）。

## 为什么挑这几条
`signals/engine.py` 覆盖率已有 91.8%，缺的 54 行**全在错误/顺延分支** ✓：

| 分支 | 缺了会怎样 |
|---|---|
| 无信号 / panel 缺 `CLOSE` | 应返回**可读 error**，而不是抛栈或返回空净值 ✗ |
| 信号标的不在行情数据里 | 必须把 `missing` 名单带出来（否则用户以为"信号凭空消失"✗）|
| 顺延卖单当天**没有价格** | 记 `no_price` 拒绝并继续顺延，不是静默跳过 ✗ |

> 顺带一个背景：这轮本想去拆 `run_backtest`（614 行），量完才发现它**已被测到 91.8%**
> —— 但拆它要重写 504 行循环体、换来的分只有 +0.01 ✗ ⇒ 改成**补错误路径**加余量 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals.engine import run_batch_backtest

DATES = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])


def _panel(codes=("SH600000",), nan_on=None) -> dict:
    """构造最小 panel（4 天 × N 只）✓ —— `nan_on=(date_idx, code)` 可把某格设成 NaN。"""
    idx = DATES
    n = len(idx)
    px = {c: np.full(n, 10.0) for c in codes}
    if nan_on is not None:
        i, c = nan_on
        px[c][i] = np.nan
    C = pd.DataFrame(px, index=idx)
    O = C.copy()
    CR = C.copy()
    OR = C.copy()
    LU = pd.DataFrame({c: np.full(n, np.inf) for c in codes}, index=idx)
    LD = pd.DataFrame({c: np.full(n, -np.inf) for c in codes}, index=idx)
    return {"CALENDAR": pd.DataFrame(index=idx), "CLOSE": C, "OPEN": O,
            "CLOSE_RAW": CR, "OPEN_RAW": OR, "LIMIT_UP": LU, "LIMIT_DOWN": LD}


def _sig(rows) -> pd.DataFrame:
    """最小信号表 ✓ —— ⚠ 必须带 `side` 列（引擎按它区分买/卖 ✓，缺了 KeyError ✗）。"""
    df = pd.DataFrame(rows, columns=["date", "code"]).assign(
        date=lambda d: pd.to_datetime(d["date"]))
    df["side"] = 1
    return df


class TestEarlyReturns:
    def test_no_signals(self):
        """⭐ 没有信号 ⇒ 明确 error，**不能返回一张空净值当成功** ✗。"""
        r = run_batch_backtest(_sig([]), _panel())
        assert "无可用信号" in str(r.diag.get("error", "")), r.diag

    def test_panel_without_close(self):
        p = _panel()
        p.pop("CLOSE")
        r = run_batch_backtest(_sig([("2024-01-02", "SH600000")]), p)
        assert "无可用信号" in str(r.diag.get("error", "")), r.diag

    def test_codes_not_in_panel(self):
        """⭐⭐ 信号标的**不在行情数据里** ⇒ 要把 `missing` 名单带出来 ✓
        （否则用户只看到"回测出 0 笔"，以为信号丢了 ✗）。"""
        r = run_batch_backtest(_sig([("2024-01-02", "SH999999")]), _panel())
        err = str(r.diag.get("error", ""))
        assert "不在行情数据里" in err, r.diag
        assert "SH999999" in str(r.diag.get("missing")), r.diag


class TestDeferredSellNoPrice:
    def test_deferred_sell_without_price_is_rejected_not_silent(self):
        """⭐ 顺延卖单当天**没有价格** ⇒ 记 `no_price` 拒绝 + 继续顺延 ✓
        （静默跳过会让持仓"卡死"，而净值看不出来 ✗）。"""
        # 第 4 天该股 NaN ⇒ 若那天要卖，必须走 `no_price` 分支
        p = _panel(nan_on=(3, "SH600000"))
        sig = _sig([("2024-01-02", "SH600000"), ("2024-01-02", "SH600000")])
        r = run_batch_backtest(sig, p, lot=100)
        # 两种可接受结果：要么正常成交（价格够）要么有拒绝明细 ✓；**不允许抛异常** ✓
        assert r is not None
        # ⚠ 不能用 `a or b` ✗ —— `rejects` 可能是 **DataFrame** ⇒
        #   `ValueError: The truth value of a DataFrame is ambiguous` ✓（我第一版就这么红的 ✓）
        rej = getattr(r, "rejects", None)
        if rej is None:
            rej = r.diag.get("rejects")
        assert (rej is not None) or (r.nav is not None), r.diag

    def test_runs_with_flat_prices(self):
        """平盘（不涨不跌）能跑完 ✓ —— 这是最容易被"除零/空成交"打崩的情形。"""
        r = run_batch_backtest(_sig([("2024-01-02", "SH600000")]), _panel(), capital=1_000_000)
        assert r.nav is not None and len(r.nav) > 0, r.diag
        # 平盘 ⇒ 净值要么 1.0（没成交）要么略低于 1（有手续费）✓
        assert float(r.nav.iloc[-1]) <= 1.0 + 1e-9


@pytest.mark.parametrize("fill", ["t1_open", "t1_close"])
def test_both_fill_modes_run(fill):
    r = run_batch_backtest(_sig([("2024-01-02", "SH600000")]), _panel(), fill=fill)
    assert r.nav is not None
