# -*- coding: utf-8 -*-
"""回归：同比类字段的**分母取绝对值** ✓（2026-10-14 用户定稿「选 B」✓）。

口径 ✓：`yoy = (本期 − 去年同期) ÷ |去年同期| × 100` ✓，仅 `去年同期 == 0`（或缺失）⇒ NaN ✓。
**Why**（旧口径"分母须为正"的两个毛病 ✗）：
  ① **扭亏股整段被丢** ✗（去年同期亏、今年赚 ⇒ NaN ⇒ "低估值 + 扭亏"这类因子永远选不出来 ✗）；
  ② 亏损扩大 / 收窄也全丢 ✗。
**供应商口径一致** ✓：实测 tushare `fina_indicator.netprofit_yoy`，`600029.SH` 2026 中报
（归母 −36.96 亿、去年同期 −15.33 亿）给 **−141.0959** ✓ = `(−36.96+15.33)/15.33` ✓。
两条链（米筐 / tushare）必须**同规则** ✓：米筐走 `dump_finance.tiered_report_values` 与
`dump_finance` 里的累计档 ✓；tushare 走 `dump_tushare_finance.yoy2` ✓。
"""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "_fin_rq", Path(__file__).parents[1] / "tools" / "dump_finance.py")
RQ = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RQ)

SPEC2 = importlib.util.spec_from_file_location(
    "_fin_ts", Path(__file__).parents[1] / "tools" / "dump_tushare_finance.py")
TS = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(TS)

WHEN = int(np.datetime64("2021-06-30", "D").astype(np.int64))
Q = "2020q4"
BASE_Q = "2019q4"


def qget_of(np_cur, np_base, np_prev_q3=80.0, np_base_q3=70.0):
    """构造一个 PIT 取值函数 ✓（只填 `net_profit_parent_company` ✓）。"""
    vals = {("np_parent", Q): np_cur, ("np_parent", BASE_Q): np_base,
            ("np_parent", "2020q3"): np_prev_q3, ("np_parent", "2019q3"): np_base_q3}

    def qget(name, quarter, when):
        return float(vals.get((name, quarter), float("nan")))
    return qget


def tiered_yoy(np_cur, np_base, suffix="_ttm"):
    """米筐链（三档 + 累计档共用 `tiered_report_values` ✓）⇒ 取 `fin_np_yoy{suffix}` ✓。

    `q=2020q4` 时 tier=2（年化）/tier=3（TTM）都**退化为累计** ✓ ⇒ 便于钉住数值 ✓。
    """
    out = RQ.tiered_report_values(qget_of(np_cur, np_base), Q, WHEN)
    return out["fin_np_yoy" + suffix]


def ts_yoy(cur, base):
    """tushare 链的 `yoy2` ✓（只需把"去年同期"放进 PIT 表 ✓）。"""
    rows = {"income": [{"ts_code": "600000.SH", "report_type": "1", "end_date": "20191231",
                        "ann_date": "20200331", "n_income_attr_p": base}]}
    return TS.yoy2(cur, TS.TsPit(rows), "net_profit_parent_company", BASE_Q, WHEN)


@pytest.mark.parametrize("np_cur,np_base,expected", [
    (150.0, 100.0, 50.0),        # 正常：都为正 ✓
    (-50.0, 100.0, -150.0),      # 盈利转亏 ✓
    (50.0, -100.0, 150.0),       # ★ 扭亏（旧口径给 NaN ✗）
    (-50.0, -100.0, 50.0),       # ★ 亏损收窄（旧口径给 NaN ✗）
    (-200.0, -100.0, -100.0),    # ★ 亏损扩大 ⇒ 与"盈利下滑"**同号**（负 ✓）
])
def test_rq_tiered_and_cumulative_yoy_use_abs_base(np_cur, np_base, expected):
    assert tiered_yoy(np_cur, np_base, "_ttm") == pytest.approx(expected)
    assert tiered_yoy(np_cur, np_base, "_ann") == pytest.approx(expected)
    out = RQ.tiered_report_values(qget_of(np_cur, np_base), Q, WHEN)
    # 单季档（tier=1）：本期单季 = cur − 2020q3、基期单季 = base − 2019q3 ✓
    sq_cur, sq_base = np_cur - 80.0, np_base - 70.0
    assert out["fin_np_yoy_q"] == pytest.approx((sq_cur - sq_base) / abs(sq_base) * 100.0)


@pytest.mark.parametrize("np_cur,np_base,expected", [
    (150.0, 100.0, 50.0),
    (50.0, -100.0, 150.0),
    (-200.0, -100.0, -100.0),
])
def test_ts_yoy_uses_abs_base(np_cur, np_base, expected):
    assert ts_yoy(np_cur, np_base) == pytest.approx(expected)


def test_zero_base_is_nan_in_both_chains():
    assert np.isnan(tiered_yoy(50.0, 0.0))
    assert np.isnan(ts_yoy(50.0, 0.0))


def test_missing_base_is_nan_in_both_chains():
    assert np.isnan(tiered_yoy(50.0, float("nan")))
    assert np.isnan(ts_yoy(50.0, float("nan")))


def test_semantics_version_bumped():
    """改口径必须递增 `FINANCE_SEMANTICS` ✓（否则老机器不会提示重跑物化 ✗）。"""
    assert RQ.FINANCE_SEMANTICS >= "2.1.0"
