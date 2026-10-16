# -*- coding: utf-8 -*-
"""「离场曲线」导出（v2.8.8b，用户 2026-10-16 ✓）—— **第 1 页截图 + 每个离场一页调仓记录** ✓

用户原话（两段 ✓）：
  「然后离场曲线也要能导出**调仓记录**和**曲线截图**……第一页是截图，第二页是调仓记录。
   **方便我检查离场逻辑**」✓
  「汇总这里就也加导出吗？**第一页是截图**，后面**有几个离场因子，就加几个页面**导出」✓

⇒ 本模块按此定稿：
  · **第 1 页 = 曲线截图** ✓（前端按**当前显隐**抓的 PNG ⇒ 所见即所得 ✓）+ 参数/口径/页目录；
  · 之后**每个离场一页**（含「对照（不离场）」一页 ✓ —— 没有它就没法回答"离场到底改了什么" ✓）；
  · 每页正文 = 该次「每期全仓滚动」的**调仓记录**（成交明细 ✓）；`原因` 列会明写
    `exit_signal`（**离场卖出** ✓）/ `sell_expiry`（持有到期 ✓）/ `sell_trim_units`（超配减仓 ✓）
    —— 用户就是靠这一列检查离场逻辑 ✓；
  · 数据来源 = `routers/factors.pr_bt_stash_get`（回测时**顺手登记**的成交明细 ✓）⇒
    与页面那条曲线**同一次回测** ✓（不重跑、不漂移 ✓ —— 重跑很可能跑出另一批数字 ✗）。

⚠ 与 `topk.py` / `event_study.py` 的分工一致：本模块只做 **doc → sheet** ✓；
  取数、文案、页名清洗在路由层（`routers/export_xlsx.py`）决定 ✓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import xl

SHEET_SHOT = "曲线截图"                  # 第 1 页（用户指定：第一页是截图 ✓）
COL_SHOT = 10                            # 第 1 页用到的列数（分区标题/说明块合并到这一列 ✓）
TRADE_HEADERS = ["日期", "代码", "方向", "股数", "价格", "金额", "费用", "原因"]

#: 成交原因 → 人话 ✓（**这张表就是"检查离场逻辑"的钥匙** ✗→✓）
REASON_LABELS: Dict[str, str] = {
    "buy_signal": "买入（进场信号）",
    "buy_roll_in": "买入（加仓）",
    "buy_rebalance": "买入（补低配）",
    "sell_signal": "卖出（卖出信号）",
    "sell_expiry": "卖出（持有到期）",
    "exit_signal": "★离场卖出（离场信号触发）",
    "sell_trim_units": "卖出（超配减仓）",
    "sell_trim": "卖出（减仓）",
    "sell_rebalance": "卖出（再平衡减仓）",
    "sell_roll_trim": "卖出（减仓）",
}


@dataclass
class ExitRunDoc:
    """一条曲线（对照 或 某条离场）的一页 ✓。"""

    sheet: str = "页"
    title: str = ""
    headline: List[str] = field(default_factory=list)
    trades: List[List[Any]] = field(default_factory=list)
    note: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitCurveDoc:
    """整本工作簿 ✓（第 1 页截图 + N 个离场页 ✓）。"""

    title: str = "离场曲线"
    headline: List[str] = field(default_factory=list)
    subtitle: str = ""
    param_pairs: List[tuple] = field(default_factory=list)
    shot_png: Optional[bytes] = None
    shot_title: str = ""
    shot_note: str = ""
    runs: List[ExitRunDoc] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def build_exit_curve_workbook(doc: ExitCurveDoc) -> bytes:
    """doc → xlsx bytes（失败向上抛 ⇒ 路由转成可读 500 ✓，绝不静默出半本书 ✗）。"""
    wb, ws1 = xl.new_workbook(SHEET_SHOT)          # ★ 第 1 页 = 截图（创建顺序即页序 ✓）
    pages = [(run, wb.create_sheet(run.sheet)) for run in doc.runs]

    # ---------------------------------------------------------------- 第 1 页：截图
    w = xl.SheetWriter(ws1)
    w.title(doc.title)
    for line in doc.headline:
        if line:
            w.subtitle(line)
    if doc.subtitle:
        w.hint(doc.subtitle)
    w.blank()
    w.section("一、曲线截图", COL_SHOT)
    if doc.shot_title:
        w.subtitle(doc.shot_title)
    if doc.shot_png:
        w.image_block(doc.shot_png, width_cm=25.0, name="exit_curve")
    else:
        w.note("· ⚠ 本次**没带上曲线截图**（浏览器抓图未成功）⇒ 曲线读数看下面的「参数与口径」，"
               "离场逻辑看各离场页的「调仓记录」✓")
    if doc.shot_note:
        w.note(doc.shot_note)
    w.blank()
    if doc.param_pairs:
        w.section("二、参数与口径", COL_SHOT)
        w.kv(doc.param_pairs, per_row=1)
        w.blank()
    w.section("三、本文件包含的页", COL_SHOT)
    for run in doc.runs:
        w.note("· 「%s」= %s%s" % (run.sheet, run.title,
                                 ("　｜　" + "　｜　".join(run.headline)) if run.headline else ""))
    w.blank()
    for line in doc.notes:
        w.note(line)
    ws1.column_dimensions["A"].width = 118

    # ---------------------------------------------------------------- 各离场页：调仓记录
    for run, ws in pages:
        wr = xl.SheetWriter(ws)
        wr.title(run.title)
        for line in run.headline:
            if line:
                wr.subtitle(line)
        wr.blank()
        if run.trades:
            wr.table(TRADE_HEADERS, run.trades, freeze=True)
            wr.blank()
            wr.note("· `原因` 列就是**离场逻辑的证据** ✓：`%s` = 离场信号触发卖出；"
                    "`%s` = 持有到期卖出；`%s` = 超配减仓；其余为常规买卖 ✓。"
                    % (REASON_LABELS["exit_signal"], REASON_LABELS["sell_expiry"],
                       REASON_LABELS["sell_trim_units"]))
            wr.note("· 方向/股数/价格/金额/费用都是**真实口径**（真实股数 × 真实价 ✓，"
                    "账目仍按复权单位算、金额恒等 ✓）；与页面上那条曲线同一次回测 ✓。")
        else:
            wr.note(run.note or ("· ⚠ 这一条**没有调仓记录**（该档未回测、或已超出后端的保留上限）"
                                 "⇒ 重跑一次单因子测试再导出 ✓"))
        ws.column_dimensions["A"].width = 12
        ws.column_dimensions["B"].width = 13
        for col in ("C", "D", "E", "F", "G"):
            ws.column_dimensions[col].width = 11
        ws.column_dimensions["H"].width = 30
    return xl.workbook_bytes(wb)


def trade_rows(st: Optional[dict]) -> List[List[Any]]:
    """登记的成交明细 → 表格行（**原因**译成人话 ✓；取不到 ⇒ 空表 ✓ 由调用方写说明 ✓）。"""
    tr = (st or {}).get("trades")
    if tr is None or not len(tr):
        return []
    out: List[List[Any]] = []
    for _, r in tr.iterrows():
        reason = str(r.get("reason") or "")
        try:
            out.append([str(r.get("date") or ""), str(r.get("code") or ""),
                        str(r.get("side") or ""), int(r.get("shares") or 0),
                        round(float(r.get("price") or 0.0), 4),
                        round(float(r.get("amount") or 0.0), 2),
                        round(float(r.get("fee") or 0.0), 2),
                        REASON_LABELS.get(reason, reason or "-")])
        except Exception:                                        # noqa: BLE001 —— 单行坏不拖垮整页 ✓
            continue
    return out
