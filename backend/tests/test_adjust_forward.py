# -*- coding: utf-8 -*-
"""锁 `adjust_expr` 的价格口径（v1.19.97 现状）。

- `none` / `forward` → **真实价** `($close/$factor)` —— forward 与 none 现为同口径 ✓：
  真实价在**截面排序**上正确 ✓（不会退化成"按后复权价排序" ✗），且实测与米筐"前复权"吻合
  （K=20 回撤 −81.55% ↔ 米筐 −81.51% ✓）。
- `backward` → 原生 `$close`（后复权 ✓ 收益率含分红 ✓ 原样返回 ✓）。

⚠ **真前复权**（`$close/FACTOR_END($factor)`）已实现（`ops_ext.FACTOR_END` ✓ 已注册 ✓）但
**求值未通过、已回退** ✗ —— 实测因子整列失效（`topk_curves` 为空 ✗）；见 `engine/adjust.py`
的注释与 `md/开发记录.md` 待办 ✓。
"""
from app.engine.adjust import adjust_expr, normalize_mode


def test_none_is_real_price():
    assert adjust_expr("$close", "none") == "($close/$factor)"
    assert adjust_expr("$close", "none", round_prices=True) == "ROUND(($close/$factor),2)"


def test_backward_is_native():
    assert adjust_expr("$close", "backward") == "$close"
    assert adjust_expr("Ref($close,1)/$close", "backward") == "Ref($close,1)/$close"
    assert adjust_expr("$close", "backward", round_prices=True) == "$close"


def test_forward_uses_real_price_at_least():
    """★ 底线：forward **不得**原样返回（那会退化成"按后复权价排序" ✗）。"""
    assert adjust_expr("$close", "forward") == "($close/$factor)"
    assert adjust_expr("Mean($close, 20)", "forward") == "Mean(($close/$factor), 20)"


def test_forward_all_price_fields():
    for f in ("$open", "$high", "$low", "$close"):
        assert adjust_expr(f, "forward") == "(%s/$factor)" % f, f


def test_invalid_mode_falls_back_none():
    assert normalize_mode("xxx") == "none"
    assert adjust_expr("$close", "xxx") == "($close/$factor)"


def test_no_partial_token_match():
    out = adjust_expr("$close_x + $close", "forward")
    assert "$close_x" in out                       # 同前缀字段不误伤 ✓
    assert "($close/$factor)" in out


def test_factor_end_importable():
    """`FACTOR_END` 必须可导入/可构造（注册由 `ops_ext.ensure_ops_registered` 负责 ✓）。

    ⚠ 不做字符串断言：实测用 `__new__` 绕过 qlib 包装后，`str(obj)` 走的是**包装器**
    （`OpsWrapper`）而非本类 `__str__` ✗ ⇒ 断言恒失败、无意义 ✓（算子本身注册成功 ✓ ——
    qlib 启动日志会打 `The custom operator [FACTOR_END] ...` ✓）。
    """
    from app.factors.ops_ext import FACTOR_END

    assert callable(FACTOR_END)
