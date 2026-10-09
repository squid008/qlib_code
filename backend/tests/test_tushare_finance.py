# -*- coding: utf-8 -*-
"""tushare 财务链（`tools/dump_tushare_finance.py`）单测（2026-10-09 加）。

背景（用户 2026-10-09）：米筐 bundle 是**本机快照**（财务只到 2026 中报、行情到 08-21 ✗），
tushare 能持续更新 ⇒ 用户决定"**后面的新数据就用它**" ✓，但前提是**与米筐对拍核对通过** ✓
（对拍脚本：`ai_test/probe_crosscheck_fin.py` / `probe_crosscheck_many.py`：2173 格中 99% 逐位一致 ✓）。

本测试钉住四件事（都不联网 ✓）：
1. **字段表与 codegen 逐项一致** ✓（与 test_finance_func.py 同一条守卫 —— 换源绝不能改字段顺序 ✗）；
2. **日期/代码口径**：tushare `YYYYMMDD` → `YYYY-MM-DD`（踩过：`np.datetime64('20260425','D')`
   解析出来的不是日期 ✗ ⇒ 取值恒 NaN ✗）、`000001.SZ` ↔ `sz000001`；
3. ★ **PIT 语义**：同一报告期有"原始 + 更正"两行时，更正后的数字**只有在其公告日之后**才生效 ✓
   （这就是"无未来函数"的命门 ✓）；
4. **派生口径**：TTM = 累计(Q)+累计(上年年报)−累计(上年同期Q)、累计同比、毛利/ROA/FCF，
   以及 ROE 毛刺的**记录**（平安 2026q1 实测 tushare 给 0.24 ✗，米筐 2.83 ✓）。
"""
import importlib.util
import os
import sys

import numpy as np
import pytest

from app.factors.parser.codegen import FINANCE_FIELDS

_TOOL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools",
                                     "dump_tushare_finance.py"))


