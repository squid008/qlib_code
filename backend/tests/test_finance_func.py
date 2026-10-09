# -*- coding: utf-8 -*-
"""FINANCE(q) 守卫（2026-10-09 加）。

用户需求：益盟盘口那几个基本面指标（市盈率TTM/市净率/营收增长率/净利增长率/毛利率/ROE/ROA/EPS）
要能在公式里取历史序列 —— 即 `FINANCE(1)`..`FINANCE(9)`。

本测试钉住三件事：
1. q → 字段名 的映射（1..9）与 q 的常量/范围校验（错误提示要能自己看懂怎么改）；
2. ★ 跨文件一致性：`codegen.FINANCE_FIELDS` 的顺序必须与 **物化脚本** `tools/dump_finance.py`
   的 `FIN_FIELDS` **逐项一致** —— 这两处一旦错位，用户写 FINANCE(3) 会**静默**拿到别的指标
   （数值看着也合理 ✗，最危险的一类 bug ⇒ 必须在测试里钉死 ✓）。
3. 中间变量/参数写成常量时也能用（`N:=2; FINANCE(N)`），非匀速的表达式要**报错**而不是取整蒙混 ✓。
"""
import importlib.util
import os

import pytest

from app.factors.parser import translate_formula
from app.factors.parser.codegen import FINANCE_FIELDS, CodeGenError
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
        expect = [
            "$fin_pe_ttm", "$fin_pb", "$fin_rev_yoy", "$fin_np_yoy", "$fin_gross_margin",
            "$fin_roe", "$fin_roa", "$fin_eps", "$fin_op_yoy",
            "$fin_fcf",                      # ★ v1.20.81：自由现金流TTM（元）
        ]
        for i, expr in enumerate(expect, 1):
            r = translate_formula(f"OUT:FINANCE({i});")
            assert r.expression == expr, f"FINANCE({i}) 应映射到 {expr}"

    def test_reported_names_order(self):
        """编号说明的顺序 = 用户点名的那几项（1 市盈率TTM … 8 每股收益，9 营业利润增长率）。"""
        assert [d for _f, d in FINANCE_FIELDS] == _EXPECTED

    def test_lowercase_and_spaces_ok(self):
        """大小写不敏感（用户写的是 `finance(1)`）。"""
        assert translate_formula("OUT:finance(1);").expression == "$fin_pe_ttm"
        assert translate_formula("OUT:FINANCE( 8 );").expression == "$fin_eps"

    def test_with_intermediate_var_and_in_formula(self):
        """能嵌进真实公式，且字段可参与运算（输出线名不要用 LOW 这类行情字段名 ✗）。"""
        r = translate_formula("PE:=FINANCE(1); 低估:PE>0 AND PE<20;")
        assert r.expression == "And(Gt($fin_pe_ttm,0),Lt($fin_pe_ttm,20))"

    def test_const_expression_q_ok(self):
        """q 写成常量表达式（参数代入后常见）也能算出来。"""
        assert translate_formula("N:=2; OUT:FINANCE(N);").expression == "$fin_pb"

    def test_lag_hist_not_24(self):
        """金融股无毛利率：FINANCE(5) 是字段（空值由数据决定），不是编译期报错。"""
        assert translate_formula("OUT:FINANCE(5);").expression == "$fin_gross_margin"


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
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:FINANCE(1,2);")
        assert "1 个参数" in str(e.value)
        # 报错里要带上编号说明（用户不用去翻文档就能改对）
        assert "市盈率TTM" in str(e.value)


class TestFinanceCrossFileConsistency:
    """★ 物化脚本与 codegen 的字段表必须逐项一致（顺序错位 = 静默取错指标）。"""

    def test_field_list_matches_dump_tool(self):
        mod = _load_dump_tool()
        assert [f for f, _u in mod.FIN_FIELDS] == [f for f, _d in FINANCE_FIELDS], (
            "tools/dump_finance.py 的 FIN_FIELDS 与 codegen.FINANCE_FIELDS 不一致：\n"
            f"  dump : {[f for f, _u in mod.FIN_FIELDS]}\n"
            f"  codegen: {[f for f, _d in FINANCE_FIELDS]}\n"
            "⇒ 两处顺序必须逐项相同（FINANCE(q) 按下标取字段）"
        )

    def test_units_declared(self):
        mod = _load_dump_tool()
        assert len(mod.FIN_FIELDS) == 10
        # 单位表存在（dump 侧用它写 meta），且长度一致
        assert all(u for _f, u in mod.FIN_FIELDS)
