# -*- coding: utf-8 -*-
"""`EXP(X)` 支持回归（v1.20.34，用户 2026-09-20 要求）。

要点：
- **编译**：`EXP(...)` 不再报 `CodeGenError: 不支持的函数：EXP` ✓；
- **面板层**（单因子测试路径）：`_UNARY` 里注册了 `Exp` ⇒ 走 `np.exp` **向量化** ✓
  （与 `Sqrt`/`Log` 同路径、同量级 ⇒ 不会变慢 ✓）；
- **qlib 层**（多因子训练 / 回测路径）：`ops_ext.Exp` 已注册，`_load_internal` 只调一次
  `np.exp` ✓ **不是**逐点 Python 循环、**不**走 `PATCH:` 补丁通道 ✓；
- **数值**：`EXP(LOG(x)) == x`（浮点近似）；溢出 → inf（由 CleanInf 清洗 ✓）。
"""
import numpy as np
import pandas as pd
import pytest

from app.factors import ops_ext
from app.factors.parser import translate_formula


def test_exp_compiles():
    """★ `EXP(...)` 必须能编译（此前直接 CodeGenError ✗）。"""
    t = translate_formula("X:EXP(CLOSE);")
    assert "Exp(" in t.expression, t.expression
    assert t.name == "X"


def test_exp_registered_panel_and_qlib():
    """两侧都要注册：面板 `_UNARY`（单因子 ✓）+ qlib `_ALL_OPS`（训练/回测 ✓）。"""
    from app.factors.panel_expr import _UNARY
    assert _UNARY.get("Exp") == "exp", "面板层必须映射到 np.exp（向量化 ✓）"

    ops_ext.ensure_ops_registered()
    assert ops_ext.Exp in ops_ext._ALL_OPS, "qlib 层必须注册 Exp（否则训练/回测报 operator 未注册 ✗）"


def test_exp_qlib_load_is_vectorized():
    """`Exp._load_internal` 直接对整条 Series 调 `np.exp`（一次 ✓），不是逐点循环 ✓。"""
    from qlib.data.base import ExpressionOps

    class _Leaf(ExpressionOps):
        def __init__(self, s):
            self._s = s
            super().__init__()

        def _load_internal(self, instrument, start_index, end_index, *args):
            return self._s

        def get_longest_back_rolling(self):
            return 0

        def get_extended_window_size(self):
            return 0, 0

    idx = pd.date_range("2021-01-04", periods=5, freq="D")
    s = pd.Series([0.0, 1.0, 2.0, np.nan, -1.0], index=idx)
    out = ops_ext.Exp(_Leaf(s))._load_internal("SH600000", 0, 4)

    assert isinstance(out, pd.Series)
    assert out.index.equals(idx)
    exp = np.exp(s.to_numpy(dtype=float))
    assert np.allclose(out.to_numpy(dtype=float), exp, equal_nan=True)


def test_exp_log_roundtrip():
    """`EXP(LOG(x)) ≈ x`（研报里 `EXP(SUM(LOG(1+r),N))` 的复利还原靠这条 ✓）。"""
    idx = pd.date_range("2021-01-04", periods=4, freq="D")
    s = pd.Series([1.0, 1.5, 2.0, 3.0], index=idx)

    class _Leaf:
        def __init__(self, v):
            self._v = v

        def load(self, *a):
            return self._v

    from app.factors.ops_ext import Exp
    exp_op = Exp(_Leaf(np.log(s.to_numpy(dtype=float))))
    out = exp_op._load_internal("X", 0, 3)
    assert np.allclose(out, s.to_numpy(dtype=float), rtol=1e-12)


def test_exp_overflow_is_inf_not_error():
    """输入过大 ⇒ `inf`（**不抛异常** ✓，交由既有 CleanInf 处理器清洗 ✓）。"""
    class _Leaf:
        def __init__(self, v):
            self._v = v

        def load(self, *a):
            return self._v

    out = ops_ext.Exp(_Leaf(np.array([1000.0, 0.0])))._load_internal("X", 0, 1)
    assert out[0] == np.inf and out[1] == pytest.approx(1.0)


def test_exp_in_formula_with_dyn_sum():
    """端到端形态（用户那条 Alpha14 严格等价版 ✓）：`EXP(DYN_SUM(R, BARSCOUNT(CLOSE)))`。"""
    txt = ("R:=IF(CLOSE>REF(CLOSE,1),LOG(CLOSE/REF(CLOSE,1)),0);\n"
           "ALPHA14:EXP(DYN_SUM(R,BARSCOUNT(CLOSE)));")
    t = translate_formula(txt)
    assert t.name == "ALPHA14"
    assert "Exp(" in t.expression and "DYN_SUM(" in t.expression
