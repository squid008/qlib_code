# -*- coding: utf-8 -*-
"""导出文件必须**写清数据集**（★ 2026-10-14 ✓，用户："EXCEL 里好像分辨不出是米筐还是 TUSHARE" ✗）。

**为什么锁这几件事**：
  · 米筐（`cn_data`）与 tushare（`cn_data2`）两份导出，参数**逐项相同** ✗ ⇒ 不写数据集就**完全不可辨** ✗，
    回头对照"今天结果为什么高"时第一步就卡住 ✓（2026-10-14 实测 ✓）；
  · 数据集写进**机器可读**的隐藏页 `_meta`（`dataset` / `dataset_label` ✓）⇒ `read_export` 要能读回来 ✓，
    且**与当前生效数据集不一致时要告警** ✓（拿 tushare 的文件在米筐上回灌 ⇒ 数值必然不同 ✓，别当算法变了 ✗）；
  · 人读的第 1 页小灰字标题行也要带「数据集 X（口径）」✓。
"""
import os

from app.exports import spec


def _wb_with(dataset: str):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = spec.SHEET_PARAMS          # ⚠ 必须留一个**可见**页 ✓：
    #   openpyxl 不允许唯一的工作表被隐藏 ✗（`The only worksheet of a workbook cannot be hidden` ✓）
    params = {spec.P_KIND: "event_study", spec.P_APP_VERSION: "9.9.9",
              spec.P_GENERATED_AT: "2026-10-14 08:30:00"}
    spec.write_meta_sheet(wb, params)
    ws = wb[spec.SHEET_META]
    for row in ws.iter_rows(min_row=2, max_col=2):
        if row[0].value == spec.K_DATASET:
            row[1].value = dataset
    return wb


def test_dataset_tag_matches_active_dataset():
    t = spec.dataset_tag()
    assert t["name"], "拿不到当前数据集名 ✗（active_dataset.json / QLIB_PROVIDER_URI 出问题了 ✓）"
    # 与 `data/<ds>/.dataset.json` 同源 ⇒ 口径名应能读到 ✓（cn_data = 米筐 / cn_data2 = tushare ✓）
    assert t["label"] or t["convention"]


def test_dataset_line_is_human_readable():
    line = spec.dataset_line()
    assert line.startswith("数据集 ")
    assert spec.dataset_tag()["name"] in line


def test_meta_sheet_carries_dataset_and_reads_back():
    from io import BytesIO
    wb = _wb_with(spec.dataset_tag()["name"])
    bio = BytesIO()
    wb.save(bio)
    doc = spec.read_export(bio.getvalue())
    assert doc.dataset == spec.dataset_tag()["name"], "`_meta.dataset` 没写对/没读回 ✗"
    assert doc.dataset_label.startswith("数据集 ")
    # 数据集一致 ⇒ **不该**有那条告警 ✓
    assert not any("当前生效的是" in w for w in doc.warnings), doc.warnings


def test_warn_when_dataset_differs():
    from io import BytesIO
    cur = spec.dataset_tag()["name"]
    other = "cn_data2" if cur != "cn_data2" else "cn_data"
    wb = _wb_with(other)
    bio = BytesIO()
    wb.save(bio)
    doc = spec.read_export(bio.getvalue())
    assert doc.dataset == other
    assert any("当前生效的是" in w for w in doc.warnings), "换源回灌没告警 ✗（会让人以为是算法变了 ✓）"


def test_label_map_untouched():
    """`_meta` 新增行**不能**进 `PARAM_LABELS` ✗ —— 那是回灌参数契约 ✓（加了会污染参数回填 ✓）。"""
    assert spec.K_DATASET not in spec.PARAM_LABELS
    assert spec.K_DATASET_LABEL not in spec.PARAM_LABELS
    assert os.path.basename(spec.FORMAT_ID) == spec.FORMAT_ID
