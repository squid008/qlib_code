# -*- coding: utf-8 -*-
"""`_pair_and_verdict` 测试（★ v2.9.83，2026-10-19 ✓ —— 触发/未触发的**配对日口径** ✓）。

## 为什么这块非钉不可

它算的是**事件研究最核心的几个数** ✓：`diff`（触发 − 未触发）、`p_value`、
以及**按日配对**的 `daily_trig_mean` / `daily_not_mean` / `daily_diff` / `daily_t` / `daily_win` ✓。

其中**配对日口径**是最容易改坏、也最难靠肉眼发现的一条 ✗ ——
注释里写着用户最初的困惑：「触发 **0.717%** − 非触发 **0.037%** ≠ 配对差 **0.221%**」✓。
原因是**分母不同** ✗：`0.037%` 是"被大量无信号平淡日稀释的全期非触发均值"✓，
而触发信号恰恰集中在"非触发组当天也普涨"的日子（同日非触发实际约 `0.5%`）✓。
⇒ 现在两边都只在"**当天两组都有样本**"的配对日上算 ✓，三者才自洽 ✓。
本文件用一个"未触发组在**非配对日**有极高收益"的构造，**专门盯住这一点** ✓：
如果哪天有人把 `reindex(_pair)` 去掉 ✗ ⇒ `daily_not_mean` 会暴涨 ⇒ 这里立刻红 ✓。

另钉：小样本（<5）**不给 `p_value`** ✓、单配对日**不给 t/胜率** ✓、取消要**抛出** ✓、
`trig`/`not_trig` 任一为空 ⇒ **一个键都不写** ✓（保持结果干净 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import FactorTestCancelled, _pair_and_verdict


def _frame(rows) -> pd.DataFrame:
    """rows = [(inst, date, label), ...] ⇒ index=[instrument, datetime] ✓。"""
    idx = pd.MultiIndex.from_tuples(
        [(i, pd.Timestamp(d)) for i, d, _ in rows], names=["instrument", "datetime"],
    )
    return pd.DataFrame({"LABEL": [lab for _, _, lab in rows]}, index=idx)


def _call(trig, not_trig, sub=None, cancelled=None, name="因子X"):
    result: dict = {"is_binary": True}
    sub = sub if sub is not None else pd.concat([trig, not_trig])
    _pair_and_verdict(cancelled=cancelled, name=name, not_trig=not_trig,
                      result=result, sub=sub, trig=trig)
    return result


D1, D2, D3, D4 = "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"


def test_diff_is_mean_gap_rounded():
    trig = _frame([("A", D1, 0.10), ("B", D1, 0.12)])
    not_trig = _frame([("C", D1, 0.01), ("D", D1, 0.03)])
    r = _call(trig, not_trig)
    assert r["diff"] == pytest.approx(0.11 - 0.02, abs=1e-9)
    # 小样本（各 2 行 < 5）⇒ 不给 p_value ✓
    assert "p_value" not in r


def test_p_value_only_with_five_or_more_each_side():
    trig = _frame([("A", D1, 0.1), ("B", D1, 0.2), ("C", D1, 0.3),
                   ("D", D1, 0.4), ("E", D1, 0.5)])
    not_trig = _frame([("F", D1, 0.0), ("G", D1, 0.01), ("H", D1, 0.02),
                       ("I", D1, 0.03), ("J", D1, 0.04)])
    r = _call(trig, not_trig)
    assert "p_value" in r
    assert r["p_value"] is not None and 0.0 <= r["p_value"] <= 1.0


def test_pairing_uses_only_days_where_both_groups_exist():
    """⭐ 配对日口径：未触发组在**非配对日**的高收益**不能**稀释配对均值 ✓。"""
    # 触发只在 D1/D2 出现 ✓
    trig = _frame([("A", D1, 0.10), ("B", D1, 0.10),
                   ("A", D2, 0.02), ("B", D2, 0.02)])
    # 未触发：D1/D2 很低（0.01 / 0.00），但 D3/D4 **极高**（0.50）✓
    not_trig = _frame([("C", D1, 0.01), ("D", D1, 0.01),
                       ("C", D2, 0.00), ("D", D2, 0.00),
                       ("C", D3, 0.50), ("D", D3, 0.50),
                       ("C", D4, 0.50), ("D", D4, 0.50)])
    r = _call(trig, not_trig)

    # 配对日 = D1/D2 ✓ ⇒ 未触发均值 = (0.01+0.00)/2 = 0.005 ✓
    assert r["daily_not_mean"] == pytest.approx(0.005, abs=1e-9)
    # ✗ 若退化成"全期均值"会是 0.2525 ⇒ 差 50 倍 ✓（这就是当年那个困惑 ✓）
    assert r["daily_not_mean"] != pytest.approx(0.2525, abs=1e-3)
    assert r["daily_trig_mean"] == pytest.approx(0.06, abs=1e-9)
    # 日差 = 0.09 / 0.02 ⇒ 均值 0.055 ✓
    assert r["daily_diff"] == pytest.approx(0.055, abs=1e-9)
    assert r["daily_n"] == 2


def test_daily_win_and_t_need_two_days():
    one_day_trig = _frame([("A", D1, 0.05)])
    one_day_not = _frame([("B", D1, 0.01)])
    r1 = _call(one_day_trig, one_day_not)
    assert r1["daily_n"] == 1
    assert "daily_diff" in r1
    # 单配对日不做推断 ✓（t / 胜率都不写 ✓）
    assert "daily_t" not in r1 and "daily_win" not in r1

    two_day_trig = _frame([("A", D1, 0.05), ("A", D2, -0.02)])
    two_day_not = _frame([("B", D1, 0.01), ("B", D2, 0.03)])
    r2 = _call(two_day_trig, two_day_not)
    assert r2["daily_n"] == 2
    assert r2["daily_win"] == pytest.approx(0.5, abs=1e-9)   # 一天正、一天负 ✓
    assert "daily_t" in r2


def test_acf_and_var_ratio_present_with_enough_days():
    days = [str(pd.Timestamp("2024-01-02") + pd.Timedelta(days=i))[:10] for i in range(8)]
    trig = _frame([("A", d, 0.05 + 0.001 * i) for i, d in enumerate(days)])
    not_trig = _frame([("B", d, 0.01) for d in days])
    r = _call(trig, not_trig)
    assert "daily_acf1" in r and "daily_acf5" in r
    assert "daily_var_ratio" in r
    assert "daily_t_hac" in r          # 8 个配对日足够 ✓


def test_empty_side_writes_nothing():
    trig = _frame([("A", D1, 0.1)])
    empty = _frame([])
    r = _call(trig, empty)
    # 只保留外部塞进来的键 ✓（这一段不写任何东西 ✓）
    assert r == {"is_binary": True}


def test_cancelled_raises_and_is_not_swallowed():
    """⭐ 取消要**抛出去** ✓（被 `except Exception` 吞掉 = 用户点了取消但任务继续跑 ✗）。"""
    trig = _frame([("A", D1, 0.1), ("B", D1, 0.2), ("C", D1, 0.3),
                   ("D", D1, 0.4), ("E", D1, 0.5)])
    not_trig = _frame([("F", D1, 0.0), ("G", D1, 0.01), ("H", D1, 0.02),
                       ("I", D1, 0.03), ("J", D1, 0.04)])
    with pytest.raises(FactorTestCancelled):
        _call(trig, not_trig, cancelled=lambda: True)
