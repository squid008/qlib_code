# -*- coding: utf-8 -*-
"""v1.2.17 回归：`$chip_*` 取数**物化优先**（用户 2026-09-18 提问"cost 那么慢"）。

规则（`panel_expr.PanelEvaluator._chip_or_bin`）：
- 字段在 `chip_store.DEFAULT_FIELDS`（**已物化** ✓）⇒ **直读 bin**（与 `$close` 同路径 ✓）；
- 否则（如 `COST(36)` ⇒ `$chip_cost_36`）⇒ 退回 `_chip_field` **现算** ✓（用户明确要求 ✓）；
- 面板未就绪（轻量构造无 `_full` ✓）⇒ 退回现算 ✓（不抛 ✓）。
"""
import pandas as pd
import pytest

from app.factors import chip_store, panel_expr


def test_default_fields_match_materialize():
    """物化清单必须与 `chip_store.materialize` 默认口径一致（改一处忘另一处 = 白物化 ✗）。"""
    assert set(chip_store.DEFAULT_FIELDS) == {
        "chip_cost_5", "chip_cost_30", "chip_cost_75", "chip_cost_95",
        "chip_win_close", "chip_win_high", "chip_win_low",
    }


class _FakeEvaluator(panel_expr.PanelEvaluator):
    """只装配路由所需字段（不建真面板）；两个分支各记一笔调用，便于断言 ✓。"""

    def __init__(self, with_panel=True):
        self._field_cache = {}
        self.calls = []
        if with_panel:
            self._full = pd.Index([pd.Timestamp("2021-01-04")])   # 面板"就绪"哨兵
            self._layout, self._req_lo, self._req_hi = [], 0, 0

    def _chip_field(self, key):
        self.calls.append(("compute", key))
        return pd.Series([1.0], index=pd.Index([pd.Timestamp("2021-01-04")]))


CACHED_BIN = "chip_cost_95"       # 已物化 ✓
UNCACHED_BIN = "chip_cost_36"     # ★ 用户点名的未物化档位 ⇒ 现算 ✓


@pytest.mark.parametrize("key", list(chip_store.DEFAULT_FIELDS))
def test_materialized_reads_bin_not_compute(monkeypatch, key):
    """★ 已物化的 7 个字段**必须直读 bin**（不得再走现算 ✗ —— 那正是本次修的快路径）。"""
    monkeypatch.setattr(panel_expr, "_load_field_on",
                        lambda *a: [9.9])
    e = _FakeEvaluator()
    out = e._chip_or_bin(key)
    assert e.calls == [], "已物化字段不能再调 `_chip_field`（现算）✗"
    assert float(out.iloc[0]) == 9.9, "值应来自 bin ✓"


@pytest.mark.parametrize("key", [UNCACHED_BIN, "chip_cost_50", "chip_win_vwap"])
def test_unmaterialized_falls_back_to_compute(key):
    """★ 未物化档位（如 `COST(36)`）**必须走现算** ✓（用户要求；结果正确、只是慢 ✓）。"""
    e = _FakeEvaluator()
    out = e._chip_or_bin(key)
    assert e.calls == [("compute", key)], "未物化档位必须现算 ✓"
    assert float(out.iloc[0]) == 1.0


def test_no_panel_falls_back_to_compute(monkeypatch):
    """面板未就绪（无 `_full`，单测轻量构造 ✓）⇒ 退回现算 ✓，不得抛 AttributeError ✗。"""
    monkeypatch.setattr(panel_expr, "_load_field_on", lambda *a: [9.9])
    e = _FakeEvaluator(with_panel=False)
    assert e._chip_or_bin(CACHED_BIN) is not None
    assert e.calls == [("compute", CACHED_BIN)]
