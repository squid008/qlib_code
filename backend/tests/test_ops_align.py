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


# ---------------- ★ v1.20.61：`If`（qlib 唯一的 Triple-wise）也要对齐 ----------------
# 用户 2026-09-23 第二次报：任务 `4e8e5fe991d3`
#     operands could not be broadcast together with shapes (4992,) () (5110,)
# 这是 qlib `ops.py:670` 那行 `np.where(series_cond, series_left, series_right)` 的形态：
# **三个输入只取条件的索引、不做对齐** ✗（`()` 是常量分支，如 `IF(cond, 比值, 0)` ✓）。

IDX5 = pd.date_range("2026-01-01", periods=5, freq="D")


def test_if_aligns_three_inputs():
    """条件 4 天、分支 5 天、另一分支是常量 ⇒ 原来 `np.where` 直接崩 ✓，现在必须对齐 ✓。"""
    from app.factors.ops_ext import If

    cond = pd.Series([1.0, 0.0, 1.0, 0.0], index=IDX)
    left = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0], index=IDX5)
    out = If(_S(cond), _S(left), 0.0)._load_internal("SH600000", 0, 5)
    assert isinstance(out, pd.Series) and len(out) == 5
    # 第 5 天条件缺失 ⇒ 视作假 ⇒ 取右支 0 ✓（其余按条件取值 ✓）
    assert out.tolist() == [10.0, 0.0, 12.0, 0.0, 0.0]


def test_if_all_scalar_still_works():
    """三分支都是常量 ⇒ 仍按 `np.where` 语义（非 0 即真 ✓）。"""
    from app.factors.ops_ext import If

    out = If(1, 7.0, 0.0)._load_internal("SH600000", 0, 1)
    assert float(out.iloc[0]) == 7.0


def test_if_override_is_registered():
    """⚠ 覆盖必须**真的生效** ✗ —— 光定义类不够：qlib 解析表达式时是从**注册表**取 `If` 的 ✓。

    ⚠ 这里**不自己猜 qlib 的私有 API** ✗ —— "注册表里有没有这个算子"由仓库既有的守卫测试
      `tests/test_operator_names_registered.py` 负责 ✓（它按 `_ALL_OPS` / `__all__` 核对 ✓）
      ⇒ 本测试只为"类本身可用"提供一条直连断言 ✓。
    """
    from app.factors.ops_ext import If, _ALL_OPS, ensure_ops_registered

    ensure_ops_registered()
    assert If in tuple(_ALL_OPS)          # 已进注册清单 ⇒ 会被 Operators.register 注册 ✓
