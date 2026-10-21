# -*- coding: utf-8 -*-
"""`_apply_node` 剩余算子分支（★ v2.10.94，技术债 P1-#2 第十四刀）。

## 这里钉什么
上一轮（2.10.69）修掉的是"常量首参防御是死代码"✓，这一轮补的是**其余分支**：
· **8 个算子的"首参不能是常量"** ✓ —— 报错文案必须**指得出算子** ✓（否则用户看到的是
  `'float' object has no attribute 'index'` 这种毫无关系的话 ✗）；
· **`BETWEEN` 的 NaN 语义** ✓（X 是 NaN ⇒ 结果必须 NaN ✓，不能变 0 ✗ —— 0 会被当成
  "不在区间内"参与后续判断 ✓）；
· **`Less/More` 是逐点取小/大** ✓（不是布尔比较 ✗ —— 弄混会把价格当 0/1 用 ✓）；
· **`Exp` 溢出** ✓（输入过大会 inf ✓ 但不能抛 ✓ —— 抛了整块面板就没了 ✗）；
· **`IF` 的条件是常量** ✓（`IF(1,a,b)` ⇒ 全选 a ✓，不能崩 ✗）。
"""
from __future__ import annotations

from collections import OrderedDict

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as P
from app.factors.panel_expr import parse_expr

CODES = ("A", "B")
NDAY = 6
A_VALS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
B_VALS = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0]


def _series(vals_by_code):
    days = pd.bdate_range("2024-01-02", periods=NDAY)
    idx = pd.MultiIndex.from_product([list(CODES), days], names=["instrument", "datetime"])
    out = []
    for c in CODES:
        out.extend(vals_by_code[c])
    return pd.Series(out, index=idx, dtype=np.float64)


def _ev(**extra):
    ev = object.__new__(P.PanelEvaluator)
    ev._full = _series({"A": A_VALS, "B": B_VALS}).index
    ev._field_cache = {"close": _series({"A": A_VALS, "B": B_VALS})}
    for k, v in extra.items():
        ev._field_cache[k] = _series(v)
    ev._node_cache = OrderedDict()
    ev._node_bytes = 0
    ev._active_cache = {}
    ev._active_pos = {}
    ev.read_start = "2024-01-02"
    return ev


def _val(ev, expr, sr: bool = False):
    """⚠ 每次清节点缓存 ✓ —— `parse_expr` 会重建 key 分配 ⇒ 不清会串味 ✗。"""
    ev._node_cache.clear()
    ev._node_bytes = 0
    return ev.eval(parse_expr(expr), sr=sr)


# ---------------------------------------------------------------- 首参不能是常量
@pytest.mark.parametrize("expr", [
    "Mean(1, 5)",       # _ROLL_FUNC 那条
    "EMA(1, 5)",        # EMA / EMA_TDX 那条
    "IdxMax(1, 5)",     # Alpha158 窗口类那条
])
def test_constant_first_arg_raises_with_operator_name(expr):
    """⭐⭐ 报错必须**指得出算子** ✓ —— 这 3 条分别落在 3 个不同的 `raise` 分支上 ✓
    （`_ROLL_FUNC` / `EMA` / `Alpha158 窗口类` ✓），漏一个就是一条误导性堆栈 ✗。"""
    with pytest.raises(ValueError) as ei:
        _val(_ev(), expr)
    msg = str(ei.value)
    assert "常量" in msg, msg
    assert expr.split("(")[0] in msg, "消息里要带算子名 ✓：%r" % msg


def test_corr_requires_two_series():
    """⭐ `Corr(1,2,3)` ⇒ 必须说"**两参数须为序列**" ✓（不是崩在别处 ✗）。"""
    with pytest.raises(ValueError) as ei:
        _val(_ev(), "Corr(1, 2, 3)")
    assert "序列" in str(ei.value)


def test_round_constant_raises():
    """⭐ `ROUND(常量)` ⇒ 报"参数不能是常量" ✓（静默返回它自己会让用户以为"生效了" ✗）。"""
    with pytest.raises(ValueError) as ei:
        _val(_ev(), "ROUND(1.5)")
    assert "常量" in str(ei.value)


