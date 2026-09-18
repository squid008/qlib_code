# -*- coding: utf-8 -*-
"""锁 `adjust_expr` 的价格口径（v1.19.97）。

- `none`     → 真实价 `($close/$factor)`
- `backward` → 原生 `$close`（后复权 ✓ 收益率口径 ✓）
- `forward`  → **真前复权** `Add($preclose,0)` —— `$preclose` 是**物化字段**（= `$close/factor_last` ✓，
  由 `ai_test/build_preclose.py` 生成、qlib 自动映射 ✓）；**必须包一层** ✗：
  裸字段名 `$preclose` 会被 qlib/面板层规范化 ⇒ 与**按位置对齐**的 `all_cols` 错位
  ⇒ 因子列拿到价格列 ⇒ 数值爆炸（实测 nav `1.86e32`、年化 172 万倍 ✗）；`Add(...,0)` 恒等但列名唯一 ✓。

演进（2026-09-18）：① 原实现 forward 原样返回 ⇒ 按**后复权**排序 ✗（K=20 +9.03% vs 米筐 −5.24% ✗）；
② 先借用真实价 ✓（排序修好 ✓）；③ 现升级为**真前复权**（物化 `$preclose` ✓ 实测零报错 ✓）。
"""
from app.engine.adjust import adjust_expr, normalize_mode


def test_none_is_real_price():
    assert adjust_expr("$close", "none") == "($close/$factor)"
    assert adjust_expr("$close", "none", round_prices=True) == "ROUND(($close/$factor),2)"


def test_backward_is_native():
    assert adjust_expr("$close", "backward") == "$close"
    assert adjust_expr("$close", "backward", round_prices=True) == "$close"


def test_forward_is_true_pre_adjust_and_wrapped():
    """★ 核心：forward = 物化前复权价，且**必须带包装**（裸字段会错位爆炸 ✗）。"""
    assert adjust_expr("$close", "forward") == "Add($preclose,0)"
    assert adjust_expr("Mean($close, 20)", "forward") == "Mean(Add($preclose,0), 20)"


def test_forward_other_price_fields_still_real_price():
    """`$open/$high/$low` 暂无物化前复权字段 ⇒ 仍走真实价 ✓（待按同法补物化 ✓）。"""
    for f in ("$open", "$high", "$low"):
        assert adjust_expr(f, "forward") == "(%s/$factor)" % f, f


def test_invalid_mode_falls_back_none():
    assert normalize_mode("xxx") == "none"
    assert adjust_expr("$close", "xxx") == "($close/$factor)"


def test_no_partial_token_match():
    out = adjust_expr("$close_x + $close", "forward")
    assert "$close_x" in out                       # 同前缀字段不误伤 ✓
    assert "Add($preclose,0)" in out
