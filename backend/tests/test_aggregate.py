# -*- coding: utf-8 -*-
"""净值汇总指标计算的单元测试。"""
import pytest

from app.engine.metrics import _aggregate_from_nav


class TestAggregateFromNav:
    def test_empty(self):
        result = _aggregate_from_nav([], [])
        assert result.total_return is None

    def test_simple_nav(self):
        """净值从 1.0 涨到 1.1（10%），总收益应为 0.1。"""
        nav = [{"date": "2022-01-04", "value": 1.0, "benchmark": 1.0},
               {"date": "2022-01-05", "value": 1.1, "benchmark": 1.05}]
        result = _aggregate_from_nav(nav, [])
        assert result.total_return == pytest.approx(0.1)
        assert result.benchmark_return == pytest.approx(0.05)
        assert result.nav == nav

    def test_max_drawdown(self):
        """净值 1.0 -> 1.2 -> 0.9 -> 1.0，最大回撤应为 (0.9/1.2 - 1) = -0.25。"""
        nav = [{"date": "d1", "value": 1.0},
               {"date": "d2", "value": 1.2},
               {"date": "d3", "value": 0.9},
               {"date": "d4", "value": 1.0}]
        result = _aggregate_from_nav(nav, [])
        assert result.max_drawdown == pytest.approx(0.9 / 1.2 - 1)
