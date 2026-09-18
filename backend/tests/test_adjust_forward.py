# -*- coding: utf-8 -*-
"""v1.19.96 回归：`adjust_expr(mode="forward")` 必须**替换价格字段**（价格量纲因子按真实价排序）。

背景（2026-09-18 用户实测）：原实现 `forward` **直接 return expr** ✗ ⇒ 平台给"LLT 最小 20 只"
排序用的是 **qlib 原生 `$close`（后复权）** ✗ ⇒ 选出的是"**长期涨幅最小的股票**"而不是"股价最低
的股票" ✗。与米筐 rqalpha（前复权/真实价）对拍：平台 K=20 年化 **+9.03%**、回撤 −54.16%，
米筐 **−5.24%**、回撤 **−81.51%** ✗。修复（forward 与 none 一样替换）后：
K=20 = **−3.86%**、回撤 **−81.55%** ✓（与米筐回撤几乎逐位一致 ✓）。

标尺（米筐真实成交价）：`SH600734` 2021-01-04 `$close`=0.113505 `$factor`=0.096191
⇒ `$close/$factor` = **1.18** = 米筐 1.18 ✓；`SZ000662` ⇒ **0.79** = 米筐 0.79 ✓
⇒ 结论：`$close` = **后复权** ✓、`$close/$factor` = **真实价** ✓。
"""
from app.engine.adjust import adjust_expr


def test_forward_replaces_price_fields():
    """★ 核心：forward 不能再"原样返回"（否则截面排序 = 按后复权价 ✗）。"""
    assert adjust_expr("$close", "forward") == "($close/$factor)"
    assert adjust_expr("Mean($close, 20)", "forward") == "Mean(($close/$factor), 20)"


def test_forward_all_price_fields():
    for f in ("$open", "$high", "$low", "$close"):
        assert adjust_expr(f, "forward") == "(%s/$factor)" % f, f


def test_backward_stays_native():
    """后复权 = 数据原生 `$close` ⇒ 必须原样返回 ✓（收益率口径靠它 ✓）。"""
    assert adjust_expr("$close", "backward") == "$close"
    assert adjust_expr("Ref($close,1)/$close", "backward") == "Ref($close,1)/$close"


def test_none_unchanged():
    assert adjust_expr("$close", "none") == "($close/$factor)"


def test_round_only_applies_to_none():
    """取整：`none` 与现在的 `forward` 都走真实价 ⇒ 都取整；`backward`（原生后复权）不取整 ✓。

    ⚠ 2026-09-18 修正断言：原先写"forward 不取整" ✗ —— 那是 `forward` 还"原样返回"时代的假设；
    现在 forward = 真实价口径 ⇒ 与 none 同样有"整分"语义 ⇒ `ROUND(...,2)` ✓。
    """
    assert adjust_expr("$close", "none", round_prices=True) == "ROUND(($close/$factor),2)"
    assert adjust_expr("$close", "forward", round_prices=True) == "ROUND(($close/$factor),2)"
    assert adjust_expr("$close", "backward", round_prices=True) == "$close"


def test_llt_expression_carries_factor():
    """LLT（价格量纲）在 forward 下必须带 `$factor` —— 正是本次 bug 的表征 ✓。"""
    expr = adjust_expr("EMA($close,30) + $close", "forward")
    assert "$factor" in expr and "$close/$factor" in expr


def test_no_partial_token_match():
    """只替换独立字段 token，不误伤 `$close_x` 这类同前缀字段。"""
    out = adjust_expr("$close_x + $close", "forward")
    assert "$close_x" in out                     # 未被动（不是独立 token ✓）
    assert "($close/$factor)" in out             # 独立 token 被替换 ✓
