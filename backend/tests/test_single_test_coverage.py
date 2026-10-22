# -*- coding: utf-8 -*-
"""覆盖率口径 `coverage_of`（★ v2.11.5 —— 单独抽出后终于能直接单测）。

## 为什么要钉这个数字
它是用户**唯一能看到的"数据够不够"信号** ✓，而它**错过一次**（2026-10-10 追查实锤 ✓）：
同一因子、同一参数，只因**同桌多测了一个用 `fin_*` 的因子**，
覆盖率就从 **99.75% 掉到 98.16%** ✓（cn_data 96.04% ✓）—— 用户拿它对比两套数据源时
会以为「**数据有毛病**」✗。真因：旧分母是**面板总行数** ✓，
而 `fin_*` 物化 bin **铺满整条历法**（含上市前报告期 ✓）⇒ 大量"有财务值、没行情"的死行
在撑大分母 ✓。新口径把分母换成 **`CLOSE` 有效行** ✓ ⇒ 与同桌因子无关 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test_coverage import coverage_of


def _df(f_vals, cl_vals, with_close=True):
    d = {"F": f_vals}
    if with_close:
        d["CLOSE"] = cl_vals
    return pd.DataFrame(d)


def test_new_formula_uses_close_as_denominator():
    """新口径 = 因子非空 ∩ CLOSE 有效 ÷ CLOSE 有效 ✓，且**分母一并返回**（便于自查 ✓）。"""
    df = _df([1.0, np.nan, 3.0, np.nan], [10.0, 11.0, np.nan, np.nan])
    cov, den = coverage_of(df, "F")
    assert den == 2, "分母 = CLOSE 有效行 = 2 ✓（不是面板 4 行 ✗）"
    assert cov == 0.5, "2 行有行情里只有 1 行因子有值 ✓"


def test_denominator_is_independent_of_other_factors():
    """⭐⭐⭐ **核心语义**：同桌多测一个"铺满历法"的因子（`fin_*` 那种 ✓），
    不能改变本因子的覆盖率 ✓ —— 这正是旧口径让用户误判"数据有毛病"的地方 ✗。

    构造：同一个 `F`（在 CLOSE 有效的两天都有值 ✓），
    面板从"只有 F+CLOSE"变成"再多一个 FIN 列且它在**无行情**的行上也有值" ✓
    ⇒ 面板总行数不变（这里用同一个 df ✓），但**旧口径的分母会跟着 F 之外的死行变化** ✓。
    """
    cl = [10.0, 11.0, np.nan, np.nan]
    base = _df([1.0, 2.0, np.nan, np.nan], cl)
    # 多一个"死行也有值"的因子列（模拟 fin_* 铺满历法 ✓）
    wide = base.copy()
    wide["FIN"] = [5.0, 5.0, 5.0, 5.0]
    c1, d1 = coverage_of(base, "F")
    c2, d2 = coverage_of(wide, "F")
    assert (c1, d1) == (c2, d2) == (1.0, 2), "加了别的因子后本因子覆盖率必须不变 ✓"


def test_all_close_nan_gives_none_not_zero():
    """⭐⭐ 有 CLOSE 列但**全空** ⇒ `(None, 0)` ✓ —— **不是 0.0** ✗：
    0.0 会被读成"这因子一行都没值"✓，而真相是"这段区间根本没有行情"✓，两者处置完全不同 ✗。"""
    df = _df([1.0, 2.0], [np.nan, np.nan])
    assert coverage_of(df, "F") == (None, 0)


def test_fallback_without_close_column_returns_no_denominator():
    """没有 CLOSE 列 ⇒ 退回旧口径 ✓，且分母返回 **None**（调用方据此**不写** `coverage_den` ✓，
    与改造前的行为逐位一致 ✓）。"""
    df = _df([1.0, np.nan, 3.0, 4.0], None, with_close=False)
    cov, den = coverage_of(df, "F")
    assert cov == 0.75 and den is None


def test_coverage_is_rounded_to_4dp():
    """⚠ 保留 4 位小数 ✓（前端直接显示 ✓；`round` 掉的话 0.3333 会变 0.33 ✓，
    同一因子在不同数据集上的细微差别就被抹平了 ✗）。"""
    df = _df([1.0, np.nan, np.nan], [1.0, 1.0, 1.0])
    cov, _den = coverage_of(df, "F")
    assert cov == pytest.approx(0.3333)
