# -*- coding: utf-8 -*-
"""通达信源基本面（`tools/dump_finance_tdx.py` + 已下线的 `FINANCE_TDX(q)`）。

★ 2026-10-10（用户第 4 项）：`FINANCE_TDX(q)` **下线** ✗ —— 公式语言里不再提供它
（写它会得到一条"请改用 FINANCE(q[,口径])"的友好错 ✓），
**但字段表与 `ftdx_*` bin 数据保留** ✓（只作多源复核用 ✓）。

本测试守护：
1. `FINANCE_TDX(q)` **确实不能用**、且报错要指向 `FINANCE` ✓；
2. 字段表仍在、且编号说明与 `FINANCE(q)` 逐个对齐（多源复核的前提 ✓）；
3. **PIT 规则**：可用日 = 法定披露截止日（保守），以及"往前数季度"的换算 ✓。
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


class TestRetired:
    def test_function_is_retired_with_friendly_error(self):
        """★ 下线后必须给**指向 FINANCE** 的友好错（不能只说"不支持的函数" ✗）。"""
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:FINANCE_TDX(1);")
        msg = str(e.value)
        assert "下线" in msg and "FINANCE(q[,口径])" in msg

    def test_numbering_still_aligned_with_finance(self):
        """字段表保留 ⇒ 编号说明必须与 `FINANCE(q)` 逐项一致（多源对拍的前提 ✓）。"""
        assert [d for _f, d in FINANCE_TDX_FIELDS] == [d for _f, d in FINANCE_FIELDS]
        assert len(FINANCE_TDX_FIELDS) == 10


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
