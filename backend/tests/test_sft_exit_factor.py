# -*- coding: utf-8 -*-
"""单因子测试「**离场因子**」的前置校验（v2.4.3，用户 2026-10-15 拍板 ✓）。

用户当天把 4 条口径一次定了 ✓：
  ① 离场信号 = `因子值 > 阈值（默认 0）`（二值/连续通用 ✓，不写"只能二值"✗）；
  ② **对照列必算** ✓；
  ③ 同一公式既进又出 ⇒ **直接拦** ✓；
  ④ 触发率 > 50% 前端黄字提示 ✓。

本文件只守**③**（在入队前就拒 ✗，别等跑完几分钟才发现 ✓）+ 那条"勾了离场自动开 period_roll"✓。
（②/④ 的数值逻辑在 `test_period_roll.py`（引擎侧 ✓）与 `test_sft_progress.py`（进度 ✓）里 ✓；
  真金白银的端到端由 `factors.py::_panel_period_roll_nav(with_exit=True)` 走同一条 `run_period_roll` ✓。）
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers.factors import (SingleFactorTestFactor, SingleFactorTestRequest,
                                 _apply_exit_validation)


def _req(entry_expr: str = "Add($close,1)", exit_expr: str = "Sub($close,1)",
         period_roll: bool = False) -> SingleFactorTestRequest:
    return SingleFactorTestRequest(
        universe="all", start_date="2024-01-01", end_date="2024-06-30",
        factors=[SingleFactorTestFactor(id="a", name="进场A", expression=entry_expr)],
        exit_factor=SingleFactorTestFactor(id="b", name="离场B", expression=exit_expr),
        period_roll=period_roll,
    )


def test_same_formula_on_both_sides_is_rejected():
    """★ 用户选的"直接拦"：同公式既进又出 ⇒ 400，并把**为什么**写清楚 ✓。"""
    req = _req(entry_expr="Add($close,1)", exit_expr="Add($close,1)")
    with pytest.raises(HTTPException) as ei:
        _apply_exit_validation(req)
    assert ei.value.status_code == 400
    msg = str(ei.value.detail)
    assert "同一个公式" in msg and "净值恒 1" in msg, msg


def test_blank_expression_is_treated_as_not_selected():
    """空表达式 ⇒ 视为"没勾" ✓（前端清理选择时常见 ✓，不能因此 400 ✗）。"""
    req = _req(exit_expr="   ")
    _apply_exit_validation(req)
    assert req.exit_factor is None
    assert req.period_roll is False, "没勾离场 ⇒ 不该顺带打开 period_roll ✓"


def test_exit_turns_on_period_roll():
    """★ 勾了离场 ⇒ **自动打开 `period_roll`** ✓（离场只能作用在真金白银那条回测上 ✓）。"""
    req = _req()
    _apply_exit_validation(req)
    assert req.period_roll is True
    assert req.exit_factor is not None


def test_no_exit_factor_is_noop():
    """不勾离场 ⇒ 一切照旧 ✓（回归保护 ✓）。"""
    req = _req()
    req.exit_factor = None
    _apply_exit_validation(req)
    assert req.exit_factor is None and req.period_roll is False


def test_trailing_whitespace_still_counts_as_same_formula():
    """同公式判定要**先 strip** ✓（前端偶尔带空格/换行 ⇒ 不能因此漏拦 ✗）。"""
    req = _req(entry_expr="Add($close,1) ", exit_expr=" Add($close,1)")
    with pytest.raises(HTTPException):
        _apply_exit_validation(req)
