# -*- coding: utf-8 -*-
"""单因子测试「覆盖率」口径守卫（v1.21.6 ✓）。

**为什么锁**（2026-10-10 用户报障现场）：旧口径 `因子非空行 ÷ 面板行数` ✗ —— 面板行集是
**本次请求全部参与字段覆盖区间的并集**（`panel_expr._union_layout` ✓），而 `fin_*` 物化 bin
铺满整条历法（含上市前报告期 / 退市后前向填充）⇒ 同一个因子、同一参数，只因同桌多测了
一个用 `fin_*` 的因子，覆盖率就从 99.75% 掉到 98.16%（cn_data 96.04% ✗）⇒ 用户拿它对比
两套数据源时被误导成"数据坏了"✗。

**新口径**：`因子非空行 ÷ CLOSE 有效行`（在有行情的交易日里，该因子有多少比例有值 ✓）——
分母与同桌因子无关 ⇒ 任意组合、任意数据集都可直接对比 ✓；面板死行**不得**影响它 ✓。
"""
import numpy as np
import pandas as pd

from app.factors.single_test import _test_one

# `_test_one` 内部按列名取值的全部列（缺失会 KeyError ⇒ 这里一次给全 ✓）
_COLS = ["F0", "LABEL", "CLOSE", "CLOSE_FF", "CHANGE", "T1_CLOSE", "T1_CHANGE",
         "T1_IS_ST", "LIMIT_UP", "T1_LIMIT_UP", "IS_ST"]


def _frame(n_trade, n_dead=0, n_hole=0):
    """构造面板：

    · `n_trade`   = **有行情且因子有值**的行 ✓
    · `n_dead`    = **死行**（CLOSE 空 + 因子空 —— fin bin 撑出来的那些 ✗）
    · `n_hole`    = **有行情但因子空**（金融股无毛利 / 历史不足 ✓）
    ⇒ CLOSE 有效行 = n_trade + n_hole ✓，因子非空 = n_trade ✓
    """
    rows = n_trade + n_dead + n_hole
    idx = pd.MultiIndex.from_product([["sz000001"], pd.bdate_range("2024-01-01", periods=rows)],
                                     names=["instrument", "datetime"])
    close = np.full(rows, 10.0)
    f0 = np.ones(rows)
    if n_dead:                                   # 死行放在尾巴：CLOSE 与因子一起空 ✓
        close[-n_dead:] = np.nan
        f0[-n_dead:] = np.nan
    if n_hole:                                   # 空洞：CLOSE 有值、因子空 ✓
        f0[rows - n_dead - n_hole: rows - n_dead] = np.nan
    df = pd.DataFrame({c: np.zeros(rows) for c in _COLS}, index=idx)
    df["CLOSE"], df["CLOSE_FF"] = close, close
    df["F0"] = f0
    df["LABEL"] = 0.01
    df["T1_CLOSE"] = close
    return df


def _cov(df):
    r = _test_one(df, {"id": "t", "name": "t"}, "F0", horizon=1, quantiles=10, topk_list=[0.1])
    return r["coverage"], r["coverage_den"]


class TestCoverageDenominator:
    def test_denominator_is_close_valid_rows(self):
        """分母 = CLOSE 有效行数（**不是**面板行数 ✓）。"""
        cov, den = _cov(_frame(n_trade=100))
        assert cov == 1.0 and den == 100

    def test_dead_rows_do_not_change_coverage(self):
        """★ 回归：面板死行（CLOSE 空 + 因子空）**不得**影响覆盖率。

        旧口径下同一因子加 200 行死行 ⇒ 覆盖率从 1.0 掉到 0.33 ✗（这正是用户看到的"两套
        数据集覆盖率不一致" ✗）。
        """
        a, b, c = (_cov(_frame(n_trade=100, n_dead=d)) for d in (0, 50, 200))
        assert a[0] == b[0] == c[0] == 1.0
        assert a[1] == b[1] == c[1] == 100          # 分母只数 CLOSE 有效行 ✓

    def test_hole_on_trading_day_still_counted(self):
        """有行情但因子空（如金融股无毛利）⇒ 覆盖率照实下降 ✓。"""
        cov, den = _cov(_frame(n_trade=80, n_dead=200, n_hole=20))
        assert den == 100                            # 80 有值 + 20 空洞 = 100 个交易日 ✓
        assert cov == 0.8                            # 80/100

    def test_no_close_column_falls_back(self):
        """兜底：面板没有 CLOSE 列时退回旧口径（不抛异常 ✓）。"""
        df = _frame(n_trade=100).drop(columns=["CLOSE", "CLOSE_FF"])
        r = _test_one(df, {"id": "t", "name": "t"}, "F0", horizon=1, quantiles=10, topk_list=[0.1])
        assert r["coverage"] == 1.0
