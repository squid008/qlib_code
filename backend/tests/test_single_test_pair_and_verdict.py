# -*- coding: utf-8 -*-
"""`_pair_and_verdict` ＋ `_resolve_instruments`（★ v2.10.95 —— `single_test.py` 的第一刀）。

## 为什么挑这两个
`single_test.py` 2643 行、26 个函数里 18 个有缺口 ✓（覆盖 52.24% ✗）。
但大头（`run_single_factor_tests` 245 行 / `_quantile_curve_block` 80 /
`_load_feature_panel` 69）都**要真面板**✗ ⇒ 这轮只打**能脱数据测**的：
· `_pair_and_verdict`（26 行）—— 触发组 vs 非触发组的**配对日检验** ✓；
· `_resolve_instruments`（12 行）—— 股票池 → 代码清单（**要排除北交所与指数** ✓）。

## ⭐⭐ `_pair_and_verdict` 里最该钉的一条：**配对日口径**
源码注释用了 10 行解释一个**用户真实困惑**：
> 「触发 0.717% − 非触发 0.037% ≠ 配对差 0.221%」

原因：`非触发均值 0.037%` 是**被大量无信号平淡日稀释**的全期均值 ✓，
而触发信号集中在“非触发组当天也普涨”的日子 ✓ ⇒ 两组分母不同、直接相减无意义 ✗。
修法是**只在"当天两组都有样本"的日期集合上算** ✓（`_pair = daily.index` ✓）。
⇒ 若哪天有人"优化"成各自全集的均值，本文件的 `test_daily_means_use_paired_days_only`
会立刻红 ✓ —— 那正是这条注释存在的意义 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import single_test as S

CODES = ("SZ000001", "SZ000002")


def _frame(rows):
    """rows = [(code, date, label), ...] ⇒ **DataFrame**（列 `LABEL`）✓。

    ⚠ 必须是 DataFrame 而不是 Series ✗→✓ —— 函数体里是 `trig["LABEL"]` ✓，
    传 Series 会 `KeyError: 'LABEL'` ✓（我第一版就是这么红的 ✓）。
    """
    idx = pd.MultiIndex.from_tuples([(c, d) for c, d, _ in rows], names=["instrument", "datetime"])
    return pd.DataFrame({"LABEL": [v for _, _, v in rows]}, index=idx, dtype=float)


def _result():
    return {"is_binary": False}


def _sub(rows):
    """`sub` 只为拿 `index.names`（`datetime` 在第几层 ✓）—— 直接复用 `_frame` ✓
    （`_frame` 现在已经是 DataFrame ✓，再 `.to_frame()` 会 `AttributeError` ✗）。"""
    return _frame(rows)


# ---------------------------------------------------------------- _pair_and_verdict
def test_diff_is_trigger_minus_non_trigger():
    """`diff` = 触发组均值 − 非触发组均值 ✓（符号反了 ⇒ 好因子被读成坏因子 ✗）。"""
    trig = _frame([("SZ000001", "2024-01-02", 0.02), ("SZ000002", "2024-01-02", 0.04)])
    notr = _frame([("SZ000001", "2024-01-02", 0.00), ("SZ000002", "2024-01-02", 0.02)])
    res = _result()
    S._pair_and_verdict(cancelled=None, name="f", not_trig=notr, result=res,
                        sub=_sub([("SZ000001", "2024-01-02", 0.0)]), trig=trig)
    assert res["diff"] == pytest.approx(0.02)


def test_requires_both_sides_for_diff():
    """只有一侧有样本 ⇒ **`diff` 不设** ✓（拿单侧均值当"差值"= 编数 ✗）。"""
    res = _result()
    S._pair_and_verdict(cancelled=None, name="f", not_trig=_frame([]), result=res,
                        sub=_sub([("SZ000001", "2024-01-02", 0.0)]),
                        trig=_frame([("SZ000001", "2024-01-02", 0.02)]))
    assert "diff" not in res
    assert "daily_diff" not in res


def test_daily_means_use_paired_days_only():
    """⭐⭐⭐ **配对口径**：`daily_trig_mean` / `daily_not_mean` 只在
    "**当天两组都有样本**"的日期上算 ✓ —— 这正是源码里 10 行注释解释的那件事 ✓。

    构造：`day1` 只有触发组、`day2` 两组都有 ✓
    · 配对口径 ⇒ 两者都只看 `day2` ✓（`trig=0.03`、`not=0.01`）；
    · 若按"各自全集"✗ ⇒ trig 会变成 `(0.02+0.03)/2=0.025` ✓ 两者就对不上了 ✗。
    """
    trig = _frame([
        ("SZ000001", "2024-01-02", 0.02),                 # day1：只有触发组
        ("SZ000001", "2024-01-03", 0.03),                 # day2：两组都有 ✓
        ("SZ000002", "2024-01-03", 0.03),
    ])
    notr = _frame([
        ("SZ000001", "2024-01-03", 0.01),                 # day2 ✓
        ("SZ000002", "2024-01-03", 0.01),
    ])
    res = _result()
    S._pair_and_verdict(cancelled=None, name="f", not_trig=notr, result=res,
                        sub=_sub([("SZ000001", "2024-01-02", 0.0)]), trig=trig)
    assert res["daily_trig_mean"] == pytest.approx(0.03), "只算配对日 day2 ✓（不是 0.025 ✗）"
    assert res["daily_not_mean"] == pytest.approx(0.01)
    assert res["daily_diff"] == pytest.approx(0.02), "日差 = 配对日上两组均值之差 ✓"
    assert res["daily_n"] == 1


def test_single_paired_day_has_no_inference_stats():
    """⭐⭐ 只有 **1 个配对日** ⇒ `daily_diff`/`daily_n` 有 ✓，
    但 **`daily_t` / `daily_win` 不许有** ✓ —— 单点做 t 检验无意义 ✗（会给出 t=inf/nan ✓）。"""
    trig = _frame([("SZ000001", "2024-01-02", 0.05)])
    notr = _frame([("SZ000001", "2024-01-02", 0.01)])
    res = _result()
    S._pair_and_verdict(cancelled=None, name="f", not_trig=notr, result=res,
                        sub=_sub([("SZ000001", "2024-01-02", 0.0)]), trig=trig)
    assert res["daily_n"] == 1
    for k in ("daily_t", "daily_win", "daily_t_hac", "daily_acf1"):
        assert k not in res, "单配对日不该产出 %s ✗" % k


def test_two_paired_days_produce_inference_stats():
    """⭐⭐ ≥2 个配对日 ⇒ `daily_t` / `daily_win` / `daily_acf1` / `daily_var_ratio` 都要有 ✓
    （这些是"这个因子是不是真的有用"的主显示指标 ✓）。"""
    trig = _frame([
        ("SZ000001", "2024-01-02", 0.05), ("SZ000001", "2024-01-03", 0.06),
    ])
    notr = _frame([
        ("SZ000001", "2024-01-02", 0.01), ("SZ000001", "2024-01-03", 0.02),
    ])
    res = _result()
    S._pair_and_verdict(cancelled=None, name="f", not_trig=notr, result=res,
                        sub=_sub([("SZ000001", "2024-01-02", 0.0)]), trig=trig)
    assert res["daily_n"] == 2
    assert res["daily_t"] > 0, "两天都为正 ⇒ t 必须为正 ✓"
    assert res["daily_win"] == 1.0
    assert "daily_acf1" in res and "daily_acf5" in res
    assert "daily_var_ratio" in res


def test_mwu_p_value_present_when_sample_large_enough():
    """两侧各 ≥5 ⇒ 有 `p_value` ✓（样本太少时不做 MWU ✓ —— 5 个点做检验是假显著 ✗）。"""
    trig = _frame([("SZ000001", "2024-01-02", 0.10 + i * 0.01) for i in range(10)])
    notr = _frame([("SZ000001", "2024-01-02", 0.00 + i * 0.001) for i in range(10)])
    res = _result()
    S._pair_and_verdict(cancelled=None, name="f", not_trig=notr, result=res,
                        sub=_sub([("SZ000001", "2024-01-02", 0.0)]), trig=trig)
    assert isinstance(res["p_value"], float)
    assert 0.0 <= res["p_value"] <= 1.0


def test_cancelled_raises_instead_of_returning_partial():
    """⭐ 取消信号 ⇒ **抛 `FactorTestCancelled`** ✓（不是"算一半就返回" ✗ ——
    半截结果会被当成真结论显示给用户 ✓）。"""
    res = _result()
    with pytest.raises(S.FactorTestCancelled):
        S._pair_and_verdict(cancelled=lambda: True, name="f",
                            not_trig=_frame([("SZ000001", "2024-01-02", 0.01)]), result=res,
                            sub=_sub([("SZ000001", "2024-01-02", 0.0)]),
                            trig=_frame([("SZ000001", "2024-01-02", 0.05)]))


# ---------------------------------------------------------------- _resolve_instruments
def test_resolve_instruments_filters_bj_and_indices(monkeypatch):
    """⭐⭐ 必须**排除北交所（BJ…）与指数代码**（`SH000…`/`SH88…`/`SH89…`/`SZ39…`）✓ ——
    漏了它们 ⇒ 指数被当成一只"股票"参与因子测试 ✓，它的"收益"会污染分层/IC ✓。
    同时要**保留**正常股票 ✓，且代码统一**大写** ✓。"""
    import qlib.data as qd

    listed = ["sz000001", "SH600000", "BJ430047", "SH000300", "SH880001", "SH890001", "SZ399001"]

    class _D:
        @staticmethod
        def instruments(market=None):
            return ("MARKET", market)

        @staticmethod
        def list_instruments(scope, start_time=None, as_list=False):
            assert scope[0] == "MARKET" and scope[1] == "csi300", "股票池参数要原样传下去 ✓"
            return listed

    monkeypatch.setattr(qd, "D", _D)
    got = S._resolve_instruments("csi300", "2024-01-01")
    assert got == ["SZ000001", "SH600000"], "只剩正常股票且已大写 ✓（实际：%r）" % got
