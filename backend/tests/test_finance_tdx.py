# -*- coding: utf-8 -*-
"""`FINANCE_TDX(q)` 守卫（通达信源基本面，2026-10-09）。

守护：
1. q → 字段名 映射（1..9）与校验（报错要能自助）；
2. **编号与 `FINANCE(q)` 逐个对齐**（用户要求两源可交叉校验 ⇒ 同 q 同含义 ✓）；
3. **跨文件一致**：物化脚本 `tools/dump_finance_tdx.py` 用的就是 codegen 的表（再钉一次，防重构走偏 ✓）；
4. **PIT 规则**：可用日 = 法定披露截止日（保守），以及"往前数季度"的换算 ✓。
"""
import importlib.util
import os

import pytest

from app.factors.parser import translate_formula
from app.factors.parser.codegen import (
    FINANCE_FIELDS, FINANCE_TDX_FIELDS, CodeGenError,
)

_DUMP_TOOL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools", "dump_finance_tdx.py"))


def _load_dump_tool():
    if not os.path.exists(_DUMP_TOOL):
        pytest.skip("dump_finance_tdx.py 不存在（改名了 ⇒ 本测试同步改）")
    spec = importlib.util.spec_from_file_location("_dump_finance_tdx_under_test", _DUMP_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMapping:
    def test_q_1_to_9(self):
        expect = ["$ftdx_pe_ttm", "$ftdx_pb", "$ftdx_rev_yoy", "$ftdx_np_yoy", "$ftdx_gross_margin",
                  "$ftdx_roe", "$ftdx_roa", "$ftdx_eps", "$ftdx_op_yoy"]
        for i, expr in enumerate(expect, 1):
            assert translate_formula("OUT:FINANCE_TDX(%d);" % i).expression == expr

    def test_same_numbering_as_finance(self):
        """同 q 同含义（两源交叉校验的前提 ✓）—— 编号说明必须逐项一致。"""
        assert [d for _f, d in FINANCE_TDX_FIELDS] == [d for _f, d in FINANCE_FIELDS]
        assert len(FINANCE_TDX_FIELDS) == 9

    def test_lowercase_and_in_formula(self):
        assert translate_formula("OUT:finance_tdx(2);").expression == "$ftdx_pb"
        r = translate_formula("N:=6; OUT:FINANCE_TDX(N)>15;")
        assert r.expression == "Gt($ftdx_roe,15)"


class TestErrors:
    @pytest.mark.parametrize("text", ["OUT:FINANCE_TDX(0);", "OUT:FINANCE_TDX(10);"])
    def test_out_of_range(self, text):
        with pytest.raises(CodeGenError) as e:
            translate_formula(text)
        assert "1~9" in str(e.value) or "需在 1" in str(e.value)

    @pytest.mark.parametrize("text", ["OUT:FINANCE_TDX(CLOSE);", "OUT:FINANCE_TDX(1.5);", "OUT:FINANCE_TDX();"])
    def test_non_const(self, text):
        with pytest.raises(Exception):
            translate_formula(text)


class TestDumpToolConsistency:
    def test_field_table_shared(self):
        mod = _load_dump_tool()
        assert [f for f, _u in mod.FINANCE_TDX_FIELDS] == [f for f, _d in FINANCE_TDX_FIELDS]

    def test_pit_rule_is_legal_deadline(self):
        """可用日必须是**法定披露截止日**（保守：早于它的数字一律不许用 ✓）。"""
        mod = _load_dump_tool()
        assert mod.avail_date(20260331) == "2026-04-30"
        assert mod.avail_date(20260630) == "2026-08-31"
        assert mod.avail_date(20260930) == "2026-10-31"
        assert mod.avail_date(20261231) == "2027-04-30"

    def test_prev_quarter(self):
        mod = _load_dump_tool()
        assert mod.prev_quarter("2026q2") == "2026q1"
        assert mod.prev_quarter("2026q1") == "2025q4"
        assert mod.prev_quarter("2026q2", 3) == "2025q3"

    def test_semantics_version_declared(self):
        mod = _load_dump_tool()
        assert mod.FINANCE_TDX_SEMANTICS
        assert "截止日" in mod.__doc__
