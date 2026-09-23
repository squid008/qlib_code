# -*- coding: utf-8 -*-
"""★ 算子名**守卫测试**（v1.20.54）：`codegen` 映射出的每个 qlib 算子名，**必须真的已注册** ✓。

背景（2026-09-23 用户报障「强龙起势：The operator [Sqrt] is not registered」）：
`codegen` 里有映射**指向不存在的算子** ✗ —— `SQRT → "Sqrt"`（qlib 无 `Sqrt` ✗）、
`MOD → "Mod"`（qlib 无 `Mod` ✗），历史上还有过 `POW → "Pow"`（已修 ✓）。
⚠ 这类错误**极隐蔽** ✗：**编译期一切正常**（公式能保存 ✓、`/translate` 也 200 ✓），
只有**真正求值**时才抛 `is not registered` ✗ —— 用户是在跑回测/看特征时才发现 ✓。
⇒ 本测试把"映射名 ⊆ 注册表"钉死 ✓，以后再加/改算子立刻会红 ✓。

⚠ 审计方法上**踩过两个坑**（所以实现长这样 ✓，别改回去 ✗）：
  ① **不能**用 `dir(qlib.data.ops)` 判断 ✗ —— 那只是 qlib **模块属性**，本项目外挂算子
     （`ops_ext` 的 `Exp`/`Sqrt`/`DYN_*` …）不在里面 ⇒ 会把 `Exp` 误判成不存在 ✗；
  ② **不能**只 `ensure_ops_registered()` 就查 ✗ —— `Operators` 注册表在 `qlib.init()` /
     `register_all_ops` **之前是空的** ⇒ 连 `Add`/`Gt` 都会被误判 ✗（第一次审计就栽这 ✓）。
  ⇒ 正确：先 `register_all_ops(qlib.data.ops)`（**不需要真实数据** ⇒ CI 可跑 ✓）再 `hasattr` ✓。
"""
import numpy as np
import pandas as pd

from app.factors.parser import codegen as CG


def _mapped_names() -> set:
    """`codegen` 里可能出现在生成表达式中的算子名（字段 `$xxx` / 文档串不算 ✓）。"""
    names = set(CG.BINOP_MAP.values()) | set(CG.FUNC_QLIB.values()) | set(CG.UNARY_MAP.values())
    for attr in dir(CG):
        d = getattr(CG, attr, None)
        if isinstance(d, dict) and attr.isupper():
            names |= {v for v in d.values() if isinstance(v, str)}
    return {n for n in names if n and not n.startswith("$") and "（" not in n}


def _prepare_registry():
    """注册 qlib 内置 + 本项目外挂算子（无需真实行情数据 ✓）。"""
    import qlib.data.ops as _ops
    from app.factors.ops_ext import ensure_ops_registered

    ensure_ops_registered(force=True)
    _ops.register_all_ops(_ops)


class _FakeExpr:
    """给算子做**单元求值**用的最小桩（只需 `.load()` 与 `get_*` ✓）。"""

    def __init__(self, arr):
        self.arr = np.asarray(arr, dtype=float)

    def load(self, *_a, **_k):
        return pd.Series(self.arr)

    def get_longest_back_rolling(self):
        return 0

    def get_extended_window_size(self):
        return (0, 0)


# ---------------- ① 名字层：映射必须全部已注册 ----------------

def test_mapped_names_are_registered():
    """★ 核心守卫：`codegen` 映射出的算子名必须都在 qlib 注册表里 ✓。"""
    from qlib.data.ops import Operators

    _prepare_registry()
    missing = sorted(n for n in _mapped_names() if not hasattr(Operators, n))
    assert not missing, (
        "这些算子名在 codegen 里有映射，但 qlib 注册表里**不存在** ⇒ 公式一求值就报 "
        "`The operator [X] is not registered`：\n  " + "、".join(missing)
        + "\n⇒ 要么改映射到已注册名，要么在 `app/factors/ops_ext.py` 注册它并加进 `_ALL_OPS`。"
    )


def test_historically_broken_names_are_ok():
    """回归：历史坏过的名字都要在 ✓（`Sqrt`/`Mod` 由 ops_ext 提供、`Power` 是 qlib 内建 ✓）。"""
    from qlib.data.ops import Operators

    _prepare_registry()
    for n in ("Sqrt", "Mod", "Power", "Exp", "Log"):
        assert hasattr(Operators, n), "算子 %s 未注册" % n


def test_neg_is_not_used_by_codegen():
    """`Neg` 不该被生成（qlib 无 `Neg` ✗）：负数走**字面量**或 `Mul(-1,X)` ✓。"""
    from app.factors.parser import translate_formula

    assert "Neg" not in _mapped_names()
    assert translate_formula("OUT:-100*(CLOSE-OPEN);").expression == "Mul(-100,Sub($close,$open))"
    assert translate_formula("OUT:-(CLOSE+OPEN);").expression == "Mul(-1,Add($close,$open))"
    assert "Neg(" not in translate_formula("OUT:-(MA(CLOSE,5)*2);").expression


# ---------------- ② 翻译层：SQRT / MOD 映射正确 ----------------

def test_sqrt_translate():
    from app.factors.parser import translate_formula

    assert translate_formula("OUT:SQRT(CLOSE);").expression == "Sqrt($close)"
    assert translate_formula("OUT:SQRT(SQRT(CLOSE*CLOSE));").expression == "Sqrt(Sqrt(Mul($close,$close)))"


def test_mod_translate():
    from app.factors.parser import translate_formula

    assert translate_formula("OUT:MOD(CLOSE,5);").expression == "Mod($close,5)"
    assert translate_formula("OUT:MOD(CLOSE,MA(CLOSE,5));").expression == "Mod($close,Mean($close,5))"


# ---------------- ③ 求值层：语义（含 fmod vs np.mod 的差别） ----------------

def test_sqrt_op_values():
    from app.factors.ops_ext import Sqrt

    out = Sqrt(_FakeExpr([4.0, 9.0, np.nan, -1.0]))._load_internal("x", 0, 4)
    assert out.iloc[0] == 2.0
    assert out.iloc[1] == 3.0
    assert np.isnan(out.iloc[2]), "NaN（停牌）必须保持 NaN ✓"
    assert np.isnan(out.iloc[3]), "负值 ⇒ NaN（与 numpy/通达信一致 ✓）"


def test_mod_sign_follows_dividend():
    """★ 关键语义：`MOD` 的**符号随被除数**（通达信/益盟口径 ✓ = `np.fmod` ✓）。

    ⚠ 若误用 `np.mod`/`%` ✗ ⇒ `MOD(-7,3)` 会得到 **2**（符号随除数 ✗）⇒ 手册/通达信都不一致 ✗。
    """
    from app.factors.ops_ext import Mod

    out = Mod(_FakeExpr([-7.0, 7.0, -7.0, np.nan]), _FakeExpr([3.0, 3.0, 0.0, 3.0]))._load_internal(
        "x", 0, 4)
    assert out.iloc[0] == -1.0, "符号随被除数 ✓（np.mod 会给 2 ✗）"
    assert out.iloc[1] == 1.0
    assert np.isnan(out.iloc[2]), "B=0 ⇒ NaN（不抛异常 ✓）"
    assert np.isnan(out.iloc[3]), "任一 NaN ⇒ NaN ✓"
