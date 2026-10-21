# -*- coding: utf-8 -*-
"""`_apply_node` 的**边界分支**（★ v2.10.69，2026-10-21 ✓ —— 技术债 #17 的修复验收）。

## 修的是什么（两个真 bug）

1. **12 处 `if s is None: raise ValueError("… 首参不能是常量")` 是死代码** ✗
   —— 常量求值是 **`float`**，永远不是 `None` ✓ ⇒ 用户写 `FILTER(1, 5)`（恒定信号）时
   **不报错**，而是往下一路崩出 `AttributeError: 'float' object has no attribute 'index'` ✓
   （一个与"你写错了"毫无关系的消息 ✗）。
2. **`eval` 的缓存收尾假设结果是 `pd.Series`** ✗ ⇒ 常量子树（`POW(2,3)` ✓）算完后
   对 `float` 调 `_series_mem_bytes(out)` ⇒ 读 `out.index` ⇒ **AttributeError** ✓
   ⇒ 表现是"标量在**右边**没事、在**左边**就崩"✓（`$close*(1+2)` 正常 / `MAX(1,2)+…` 崩 ✗），
   这是**最难自查**的那种不对称 ✓。

## ⚠ 两条实测得出的语义（别按直觉写断言）

- **`MAX(a,b)` 是滚动最大值**（`Max` 在 `_ROLL_FUNC` 里 ✓），**不是**"两个数取大" ✗
  ⇒ `MAX(1,2)` 抛「首参不能是常量」是**正确行为** ✓（滚动算子首参必须是序列 ✓）。
  要"取大"请写 `IF($close>3, $close, 3)` ✓ —— 本轮**不测**它（不是这次的主题 ✓）。
- **解析器不认 `>` / `^`** ✗ ⇒ 比较写 `Gt(a,b)` ✓、幂写 `POW(a,b)` ✓。
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


def _ev():
    ev = object.__new__(P.PanelEvaluator)
    ev._full = _series({"A": A_VALS, "B": B_VALS}).index
    ev._field_cache = {"close": _series({"A": A_VALS, "B": B_VALS})}
    ev._node_cache = OrderedDict()
    ev._node_bytes = 0
    ev._active_cache = {}
    ev._active_pos = {}
    ev.read_start = "2024-01-02"
    return ev


def _val(expr):
    return _ev().eval(parse_expr(expr), sr=False)


# ------------------------------------------------ ① 常量首参：必须**报对**的错
@pytest.mark.parametrize("expr,name", [
    ("REF(1, 2)", "Ref"),
    ("FILTER(1, 2)", "FILTER"),
    ("SMA(1, 2, 1)", "SMA"),
    ("BARSCOUNT(1)", "BARSCOUNT"),
    ("BARSSINCE(1)", "BARSSINCE"),
])
def test_constant_first_arg_raises_keyword_error(expr, name):
    """⭐⭐ 修前这些写法的报错是 **`AttributeError: 'float' object has no attribute 'index'`** ✗
    —— 用户完全看不出"我把常量写进了需要序列的位置"✓。

    修后必须是 **`ValueError` + 明确说"首参不能是常量"** ✓（消息里还要带算子名 ✓，
    这样一屏报错里能定位到是哪个算子 ✓）。
    """
    with pytest.raises(ValueError) as ei:
        _val(expr)
    msg = str(ei.value)
    assert "常量" in msg, "报错必须说明**常量不能当序列** ✓（原始 AttributeError 等于没提示 ✗）: " + msg
    assert name in msg, "报错要带算子名 ✓: " + msg


# ------------------------------------------------ ② 标量在左 / 在右（不对称 bug）
def test_scalar_on_the_left_no_longer_crashes():
    """⭐⭐⭐ 修前：标量在**左**侧 ⇒ 缓存收尾对 `float` 读 `.index` ⇒ **AttributeError** ✗。

    `POW(2,3)` 是常量子树 ✓，作为左操作数时结果应为「`$close` 加 8」的序列 ✓。
    """
    got = _val("POW(2,3)+$close")
    assert isinstance(got, pd.Series), "标量在左侧也必须产出序列 ✓（修前这里直接抛 ✗）"
    arr = np.asarray(got)[:NDAY]
    assert list(arr) == [v + 8.0 for v in A_VALS]


def test_scalar_on_the_right_still_works():
    """对称性检查 ✓：标量在**右**侧一直是好的 ✓ —— 修完不能反而弄坏它 ✗。"""
    got = _val("$close*(1+2)")
    assert list(np.asarray(got)[:NDAY]) == [v * 3.0 for v in A_VALS]


def test_pure_constant_expression_returns_scalar():
    """纯常量表达式 ⇒ 合理的标量结果 ✓，且**不进节点缓存** ✓（float 没有 `.index` ✗）。"""
    got = _val("POW(2,3)")
    assert float(got) == pytest.approx(8.0)


# ------------------------------------------------ ③ 比较算子（Gt，不是 `>`）
def test_comparison_operator_returns_0_1_series():
    """⭐ 比较结果必须是 **0/1 的 float 序列** ✓ —— 回 `bool` 会让下游 `dtype` 变 `object` ✓，
    `rolling` 直接崩 ✗（而报错点离这里很远 ✓）。"""
    got = _val("Gt($close,3)")
    arr = np.asarray(got)[:NDAY]
    assert set(np.unique(arr)) <= {0.0, 1.0}
    assert list(arr) == [1.0 if v > 3 else 0.0 for v in A_VALS]
    assert np.asarray(got).dtype == np.float64, "dtype 必须是 float ✓"


# ------------------------------------------------ ④ 陌生算子
def test_unknown_operator_raises_with_its_name():
    """⭐⭐ **陌生算子必须抛且带名字** ✓ —— 静默返回 NaN 的后果最隐蔽 ✓：
    用户会以为"公式对了、只是这段没数据"✗，于是去查数据/查口径 ✓，
    真正的原因（算子不支持）永远找不到 ✓。
    """
    ev = _ev()
    series = ev._field_cache["close"]
    # 走 `_apply`（= 模块级 `_apply_node` 的委托 ✓，args 是**已求值结果** ✓ 不是语法节点 ✗）
    with pytest.raises(ValueError) as ei:
        ev._apply("NO_SUCH_OP_XYZ", [series], sr=False)
    assert "NO_SUCH_OP_XYZ" in str(ei.value), "错误消息必须带算子名 ✓"
