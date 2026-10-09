# -*- coding: utf-8 -*-
"""★ v1.20.83：`COUNT(X, N)` 的语义修复（常量窗口原来**静默算错** ✗）。

【怎么发现的】给用户写"黄金坑选股"公式时做真实数据体检 ✓ —— 公式能编译 ✓，但面板链一跑就报
`ValueError: panel_expr 不支持算子 Count` ✗，顺手去查 qlib `Count` 的定义：
`qlib/data/ops.py: class Count(Rolling)` → `Rolling(feature, N, "count")` =
**窗口内非 NaN 个数** ✗（文档原话 "rolling count of number of non-NaN elements" ✓）。

【为什么这是"静默错"而不是"报错"】条件序列几乎全是 0/1、极少 NaN ⇒ qlib `Count(cond, 30)`
**恒等于 30** ⇒
  · 回测链（走 qlib）把 `COUNT(IS_GOLD_PIT,30)>=15` 算成**恒真** ✗（选股条件白写 ✓）；
  · 单因子测试链（走 panel_expr）直接抛"不支持算子 Count" ✗。
同一个公式换条链换种错法，且都不像"COUNT 写错了" ✓。

【修法】常量窗口的 `COUNT(X,N)` 由 codegen 展开为 `Sum(Gt(Abs(X),0),N)` ✓
（通达信口径 = 窗口内**非 0 且非 NaN** 的天数 ✓，与**变量**窗口走的 `DYN_COUNT` 完全一致 ✓）。
本文件守三件事：
  ① 翻译层：常量窗口必须是那个展开式、且**绝不能**出现 `Count(` ✗；变量窗口仍走 DYN_COUNT ✓；
  ② 数值：`COUNT` 必须给出"真成立的天数"（不是窗口长度 ✗）—— 含 NaN/负数/信号值 2 的用例 ✓；
  ③ **两条链对拍**：panel_expr 的实现 与 qlib 算子实现，在同一输入上必须逐位一致 ✓
     （否则"单因子测试看着行、回测另一回事" ✓——本项目最贵的一类坑 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.parser import CodeGenError, translate_formula
from app.factors.panel_expr import PanelEvaluator, _roll


# ---------------------------------------------------------------------------
# ① 翻译层
# ---------------------------------------------------------------------------

class TestTranslate:
    def test_const_window_expands_to_sum_of_abs_gt(self):
        r = translate_formula("OUT:COUNT(CLOSE>OPEN,20);")
        assert r.expression == "Sum(Gt(Abs(Gt($close,$open)),0),20)", r.expression

    def test_never_emits_qlib_count(self):
        """★ 核心回归：`Count(` 是**非 NaN 计数** ✗ ⇒ 生成结果里绝不能出现它 ✓。"""
        for text in ("OUT:COUNT(CLOSE>OPEN,20);",
                     "OUT:COUNT(CLOSE>MA(CLOSE,5),34);",
                     "参数 N=30;\nOUT:COUNT(C>O,N);"):
            expr = translate_formula(text).expression
            assert "Count(" not in expr, (text, expr)

    def test_param_window_is_const_folded(self):
        """`参数 N=30;` ⇒ 窗口是**常量表达式** ✓ ⇒ 同样走展开式（不是 DYN_COUNT ✓）。

        ⚠ `参数` 声明必须**独占一行**（与库里 `调整后纯度` 的写法一致 ✓）；
          写成 `参数 N=30; OUT:...;` 同一行会被解析器当成赋值语句报 ParseError ✓。
        """
        r = translate_formula("参数 N=30;\nOUT:COUNT(C>O,N);")
        assert r.expression == "Sum(Gt(Abs(Gt($close,$open)),0),30)"

    def test_variable_window_still_dyn_count(self):
        """变量窗口语义不变 ✓（本来就走 DYN_COUNT，口径与非 0 计数一致 ✓）。"""
        r = translate_formula("OUT:COUNT(CLOSE>OPEN,BARSLAST(CLOSE>OPEN)+1);")
        assert r.expression.startswith("DYN_COUNT(")

    def test_arg_count_still_guarded(self):
        with pytest.raises(CodeGenError):
            translate_formula("OUT:COUNT(CLOSE>OPEN);")


# ---------------------------------------------------------------------------
# ②/③ 数值：panel 实现 vs qlib 实现（同一份输入，逐位一致）
# ---------------------------------------------------------------------------

VALS = np.array([0.0, 1.0, np.nan, 2.0, -1.0, 0.0, 3.0, np.nan, 1.0, 0.0])
N = 4


def _panel_idx(n: int) -> pd.MultiIndex:
    """⚠ 面板求值器的行索引必须是 **(instrument, datetime) 多级索引** ✗ ——
    滚动算子是**按段（每股一段）**做的（`_seg_roll` 从 level=0 切段 ✓）；
    喂普通 RangeIndex 会被当成"每行一段" ⇒ 每个窗口只含自己 1 行 ⇒ 结果全错 ✗
    （第一版用例就栽在这里 ✓：`Sum(ones,30)` 返回全 1 而不是 1..20 ✓）。
    """
    return pd.MultiIndex.from_arrays(
        [["A"] * n, pd.date_range("2025-01-01", periods=n, freq="B")],
        names=["instrument", "datetime"])


def _panel_count(vals: np.ndarray, n: int) -> np.ndarray:
    """走 **panel_expr 自己的算子实现**（`_apply` 的 Gt/Abs 分支 + `_roll` 的 sum 分支 ✓）。

    用 `__new__` 造实例：`_apply` 对这几个算子只依赖模块级函数（`_roll`/`_align`/ufunc ✓），
    不需要构造时的面板布局 ✓（真实 `__init__` 要读盘建索引 ✗，单测里没必要 ✓）。
    """
    ev = PanelEvaluator.__new__(PanelEvaluator)
    s = pd.Series(vals.astype(float), index=_panel_idx(len(vals)))
    absed = ev._apply("Abs", [s], sr=False)
    cond = ev._apply("Gt", [absed, 0], sr=False)
    return ev._apply("Sum", [cond, n], sr=False).to_numpy()


def _qlib_count(vals: np.ndarray, n: int) -> np.ndarray:
    """走 **qlib 真算子**：`Sum(Gt(Abs(X),0),N)`（用桩 feature 喂数据 ✓，不碰真实行情 ✓）。"""
    from qlib.data.ops import Abs, Sum

    from app.factors import ops_ext

    class _Leaf:
        def __init__(self, arr):
            self.arr = np.asarray(arr, dtype=float)

        def load(self, instrument, start_index, end_index, *args):
            return pd.Series(self.arr)

        def get_longest_back_rolling(self):
            return 0

        def get_extended_window_size(self):
            return (0, 0)

    leaf = _Leaf(vals)
    out = Sum(ops_ext.Gt(Abs(leaf), 0), n)._load_internal("X", 0, len(vals) - 1)
    return np.asarray(out, dtype=float)


class TestNumPySemantics:
    def test_panel_matches_qlib(self):
        """★ 两条链逐位一致（含 NaN 行 ✓）—— 不一致就是"单因子测试 ≠ 回测" ✗。"""
        p = _panel_count(VALS, N)
        q = _qlib_count(VALS, N)
        np.testing.assert_allclose(p, q, rtol=0, atol=0)
        assert not np.isnan(p).any(), "该展开式不应产生 NaN（NaN 输入记 0 ✓）"

    def test_counts_true_days_not_window_length(self):
        """★ 治本断言：结果必须是**真成立的天数**，不是"窗口长度" ✗。

        VALS 前 4 天 非 0 且非 NaN 的有：[0]=0 ✗、[1]=1 ✓、[2]=NaN ✗、[3]=2 ✓ ⇒ 2 天。
        （旧实现 qlib `Count` 会在这里给 4 = 窗口长度 ✗✗。）
        """
        p = _panel_count(VALS, N)
        assert p[3] == 2.0, p[:4]
        assert p[0] == 0.0                       # 首日：值 0 ⇒ 0 天 ✓
        assert p[4] == 3.0                       # 窗口 [1,2,3,4] ⇒ 1,2,-1 三个非 0 ✓（NaN 不算 ✓）
        # 末位窗口 [6,7,8,9] = 3, NaN, 1, 0 ⇒ 2 天 ✓
        assert p[-1] == 2.0

    def test_threshold_is_not_always_true(self):
        """★ 用户场景回归：`COUNT(条件,30)>=15` 必须**可能为假** ✓。

        旧口径下它恒真（恒等于窗口长度 30 ✗）⇒ 选股条件静默失效 ✗。
        """
        few = np.array([1.0, 0.0, 0.0, 1.0, 0.0, 0.0])           # 只有 2 天成立
        assert (_panel_count(few, 30) >= 15).sum() == 0
        many = np.ones(20)                                        # 逐日累计 1..20 ⇒ 只有后 6 天 ≥15 ✓
        assert (_panel_count(many, 30) >= 15).sum() == 6
        assert _panel_count(many, 30)[-1] == 20.0

    def test_nan_days_not_counted(self):
        allnan = np.array([np.nan] * 5)
        np.testing.assert_allclose(_panel_count(allnan, 5), np.zeros(5))

    def test_legacy_qlib_count_is_compatible_in_panel(self):
        """兼容路径：旧缓存里若还有 `Count(X,N)` ⇒ panel 不再报"不支持算子" ✗，
        且口径与 qlib 一致（= 非 NaN 计数 ✓，两边一样"错得一致" ✓）。"""
        s = pd.Series(VALS, index=_panel_idx(len(VALS)))
        got = _roll(s, "count", N, sr=False).to_numpy()
        # 非 NaN 计数：第 3 位窗口 [0,1,NaN,2] ⇒ 3 个非 NaN ✓
        assert got[3] == 3.0
        from qlib.data.ops import Count
        exp = Count(_QLibLeaf(VALS), N)._load_internal("X", 0, len(VALS) - 1)
        np.testing.assert_allclose(got, np.asarray(exp, dtype=float), equal_nan=True)


class _QLibLeaf:
    def __init__(self, arr):
        self.arr = np.asarray(arr, dtype=float)

    def load(self, instrument, start_index, end_index, *args):
        return pd.Series(self.arr)

    def get_longest_back_rolling(self):
        return 0

    def get_extended_window_size(self):
        return (0, 0)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
