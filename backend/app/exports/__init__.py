# -*- coding: utf-8 -*-
"""XLSX 导出 / 回灌子系统（v2.1.0 起）。

分工（**导出这块唯一的分层约定，别把职责揉在一起** ✓）：
  · `spec.py`   —— **对外契约**：sheet 名 / 列名 / 参数键 / 格式版本 + 读取器 `read_export()`。
                  「写」与「回灌读」**共用同一份定义** ⇒ 改列名不会两边脱钩 ✓；
  · `xl.py`     —— openpyxl 薄封装（标题 / KV / 表格 / 原生图表 / 样式），**零业务语义** ✓；
  · `topk.py`   —— 「单因子测试 · 持仓期收益曲线」→ 工作簿（只做 doc → sheet ✓）；
  · `event_study.py`（后续）—— 「事件研究」→ 工作簿；
  · `routers/export_xlsx.py` —— HTTP 层：取任务状态 → 组 doc → 返回字节流。

★ 为什么由**后端**生成、而不是前端用 SheetJS：
  ① 面板要"把曲线展示出来" ⇒ 需要**原生 Excel 图表**，而 SheetJS 社区版**画不了图** ✗
     （截图方案也不行：得引 `html2canvas`、出位图、且机器读不回来 ✗）；
  ② 「每期持仓明细」**根本没下发给前端**（测试进程内算完即丢 ✓）⇒ 只能后端取；
  ③ 回灌要读回参数与持仓 ⇒ 生成与读取用同一份契约 ✓ 前端只负责"按钮 + 下载" ✓（薄 ✓）。
"""
from .spec import (  # noqa: F401
    FORMAT_ID,
    FORMAT_VERSION,
    KIND_EVENT_STUDY,
    KIND_TOPK,
    SHEET_HOLDINGS,
    SHEET_META,
    SHEET_NOTES,
    SHEET_PARAMS,
    ExportDoc,
    read_export,
)
from .topk import TopkExportDoc, build_topk_workbook  # noqa: F401

__all__ = [
    "FORMAT_ID",
    "FORMAT_VERSION",
    "KIND_TOPK",
    "KIND_EVENT_STUDY",
    "SHEET_PARAMS",
    "SHEET_HOLDINGS",
    "SHEET_NOTES",
    "SHEET_META",
    "ExportDoc",
    "read_export",
    "TopkExportDoc",
    "build_topk_workbook",
]
