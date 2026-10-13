# -*- coding: utf-8 -*-
"""回归：`fin_roe` 在 `roe_waa` **缺失**时用自算值兜底（2026-10-13 用户同意 ✓）。

口径 ✓：`fin_roe = roe_waa`；**没有** `roe_waa` 时 = 累计归母净利 ÷ 期末归母权益 × 100 ✓，
**硬门槛 权益 > 0** ✓（资不抵债 ⇒ 分母为负 ⇒ 保持 NaN ✓，不造假 ✗）。
背景（实测 ✓）：tushare 的 `roe_waa` 覆盖与"亏损/资不抵债"高度相关 —— 2026 中报四象限里，
净资产<0 的 108 只中 **80 只无 roe** ✓，净资产>0 的 5569 只中**仅 12 只无** ✓
（`ai_test/fin39_probe.py` ✓）。这批股票此前 `fin_roe` 整段 NaN ✗。
"""
import importlib.util
from pathlib import Path

import numpy as np

SPEC = importlib.util.spec_from_file_location(
    "_tushare_fin_roe", Path(__file__).parents[1] / "tools" / "dump_tushare_finance.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

WHEN = 7390000000                       # 2026 年前后（int days ✓）


def income(np_parent=18.0, rev=100.0):
    return {"ts_code": "600000.SH", "report_type": "1", "end_date": "20260630",
            "ann_date": "20260828", "update_flag": None, "total_revenue": rev,
            "oper_cost": 60.0, "operate_profit": 30.0, "n_income": 20.0,
            "n_income_attr_p": np_parent, "basic_eps": 0.5}


def balance(eq=120.0):
    return {"ts_code": "600000.SH", "report_type": "1", "end_date": "20260630",
            "ann_date": "20260828", "update_flag": None, "total_assets": 300.0,
            "total_hldr_eqy_exc_min_int": eq}


def fina(roe):
    return {"ts_code": "600000.SH", "report_type": "1", "end_date": "20260630",
            "ann_date": "20260828", "update_flag": None, "roe_waa": roe}


def roe_of(rows, fill=True):
    M.ROE_FILL_MISSING = fill
    M.ROE_FILLED.clear()
    tab = M.TsPit(rows)
    out = M.build_report_metrics(tab, "sh600000")["fin_roe"]
    return out[-1][2] if out else float("nan")


def test_reported_roe_is_used_when_present():
    assert roe_of({"income": [income()], "balancesheet": [balance()],
                   "fina_indicator": [fina(3.96)]}) == 3.96


def test_missing_roe_falls_back_to_calc():
    """`roe_waa` 缺失 ⇒ 18/120×100 = 15.0 ✓（此前是 NaN ✗）。"""
    v = roe_of({"income": [income()], "balancesheet": [balance()], "fina_indicator": []})
    assert v == 15.0


def test_negative_equity_stays_nan():
    """资不抵债 ⇒ 分母为负 ⇒ **保持 NaN** ✓（自算出的数会反向 ✗）。"""
    v = roe_of({"income": [income(np_parent=-10.0)], "balancesheet": [balance(eq=-5.0)],
                "fina_indicator": []})
    assert np.isnan(v)


def test_zero_equity_stays_nan():
    assert np.isnan(roe_of({"income": [income()], "balancesheet": [balance(eq=0.0)],
                            "fina_indicator": []}))


def test_missing_income_component_stays_nan():
    assert np.isnan(roe_of({"income": [income(np_parent=None)], "balancesheet": [balance()],
                            "fina_indicator": []}))


def test_switch_off_restores_plain_nan():
    """`--no-roe-fill-missing` ⇒ 回到「源没有就 NaN」✗（供复现旧结果 ✓）。"""
    assert np.isnan(roe_of({"income": [income()], "balancesheet": [balance()],
                            "fina_indicator": []}, fill=False))


def test_filled_counters_are_recorded():
    roe_of({"income": [income()], "balancesheet": [balance()], "fina_indicator": []})
    assert M.ROE_FILLED.get("sh600000") == 1
