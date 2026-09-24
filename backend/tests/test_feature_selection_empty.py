# -*- coding: utf-8 -*-
"""★ v1.20.67：`selected_features` 的**空数组 = 真的一个都不要** ✓（≠ `None` = 该特征集全量 ✓）。

用户 2026-09-24 报障：在 `mixed` 里**取消勾选全部 A158 因子**后，
**特征重要性里仍然全是 A158** ✗（模型 `feature_names` 实测 **562 列** ✓ = 158+360+44 公式 ✓）。

根因是**两层"空 = 全量"陷阱** ✗：
  ① 前端 `selected_features: next.length > 0 ? next : null` ⇒ 全不勾时传 `null` ✗
     ⇒ 后端按"该特征集**全量**"解释（见 `models/backtest.py` 的字段说明 ✓）；
  ② 后端 `if fields:` / `if req.selected_features:` ⇒ **空数组**同样落回"全量" ✗
     （`handler.py` 三处 + `qlib_engine.py` 三处 ✓ 已全部改为 `is not None` ✓）。
⚠ 本文件只构造 Handler 的**裸实例**（`__new__`）✓ —— 只测"选哪些列"的纯逻辑 ✓，
不碰 qlib 的数据加载/进程池 ⇒ 秒级、无副作用 ✓。
"""
from app.factors.handler import MixedHandler, SelectedAlpha158, SelectedAlpha360

_FORMULA = "OUT:MA(CLOSE,5);"


def _bare(cls, selected, formulas=()):
    h = cls.__new__(cls)
    h._selected = selected
    h._price_adjust = "backward"
    if cls is MixedHandler:
        h._formulas = list(formulas)
    return h


# --------------------------- 单一特征集：空集合 ⇒ 0 列 ---------------------------

def test_selected_alpha158_empty_means_zero_columns():
    """★ 空集合 ⇒ 一个都不要 ✓（旧实现 `if self._selected:` 会**全量返回** ✗ ✗）。"""
    exprs, names = _bare(SelectedAlpha158, set()).get_feature_config()
    assert exprs == [] and names == []


def test_selected_alpha360_empty_means_zero_columns():
    exprs, names = _bare(SelectedAlpha360, set()).get_feature_config()
    assert exprs == [] and names == []


def test_selected_alpha158_none_still_means_all():
    """⚠ 反过来：`None` 仍必须是**全量** ✓（别把"没传"也当成"不要" ✗）。"""
    exprs, names = _bare(SelectedAlpha158, None).get_feature_config()
    assert len(names) > 100 and len(names) == len(exprs)


# --------------------------- 混合模式：用户本次的场景 ---------------------------

def test_mixed_empty_alpha_keeps_formulas_only():
    """★★ 用户原案：mixed + 取消勾选全部 A158/A360 + 勾了公式 ⇒ **只剩公式特征** ✓。"""
    exprs, names = _bare(MixedHandler, set(), [_FORMULA]).get_feature_config()
    assert len(names) == 1, names
    assert not any(n.startswith(("A158_", "A360_")) for n in names), names
    assert "Mean($close,5)" in exprs[0], exprs


def test_mixed_none_means_both_alpha_blocks():
    """`None`（= 没做逐项选择）⇒ 两套 Alpha **全量** ✓（历史行为不变 ✓）。"""
    exprs, names = _bare(MixedHandler, None, [_FORMULA]).get_feature_config()
    assert any(n.startswith("A158_") for n in names)
    assert any(n.startswith("A360_") for n in names)
    assert len(names) == len(exprs) > 500


def test_mixed_partial_selection_only_keeps_those():
    """部分勾选（带来源前缀 ✓）⇒ 只留勾中的那些 ✓（本就不受本次改动影响 ✓）。"""
    exprs, names = _bare(MixedHandler, {"A158_KMID", "A360_CLOSE0"}).get_feature_config()
    assert names == ["A158_KMID", "A360_CLOSE0"], names
    assert len(exprs) == 2