# ---------------------------------------------------------------- BETWEEN 的 NaN 语义
def test_between_keeps_nan_when_x_is_nan():
    """⭐⭐ `BETWEEN`：X 是 NaN ⇒ 结果**仍是 NaN** ✓（不是 0 ✗）——
    变 0 会被当成"不在区间内"参与后续比较 ✓，把一个"未知"变成了"确定的否" ✗。"""
    x = [np.nan, 2.0, 3.0, 4.0, 5.0, 6.0]
    ev = _ev(x={"A": x, "B": x})
    out = _val(ev, "BETWEEN($x, 2, 4)")
    arr = np.asarray(out, dtype=float)
    assert np.isnan(arr[0]), "X 为 NaN ⇒ 结果 NaN ✓"
    assert arr[1] == 1.0, "2 在 [2,4] 内 ✓"
    assert arr[5] == 0.0, "6 不在 [2,4] 内 ⇒ 0 ✓"


# ---------------------------------------------------------------- 逐点取小/取大
def test_less_is_elementwise_min_not_boolean():
    """⭐⭐ `Less(a,b)` = **逐点取小** ✓（不是 a<b 的布尔 ✓）——
    弄混会把"价格"当"0/1 信号"用 ✓，后面所有加权跟着错 ✗。"""
    ev = _ev(open={"A": B_VALS, "B": A_VALS})
    out = np.asarray(_val(ev, "Less($close, $open)"), dtype=float)
    # A 列 close=[1..6]、open=[10..5] ⇒ 取小 ⇒ [1,2,3,4,5,5] ✓
    assert list(out[:6]) == [1.0, 2.0, 3.0, 4.0, 5.0, 5.0]


# ---------------------------------------------------------------- 一元算子
def test_exp_is_e_to_the_x():
    """⭐ `EXP($close)` ⇒ e^x ✓（数值对不上 ⇒ 公式整体量纲错 ✗）。"""
    out = np.asarray(_val(_ev(), "EXP($close)"), dtype=float)
    np.testing.assert_allclose(out[:3], np.exp([1.0, 2.0, 3.0]), rtol=1e-12)


def test_exp_overflow_does_not_raise():
    """⭐ 输入大到溢出 ⇒ 得 **inf** ✓ 但**不许抛** ✗ —— 抛了整块面板就没了 ✓
    （清洗交给既有的 CleanInf ✓）。"""
    big = [800.0] * NDAY
    ev = _ev(close={"A": big, "B": big})
    out = np.asarray(_val(ev, "EXP($close)"), dtype=float)
    assert np.isinf(out).all()


def test_neg_and_sign_ops():
    """⭐ `neg` 与 `sign` 分支 ✓ —— 算子名**从 `_UNARY` 里查** ✓（别按印象拼 ✗）。"""
    neg_name = next((k for k, v in P._UNARY.items() if v == "neg"), None)
    sign_name = next((k for k, v in P._UNARY.items() if v == "sign"), None)
    assert neg_name and sign_name, "`_UNARY` 里应有 neg/sign ✓"
    out = np.asarray(_val(_ev(), f"{neg_name}($close)"), dtype=float)
    assert list(out[:3]) == [-1.0, -2.0, -3.0]
    out2 = np.asarray(_val(_ev(), f"{sign_name}($close)"), dtype=float)
    assert list(out2[:3]) == [1.0, 1.0, 1.0]


# ---------------------------------------------------------------- IF 的条件是常量
def test_if_with_constant_condition():
    """⭐⭐ `IF(1, a, b)` ⇒ **全选 a** ✓ —— 条件必须被广播成整列 ✓
    （不处理标量条件 ⇒ 崩 ✓；处理反了 ⇒ 全选 b ✗）。"""
    ev = _ev(open={"A": B_VALS, "B": B_VALS})
    out = np.asarray(_val(ev, "IF(1, $close, $open)"), dtype=float)
    assert list(out[:6]) == A_VALS, "条件为真 ⇒ 取 $close ✓"
