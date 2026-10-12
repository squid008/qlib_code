# -*- coding: utf-8 -*-
"""「单因子测试 · 持仓期收益曲线」→ XLSX 工作簿（**整页复刻面板** + 持仓明细）。

★ 与第一版（被用户否掉的那版）的区别（2026-10-11 用户反馈）：
  · 第一版是"**数据转储**"：参数块 + 一张表 + 一条把所有列都画进去的图（连**换手**都画成线 ✗），
    用户评价"排版太丑 ✗"；
  · 本版**按面板的分区顺序与文案复刻**：标题/摘要 → 参数与公式 → 曲线口径 → 组合·基准绩效卡
    → 持仓期净值曲线（原生图，配色与页面一致）→ 黄色"口径说明"块 → 十分位累计收益曲线
    （10 条分位线 + 多空**次轴**）→ 成本敏感度表 ✓；图上**只画曲线**，换手只进表/数据页 ✓。

页序：
  1. `参数与指标`  —— 面板复刻（上面那串 ✓）；
  2. `持仓明细`    —— 每期持仓（供「交易信号测试」回灌复现净值 ✓）；
  3. `曲线数据`    —— 两张图的数据源（图表引用它 ⇒ 第 1 页保持"像面板" ✓）；
  4. `说明`        —— 口径/简化假设/回灌方法（人读 ✓）；
  隐藏页 `_meta`   —— 机器可读参数（回灌读它 ✓）。

⚠ 本模块只做"doc → sheet"的组装：算法/取数/文案都在路由层（`routers/export_xlsx.py`）决定 ✓
  —— 这样"面板改一个数字/一句话"只需改一处，两边不会各自漂移 ✓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl.utils import get_column_letter

from . import spec, xl

Pair = Tuple[str, Any, Optional[str]]


@dataclass
class TopkExportDoc:
    """一本「持仓期收益曲线」工作簿所需的**全部**内容（路由层按面板结构填好 ✓）。"""

    # ---- ① 标题区（面板标题栏）----
    factor_name: str
    headline: List[str]                       # 标题下摘要行（调仓期/次数/分位档、分段、单调性 ✓）
    title: str = "持仓期收益曲线（含三档成本）"

    # ---- ② 因子与公式 + 详细参数 ----
    params: Dict[str, Any] = field(default_factory=dict)   # 契约参数（写隐藏页 ✓）
    formula_pairs: List[Pair] = field(default_factory=list)  # 因子名/ID/原文公式/具体公式 ✓
    param_pairs: List[Pair] = field(default_factory=list)    # 池子/区间/调仓期/成本/基准… ✓

    # ---- ③ 曲线口径 ----
    basis_note: str = ""

    # ---- ④ 绩效卡（组合 / 基准）----
    cost_tier_label: str = ""
    combo_title: str = ""
    combo_kpi: List[Pair] = field(default_factory=list)     # 年化/最大回撤/夏普/索提诺/卡玛 ✓
    combo_sub: str = ""                                     # 波动｜日胜率｜样本｜口径｜三档年化 ✓
    bench_title: str = ""
    bench_kpi: List[Pair] = field(default_factory=list)
    bench_sub: str = ""

    # ---- ⑤ 图 1：持仓期净值曲线 ----
    nav_title: str = ""
    nav_switches: str = ""                    # 组合/基准切换说明（等价于页面那排按钮 ✓）
    nav_note: str = ""
    nav_summary: str = ""                     # 区间累计（复利）：组合…｜基准…｜⇒ 超额… ✓

    # ---- ⑥ 口径说明（黄底）----
    note_a: str = ""

    # ---- ⑦ 图 2：十分位累计收益曲线 ----
    dec_title: str = ""
    dec_note: str = ""

    # ---- ⑧ 成本敏感度表 ----
    sens_title: str = ""
    sens_headers: List[str] = field(default_factory=list)
    sens_rows: List[List[Any]] = field(default_factory=list)
    sens_note: str = ""

    # ---- ⑨ 两张图的**截图**（前端按当前档位渲染的 PNG ⇒ 所见即所得 ✓）+ 数值数据 ----
    # ⚠ 用图片而不是 openpyxl 原生图表：原生图在部分 Excel/WPS 里**渲染不出来**（用户实测空白 ✗），
    #   且用户要的就是"我参数选到那档就截图那档" ✓（抓图在前端，见 `frontend/src/captureChart.ts`）。
    nav_png: Optional[bytes] = None
    dec_png: Optional[bytes] = None
    nav_headers: List[str] = field(default_factory=list)
    nav_data: List[List[Any]] = field(default_factory=list)
    dec_headers: List[str] = field(default_factory=list)
    dec_data: List[List[Any]] = field(default_factory=list)

    # ---- ⑩ 第 2 页持仓 + 说明 ----
    holdings: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    subtitle: str = ""                        # 工具版本/导出时间/格式（写在标题行下方 ✓）


# 图表占位高度（行数）：原生图是"浮"在单元格上的 ⇒ 必须留白，否则文字被图压住 ✗
# 第 1 页实际用到的列数（参数块 `per_row=4` 会占到 K 列 ✓）—— 分区标题/注释块合并到这一列 ✓
_PAGE_COLS = 11


def build_topk_workbook(doc: TopkExportDoc) -> bytes:
    """doc → xlsx bytes（失败向上抛 ⇒ 路由层转成可读 500 ✓，不静默出半本书 ✗）。

    ⚠ 顺序有讲究：**先按"页序"建好各 sheet**（openpyxl 的页序 = 创建顺序 ✓），
      再先写"曲线数据"页拿到 `TableRef`（图表要引用它 ✓），最后回头写第 1 页
      —— 这样既能保证「持仓明细」稳稳是第 2 页（用户要求 ✓），又不必手算行号 ✗。
    """
    wb, ws1 = xl.new_workbook(spec.SHEET_PARAMS)
    ws_hold = wb.create_sheet(spec.SHEET_HOLDINGS)
    ws_data = wb.create_sheet(spec.SHEET_DATA)
    ws_notes = wb.create_sheet(spec.SHEET_NOTES)
    nav_ref, dec_ref = _write_data_sheet(ws_data, doc)
    w = xl.SheetWriter(ws1)
    _write_header(w, doc)
    _write_params(w, doc)
    _write_basis(w, doc)
    _write_perf_cards(w, doc)
    _write_nav_chart(w, doc)
    _write_decile_chart(doc, w)
    _write_sensitivity(w, doc)
    ws1.column_dimensions["A"].width = 26
    ws1.column_dimensions["B"].width = 30
    _write_holdings_sheet(ws_hold, doc)
    _write_notes_sheet(ws_notes, doc)
    # ★ v2.1.3：隐藏页「买入信号」—— 口径 = **每期组合整体买入**（持仓曲线模型就是
    #   "每期换到新组合、持有到下一个调仓日" ✓，故每期的持仓都是该期的买入 ✓，
    #   且 `hold_days = 调仓期` ⇒ 信号在下一期恰好到期 ✓，与回灌引擎的持有语义一致 ✓）。
    #   ⚠ 不能用"每期快照"当信号：同一只票跨期重复出现会被重复买入 ✗（见 `spec.SHEET_SIGNALS` 注释 ✓）。
    #   ⚠ v2.1.7：**不再写 `bucket`** ✗ —— v2.1.6 曾按"每期一批"合成它，但那会启用引擎的
    #     `batch_even`（"每批固定 1/N 份额" ✗）⇒ 与"每期整体换仓"不是一回事（用户实测差 53pp ✓）。
    #     批次只用于"一周 5 个并发批次"那类信号（如同事的 `持仓周期` 列 ✓）。
    spec.write_signals_sheet(wb, [{"date": h.get("rebalance_date"), "code": h.get("code"),
                                   "mode": "", "bucket": ""}
                                  for h in doc.holdings])
    spec.write_meta_sheet(wb, doc.params)
    return xl.workbook_bytes(wb)


# ---------------------------------------------------------------- 第 1 页各分区
def _write_header(w, doc: TopkExportDoc) -> None:
    """标题栏：大标题 → **面板那两行摘要** → 导出元信息（版本/时间/格式，小灰字 ✓）。

    ⚠ 顺序有意：先摘要（与页面一致 ✓）、元信息放最后 —— 元信息是我们的记账，不是面板内容 ✓。
    """
    w.title(doc.title)
    for line in doc.headline:
        if line:
            w.subtitle(line)
    if doc.subtitle:
        w.hint(doc.subtitle)
    w.blank()


def _write_params(w, doc: TopkExportDoc) -> None:
    """因子与公式（用户点名要"详细参数、用到的公式名和具体公式" ✓）+ 其余参数 ✓。"""
    w.section("一、因子与参数", _PAGE_COLS)
    if doc.formula_pairs:
        w.kv(doc.formula_pairs, per_row=1)
    if doc.param_pairs:
        w.kv(doc.param_pairs, per_row=4)
    w.blank()


def _write_basis(w, doc: TopkExportDoc) -> None:
    if not doc.basis_note:
        return
    w.section("二、曲线口径", _PAGE_COLS)
    w.note(doc.basis_note)
    w.blank()


def _write_perf_cards(w, doc: TopkExportDoc) -> None:
    """组合 / 基准两张指标卡（与页面 `PerfCard` 同结构：5 格 + 一行补充说明 ✓）。"""
    if not (doc.combo_kpi or doc.bench_kpi):
        return
    w.section("三、组合 / 基准绩效（成本档：%s）" % (doc.cost_tier_label or "-"), _PAGE_COLS)
    for title, kpi, sub in ((doc.combo_title, doc.combo_kpi, doc.combo_sub),
                            (doc.bench_title, doc.bench_kpi, doc.bench_sub)):
        if not (kpi or title):
            continue
        w.caption(title)
        if kpi:
            w.kpi_row(kpi)
        if sub:
            w.note(sub)
    w.blank()


def _write_nav_chart(w, doc: TopkExportDoc) -> None:
    """图 1：持仓期净值曲线（**贴前端截图** ⇒ 与页面像素一致 ✓；换手不进图 ✓）。"""
    if not doc.nav_title and not doc.nav_png:
        return
    w.section("四、持仓期净值曲线", _PAGE_COLS)
    if doc.nav_title:
        w.subtitle(doc.nav_title)
    if doc.nav_switches:
        w.note(doc.nav_switches)
    if doc.nav_note:
        w.note(doc.nav_note)
    if doc.nav_png:
        w.image_block(doc.nav_png, width_cm=25.0, name="nav")
    else:
        w.note("· ⚠ 本次没带上曲线截图（浏览器抓图未成功）⇒ 数值请看第 3 页「曲线数据」✓")
    if doc.nav_summary:
        w.note(doc.nav_summary)
    w.blank()
    if doc.note_a:
        w.note_block(doc.note_a, cols=_PAGE_COLS)
        w.blank()


def _write_decile_chart(doc: TopkExportDoc, w) -> None:
    """图 2：十分位累计收益曲线（10 条分位线 + 多空**次轴**虚线 —— 同样贴截图 ✓）。"""
    if not doc.dec_title and not doc.dec_png:
        return
    w.section("五、十分位累计收益曲线", _PAGE_COLS)
    if doc.dec_title:
        w.subtitle(doc.dec_title)
    if doc.dec_note:
        w.note(doc.dec_note)
    if doc.dec_png:
        w.image_block(doc.dec_png, width_cm=25.0, name="decile")
    else:
        w.note("· ⚠ 本次没带上截图（浏览器抓图未成功）⇒ 数值请看第 3 页「曲线数据」✓")
    w.blank()


def _write_sensitivity(w, doc: TopkExportDoc) -> None:
    if not doc.sens_rows:
        return
    w.section("六、%s" % (doc.sens_title or "成本敏感度"), _PAGE_COLS)
    w.table(doc.sens_headers, doc.sens_rows,
            {1: xl.PCT, 2: xl.PCT, 3: xl.PCT, 4: xl.PCT, 5: xl.PCT})
    if doc.sens_note:
        w.note_block(doc.sens_note, cols=_PAGE_COLS, box=False, per_line=64)
    w.blank()


def _write_data_sheet(ws, doc: TopkExportDoc) -> Tuple[Optional[xl.TableRef], Optional[xl.TableRef]]:
    """第 3 页：两张图的数据源（第 1 页只放图 ⇒ 看着像面板 ✓，数据仍可核对 ✓）。"""
    if not (doc.nav_data or doc.dec_data):
        return None, None
    w = xl.SheetWriter(ws)
    nav_ref = dec_ref = None
    if doc.nav_data:
        w.caption("图 1 · 持仓期净值曲线（起点 = 1；%s）"
                  % (doc.cost_tier_label or "三档成本"))
        nav_ref = w.table(doc.nav_headers, doc.nav_data,
                          {i: xl.PCT for i in range(1, len(doc.nav_headers))})
        w.blank(2)
    if doc.dec_data:
        w.caption("图 2 · 十分位累计收益曲线（起点 = 0；多空为次轴 ✓）")
        dec_ref = w.table(doc.dec_headers, doc.dec_data,
                          {i: xl.PCT for i in range(1, len(doc.dec_headers))})
        w.blank()
        w.note("· 多空 = 最强 Q − 最弱 Q 的逐期价差（不可实现，仅作参考 ✓）；"
               "表中数值即图 2 所画的点 ✓。")
    ws.column_dimensions["A"].width = 14
    for i in range(2, 14):
        ws.column_dimensions[get_column_letter(i)].width = 13
    return nav_ref, dec_ref


# ---------------------------------------------------------------- 第 2 页：持仓明细
def _write_holdings_sheet(ws, doc: TopkExportDoc) -> None:
    """每期持仓（列顺序/表头**全部取自契约** ⇒ 回灌按表头反查 ✓）。"""
    cols = spec.HOLDING_COLS[spec.KIND_TOPK]
    rows = [[h.get(key) for key, _zh in cols] for h in doc.holdings]
    # 只有这一页值得冻表头（表头在第 1 行、数据可能几千行 ✓）——别处一律不冻 ✗（会滚不动）
    xl.SheetWriter(ws).table([zh for _k, zh in cols], rows, {2: "0", 3: xl.PCT, 4: "0"},
                             freeze=True)
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["C"].width = 12


# ---------------------------------------------------------------- 第 4 页：说明
def _write_notes_sheet(ws, doc: TopkExportDoc) -> None:
    """说明（口径 + 回灌方法）。⚠ 写进导出文件 ⇒ 用户/同事拿到就懂，不必回头问 ✓。"""
    w = xl.SheetWriter(ws)
    w.title("导出说明（%s / 格式 %s v%s）" % (spec.KIND_TOPK, spec.FORMAT_ID, spec.FORMAT_VERSION))
    w.blank()
    generic = [
        "· 第 1 页「%s」= **按页面面板复刻**：标题摘要 → 因子与参数（含公式名与公式原文）→ 曲线口径 "
        "→ 组合/基准绩效 → 持仓期净值曲线 → 黄色口径说明 → 十分位累计收益曲线 → 成本敏感度表 ✓。"
        % spec.SHEET_PARAMS,
        "· 第 2 页「%s」= 每期持仓明细（调仓日 / 序号 / 代码 / 等权权重 / 当期只数）✓。" % spec.SHEET_HOLDINGS,
        "· 第 3 页「曲线数据」= 两张图的数据源（放在独立页 ⇒ 第 1 页保持「像面板」、同时数据可核对 ✓）。",
        "· 曲线为「每期等权持有到下一个调仓日」的累计口径；成本按调仓期扣"
        "（`成本_t = 费率 × 单边换手_t`），首期建仓不计费 ✓（换手只进表，**不画成曲线** ✓）。",
        "· 回灌：把本文件拖入「交易信号测试」的导入区 ⇒ 自动按第 1 页参数对齐设置（持仓周期/成本/基准），"
        "并用第 2 页持仓复现净值曲线 ✓。",
        "· 隐藏页「%s」是机器可读参数（回灌读它）；**请勿删除或改动** ✗。" % spec.SHEET_META,
    ]
    for line in generic + list(doc.notes or []):
        w.note(line)
    ws.column_dimensions["A"].width = 130
