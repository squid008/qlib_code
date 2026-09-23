# -*- coding: utf-8 -*-
"""公式库 `expression` 缓存的**自动重编**（v1.20.59）。

背景（用户 2026-09-23「还是报错啊」✓）：`深跌10` 在 v1.20.58 修完之后，
**单因子测试仍然报** `'numpy.bool' object has no attribute 'name'` ✗ ——
因为**单因子测试直接求值库里缓存的 `expression`** ✗（`SingleFactorTestFactor.expression` ✓
前端从 `/api/factors/custom-formulas` 取 ✓），而**回测**那条链传的是**原文** ✓（会重译 ✓）
⇒ 出现最误导的现象：**代码修好了、回测能用，单因子测试照旧报错** ✗✓。

处理：`custom_formulas.refresh_stale_expressions()` —— 戳过期（**含没有戳**的旧条目 ✓）就按 `text`
重编 ✓；口径与 `qlib_engine.TRAIN_SEMANTICS` / `chip_store.CHIP_SEMANTICS` 一致 ✓。
"""
from app.factors.parser.codegen import CODEGEN_SEMANTICS
from app.services.custom_formulas import refresh_stale_expressions

STALE_SHEN_DIE = {
    "id": "x", "name": "深跌10", "codegen": "",
    "text": "n=10;\n深跌10:IF(n<3,CLOSE,MA(CLOSE,10));",
    "expression": "If(Lt(10,3),$close,Mean($close,10))",     # ← 修复前编译出来的旧串 ✓
}


def test_stale_entry_is_recompiled():
    items = [dict(STALE_SHEN_DIE)]
    assert refresh_stale_expressions(items) is True
    assert items[0]["expression"] == "Mean($close,10)"        # 旧串被换成新编译结果 ✓
    assert items[0]["codegen"] == CODEGEN_SEMANTICS
    assert refresh_stale_expressions(items) is False          # 已同步 ⇒ 不再动 ✓（幂等 ✓）


def test_bad_formula_keeps_old_expression():
    """编译失败 ⇒ 保留旧串、**不推进戳** ✓（下次还会重试 ✓；绝不能让列表接口 500 ✗）。"""
    items = [{"id": "y", "name": "坏公式", "codegen": "",
              "text": "用:没有这个变量;", "expression": "OLD"}]
    assert refresh_stale_expressions(items) is False
    assert items[0]["expression"] == "OLD" and not items[0].get("codegen")


def test_current_entry_untouched():
    """戳已是最新 ⇒ **一个字节都不动** ✓（避免每次列表请求都重编 60+ 条 ✗）。"""
    items = [{"id": "z", "name": "已同步", "codegen": CODEGEN_SEMANTICS,
              "text": "用:MA(CLOSE,10);", "expression": "Mean($close,10)"}]
    assert refresh_stale_expressions(items) is False
    assert items[0]["expression"] == "Mean($close,10)"


def test_library_visible_during_refresh():
    """重编时**库要能被公式间调用看到** ✓（口径与 `handler._translate_all` 一致 ✓）。"""
    items = [
        {"id": "a", "name": "基", "codegen": CODEGEN_SEMANTICS,
         "text": "基:MA(CLOSE,5);", "expression": "Mean($close,5)"},
        {"id": "b", "name": "用", "codegen": "", "text": "用:基>CLOSE;", "expression": "OLD"},
    ]
    assert refresh_stale_expressions(items) is True
    assert items[1]["expression"] == "Gt(Mean($close,5),$close)"
