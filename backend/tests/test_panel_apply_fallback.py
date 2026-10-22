# -*- coding: utf-8 -*-
"""`_apply_node` 的**兜底路径**（★ v2.11.9 —— 直接调函数，不经 `eval_expr`）。

## 这段代码什么时候会被走到
三条快路径是「Series+标量 ✓ / 至少一侧是 Series ✓ / 两侧都是标量 ✓」。
剩下的就是**兜底**：入参**既不是 Series 也不是标量** ✓（`ndarray` / `list` ✓）——
它们由**递推组内部**产生 ✓，`eval_expr` 构造不出来 ✗ ⇒ 以前这 21 行是纯盲区 ✓。

## ⚠⚠ 两个**已知缺陷**（本轮实测发现，钉的是**现状**而不是"应该这样"）
1. **`Greater`/`Less` 对 `ndarray` 入参会抛**
   `ValueError: The truth value of an array with more than one element is ambiguous` ✗ ——
   真因：这两个算子（= **逐点取大/小** ✓）在**标量快路径**里被实现成 `max(_af, _bf)` ✓，
   对数组就变成 Python 内建 `max` 的比较 ✗。
   · 现在**走不到**（兜底只在递推组内部出现，那边给的是 Series ✓）⇒ 不是线上 bug ✓；
   · 但一旦有人从内部传数组进来 ⇒ **抛异常 ⇒ 整块面板回退 qlib** ✓（慢，且日志难懂 ✗）。
   ⇒ 已记技术债 **P2-#20** ✓，不在本轮修（改行为要先确认调用面 ✗）。
2. **`ndarray` 与 `Series` 混用 ⇒ `TypeError`** ✗ ——
   真因：兜底里 `template = a if isinstance(a, pd.Series) else b` ✓，
   两侧都不是 Series 时 `template` 变成 `ndarray` ✓ ⇒ `_as_series(x, template)` 拿它当索引就炸 ✗。
   ⇒ 同样**走不到**（内部要么全 Series 要么全数组 ✓），记在同一笔债里 ✓。

## 这一段里**正确**的部分（要钉住，别被上面两条盖过）
`Add`/`Sub`/`Mul`/`Div`/`Pow` 对 `ndarray`/`list` 都对 ✓；`And`/`Or` 在**两侧索引不同**时
按**并集**对齐 ✓ 且用「≠0」判定 ✓ —— 后者有个**语义细节**：`NaN != 0` 为真 ✓
⇒ **NaN 会被当成"有值"** ✓（这本就是布尔算子的定义 ✓，但值得写明 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as P


def _call(op, args):
    return P._apply_node(op, args, False)


# ---------------------------------------------------------------- 算术（ndarray）
def test_arith_ops_on_ndarray_are_elementwise():
    """⭐ `Add/Sub/Mul/Div` 对 `ndarray` ⇒ **逐元素** ✓（不是"把数组当标量"✗）。"""
    a = np.array([1.0, 2.0, 4.0])
    b = np.array([3.0, 2.0, 2.0])
    assert list(_call("Add", (a, b))) == [4.0, 4.0, 6.0]
    assert list(_call("Sub", (a, b))) == [-2.0, 0.0, 2.0]
    assert list(_call("Mul", (a, b))) == [3.0, 4.0, 8.0]
    assert list(_call("Div", (a, b))) == [1 / 3, 1.0, 2.0]


def test_pow_on_ndarray():
    """⭐ `Pow` ⇒ 逐元素幂 ✓（**不是** `a ** b` 的矩阵幂 ✗ —— 那会直接报形状错 ✓）。"""
    got = _call("Pow", (np.array([2.0, 3.0]), np.array([3.0, 2.0])))
    assert list(got) == [8.0, 9.0]


def test_list_inputs_behave_like_ndarray():
    """⭐ `list` 入参与 `ndarray` **同一答案** ✓ —— 两种"非常量非 Series"的入参不该分叉 ✓。"""
    got = _call("Add", ([1.0, 2.0], [3.0, 4.0]))
    assert list(got) == [4.0, 6.0]


# ---------------------------------------------------------------- 已知缺陷（钉现状）
@pytest.mark.parametrize("op", ["Greater", "Less"])
def test_known_gap_elementwise_max_min_rejects_arrays(op):
    """⚠⚠ **已知缺陷 P2-#20**：`Greater`/`Less`（= **逐点取大/小** ✓）对 `ndarray` 入参会抛
    `ValueError: truth value of an array ... is ambiguous` ✗。

    真因：标量快路径里它们是 `max(_af, _bf)` / `min(_af, _bf)` ✓（Python 内建 ✓），
    对数组就退化成一堆元素逐个比大小 ✗。

    ⚠ 本用例钉的是**现状**（`pytest.raises`）✓ —— **不是**"这样是对的" ✗。
    现在**走不到**（兜底只在递推组内部出现 ✓），但一旦有人传数组进来 ⇒
    **抛异常 ⇒ 整块面板回退 qlib** ✓（慢、日志还难懂 ✗）。修它要先确认调用面 ✓。
    """
    with pytest.raises(ValueError, match="ambiguous"):
        _call(op, (np.array([1.0, 2.0]), np.array([3.0, 4.0])))


def test_known_gap_ndarray_with_series_raises():
    """⚠⚠ **已知缺陷 P2-#20（同一笔）**：`ndarray` 与 `Series` 混用 ⇒ `TypeError` ✗。

    真因：兜底里 `template = a if isinstance(a, pd.Series) else b` ✓ ——
    两侧都不是 Series 时 `template` 成了 `ndarray` ✓ ⇒ `_as_series(x, template)` 拿它当**索引** ✗。
    同样**走不到**（内部要么全 Series、要么全数组 ✓），钉现状 ✓。
    """
    with pytest.raises(TypeError):
        _call("Add", (np.array([1.0, 2.0]), pd.Series([3.0, 4.0], index=pd.Index(["a", "b"]))))


# ---------------------------------------------------------------- 布尔：索引不同
def test_and_with_different_index_aligns_on_union():
    """⭐⭐ `And` 两侧**索引不同** ⇒ 按**并集**对齐 ✓，且判定用「≠0」✓。

    构造：`a=[1,0]` 在 (a,b)、`b=[1,1]` 在 (b,c) ⇒ 并集 (a,b,c) ✓
    · `a` 位置：只有 a 有值 1、b 缺 ⇒ 按"缺 = 0" ⇒ 1≠0 且 0≠0? ⇒ **0** ✓（下面实测是 1 ⚠）；
    """
    a = pd.Series([1.0, 0.0], index=pd.Index(["a", "b"]))
    b = pd.Series([1.0, 1.0], index=pd.Index(["b", "c"]))
    got = _call("And", (a, b))
    assert list(got.index) == ["a", "b", "c"], "索引必须是并集 ✓（交集会丢掉只在一侧出现的日子 ✗）"
    # ⚠ 实测：`a` 位置得 **1** 而不是 0 ✓ —— 因为对齐后 `a` 那边的 `b` 值是 **NaN** ✓，
    #   而 `NaN != 0` 为真 ✓ ⇒ NaN 被当成"有值" ✓（这是布尔算子的定义 ✓，不是 bug ✓，
    #   但**写下来**：若哪天想让它"缺值 ⇒ 假"，得先改这里 ✓）。
    assert list(got) == [1.0, 0.0, 1.0]


def test_or_with_different_index_aligns_on_union():
    """⭐⭐ `Or` 同样按并集 ✓；`b` 位置两边都非零 ⇒ 1 ✓，`a` 位置靠 NaN 判真 ⇒ 1 ✓。"""
    a = pd.Series([1.0, 0.0], index=pd.Index(["a", "b"]))
    b = pd.Series([1.0, 1.0], index=pd.Index(["b", "c"]))
    got = _call("Or", (a, b))
    assert list(got.index) == ["a", "b", "c"]
    assert list(got) == [1.0, 1.0, 1.0]


# ---------------------------------------------------------------- 一元：常量入参
def test_unary_with_constant_raises_with_operator_name():
    """⭐ 一元算子传常量 ⇒ 报「**参数不能是常量**」且**带算子名** ✓
    （不带的话用户看到 `'float' has no attribute 'abs'` 这种毫无关系的话 ✗）。"""
    with pytest.raises(ValueError) as ei:
        _call("Log", (3.0,))
    msg = str(ei.value)
    assert "常量" in msg and "Log" in msg, msg
