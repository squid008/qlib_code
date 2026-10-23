# -*- coding: utf-8 -*-
"""**回测结果不变量**（★ v2.11.66）—— 覆盖"净值算错但程序不报错"的那一类。

## 为什么必须用真数据 + 为什么不能用"训练模型"那条路
· **真数据**：合成数据是随机游走 ✗ ⇒ 涨跌停/停牌/成交额约束全都不触发 ✓
  ⇒ 回测最容易错的那些分支（买不进、卖不出、逐日顺延）**一条都走不到** ✗。
· **不训练模型**：`signals/engine.run_backtest(signals, panel, …)` 直接吃「信号表 + 价格面板」✓
  ⇒ **几秒**就能跑完 ✓（不用 LightGBM、不用 qlib.init 之后的重活 ✓）。

## 钉四条（每条对应一类"静默错"）
1. **净值起点 = 1** ✓ —— 起点不是 1 ⇒ 后面所有收益率都错 ✗；
2. **净值无 NaN 且 ≥ 0** ✓ —— NaN 会顺着 `pct_change`/回撤一路污染 ✗；
3. **成交股数是最小交易单位(lot)的整数倍、价格 > 0** ✓；
4. ⭐ **不许前视**：每笔成交日必须**晚于**该票最早的信号日 ✓
   （T+1 开盘成交 ✓；若相等说明"当日信号当日成交"✗ —— 那是回测里最经典的自欺 ✓）。
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from app.factors import panel_expr as PE
from app.signals.engine import run_backtest

INSTS = ["SH600000", "SH600519", "SZ000001", "SH601318", "SZ000002"]
START, END = "2025-01-01", "2025-06-30"
FIELDS = [("$open", "OPEN"), ("$high", "HIGH"), ("$low", "LOW"), ("$close", "CLOSE"),
          ("$close/$factor", "CLOSE_RAW"), ("$limit_up", "LIMIT_UP"),
          ("$change", "CHANGE"), ("$volume", "VOLUME")]


def _real_data() -> bool:
    d = PE._feature_dir()
    try:
        return os.path.isdir(d) and len(os.listdir(d)) >= 500
    except OSError:
        return False


pytestmark = [
    pytest.mark.datareq,
    pytest.mark.skipif(not _real_data(),
                       reason="需要真实行情（合成数据没有真涨跌停/停牌 ✗）"),
]


@pytest.fixture(scope="module")
def panel() -> dict:
    """真面板 → 宽表 dict + `CALENDAR`（`run_backtest` 要的形状 ✓）。"""
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()
    df = PE.panel_features(INSTS, FIELDS, START, END)
    if not len(df):
        pytest.skip("面板 0 行（P2-#23 形态）⇒ 先修数据 ✓")
    out = {c: df[c].unstack("instrument") for c in df.columns}
    # ⚠⚠ `CALENDAR` 必须是**带 `.index` 的对象** ✗→✓ —— `_prepare` 里写的是
    #   `pd.DatetimeIndex(panel["CALENDAR"].index)` ✓；我第一版传了 `list` ✗
    #   ⇒ `list.index` 是**内置方法** ⇒ `TypeError: 'builtin_function_or_method' object is not iterable` ✓
    #   （5 条 fixture 全 ERROR，看着像"回测挂了"其实是**我的入参形状错** ✗）。
    out["CALENDAR"] = pd.DataFrame(index=pd.DatetimeIndex(out["CLOSE"].index))
    return out


@pytest.fixture(scope="module")
def result(panel):
    """三条买入信号 × 5 只票 ⇒ 跑一次回测（秒级 ✓）。"""
    # ⚠ 取日期要走 `CLOSE.index` ✓ —— `panel["CALENDAR"]` 现在是**给引擎用的 DataFrame**
    #   （它要 `.index` ✓），直接 `CALENDAR[i]` 会变成**列查找** ⇒ `KeyError: 0` ✗（我踩过 ✓）。
    cal = pd.DatetimeIndex(panel["CLOSE"].index)
    codes = list(panel["CLOSE"].columns)
    rows = [{"date": cal[i], "code": c, "side": 1}
            for c in codes for i in (0, len(cal) // 3, 2 * len(cal) // 3)]
    sig = pd.DataFrame(rows)
    return sig, run_backtest(sig, panel, hold_days=10, cost=0.001, capital=1e8)


class TestNavInvariants:
    def test_nav_starts_at_one(self, result):
        """⭐ 每个资金方案的净值**起点必须 = 1** ✓（否则整条曲线的收益都错 ✗）。"""
        _sig, res = result
        assert not res.nav.empty, "没算出净值 ✗（diag=%s）" % res.diag
        for col in res.nav.columns:
            first = float(res.nav[col].iloc[0])
            assert first == pytest.approx(1.0, abs=1e-6), (
                "方案 %s 的净值起点是 %.6f（应为 1.0 ✓）" % (col, first))

    def test_nav_has_no_nan_and_not_negative(self, result):
        """⭐ 净值不许 NaN、不许为负 ✓（NaN 会一路污染回撤/夏普 ✗）。"""
        _sig, res = result
        assert not res.nav.isna().any().any(), "净值里有 NaN ✗：%s" % res.nav.isna().sum().to_dict()
        assert (res.nav >= 0).all().all(), "净值出现负数 ✗"

    def test_nav_index_lies_on_calendar(self, panel, result):
        _sig, res = result
        # ⚠ 必须 `.normalize()` 再比 ✗→✓：净值索引可能带**时间分量**（00:00 / 15:00 ✓），
        #   直接 `difference` 会把"同一天"判成不同 ✓（我第一版就这么红的 ✗）。
        cal = pd.DatetimeIndex(panel["CLOSE"].index).normalize()
        idx = pd.DatetimeIndex(res.nav.index).normalize()
        assert len(idx) > 0
        assert len(idx.difference(cal)) == 0, "净值日期不在交易日历里 ✗（差集前 3 个：%s）" % (
            list(idx.difference(cal)[:3]),)


class TestTradeInvariants:
    def test_qty_is_lot_multiple_and_price_positive(self, result):
        _sig, res = result
        tr = res.trades
        if tr is None or not len(tr):
            pytest.skip("本次没成交（信号/约束太严）⇒ 此条不适用 ✓")
        # ⚠ 列名是 **`shares`**（不是 `qty` ✗ —— `_TRADE_KEYS` 定的 ✓，我猜错过一次 ✓）
        assert "shares" in tr.columns, "成交明细没有 shares 列 ✗（实际列：%s）" % list(tr.columns)
        assert (tr["shares"].astype(float) > 0).all(), "成交股数必须为正 ✗"
        assert (tr["shares"].astype(float) % 100 == 0).all(), (
            "成交股数不是 100 的整数倍 ✗（A 股最小单位 ✓）: %s"
            % tr.loc[tr["shares"].astype(float) % 100 != 0, "shares"].head(3).tolist())
        assert (tr["price"].astype(float) > 0).all(), "成交价必须为正 ✗"

    def test_no_lookahead(self, result):
        """⭐⭐ **最要紧的一条**：成交日必须**晚于**该票最早的信号日 ✓。

        T+1 开盘成交 ✓；若成交日 == 信号日 ⇒ **当日信号当日成交** ⇒ 前视偏差 ✗
        （这条错了，回测收益会系统性虚高 ✓，而且**完全不报错** ✗）。
        """
        sig, res = result
        tr = res.trades
        if tr is None or not len(tr):
            pytest.skip("本次没成交 ⇒ 此条不适用 ✓")
        first_sig = (pd.to_datetime(sig["date"]).groupby(sig["code"]).min())
        bad = []
        for code, g in tr.groupby("code"):
            if code not in first_sig.index:
                continue
            first_trade = pd.to_datetime(g["date"]).min()
            if first_trade <= first_sig[code]:
                bad.append((code, str(first_sig[code].date()), str(first_trade.date())))
        assert not bad, "发现前视 ✗（信号日 ≥ 成交日）：%s" % bad[:3]

    def test_no_error_in_diag(self, result):
        _sig, res = result
        assert not res.diag.get("error"), "回测报错了 ✗：%s" % res.diag.get("error")
