# -*- coding: utf-8 -*-
"""字段名收集 + `_apply_node` 的标量与错误分支（★ v2.10.74，2026-10-21 ✓ —— 技术债 #2 第七刀）。

## `_collect_field_names` 决定"面板要读哪些列"，错了后果很隐蔽

它把公式里所有 `$field` 收集起来，交给 `_union_layout` 算**行集并集** ✓。两个要点：

1. **筹码字段是计算字段** ✓（`chip_*` 没有对应 `.bin` 文件 ✓）⇒ 必须把它依赖的
   **7 个基础字段**（close/high/low/volume/amount/factor/market_cap ✓）一起纳入 union ✓，
   否则"该股有行情但字段没被 union 覆盖" ⇒ **莫名 NaN** ✓（只在部分股票上出现 ✗）。
2. **一个字段都没收着 ⇒ 兜底 `("close",)`** ✓ —— 否则 union 为空 ⇒ **整块面板空** ✓
   （公式明明写对了 ✗）。

## `_apply_node` 的标量快路径：`_BIN_ELEM` / `_BIN_CMP`

`Add/Sub/Mul/Div/Power` + `Greater(=max)/Less(=min)` + `Gt/Ge/Lt/Le/Eq/Ne` ✓。
⚠ 两侧**都是标量**时走一条独立分支 ✓（省掉建 Series 的开销 ✓）——
它与"至少一侧是 Series"那条是**两套实现** ✓，最怕一边对一边错 ✗。
"""
from __future__ import annotations

from collections import OrderedDict

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as P

# ---------------------------------------------------------------- _collect_field_names


def test_collect_falls_back_to_close_when_empty():
    """⭐⭐ 一个字段都没收着 ⇒ **兜底 `("close",)`** ✓ ——
    否则 union 为空 ⇒ 整块面板空 ✓（公式写对了却什么都算不出来 ✗）。"""
    assert P._collect_field_names([]) == ("close",)
    assert P._collect_field_names([("1+2", "常量公式")]) == ("close",)


def test_collect_dedups_and_keeps_order():
    """去重且**保序** ✓（顺序影响 union 计算顺序 ✓，虽然结果等价，但稳定性有价值 ✓）。"""
    got = P._collect_field_names([("$close+$volume", "a"), ("$close", "b")])
    assert got == ("close", "volume")


def test_collect_expands_chip_field_to_base_fields():
    """⭐⭐⭐ **筹码字段必须展开成 7 个基础字段** ✓，且**不含 `chip_cost` 本身** ✓
    （它没有 `.bin` 文件 ✓，纳入 union 只会让布局里多一个永远读不到的列 ✗）。
    """
    got = P._collect_field_names([("$chip_cost", "筹码成本")])
    assert "chip_cost" not in got, "计算字段本身不是 bin 文件 ✓"
    for b in ("close", "high", "low", "volume", "amount", "factor", "market_cap"):
        assert b in got, "必须纳入基础字段 ✓（少一个 => 该字段的覆盖区间不参与 union => 莫名 NaN ✗）"


def test_collect_mixes_chip_and_plain_fields():
    """混合场景 ✓：`$chip_cost + $limit_up` ⇒ 7 个基础字段 **＋** `limit_up` ✓。"""
    got = P._collect_field_names([("$chip_cost+$limit_up", "a")])
    assert "limit_up" in got and "chip_cost" not in got
    assert len(got) == 8, "7 个基础 + limit_up ✓（重复的 close 只算一次 ✓）"


def test_collect_ignores_non_field_dollar():
    """`$` 后面不是合法标识符（如 `$1abc` / `$$`）⇒ **不收集** ✓（不许把垃圾当成字段名 ✗）。"""
    assert P._collect_field_names([("$1abc + $$", "x")]) == ("close",)


# ---------------------------------------------------------------- _apply_node 标量快路径
def _ev():
    ev = object.__new__(P.PanelEvaluator)
    ev._node_cache = OrderedDict()
    ev._node_bytes = 0
    ev._active_cache = {}
    ev._active_pos = {}
    return ev


