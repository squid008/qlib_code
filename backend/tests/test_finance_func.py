# -*- coding: utf-8 -*-
"""FINANCE(q[,口径]) 守卫（2026-10-09 加；2026-10-10 加第二参数"口径"）。

用户需求：益盟盘口那几个基本面指标（市盈率TTM/市净率/营收增长率/净利增长率/毛利率/ROE/ROA/EPS）
要能在公式里取历史序列 —— 即 `FINANCE(1)`..`FINANCE(10)`；
2026-10-10 用户定稿再加**口径**（第二参数）：`FINANCE(q)` ≡ `FINANCE(q,1)` 单季 / `(q,2)` 年化 / `(q,3)` TTM。

本测试钉住四件事：
1. q → 字段名 的映射（1..10 **含三档**）与 q/口径 的常量、范围校验（错误提示要能自己看懂怎么改）；
2. ★ 跨文件一致性：`codegen` 的字段名必须与 **物化脚本** `tools/dump_finance.py` 的
   `ALL_FIELDS`（旧累计档 + 三档）**逐项一致** —— 这两处一旦错位，用户写 FINANCE(3) 会**静默**
   拿到别的指标（数值看着也合理 ✗，最危险的一类 bug ⇒ 必须在测试里钉死 ✓）。
3. ★ 三档口径的**自洽性**（不依赖任何数据文件 ⇒ 合成数据直接验公式）：
   `年化 = 累计 ÷ 季度数 × 4`、`TTM = 最近 4 个单季之和`、`单季 = 本期累计 − 上一季累计`；
4. 中间变量/参数写成常量时也能用（`N:=2; FINANCE(N)`），非匀速的表达式要**报错**而不是取整蒙混 ✓。
"""
import importlib.util
import os

import pytest

from app.factors.parser import translate_formula
from app.factors.parser.codegen import (
    FINANCE_FIELDS, FINANCE_TIER_SUFFIX, CodeGenError,
    finance_tiered_field_names,
)
from app.factors.parser.semantic import SemanticError

_EXPECTED = [
    "市盈率TTM", "市净率", "营业收入增长率", "净利润增长率",
    "销售毛利率", "净资产收益率ROE", "总资产收益率ROA", "每股收益(基本)", "营业利润增长率",
    "自由现金流TTM",
]
_DUMP_TOOL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools", "dump_finance.py"))


