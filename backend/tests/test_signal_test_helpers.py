# -*- coding: utf-8 -*-
"""`routers/signal_test.py` 的**纯逻辑 helper**（★ v2.11.64）。

## 为什么值得测（这是后端覆盖率最低的模块之一，21.3%）
`/run` 那条主路径要真行情 + 分钟级等待 ✗，但**它的判据全在这几个 helper 里** ✓：
· `_apply_excludes`（139 行）—— "按文件口径剔除触发样本" ✓ 决定了**回灌结果对不对** ✗；
  历史真事故：v2.1.58 把 183 条剔成 **140 条**（误剔 23.5% ✗），根因是拿**后复权价**
  去比 `LIMIT_UP`（**量纲不同** ✗）⇒ 几乎天天"涨停" ✓。
· 还有一条**更隐蔽的**（v2.1.78）：`if hit.any(): 才 append warning` ✗ ⇒
  "执行了但 0 命中" 与 "压根没执行" **无法区分** ✗ ⇒ 现在**恒报条数** ✓（本文件钉住它 ✓）。

## 口径
- 只用**构造出来的小面板**（5 天 × 3 只）✓，不碰真数据、不 init qlib ✓；
- 断言"**该剔的剔了**、**该说的话说了**"✓ —— 后半句同样重要：
  缺 `LIMIT_UP` / `IS_ST` 时必须**明说未执行** ✗ 不许静默假装剔了 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.routers.signal_test import (
    _amount_match,
    _apply_excludes,
    _official_series,
    _records,
    options,
)

CODES = ("SZ000001", "SZ300001", "SH688001")   # 普通 / 创业板 / 科创板 ✓
DAYS = 5


def _cal() -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=DAYS, freq="D")


def _panel(*, with_raw: bool = True, extra: dict | None = None) -> dict:
    cal = _cal()
    close = pd.DataFrame(1.0, index=cal, columns=pd.Index(CODES))
    panel: dict = {"CLOSE": close}
    if with_raw:
        panel["CLOSE_RAW"] = close.copy()
    panel.update(extra or {})
    return panel


def _ev(pairs) -> pd.DataFrame:
    return pd.DataFrame([{"code": c, "date": d} for c, d in pairs])


class TestRecords:
    def test_none_and_empty(self):
        assert _records(None) == []
        assert _records(pd.DataFrame()) == []

    def test_cap(self):
        df = pd.DataFrame({"a": range(5)})
        assert len(_records(df, cap=2)) == 2
        assert _records(df) == df.to_dict("records")


class TestOfficialSeries:
    def test_missing_col_or_empty(self):
        pf = pd.DataFrame({"date": _cal(), "nav": [1.0] * DAYS})
        assert _official_series(pf, "nope", _cal()) is None
        assert _official_series(pd.DataFrame(), "nav", _cal()) is None

    def test_all_nan_returns_none(self):
        pf = pd.DataFrame({"date": _cal(), "nav": [np.nan] * DAYS})
        assert _official_series(pf, "nav", _cal()) is None

    def test_ffill_aligns_to_our_calendar(self):
        # 官方序列缺 2 天 ⇒ 必须 ffill 补齐（否则对齐后是 NaN ✗）
        pf = pd.DataFrame({"date": _cal()[:3], "nav": [1.0, 1.1, 1.2]})
        s = _official_series(pf, "nav", _cal())
        assert s is not None
        assert len(s) == DAYS
        assert s.iloc[-1] == pytest.approx(1.2)      # 后两天由 ffill 填 ✓
        assert s.notna().all()


class TestAmountMatch:
    def test_empty_inputs(self):
        assert _amount_match(None, None) == (0, 0)
        assert _amount_match(pd.DataFrame(), pd.DataFrame()) == (0, 0)

    def test_consistent_days(self):
        cal = _cal()
        tr = pd.DataFrame({
            "date": [cal[0], cal[0], cal[1]],
            "side": [1, -1, 1],
            "amount": [100.0, 40.0, 10.0],
        })
        pf = pd.DataFrame({
            "date": cal,
            "buy_amt": [100.0, 10.0, 0, 0, 0],
            "sell_amt": [40.0, 0, 0, 0, 0],
        })
        ok, bad = _amount_match(tr, pf)
        assert (ok, bad) == (DAYS, 0), "5 天全部一致（后 3 天两边都是 0 ✓）"

    def test_inconsistent_day_is_counted(self):
        cal = _cal()
        tr = pd.DataFrame({"date": [cal[0]], "side": [1], "amount": [100.0]})
        pf = pd.DataFrame({"date": cal, "buy_amt": [999.0, 0, 0, 0, 0],
                           "sell_amt": [0.0] * DAYS})
        ok, bad = _amount_match(tr, pf)
        assert ok == DAYS - 1 and bad == 1, "第一天金额对不上 ⇒ 应记 1 天不一致 ✓"


class TestApplyExcludes:
    def test_none_or_empty_returns_as_is(self):
        assert _apply_excludes(None, _panel(), {}, []) is None
        empty = pd.DataFrame({"code": [], "date": []})
        assert len(_apply_excludes(empty, _panel(), {}, [])) == 0

    def test_no_close_panel_returns_as_is(self):
        ev = _ev([("SZ000001", "2024-01-01")])
        out = _apply_excludes(ev, {}, {"exclude_stock_gem": True}, [])
        assert len(out) == 1, "面板没有 CLOSE ⇒ 什么都不剔除（也不许崩 ✓）"

    def test_gem_and_kcb_are_excluded(self):
        cal = _cal()
        ev = _ev([("SZ000001", cal[0]), ("SZ300001", cal[0]), ("SH688001", cal[0])])
        w = []
        out = _apply_excludes(ev, _panel(), {"exclude_stock_gem": True,
                                            "exclude_stock_kcb": True}, w)
        assert list(out["code"]) == ["SZ000001"]
        assert any("剔除触发样本" in x for x in w), "剔了就必须报数 ✓"

    def test_suspended_next_day_is_excluded(self):
        cal = _cal()
        raw = pd.DataFrame(1.0, index=cal, columns=pd.Index(CODES))
        raw.iloc[1, list(CODES).index("SZ000001")] = np.nan   # T+1 停牌 ⇒ 买不进 ✓
        out = _apply_excludes(_ev([("SZ000001", cal[0])]), _panel(extra={"CLOSE_RAW": raw}),
                              {"exclude_suspended": True}, [])
        assert len(out) == 0, "成交日停牌必须剔除 ✓（这条错了会多进买不进的样本 ✗）"

    def test_limit_up_without_real_price_warns_and_keeps_all(self):
        """⚠ 没有"真实价"列 ⇒ **宁可不剔**，但必须**明说**（v2.1.58 的教训 ✓）。"""
        cal = _cal()
        w: list[str] = []
        out = _apply_excludes(_ev([("SZ000001", cal[0])]), _panel(with_raw=False),
                              {"exclude_limit_up_signal": True}, w)
        assert len(out) == 1, "没有真实价 ⇒ 不剔（不赌量纲 ✓）"
        assert any("真实价" in x for x in w), "必须提示未执行 ✓（不静默 ✗）: %s" % w

    def test_missing_change_field_warns(self):
        cal = _cal()
        w: list[str] = []
        _apply_excludes(_ev([("SZ000001", cal[0])]), _panel(),
                        {"exclude_limit_up_signal": True}, w)
        assert any("涨跌幅" in x for x in w), w

    def test_st_t1_without_field_warns(self):
        cal = _cal()
        w: list[str] = []
        _apply_excludes(_ev([("SZ000001", cal[0])]), _panel(),
                        {"exclude_st_t1": True}, w)
        assert any("IS_ST" in x for x in w), w

    def test_st_t1_excludes_and_reports_count(self):
        cal = _cal()
        st = pd.DataFrame(0.0, index=cal, columns=pd.Index(CODES))
        st.iloc[1, list(CODES).index("SZ000001")] = 1.0        # T+1 当日 ST ✓
        w: list[str] = []
        out = _apply_excludes(_ev([("SZ000001", cal[0]), ("SZ000001", cal[1])]),
                              _panel(extra={"IS_ST": st}), {"exclude_st_t1": True}, w)
        assert len(out) == 1, "只有第一条的 T+1 是 ST ✓"
        assert any("剔除「成交日 ST」样本 1 条" in x for x in w), w

    def test_st_t1_zero_hits_still_reports(self):
        """⭐⭐ v2.1.78 的修复：**0 命中也要报** ✓ —— 否则分不清"执行了"和"没执行" ✗。"""
        cal = _cal()
        st = pd.DataFrame(0.0, index=cal, columns=pd.Index(CODES))
        w: list[str] = []
        out = _apply_excludes(_ev([("SZ000001", cal[0])]), _panel(extra={"IS_ST": st}),
                              {"exclude_st_t1": True}, w)
        assert len(out) == 1
        assert any("剔除「成交日 ST」样本 0 条" in x for x in w), (
            "0 命中必须也留一条 ✓（否则和\"压根没执行\"无法区分 ✗）: %s" % w
        )


class TestOptionsEndpoint:
    def test_static_options_shape(self):
        d = options()
        assert set(d) == {"benchmarks", "pools", "fills", "allocs", "defaults"}
        assert d["defaults"]["cost"] == pytest.approx(0.004)
        assert d["defaults"]["horizon"] == 60
        assert all("code" in x and "name" in x for x in d["benchmarks"])
