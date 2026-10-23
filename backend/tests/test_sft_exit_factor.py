# -*- coding: utf-8 -*-
"""单因子测试「**离场因子**」前置校验（v2.4.3 / v2.5.0 —— 用户 2026-10-15 拍板 ✓）。

用户当天把口径一次定了 ✓：
  ① 离场信号 = `因子值 > 阈值（默认 0）`（二值/连续通用 ✓，不写"只能二值"✗）；
  ② **对照列必算** ✓；
  ③ 同一公式既进又出 ⇒ **直接拦** ✓；
  ④ 触发率 > 50% 前端黄字提示 ✓；
  ⑤ ★ v2.5.0（\"这个离场因子能不能搞成多选啊，要矩阵化\" ✓）：**多个离场** ⇒
     结果 = `进场 × (1 + 离场数)` 个同口径回测（`1` = 必算的"不离场"对照 ✓）。

本文件守**③④⑤ 的接线**（在入队前就拒/就规范化 ✗，别等跑完几分钟才发现 ✓）：
  · 同公式（含多选里任一）、带空格、空表达式、重复项 ✓；
  · 勾了离场 ⇒ 自动开 `period_roll` ✓；
  · 规范化后**只看 `exit_factors`** ✓（单数形态置 None ✓，下游不会两套口径 ✓）。
引擎语义在 `test_period_roll.py`（T+1 执行 / 顺延 / 调仓日覆盖 / 空转计数 ✓）；
进度模型在 `test_sft_progress.py` ✓。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers.factors import SingleFactorTestFactor, SingleFactorTestRequest, _apply_exit_validation


def _f(name: str, expr: str) -> SingleFactorTestFactor:
    return SingleFactorTestFactor(id=name, name=name, expression=expr)


def _req(entries: list, exits: list = None, single: SingleFactorTestFactor = None,
         period_roll: bool = False) -> SingleFactorTestRequest:
    return SingleFactorTestRequest(
        universe="all", start_date="2024-01-01", end_date="2024-06-30",
        factors=entries, exit_factors=exits or [], exit_factor=single,
        period_roll=period_roll)


def test_same_formula_on_both_sides_is_rejected():
    """★ 用户选的"直接拦"：同公式既进又出 ⇒ 400，并把**为什么**写清楚 ✓。"""
    req = _req([_f("进场A", "Add($close,1)")], [_f("离场B", "Add($close,1)")])
    with pytest.raises(HTTPException) as ei:
        _apply_exit_validation(req)
    assert ei.value.status_code == 400
    msg = str(ei.value.detail)
    assert "同一个公式" in msg and "净值恒 1" in msg, msg


def test_dup_check_covers_every_selected_exit():
    """★ 多选（v2.5.0）：**逐个**离场都要查 ✓ —— 不能只看第一个 ✗（v2.4.3 的洞 ✓）。"""
    req = _req([_f("进场A", "Add($close,1)")],
               [_f("离场B", "Sub($close,1)"), _f("离场C", "Add($close,1)")])
    with pytest.raises(HTTPException):
        _apply_exit_validation(req)


def test_blank_expression_is_dropped_and_same_expr_deduped():
    """空表达式 ⇒ 丢掉 ✓；同一公式选两次 ⇒ **只算一遍** ✓（矩阵里不该出现两列一样的 ✓）。"""
    req = _req([_f("进场A", "Add($close,1)")],
               [_f("空", "   "), _f("离场B", "Sub($close,1)"), _f("离场B2", " Sub($close,1) ")])
    _apply_exit_validation(req)
    assert len(req.exit_factors) == 1, [e.name for e in req.exit_factors]
    assert req.exit_factors[0].expression.strip() == "Sub($close,1)"


def test_single_form_is_normalized_into_the_list():
    """v2.4.3 的**单数**形态保留兼容 ✓：传 `exit_factor` 等于传 `[它]`，且归一后单数字段清空 ✓。"""
    req = _req([_f("进场A", "Add($close,1)")], single=_f("离场B", "Sub($close,1)"))
    _apply_exit_validation(req)
    assert [e.name for e in req.exit_factors] == ["离场B"]
    assert req.exit_factor is None, "归一后只看 exit_factors ✓（避免下游两套口径 ✓）"
    assert req.period_roll is True


def test_multiple_exits_keep_order_and_turn_on_period_roll():
    """★ 多选保序 ✓（矩阵列序 = 你勾的顺序 ✓）+ 自动开 `period_roll` ✓。"""
    req = _req([_f("进场A", "Add($close,1)")],
               [_f("离场1", "Sub($close,1)"), _f("离场2", "Mul($close,2)"),
                _f("离场3", "Div($close,3)")])
    _apply_exit_validation(req)
    assert [e.name for e in req.exit_factors] == ["离场1", "离场2", "离场3"]
    assert req.period_roll is True


def test_no_exit_factor_is_noop():
    """不勾离场 ⇒ 一切照旧 ✓（回归保护：别把 period_roll 误开 ✗）。"""
    req = _req([_f("进场A", "Add($close,1)")])
    _apply_exit_validation(req)
    assert req.exit_factors == [] and req.exit_factor is None
    assert req.period_roll is False


def test_trailing_whitespace_still_counts_as_same_formula():
    """同公式判定要**先 strip** ✓（前端偶尔带空格/换行 ⇒ 不能因此漏拦 ✗）。"""
    req = _req([_f("进场A", "Add($close,1) ")], [_f("离场B", " Add($close,1)")])
    with pytest.raises(HTTPException):
        _apply_exit_validation(req)