@pytest.mark.parametrize("op,a,b,want", [
    ("Add", 1.0, 2.0, 3.0),
    ("Sub", 7.0, 2.0, 5.0),
    ("Mul", 6.0, 7.0, 42.0),
    ("Div", 7.0, 2.0, 3.5),
    # ⚠ 是 `Pow` / `Power` ✓，**不是** `POW` ✗（`_BIN_ELEM` 的键就是这两个 ✓）
    ("Pow", 2.0, 3.0, 8.0),
])
def test_scalar_elem_ops(op, a, b, want):
    """⭐⭐ **两侧都是标量**时逐项正确 ✓ —— 这条快路径与 Series 路径是**两套实现** ✓，
    一边对一边错是最难发现的 ✓。"""
    got = _ev()._apply(op, [a, b], sr=False)
    assert float(got) == pytest.approx(want), op


@pytest.mark.parametrize("op,a,b,want", [
    ("Greater", 3.0, 5.0, 5.0),      # Greater = 逐点取**大** ✓（不是 a>b ✗）
    ("Less", 3.0, 5.0, 3.0),         # Less    = 逐点取**小** ✓
])
def test_scalar_greater_less_are_elementwise_minmax(op, a, b, want):
    """⚠ `Greater`/`Less` 在 qlib 表达式树里是**逐点取大/小**（= `MAX`/`MIN` 函数 ✓），
    **不是**布尔比较 ✗ —— 比较是 `Gt`/`Lt` ✓。弄混 ⇒ 本该出 0/1 的地方出一个价格 ✓。"""
    assert float(_ev()._apply(op, [a, b], sr=False)) == pytest.approx(want)


@pytest.mark.parametrize("op,a,b,want", [
    ("Gt", 2.0, 1.0, 1.0),
    ("Ge", 1.0, 1.0, 1.0),
    ("Lt", 2.0, 1.0, 0.0),
    ("Le", 2.0, 1.0, 0.0),
    ("Eq", 1.0, 1.0, 1.0),
    ("Ne", 1.0, 1.0, 0.0),
])
def test_scalar_comparisons_return_float_flags(op, a, b, want):
    """⭐ 比较结果必须是 **float 的 0/1** ✓（回 `bool` 会让下游 `dtype` 变 `object` ✓，
    `rolling` 直接崩 ✗，而报错点离这里很远 ✓）。"""
    got = _ev()._apply(op, [a, b], sr=False)
    assert isinstance(got, (float, np.floating)), op
    assert float(got) == want


def test_trunc_constant_raises():
    """⭐ `TRUNC(常量)` ⇒ 必须报「参数不能是常量」✓ —— 取整一个常数毫无意义 ✓，
    而静默返回它自己会让用户以为"公式生效了"✗。"""
    with pytest.raises(ValueError) as ei:
        _ev()._apply("TRUNC", [1.5], sr=False)
    assert "常量" in str(ei.value)


# ---------------------------------------------------------------- Series 路径（与标量对称）
def test_series_plus_scalar_matches_scalar_rule():
    """⭐ Series+标量 ⇒ 逐元素 ✓，且与标量分支**同一答案** ✓（对称性检查 ✓）。"""
    idx = pd.Index(["a", "b", "c"])
    s = pd.Series([1.0, 2.0, 3.0], index=idx)
    got = _ev()._apply("Add", [s, 2.0], sr=False)
    assert list(np.asarray(got)) == [3.0, 4.0, 5.0]


def test_series_greater_is_elementwise_max():
    """⭐ Series 侧的 `Greater` 也必须逐点取大 ✓（与标量分支一致 ✓，
    否则"加个序列就变语义"✓ —— 这种不一致最难查 ✗）。"""
    idx = pd.Index(["a", "b"])
    s = pd.Series([1.0, 9.0], index=idx)
    got = _ev()._apply("Greater", [s, 5.0], sr=False)
    assert list(np.asarray(got)) == [5.0, 9.0]
