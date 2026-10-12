# -*- coding: utf-8 -*-
"""「事件研究」→ XLSX 工作簿（第 1 页 = 参数与面板内容，第 2 页 = 每期持仓）。

★ 为什么**文档映射与排版放同一个模块**（与 `topk.py` + `topk_doc.py` 的分法不同）：
  · topk 那套要"复刻面板"，文案/口径极多（分段算法、区间累计、成本敏感度…）⇒ 值得单独一层；
  · 事件研究这个页面**结构是平的**：一串"标题 + 说明 + 图"的区块 ⇒ 用 `sections` 列表装下即可 ✓
    （硬拆两层只会多出 5 个几乎空的 dataclass ✗）。哪天真长出复杂口径，再按 topk 那样拆 ✓。

页序：`参数与指标` / `持仓明细` / `说明` / 隐藏 `_meta` ✓（页名与 topk 共用契约 ⇒ 回灌侧一套读取器通用 ✓）。
图同样是**前端截图**（`TopkCurveModal` 那套 `captureChart.ts` ✓）—— 所见即所得 ✓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import spec, xl

Pair = Tuple[str, Any, Optional[str]]
# 一个"图块"：标题 / 说明文字（可空）/ 截图（可空 ⇒ 文件里提示去数值页 ✓）
Section = Tuple[str, str, Optional[bytes]]

_HOLD_COLS = spec.HOLDING_COLS[spec.KIND_EVENT_STUDY]
_PAGE_COLS = 11


@dataclass
class EventStudyExportDoc:
    """事件研究导出的全部内容（路由层填好 ✓）。"""

    factor_name: str
    headline: str = ""                       # 池子/区间/最长持有/触发次数 一行摘要
    subtitle: str = ""                       # 导出时间/工具版本/格式（小灰字）
    params: Dict[str, Any] = field(default_factory=dict)
    formula_pairs: List[Pair] = field(default_factory=list)
    param_pairs: List[Pair] = field(default_factory=list)
    kpi_title: str = "触发事件统计"
    kpi: List[Pair] = field(default_factory=list)
    # v2.1.1：摘要卡片（触发事件数/中位数/胜率…）**优先用面板截图** —— 用户 2026-10-11 明确要
    #   "触发事件统计这里也用截图的形式" ✓（单元格复刻的列宽挤、且容易与面板口径脱钩 ✗）。
    #   给了图就只用图；没给才退回单元格（老前端/抓图失败时仍可用 ✓）。
    kpi_png: Optional[bytes] = None
    sections: List[Section] = field(default_factory=list)
    # v2.1.1：净值曲线的**收益/回撤统计**（面板上那是 hover 才显示的 tooltip ⇒ 导出必须给成表，
    #   否则用户只能看到图、看不到"年化/最大回撤/夏普/卡玛"这些结论性数字 ✗ —— 用户点名的
    #   「少了收益回撤面板」正是这一块 ✓）
    nav_stats_title: str = "净值曲线统计（收益 / 回撤）"
    nav_stats_headers: List[str] = field(default_factory=list)
    nav_stats_rows: List[List[Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)      # 黄底口径/简化说明
    holdings: List[Dict[str, Any]] = field(default_factory=list)
    holdings_note: str = ""
    # ★ v2.1.3：**真实买入事件**（= 回测成交明细里的"买" ✓）⇒ 写进隐藏页 `_signals`，
    #   供「交易信号测试」**准确**复现净值曲线 ✓（用第 2 页的持仓快照会重复买入 ✗，
    #   实测 event_even 会放大到 11.8 倍 ✗）。字段：`{date, code, mode}` ✓
    buy_signals: List[Dict[str, Any]] = field(default_factory=list)


def build_event_study_workbook(doc: EventStudyExportDoc) -> bytes:
    """doc → xlsx bytes（失败向上抛 ⇒ 路由层转可读 500 ✓）。"""
    wb, ws1 = xl.new_workbook(spec.SHEET_PARAMS)
    ws_hold = wb.create_sheet(spec.SHEET_HOLDINGS)
    ws_notes = wb.create_sheet(spec.SHEET_NOTES)
    w = xl.SheetWriter(ws1)
    w.title("事件研究（%s）" % (doc.factor_name or "-"))
    if doc.headline:
        w.subtitle(doc.headline)
    if doc.subtitle:
        w.hint(doc.subtitle)
    w.blank()
    if doc.formula_pairs or doc.param_pairs:
        w.section("一、因子与参数", _PAGE_COLS)
        if doc.formula_pairs:
            w.kv(doc.formula_pairs, per_row=1)
        if doc.param_pairs:
            w.kv(doc.param_pairs, per_row=4)
        w.blank()
    if doc.kpi_png:
        w.section("二、%s" % doc.kpi_title, _PAGE_COLS)
        w.image_block(doc.kpi_png, width_cm=25.0, name="kpi")
        w.blank()
    elif doc.kpi:
        w.section("二、%s" % doc.kpi_title, _PAGE_COLS)
        w.kpi_row(doc.kpi[:6])
        w.blank()
    for i, (title, note, png) in enumerate(doc.sections, start=1):
        w.section("%s、%s" % (_cn_num(i + 2), title), _PAGE_COLS)
        if note:
            w.note(note)
        if png:
            w.image_block(png, width_cm=25.0, name="es%d" % i)
        else:
            w.note("· ⚠ 本次没带上截图（浏览器抓图未成功）⇒ 数值请看后端结果或重试导出 ✓")
        w.blank()
    if doc.nav_stats_rows:
        w.section("%s、%s" % (_cn_num(len(doc.sections) + 3), doc.nav_stats_title), _PAGE_COLS)
        w.table(doc.nav_stats_headers, doc.nav_stats_rows,
                {1: xl.NUM4, 2: xl.PCT1, 3: xl.PCT1, 4: xl.PCT1, 5: xl.RATIO, 6: xl.RATIO, 7: "0"})
        w.blank()
    if doc.notes:
        w.note_block("　".join(doc.notes), cols=_PAGE_COLS)
    ws1.column_dimensions["A"].width = 26
    ws1.column_dimensions["B"].width = 30

    # 第 2 页：每期持仓（列取自契约 ⇒ 回灌/核对都按表头 ✓）
    rows = [[h.get(key) for key, _zh in _HOLD_COLS] for h in doc.holdings]
    xl.SheetWriter(ws_hold).table([zh for _k, zh in _HOLD_COLS], rows,
                                  {3: "0", 4: "0.0000", 5: "#,##0.00", 6: xl.PCT},
                                  freeze=True)
    ws_hold.column_dimensions["A"].width = 14
    ws_hold.column_dimensions["C"].width = 12
    ws_hold.column_dimensions["B"].width = 12

    # 第 3 页：说明
    wn = xl.SheetWriter(ws_notes)
    wn.title("导出说明（%s / 格式 %s v%s）" % (spec.KIND_EVENT_STUDY, spec.FORMAT_ID,
                                               spec.FORMAT_VERSION))
    wn.blank()
    for line in [
        "· 第 1 页「%s」= 参数与公式 + 触发事件统计 + 面板各区块的**截图** + 口径说明 ✓。" % spec.SHEET_PARAMS,
        "· 第 2 页「%s」= **每次调仓的持仓明细**（由回测成交明细派生的「买 − 卖」持仓快照 ✓）："
        "调仓日 / 资金方案 / 代码 / 股数 / 成交价 / 成交金额 / 权重 ✓。" % spec.SHEET_HOLDINGS,
        "· 权重 = 该股「股数 × 最近成交价」占**同日同方案**总市值的比例（用成交价估算，非收盘市值 ✓）。",
        "· 回灌：把本文件拖入「交易信号测试」的导入区 ⇒ 自动按第 1 页参数对齐设置（持仓周期/成本/基准），"
        "并用第 2 页持仓复现净值曲线 ✓。",
        "· 隐藏页「%s」是机器可读参数（回灌读它）；**请勿删除或改动** ✗。" % spec.SHEET_META,
        doc.holdings_note,
    ]:
        if line:
            wn.note(line)
    ws_notes.column_dimensions["A"].width = 130

    spec.write_signals_sheet(wb, doc.buy_signals)      # v2.1.3：真实买入信号（隐藏页 ✓）
    spec.write_meta_sheet(wb, doc.params)
    return xl.workbook_bytes(wb)


def _cn_num(n: int) -> str:
    """3 → 三、4 → 四 …（本章只需 3~8，够用 ✓；超出就退回数字）。"""
    names = "一二三四五六七八九十"
    return names[n - 1] if 1 <= n <= len(names) else str(n)


def contract_params(*, factor_name: str, factor_expression: str, universe: str, start: str, end: str,
                    max_k: Any, hold_days: Any, cost: Any, capital: Any, benchmark: str,
                    app_version: str, generated_at: str,
                    fill: str = "t1_open", strict_limit: Any = True,
                    rebal_band: Any = 0.0) -> Dict[str, Any]:
    """导出契约参数（写隐藏页 ⇒ 回灌侧据此**自动对齐设置** ✓）。

    ⚠ `hold_days`/`cost`/`benchmark` 三个键与单因子测试导出**同名同义** ⇒ 回灌侧一套逻辑通用 ✓。

    ★★ v2.1.4：`fill`/`strict_limit`/`rebal_band` **必须由调用方把"面板这次回测真正用的值"
      传进来** ✗→✓。以前这里硬编码 `t1_close`（理由写在注释里："面板标题写着 T+1 收盘" ✗）——
      但面板**净值曲线**真正的引擎参数是 `fill="t1_open"`（见 `routers/factors.py` 里
      `run_backtest(... fill="t1_open" ...)`）⇒ 文件与面板**不一致**，回灌必然对不上 ✗
      （用户 2026-10-11："难道不该逐位一致" ✓）。**曲线标题的口径 ≠ 净值引擎的口径**，
      搬运时必须搬**引擎的** ✓。
    """
    return {
        spec.P_KIND: spec.KIND_EVENT_STUDY,
        spec.P_APP_VERSION: app_version,
        spec.P_GENERATED_AT: generated_at,
        spec.P_FACTOR_NAME: factor_name,
        spec.P_FACTOR_EXPR: factor_expression,
        spec.P_UNIVERSE: universe,
        spec.P_START_DATE: start,
        spec.P_END_DATE: end,
        spec.P_MAX_K: max_k,
        spec.P_HOLD_DAYS: hold_days,
        spec.P_FILL: fill,                       # 面板净值引擎实际用的成交时点 ✓
        spec.P_STRICT_LIMIT: strict_limit,       # 涨跌停约束（严格 ⇒ 一字板买不到 ✓）
        spec.P_REBAL_BAND: rebal_band,           # 再平衡死区（面板 event_even 用 0 ✓）
        spec.P_COST: cost,
        spec.P_CAPITAL: capital,
        spec.P_BENCHMARK: benchmark,
    }


def param_pairs_from(params: Dict[str, Any], skip: Sequence[str] = ()) -> List[Pair]:
    """契约参数 → 第 1 页参数块（`skip` 里已单独展示过的键不再重复 ✓）。"""
    skip_labels = {spec.PARAM_LABELS.get(k, "") for k in skip}
    return [p for p in spec.param_display_rows(params) if p[0] not in skip_labels]
