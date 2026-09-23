# -*- coding: utf-8 -*-
"""`And/Or` 的**不等长对齐** + `DYN_*` 的**常量窗口**（v1.20.60）。

背景（2026-09-23 任务 `07bf2c5ba41e` 失败 ✓）：

    File "app/factors/ops_ext.py", line 657, in _load_internal
    File "app/factors/ops_ext.py", line 681, in _op
    ValueError: operands could not be broadcast together with shapes (4992,) (5110,)

逐条加载该任务 49 条公式 ⇒ 定位到第 35 条 `强龙起势` ✓（`(5246,) (6446,)`：**同一支股票、
两个天数** ✗）。另外真机顺手发现：`DYN_MEAN($close,5)` / `DYN_COUNT(And(...),5)` 这种
**窗口写成字面量**的写法会崩 `'int' object has no attribute 'load'` ✗。

成因：`DYN_*` 把扩展窗口拉到 **inf**（全历史 ✓）后，同一棵树里有的子式覆盖全历史、有的只覆盖
部分（`BARSCOUNT` 自上市首日起算 ✓、停牌删行的 `SR` 包装 ✓）⇒ 长度天然不同 ✗；
而 `And/Or` 原先**只处理"常量 vs 序列"**（`ndim==0` 广播 ✓）✗ —— 同文件的 `Corr` 早就为
「SR 删行导致的左右不等长」做过容错 ✓，`And/Or` 漏了 ✓。
"""
import numpy as np
import pandas as pd
from qlib.data.base import Expression

from app.factors.ops_ext import DYN_MEAN, And, Or, _align_series


class _S(Expression):
    """按给定索引返回序列的桩（`load`/`_load_internal` 都返回同一个 Series ✓）。"""

    def __init__(self, series=None, scalar=None):
        self._s, self._k = series, scalar
        super().__init__()

    def load(self, *_a, **_k):
        return self._s if self._s is not None else self._k

    def _load_internal(self, *_a, **_k):
        return self.load()

    def get_longest_back_rolling(self):
        return 0

    def get_extended_window_size(self):
        return (0, 0)

    def __str__(self):
        return "S"


IDX = pd.date_range("2026-01-01", periods=4, freq="D")


def test_align_series_union_fills_nan():
    a = pd.Series([1.0, 2.0], index=IDX[:2])
    b = pd.Series([3.0], index=IDX[:1])
    x, y = _align_series(a, b)
    assert x.index.equals(y.index) and len(x) == 2
    assert np.isnan(y.iloc[1])          # 缺侧补 NaN ⇒ 逻辑运算里视作 0 ✓


def test_and_tolerates_unequal_length():
    """★ 主案：两侧日期轴不等长 ⇒ 对齐后相与（缺侧视作 0 ✓），**不崩** ✗→✓。"""
    long_ = pd.Series([1.0, 1.0, 1.0, 1.0], index=IDX)
    short = pd.Series([1.0, 0.0], index=IDX[:2])
    out = And(_S(long_), _S(short))._load_internal("SH600000", 0, 4)
    assert isinstance(out, pd.Series) and len(out) == 4
    assert out.tolist() == [1.0, 0.0, 0.0, 0.0]


def test_or_tolerates_unequal_length():
    long_ = pd.Series([0.0, 0.0, 0.0, 0.0], index=IDX)
    short = pd.Series([1.0, 0.0], index=IDX[:2])
    out = Or(_S(long_), _S(short))._load_internal("SH600000", 0, 4)
    assert out.tolist() == [1.0, 0.0, 0.0, 0.0]


def test_and_still_supports_scalar_side():
    """原有能力**不能退化** ✓：一侧是常量时仍走广播（且不因 NaN 语义变化 ✗）。"""
    long_ = pd.Series([1.0, 0.0, 2.0], index=IDX[:3])
    out = And(_S(long_), _S(scalar=1.0))._load_internal("SH600000", 0, 3)
    assert out.tolist() == [1.0, 0.0, 1.0]


def test_dyn_op_accepts_plain_int_window():
    """★ `DYN_MEAN($close,5)`：qlib 把窗口解析成 **plain int** ✗ ⇒ 不能崩 ✓。"""
    feat = pd.Series([1.0, 2.0, 3.0, 4.0], index=IDX)
    out = DYN_MEAN(_S(feat), 2)._load_internal("SH600000", 0, 4)
    np.testing.assert_allclose(out.to_numpy(), [1.0, 1.5, 2.5, 3.5])