def _load():
    """按路径加载（tools/ 不是包 ✓）；它自己会把 tools/ 加进 sys.path 以导入 dump_finance ✓。"""
    if not os.path.exists(_TOOL):
        pytest.skip("dump_tushare_finance.py 不存在")
    spec = importlib.util.spec_from_file_location("_dtf_under_test", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if os.path.dirname(_TOOL) not in sys.path:
        sys.path.insert(0, os.path.dirname(_TOOL))
    return mod


M = _load()


def _row(table, ann, period, **kw):
    r = {"ts_code": "000001.SZ", "ann_date": ann, "end_date": period,
         "report_type": "1", "update_flag": "0"}
    r.update(kw)
    return r


def _sample_data():
    """一只股票的构造数据：2025 全年 + 2026 一季/中报（含 2026 中报的**更正行** ✓）。"""
    return {
        "income": [
            _row("income", "20260425", "20260331", total_revenue=100.0, oper_cost=60.0,
                 operate_profit=30.0, n_income=20.0, n_income_attr_p=18.0, basic_eps=1.8),
            _row("income", "20260815", "20260630", total_revenue=210.0, oper_cost=120.0,
                 operate_profit=70.0, n_income=50.0, n_income_attr_p=45.0, basic_eps=4.5),
            # ★ 更正行：同日公告、update_flag=1、数字更大 ⇒ 同日应取这一行 ✓
            _row("income", "20260815", "20260630", total_revenue=220.0, oper_cost=120.0,
                 operate_profit=75.0, n_income=52.0, n_income_attr_p=47.0, basic_eps=4.7,
                 update_flag="1"),
            # 上年同期（做同比用）
            _row("income", "20250425", "20250331", total_revenue=80.0, oper_cost=50.0,
                 operate_profit=20.0, n_income=12.0, n_income_attr_p=10.0, basic_eps=1.0),
            # 上年年报（做 TTM 用）
            _row("income", "20260321", "20251231", total_revenue=400.0, oper_cost=200.0,
                 operate_profit=120.0, n_income=60.0, n_income_attr_p=54.0, basic_eps=5.4),
        ],
        "balancesheet": [
            _row("balancesheet", "20260815", "20260630", total_assets=1000.0,
                 total_hldr_eqy_exc_min_int=500.0),
            _row("balancesheet", "20260425", "20260331", total_assets=950.0,
                 total_hldr_eqy_exc_min_int=480.0),
        ],
        "cashflow": [
            _row("cashflow", "20260815", "20260630", n_cashflow_act=90.0,
                 c_pay_acq_const_fiolta=30.0),
            _row("cashflow", "20260425", "20260331", n_cashflow_act=40.0,
                 c_pay_acq_const_fiolta=10.0),
            _row("cashflow", "20260321", "20251231", n_cashflow_act=160.0,
                 c_pay_acq_const_fiolta=60.0),
            _row("cashflow", "20250425", "20250331", n_cashflow_act=30.0,
                 c_pay_acq_const_fiolta=8.0),
        ],
        "fina_indicator": [
            _row("fina_indicator", "20260815", "20260630", roe_waa=9.4),
            _row("fina_indicator", "20260425", "20260331", roe_waa=3.75),
        ],
    }


class TestFieldTable:
    def test_matches_codegen(self):
        """★ 换数据源**绝不能**改字段顺序（FINANCE(q) 按下标取 ✓）。"""
        assert [f for f, _u in M.FIN_FIELDS] == [f for f, _d in FINANCE_FIELDS]


class TestDateAndCode:
    def test_d8_normalizes(self):
        assert M.d8("20260630") == "2026-06-30"
        assert M.d8("2026-06-30") is None          # 已是规范格式 ⇒ 不是本函数的输入 ✓
        assert M.d8("") is None and M.d8("2026") is None

    def test_numpy_parses_normalized_only(self):
        """踩坑守卫：未规范化的 8 位串被 numpy 解析成**非日期** ✗（实测 7399248751 ✗）。"""
        assert int(np.datetime64(M.d8("20260425"), "D").astype(np.int64)) == 20568
        assert int(np.datetime64("20260425", "D").astype(np.int64)) != 20568

    def test_period_and_codes(self):
        assert M.period_to_quarter("20260331") == "2026q1"
        assert M.period_to_quarter("20261231") == "2026q4"
        assert M.period_to_quarter("20260230") is None      # 不是报告期 ✓
        assert M.to_qlib_code("600519.SH") == "sh600519"
        assert M.to_qlib_code("000001.SZ") == "sz000001"
        assert M.to_ts_code("sz000001") == "000001.SZ"
        assert M.to_ts_code("sh600519") == "600519.SH"


class TestPit:
    def test_same_day_keeps_latest_update_flag(self):
        """同日公告的更正行（update_flag=1）应胜出 ✓。"""
        tab = M.TsPit(_sample_data())
        t = int(np.datetime64("2026-08-20", "D").astype(np.int64))
        assert tab.val_at("revenue", "2026q2", t) == 220.0

    def test_value_invisible_before_announce(self):
        """★ 无未来函数：公告日之前，这一期**取不到**（前向填充交给下游 ✓）。"""
        tab = M.TsPit(_sample_data())
        before = int(np.datetime64("2026-06-01", "D").astype(np.int64))
        after = int(np.datetime64("2026-08-16", "D").astype(np.int64))
        assert not np.isfinite(tab.val_at("revenue", "2026q2", before))
        assert tab.val_at("revenue", "2026q2", after) == 220.0

    def test_ttm_from_cumulative(self):
        """TTM(2026q2) = 累计(q2) + 累计(上年年报) − 累计(上年同期q2)。

        样本里没有 2025q2 ⇒ 该项 NaN ⇒ TTM 为 NaN ✓（**宁可 NaN 也不瞎算** ✓）；
        用 2026q1 验一遍完整链条：18 + 54 − 10 = 62 ✓。
        """
        tab = M.TsPit(_sample_data())
        t = int(np.datetime64("2026-08-20", "D").astype(np.int64))
        assert tab.ttm("net_profit_parent_company", "2026q1", t) == pytest.approx(62.0)
        assert not np.isfinite(tab.ttm("net_profit_parent_company", "2026q2", t))

    def test_ttm_q4_equals_cumulative(self):
        tab = M.TsPit(_sample_data())
        t = int(np.datetime64("2026-08-20", "D").astype(np.int64))
        assert tab.ttm("net_profit_parent_company", "2025q4", t) == pytest.approx(54.0)

    def test_report_type_filtered(self):
        """只认合并报表（report_type='1'）✓ —— 母公司报表混进来会把数字整体拉偏 ✗。"""
        data = _sample_data()
        data["income"].append(_row("income", "20260815", "20260630", report_type="2",
                                   total_revenue=1.0, n_income_attr_p=999.0))
        tab = M.TsPit(data)
        t = int(np.datetime64("2026-08-20", "D").astype(np.int64))
        assert tab.val_at("revenue", "2026q2", t) == 220.0


class TestReportMetrics:
    def test_derived_values(self):
        tab = M.TsPit(_sample_data())
        rep = M.build_report_metrics(tab, code="sz000001")
        t = int(np.datetime64("2026-08-20", "D").astype(np.int64))
        n = int(np.datetime64("2026-08-15", "D").astype(np.int64))

        def at(field, when):
            for ann, q, v in rep[field]:
                if int(np.datetime64(ann, "D").astype(np.int64)) == when and q == "2026q2":
                    return v
            return float("nan")

        # 营收同比 = 220 / 2025q2(缺) ⇒ NaN ✓
        assert not np.isfinite(at("fin_rev_yoy", n))
        # 毛利率 = (220−120)/220 = 45.4545% ✓
        assert at("fin_gross_margin", n) == pytest.approx(100.0 * 100.0 / 220.0)
        # ROA = 52/1000 = 5.2% ✓
        assert at("fin_roa", n) == pytest.approx(5.2)
        # EPS / ROE 用报告值 ✓
        assert at("fin_eps", n) == pytest.approx(4.7)
        assert at("fin_roe", n) == pytest.approx(9.4)
        # FCF TTM(2026q2) 缺 2025q2 ⇒ NaN ✓；2026q1 可算：(40−10)+(160−60)−(30−8) = 108 ✓
        assert not np.isfinite(at("fin_fcf", n))
        q1 = int(np.datetime64("2026-04-25", "D").astype(np.int64))
        got = [v for ann, q, v in rep["fin_fcf"]
               if int(np.datetime64(ann, "D").astype(np.int64)) == q1 and q == "2026q1"]
        assert got and got[0] == pytest.approx(108.0)

    def test_roe_suspect_recorded(self):
        """ROE 毛刺要**被记录**（默认不改数据 ✓）—— 实测平安 2026q1：报告 0.24 vs 自算 ~5 ✓。"""
        data = _sample_data()
        # ⚠ 注意下标：[0] 是 2026q2（中报）、[1] 才是 2026q1（一季报）✓ —— 第一版改错了行 ✗
        data["fina_indicator"][1] = _row("fina_indicator", "20260425", "20260331", roe_waa=0.05)
        M.ROE_SUSPECTS.clear()
        M.ROE_GUARD = False
        rep = M.build_report_metrics(M.TsPit(data), code="sz000001")
        assert M.ROE_SUSPECTS and M.ROE_SUSPECTS[0]["code"] == "sz000001"
        kept = [v for ann, q, v in rep["fin_roe"] if q == "2026q1"]
        assert kept and kept[0] == pytest.approx(0.05), "默认必须**保留原值**（只记录 ✓）"
        M.ROE_SUSPECTS.clear()
        M.ROE_GUARD = True
        rep = M.build_report_metrics(M.TsPit(data), code="sz000001")
        fixed = [v for ann, q, v in rep["fin_roe"] if q == "2026q1"]
        assert fixed and fixed[0] == pytest.approx(18.0 / 480.0 * 100.0), "--roe-guard 时被替换 ✓"
        M.ROE_GUARD = False
        M.ROE_SUSPECTS.clear()


class TestExpandDaily:
    def test_forward_fill_on_announce(self):
        cal = np.asarray([int(np.datetime64(d, "D").astype(np.int64))
                          for d in ("2026-04-24", "2026-04-27", "2026-08-14", "2026-08-17")])
        got = M.expand_daily([("2026-04-25", "2026q1", 1.0), ("2026-08-15", "2026q2", 2.0)], cal)
        assert np.isnan(got[0])                     # 公告前 ✓
        assert got[1] == 1.0 and got[2] == 1.0      # 4/25 公告 ⇒ 4/27 起生效 ✓
        assert got[3] == 2.0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
