# -*- coding: utf-8 -*-
"""`Corr` 覆盖回归：`SR` 删行导致左右不等长时**不得抛 ValueError**（2026-09-18）。

事故：用户"复用参数"回测报
    ValueError: operands could not be broadcast together with shapes (406,) (400,)
栈在 `qlib/data/ops.py:1495` 的 `Corr._load_internal`，真因是
`ops_ext.SR`（停牌删行语义）返回**删行后的短序列** ✗ ⇒ 右串 400 / 左串 406 ⇒
`res.loc[bool数组]` 两侧长度不等 ⇒ 崩 ✓。
修复：`ops_ext.Corr` 覆盖 qlib 内建 —— 左右 `reindex` 到同一索引、返回**定长** ✓。
"""
import numpy as np
import pandas as pd

from qlib.data.base import ExpressionOps
from qlib.data.ops import Corr as _QLIB_CORR

from app.factors import ops_ext


class _FixedSeries(ExpressionOps):
    """测试用叶子：直接把预置 Series 吐出来（模拟 `SR` 已**删行**的结果 ✓）。"""

    def __init__(self, series: pd.Series):
        self._series = series
        super().__init__()

    def _load_internal(self, instrument, start_index, end_index, *args):
        return self._series

    def get_longest_back_rolling(self):
        return 0

    def get_extended_window_size(self):
        return 0, 0

    def __str__(self):
        return "FIXED(len=%d)" % len(self._series)


def test_corr_is_override_not_rewrite():
    """`Corr` 必须是 qlib 内建 `Corr` 的**子类**（只覆盖 `_load_internal` ✓），
    且已注册进 qlib（覆盖同名内建 ✓）—— 否则用户表达式里的 `Corr(...)` 仍走旧实现 ✗。"""
    assert issubclass(ops_ext.Corr, _QLIB_CORR)
    ops_ext.ensure_ops_registered()
    assert ops_ext.Corr in ops_ext._ALL_OPS


def test_corr_tolerates_uneven_lengths():
    """★ 核心回归：左 406 / 右 400（`SR` 删掉 6 行 ✗）⇒ 必须返回**定长 406** 且不抛 ✓。"""
    idx = pd.date_range("2021-01-01", periods=406, freq="D")
    left = pd.Series(np.linspace(10.0, 20.0, 406), index=idx)
    # 右串模拟 `SR` 删行（去掉前 6 天 ⇒ 400 行，与原事故形状一致 ✓）
    right = pd.Series(np.linspace(1.0, 5.0, 400), index=idx[6:])

    op = ops_ext.Corr(_FixedSeries(left), _FixedSeries(right), 5)
    res = op._load_internal("SH600008", 0, 405)

    assert isinstance(res, pd.Series)
    assert len(res) == 406, "必须定长（qlib 算子契约）✓"
    assert res.index.equals(idx), "索引必须与左串一致 ✓"
    # 被 `SR` 删掉的行（前 6 天）结果应为 NaN（停牌日不参与 ✓，与 SR 语义一致 ✓）
    assert res.iloc[:6].isna().all()
    assert np.isfinite(res.iloc[-1]), "末行应有值 ✓"


def test_corr_constant_series_is_nan():
    """常数序列 ⇒ 相关系数无意义 ⇒ 置 NaN（保持 qlib 原逻辑 ✓）。"""
    idx = pd.date_range("2021-01-01", periods=30, freq="D")
    left = pd.Series([1.0] * 30, index=idx)                 # 常数 ⇒ std=0 ⇒ NaN ✓
    right = pd.Series(np.arange(30, dtype=float), index=idx)

    res = ops_ext.Corr(_FixedSeries(left), _FixedSeries(right), 5)._load_internal("X", 0, 29)
    assert res.isna().all()
