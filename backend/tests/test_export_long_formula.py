# -*- coding: utf-8 -*-
"""超长公式 / 超长参数的导出行为（★ 2026-10-14 ✓，用户："小毛病改一下" ✓）。

**为什么要锁**：`AR13_ID60` 的表达式 **92,933 字符** ✗ ⇒
  · Excel 单元格上限 **32767** ⇒ 第 1 页「原文公式」被**静默截断** ✗（实测正好 32767 ✓）；
  · `_meta.params_json` 超 32000 ⇒ 旧代码**整份不写** ✗（只留 `params_json_truncated=True` ✓）
    ⇒ 回灌**一个参数都读不到** ✗（用户那份导出就是这状态 ✓）。
新行为 ✓：**我们自己截 + 在文本里注明** ✓（`params_json_trimmed` / `params_json_dropped` 记名 ✓），
回灌能读到**参数**（只是长字段带截断标记 ✓），且 `read_export` 会**告警** ✓（不静默 ✗）。
"""
from io import BytesIO

from openpyxl import load_workbook

from app.exports import spec

LONG = "And(" + ",".join("Gt($fin_pe_ttm,%d)" % i for i in range(12000)) + ")"   # ≈ 20 万字符 ✓
assert len(LONG) > 150000


def _wb(params):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = spec.SHEET_PARAMS
    spec.write_meta_sheet(wb, params)
    return wb


def test_short_text_marks_truncation():
    s = spec.short_text(LONG)
    assert len(s) <= spec.CELL_SAFE
    assert "已截断" in s and "完整文本见" in s
    assert spec.short_text("abc") == "abc"                     # 短的原样 ✓


def test_formula_cells_note_includes_original_length():
    rows = spec.formula_cells("SRC", LONG)
    assert rows[0][0] == "原文公式" and rows[1][0] == "具体公式（表达式）"
    assert len(rows[0][1]) <= spec.CELL_SAFE
    assert any("公式长度" in r[0] and str(len(LONG)) in r[1] for r in rows), rows
    assert len(spec.formula_cells("a", "b")) == 2               # 短公式不加说明行 ✓


def test_meta_keeps_params_readable_for_long_expression():
    params = {spec.P_KIND: "event_study", spec.P_APP_VERSION: "9.9.9",
              spec.P_GENERATED_AT: "2026-10-14 12:00:00",
              spec.P_FACTOR_NAME: "AR13_ID60", spec.P_FACTOR_EXPR: LONG,
              spec.P_UNIVERSE: "all", spec.P_HOLD_DAYS: 60}
    wb = _wb(params)
    ws = wb[spec.SHEET_META]
    kv = {str(r[0].value): r[1].value for r in ws.iter_rows(min_row=2, max_col=2)}
    assert "params_json" in kv, "超长参数居然整份不写 ✗（旧 bug ✓）"
    assert not kv.get("params_json_truncated")
    assert spec.P_FACTOR_EXPR in str(kv.get("params_json_trimmed") or "")

    bio = BytesIO()
    wb.save(bio)
    doc = spec.read_export(bio.getvalue())
    # ★ 关键：**参数还在** ✓（长表达式被截断打标 ✓，短参数原样 ✓）
    assert doc.p(spec.P_UNIVERSE) == "all"
    assert int(float(doc.p(spec.P_HOLD_DAYS))) == 60
    assert doc.p(spec.P_FACTOR_NAME) == "AR13_ID60"
    assert "已截断" in str(doc.p(spec.P_FACTOR_EXPR))
    assert any("超长字段被截断" in w for w in doc.warnings), doc.warnings


def test_meta_drops_longest_when_still_too_big():
    # 截断到 `META_VALUE_CAP` 后**仍**可能超预算（这里 12 × 4000 ≈ 48k ✗）⇒ 丢最长的键，并**记名** ✓
    params = {spec.P_KIND: "topk", spec.P_FACTOR_EXPR: LONG, spec.P_UNIVERSE: "all"}
    for i in range(12):
        params["bulk_%02d" % i] = chr(97 + i % 26) * 200000
    trimmed, tk, dk = spec.trim_params_for_meta(params)
    assert tk and dk, (len(tk), len(dk))
    assert spec.P_FACTOR_EXPR in tk
    assert all(k.startswith("bulk_") for k in dk)              # 丢的只能是超长批量键 ✓
    assert trimmed.get(spec.P_UNIVERSE) == "all"                # 短参数绝不能丢 ✓
    wb = _wb(params)
    kv = {str(r[0].value): r[1].value for r in wb[spec.SHEET_META].iter_rows(min_row=2, max_col=2)}
    assert "params_json_dropped" in kv
    bio = BytesIO()
    wb.save(bio)
    doc = spec.read_export(bio.getvalue())
    assert any("因过长被丢弃" in w for w in doc.warnings), doc.warnings
    assert doc.p(spec.P_UNIVERSE) == "all"
