# -*- coding: utf-8 -*-
"""v1.20.25 回归：**物化字段缺失 ⇒ 显式警告**（用户 2026-09-19 要求）。

背景（`md/deploy.md`「物化字段」一节）：`pre*`（前复权）与 `chip_*` 是**本项目自造**的派生字段，
`data/` 被 .gitignore 排除 ⇒ 既不在数据包、也不在 git 里，**每台机器必须各生成一次**。
缺了**不报错** ✗ ⇒ `panel_expr.field()` 拿到的整列全 NaN ⇒ 公式**静默失效** ✗
（`COST()/WINNER()` 全 NaN；`forward` 退化成后复权价排序 ⇒ LLT K=20 年化 +9.03% ↔ 米筐 −5.24%）。
本模块钉住"整列全 NaN ⇒ 打一次 WARNING（含修复命令）"这条防护 ✓。
"""
import logging

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as pe


@pytest.fixture(autouse=True)
def _clear_warned():
    """每条用例前清空"已警告过"集合（模块级状态 ✓），避免互相污染。"""
    pe._MISSING_WARNED.clear()
    yield
    pe._MISSING_WARNED.clear()


def _s(vals):
    return pd.Series(vals, index=pd.Index([pd.Timestamp("2021-01-04")] * len(vals)))


@pytest.mark.parametrize("key", ["chip_cost_95", "chip_win_close", "preclose", "prevwap"])
def test_all_nan_warns_with_fix_command(key, caplog):
    """★ 核心：物化字段整列全 NaN ⇒ 必须 WARNING，且消息里带**修复命令** ✓。"""
    with caplog.at_level(logging.WARNING):
        pe._warn_if_materialized_missing(key, _s([np.nan, np.nan]))
    assert caplog.text, "%s 全 NaN 时必须告警 ✗" % key
    assert "物化文件缺失" in caplog.text
    assert "materialize_chip.py" in caplog.text or "build_preclose.py" in caplog.text


def test_warn_only_once_per_field(caplog):
    """同一字段**只警告一次**（面板里会被取上千次 ⇒ 不能刷屏 ✗）。"""
    with caplog.at_level(logging.WARNING):
        pe._warn_if_materialized_missing("chip_cost_95", _s([np.nan]))
        first = caplog.text
        caplog.clear()
        pe._warn_if_materialized_missing("chip_cost_95", _s([np.nan]))
        assert first and caplog.text == "", "第二次不应再告警 ✗"


def test_non_materialized_field_never_warns(caplog):
    """普通字段（`$close` 等）即使全 NaN 也**不该**告警（避免误报 ✗）。"""
    with caplog.at_level(logging.WARNING):
        pe._warn_if_materialized_missing("close", _s([np.nan]))
    assert caplog.text == ""


def test_partial_nan_does_not_warn(caplog):
    """**部分有值**属于正常数据（停牌/上市前）⇒ 不该告警 ✓。"""
    with caplog.at_level(logging.WARNING):
        pe._warn_if_materialized_missing("chip_cost_95", _s([np.nan, 1.0]))
    assert caplog.text == ""


def test_empty_series_does_not_warn(caplog):
    """空序列（无区间）⇒ 不告警（无从判断 ✗）。"""
    with caplog.at_level(logging.WARNING):
        pe._warn_if_materialized_missing("prehigh", _s([]))
    assert caplog.text == ""
