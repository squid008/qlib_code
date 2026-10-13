# -*- coding: utf-8 -*-
"""回归：**tushare 财务链**的 PIT 去重 —— 空值行不得覆盖有值行（2026-10-13 实测修的 bug ✗→✓）。

背景（用只读体检在真实数据上定位 ✓）：
  `fina_indicator` 对不少股票会**同一 (end_date, ann_date) 返回两行**，后一行 `roe_waa` 为 null ✗，
  且两行 `update_flag` 都缺 ⇒ 旧规则「同日取 update_flag 大者、相等则后者胜」会把**有值行顶掉** ✗
  ⇒ `fin_roe` 整段变 NaN ✗（实测：325 只活跃股的 `fin_roe` 停在 2026 中报公告日 ✗）。
  同一口径也用 `dump_finance.py` 给米筐链，故两条链必须**行为一致** ✓（都"更正后为准"且**不丢值** ✓）。
"""
import importlib.util
from pathlib import Path

import numpy as np

SPEC = importlib.util.spec_from_file_location(
    "_tushare_fin", Path(__file__).parents[1] / "tools" / "dump_tushare_finance.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def inc(**kw):
    """一行 income（TsPit 认的列：revenue/oper_cost/... + report_type/update_flag ✓）。"""
    row = {"ts_code": "600000.SH", "report_type": "1", "end_date": "20260630",
           "ann_date": "20260828", "update_flag": None, "total_revenue": 100.0,
           "oper_cost": 60.0, "operate_profit": 30.0, "n_income": 20.0,
           "n_income_attr_p": 18.0, "basic_eps": 0.5}
    row.update(kw)
    return row


def fina(**kw):
    row = {"ts_code": "600000.SH", "report_type": "1", "end_date": "20260630",
           "ann_date": "20260828", "update_flag": None, "roe_waa": None}
    row.update(kw)
    return row


def test_null_row_after_valued_row_keeps_the_value():
    tab = M.TsPit({"income": [inc()], "fina_indicator": [fina(roe_waa=3.96), fina(roe_waa=None)]})
    v = tab.val_at("roe_waa", "2026q2", 7390000000)
    assert v == 3.96                                        # ✗ 旧实现给 NaN


def test_valued_row_after_null_row_still_keeps_the_value():
    tab = M.TsPit({"income": [inc()], "fina_indicator": [fina(roe_waa=None), fina(roe_waa=3.96)]})
    assert tab.val_at("roe_waa", "2026q2", 7390000000) == 3.96


def test_correction_with_higher_update_flag_still_wins():
    """都**有值**时，仍按 update_flag 大者（更正后为准 ✓）——别把这条规则改坏 ✗。"""
    tab = M.TsPit({"income": [inc()],
                   "fina_indicator": [fina(roe_waa=3.96, update_flag="0"),
                                      fina(roe_waa=4.11, update_flag="1")]})
    assert tab.val_at("roe_waa", "2026q2", 7390000000) == 4.11


def test_correction_with_higher_flag_beats_later_lower_flag():
    tab = M.TsPit({"income": [inc()],
                   "fina_indicator": [fina(roe_waa=4.11, update_flag="1"),
                                      fina(roe_waa=3.96, update_flag="0")]})
    assert tab.val_at("roe_waa", "2026q2", 7390000000) == 4.11


def test_both_null_stays_nan():
    tab = M.TsPit({"income": [inc()], "fina_indicator": [fina(), fina()]})
    assert np.isnan(tab.val_at("roe_waa", "2026q2", 7390000000))


def test_same_rule_applies_to_income_columns():
    """income 表同样受此规则保护 ✓（实测有个别股票 `total_revenue` 也出现空值重复行 ✓）。"""
    tab = M.TsPit({"income": [inc(total_revenue=100.0), inc(total_revenue=None)]})
    assert tab.val_at("revenue", "2026q2", 7390000000) == 100.0


def test_no_duplicates_untouched():
    tab = M.TsPit({"income": [inc()], "fina_indicator": [fina(roe_waa=3.96)]})
    assert tab.val_at("roe_waa", "2026q2", 7390000000) == 3.96