def _load_dump_tool():
    """按路径加载物化脚本（tools/ 不是包，只能这么导；它 import h5py/pandas，测试环境里有）。"""
    if not os.path.exists(_DUMP_TOOL):
        pytest.skip("dump_finance.py 不存在（路径改名了 ⇒ 本测试要同步改）")
    spec = importlib.util.spec_from_file_location("_dump_finance_under_test", _DUMP_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFinanceMapping:
    def test_q_1_to_10_maps_to_fields(self):
        """q=1/2 只有一档；q=3..10 **省略口径 = 单季档**（`_q` ✓）。"""
        expect = [
            "$fin_pe_ttm", "$fin_pb",
            "$fin_rev_yoy_q", "$fin_np_yoy_q", "$fin_gross_margin_q",
            "$fin_roe_q", "$fin_roa_q", "$fin_eps_q", "$fin_op_yoy_q",
            "$fin_fcf_q",                    # ★ v1.20.81：自由现金流；2026-10-10 起默认单季档
        ]
        for i, expr in enumerate(expect, 1):
            r = translate_formula(f"OUT:FINANCE({i});")
            assert r.expression == expr, f"FINANCE({i}) 应映射到 {expr}"

    def test_tiers_map_to_suffixed_fields(self):
        """第二参数 = 口径：1 单季 / 2 年化 / 3 TTM ✓（`FINANCE(q)` ≡ `FINANCE(q,1)` ✓）。"""
        for tier, suffix in FINANCE_TIER_SUFFIX.items():
            assert translate_formula(f"OUT:FINANCE(3,{tier});").expression == "$fin_rev_yoy" + suffix
            assert translate_formula(f"OUT:FINANCE(10,{tier});").expression == "$fin_fcf" + suffix
        assert (translate_formula("OUT:FINANCE(4);").expression
                == translate_formula("OUT:FINANCE(4,1);").expression)

    def test_reported_names_order(self):
        """编号说明的顺序 = 用户点名的那几项（1 市盈率TTM … 8 每股收益，9 营业利润增长率）。"""
        assert [d for _f, d in FINANCE_FIELDS] == _EXPECTED

    def test_lowercase_and_spaces_ok(self):
        """大小写不敏感（用户写的是 `finance(1)`）。"""
        assert translate_formula("OUT:finance(1);").expression == "$fin_pe_ttm"
        assert translate_formula("OUT:FINANCE( 8 );").expression == "$fin_eps_q"
        assert translate_formula("OUT:FINANCE(8 , 3);").expression == "$fin_eps_ttm"

    def test_with_intermediate_var_and_in_formula(self):
        """能嵌进真实公式，且字段可参与运算（输出线名不要用 LOW 这类行情字段名 ✗）。"""
        r = translate_formula("PE:=FINANCE(1); 低估:PE>0 AND PE<20;")
        assert r.expression == "And(Gt($fin_pe_ttm,0),Lt($fin_pe_ttm,20))"

    def test_const_expression_q_ok(self):
        """q 写成常量表达式（参数代入后常见）也能算出来。"""
        assert translate_formula("N:=2; OUT:FINANCE(N);").expression == "$fin_pb"
        assert translate_formula("N:=9; M:=3; OUT:FINANCE(N,M);").expression == "$fin_op_yoy_ttm"

    def test_lag_hist_not_24(self):
        """金融股无毛利率：FINANCE(5) 是字段（空值由数据决定），不是编译期报错。"""
        assert translate_formula("OUT:FINANCE(5);").expression == "$fin_gross_margin_q"


class TestFinanceErrors:
    @pytest.mark.parametrize("text", ["OUT:FINANCE(0);", "OUT:FINANCE(11);", "OUT:FINANCE(-1);"])
    def test_out_of_range(self, text):
        with pytest.raises(CodeGenError) as e:
            translate_formula(text)
        assert "1~10" in str(e.value) or "需在 1" in str(e.value)

    @pytest.mark.parametrize("text", ["OUT:FINANCE(CLOSE);", "OUT:FINANCE(1.5);", "OUT:FINANCE();"])
    def test_non_const_q(self, text):
        with pytest.raises((CodeGenError, SemanticError)):
            translate_formula(text)

    def test_arg_count(self):
        """1~2 个参数；3 个参数要报清楚（用户输入了 3 个），且带上编号/口径说明 ✓。"""
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:FINANCE(1,2,3);")
        assert "1~2 个参数" in str(e.value)
        # 报错里要带上编号说明（用户不用去翻文档就能改对）
        assert "市盈率TTM" in str(e.value)

    @pytest.mark.parametrize("q", [1, 2])
    def test_pe_pb_reject_second_arg(self, q):
        """★ PE/PB 本身就一档 ⇒ 传第二参要给**友好错**（说清"只有 3~10 才有三档" ✓）。"""
        with pytest.raises(CodeGenError) as e:
            translate_formula(f"OUT:FINANCE({q},2);")
        msg = str(e.value)
        assert "只有一档" in msg and "不能传第二参数" in msg and "3~10" in msg

    @pytest.mark.parametrize("text", ["OUT:FINANCE(3,0);", "OUT:FINANCE(3,4);", "OUT:FINANCE(3,-1);"])
    def test_tier_out_of_range(self, text):
        with pytest.raises(CodeGenError) as e:
            translate_formula(text)
        assert "口径需在 1~3 之间" in str(e.value)

    @pytest.mark.parametrize("text", ["OUT:FINANCE(3,CLOSE);", "OUT:FINANCE(3,1.5);"])
    def test_tier_must_be_const_int(self, text):
        with pytest.raises((CodeGenError, SemanticError)) as e:
            translate_formula(text)
        assert "常量整数" in str(e.value)

    def test_finance_tdx_is_retired(self):
        """★ `FINANCE_TDX` 已下线（2026-10-10 用户第 4 项 ✓）—— 报错必须**指向 FINANCE** ✓。"""
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:FINANCE_TDX(1);")
        msg = str(e.value)
        assert "下线" in msg and "FINANCE(q[,口径])" in msg


class TestFinanceCrossFileConsistency:
    """★ 物化脚本与 codegen 的字段名必须逐项一致（顺序错位 = 静默取错指标）。"""

    def test_legacy_field_list_matches_dump_tool(self):
        mod = _load_dump_tool()
        assert [f for f, _u in mod.FIN_FIELDS] == [f for f, _d in FINANCE_FIELDS], (
            "tools/dump_finance.py 的 FIN_FIELDS 与 codegen.FINANCE_FIELDS 不一致：\n"
            f"  dump : {[f for f, _u in mod.FIN_FIELDS]}\n"
            f"  codegen: {[f for f, _d in FINANCE_FIELDS]}\n"
            "⇒ 两处顺序必须逐项相同（FINANCE(q) 按下标取字段）"
        )

    def test_tiered_field_names_shared(self):
        """★ 三档字段名：codegen 生成 ⇒ 物化脚本直接用它（24 个，q=3..10 × 3 档 ✓）。"""
        mod = _load_dump_tool()
        names = finance_tiered_field_names()
        assert len(names) == 8 * 3 and len(set(names)) == 24
        assert names == mod.TIERED_FIELDS
        assert mod.ALL_FIELDS == [f for f, _d in FINANCE_FIELDS] + names

    def test_units_declared(self):
        mod = _load_dump_tool()
        assert len(mod.FIN_FIELDS) == 10
        # 单位表存在（dump 侧用它写 meta），且三档字段都有单位 ✓
        assert all(u for _f, u in mod.FIN_FIELDS)
        assert all(mod.FIELD_UNITS.get(f) for f in mod.ALL_FIELDS)


# ---------------------------------------------------------------------------
# ★ 三档口径的**自洽性**（合成数据 ⇒ 直接验公式，不依赖任何数据文件 ✓）
# ---------------------------------------------------------------------------
# 单季营收（万元随便编）+ 其余量按同一套季度推进，专门用来验 单季/年化/TTM 三个式子 ✓
_SINGLES = {
    "rev":       {"2024q1": 100.0, "2024q2": 110.0, "2024q3": 115.0, "2024q4": 125.0,
                  "2025q1": 120.0, "2025q2": 130.0, "2025q3": 140.0, "2025q4": 150.0,
                  "2026q1": 150.0, "2026q2": 160.0},
    "np_parent": {"2024q1": 8.0, "2024q2": 9.0, "2024q3": 10.0, "2024q4": 11.0,
                  "2025q1": 10.0, "2025q2": 12.0, "2025q3": 14.0, "2025q4": 16.0,
                  "2026q1": 18.0, "2026q2": 20.0},
    "op":        {"2024q1": 15.0, "2024q2": 16.0, "2024q3": 17.0, "2024q4": 18.0,
                  "2025q1": 20.0, "2025q2": 22.0, "2025q3": 24.0, "2025q4": 26.0,
                  "2026q1": 30.0, "2026q2": 35.0},
    "gp":        {"2024q1": 50.0, "2024q2": 55.0, "2024q3": 58.0, "2024q4": 62.0,
                  "2025q1": 60.0, "2025q2": 66.0, "2025q3": 70.0, "2025q4": 75.0,
                  "2026q1": 80.0, "2026q2": 88.0},
    "np":        {"2024q1": 10.0, "2024q2": 11.0, "2024q3": 12.0, "2024q4": 13.0,
                  "2025q1": 12.0, "2025q2": 14.0, "2025q3": 16.0, "2025q4": 18.0,
                  "2026q1": 20.0, "2026q2": 22.0},
    "eps":       {"2024q1": 0.08, "2024q2": 0.09, "2024q3": 0.10, "2024q4": 0.11,
                  "2025q1": 0.10, "2025q2": 0.12, "2025q3": 0.14, "2025q4": 0.16,
                  "2026q1": 0.18, "2026q2": 0.20},
    "ocf":       {"2024q1": 25.0, "2024q2": 26.0, "2024q3": 27.0, "2024q4": 28.0,
                  "2025q1": 30.0, "2025q2": 32.0, "2025q3": 34.0, "2025q4": 36.0,
                  "2026q1": 40.0, "2026q2": 44.0},
    "capex":     {"2024q1": 4.0, "2024q2": 4.0, "2024q3": 5.0, "2024q4": 5.0,
                  "2025q1": 5.0, "2025q2": 6.0, "2025q3": 7.0, "2025q4": 8.0,
                  "2026q1": 9.0, "2026q2": 10.0},
}
# 存量（期末总资产 / 归母权益）
_STOCKS = {
    "ta":     {"2024q2": 950.0, "2024q3": 960.0, "2024q4": 970.0,
               "2025q2": 1000.0, "2025q3": 1010.0, "2025q4": 1020.0,
               "2026q1": 1030.0, "2026q2": 1040.0},
    "equity": {"2024q2": 470.0, "2024q3": 475.0, "2024q4": 480.0,
               "2025q2": 500.0, "2025q3": 505.0, "2025q4": 510.0,
               "2026q1": 515.0, "2026q2": 520.0},
}


def _cumulate(singles):
    """单季表 → **累计**表（每年重新起算 ✓ = 财务报表的 YTD 口径 ✓）。"""
    out, acc, year = {}, 0.0, None
    for q in sorted(singles, key=lambda s: (int(s[:4]), int(s[5]))):
        y = q[:4]
        if y != year:
            acc, year = 0.0, y
        acc += singles[q]
        out[q] = acc
    return out


def _fake_qget():
    """合成 `qget(name, quarter, when)`（累计表 + 存量表 ✓；忽略 when = 已 PIT 过滤 ✓）。"""
    cum = {name: _cumulate(s) for name, s in _SINGLES.items()}
    cum.update(_STOCKS)

    def qget(name, quarter, when):
        return cum.get(name, {}).get(quarter, float("nan"))

    return qget


class TestFinanceTierFormulas:
    """★ 三档口径公式的自洽性（合成数据，逐档核对定义 ✓）。"""

    def _vals(self, q="2026q2"):
        mod = _load_dump_tool()
        return mod.tiered_report_values(_fake_qget(), q, 99999)

    def test_single_tier_is_difference(self):
        v = self._vals()
        assert v["fin_eps_q"] == pytest.approx(0.20)
        assert v["fin_rev_yoy_q"] == pytest.approx((160.0 / 130.0 - 1.0) * 100.0)

    def test_annualized_tier_is_cum_times_4_over_n(self):
        """年化 = 本期累计 ÷ 已披露季度数 × 4（2026q2 累计 310 ⇒ 310×4/2 = 620 ✓）。"""
        v = self._vals()
        assert v["fin_eps_ann"] == pytest.approx(0.38 * 4.0 / 2.0)   # 累计 0.38 × 4/2 = 0.76
        # 年化档的增长率会退化成累计同比（分子分母同乘 4/季度数 ✓）
        assert v["fin_rev_yoy_ann"] == pytest.approx((310.0 / 250.0 - 1.0) * 100.0)

    def test_ttm_equals_sum_of_last_four_singles(self):
        """★ TTM = 最近 4 个单季之和（400+... 手算：150+160+140+150 = 600 ✓）。"""
        v = self._vals()
        assert v["fin_eps_ttm"] == pytest.approx(0.18 + 0.20 + 0.14 + 0.16)
        assert v["fin_fcf_ttm"] == pytest.approx((44.0 - 10.0) + (40.0 - 9.0)
                                                + (34.0 - 7.0) + (36.0 - 8.0))
        # TTM 增长率 = TTM / 上年同期 TTM − 1；上年同期 TTM(2025q2) = 250 + 450 − 210 = 490
        assert v["fin_rev_yoy_ttm"] == pytest.approx((600.0 / 490.0 - 1.0) * 100.0)

    def test_q1_single_equals_cumulative(self):
        """Q1 的单季 = 本期累计（没有上一季可比 ✓）。"""
        v = self._vals("2026q1")
        assert v["fin_eps_q"] == pytest.approx(0.18)
        assert v["fin_rev_yoy_q"] == pytest.approx((150.0 / 120.0 - 1.0) * 100.0)

    def test_ratio_tiers_use_period_end_stock(self):
        """比率类：分子按档取流量、分母一律**期末存量** ✓（ROE = 档内归母净利 ÷ 期末归母权益）。"""
        v = self._vals()
        assert v["fin_roe_q"] == pytest.approx(20.0 / 520.0 * 100.0)
        assert v["fin_roe_ttm"] == pytest.approx(68.0 / 520.0 * 100.0)      # 18+20+16+14
        assert v["fin_roa_q"] == pytest.approx(22.0 / 1040.0 * 100.0)
        assert v["fin_gross_margin_q"] == pytest.approx(88.0 / 160.0 * 100.0)
        assert v["fin_gross_margin_ttm"] == pytest.approx(
            (88.0 + 80.0 + 75.0 + 70.0) / 600.0 * 100.0)

    def test_missing_quarter_gives_nan_not_wrong_number(self):
        """★ 缺料就 NaN（绝不瞎算 ✓）：合成表最早只到 2024q1 ⇒ `2024q2` 的
        TTM 档要 2023 年报、增长率档要 2023q2 ⇒ 都该 NaN ✓；而**单季档**（只要本期/上一期 ✓）照算 ✓
        —— 正好验"该 NaN 的档 NaN、能算的档照算" ✓。"""
        v = self._vals("2024q2")
        assert not (v["fin_rev_yoy_ttm"] == v["fin_rev_yoy_ttm"])           # TTM 档 NaN ✓
        assert not (v["fin_rev_yoy_q"] == v["fin_rev_yoy_q"])               # 增长率基数缺 ⇒ NaN ✓
        assert v["fin_eps_q"] == pytest.approx(0.09)                        # 单季档照算 ✓
