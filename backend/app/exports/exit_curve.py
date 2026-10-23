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

from ..logger import _log_swallowed  # noqa: E402
from . import xl

SHEET_SHOT = "曲线截图"                  # 第 1 页（用户指定：第一页是截图 ✓）
COL_SHOT = 10                            # 第 1 页用到的列数（分区标题/说明块合并到这一列 ✓）
TRADE_HEADERS = ["日期", "代码", "方向", "股数", "价格", "金额", "费用", "原因"]

#: 成交原因 → 人话 ✓（**这张表就是"检查离场逻辑"的钥匙** ✗→✓）
#: ⚠ 值里**带上原码**（`中文（原码）` ✓）—— 便于在文件里直接搜原因码、也便于与我对话时对齐 ✓。
#: ⚠★ 用户 2026-10-16 追问「roll_trim 啥意思」✓ ⇒ 把**调仓日的那几个常规动作**也收进来 ✗→✓
#:  （它们**不是离场** ✗，是"每期全仓滚动"在每个调仓日重建等权时必然产生的买卖 ✓）：
#:   · `roll_out`  = 本期**不在名单**了 ⇒ **清仓卖出** ✓（`period_roll.py` ✓）
#:   · `roll_trim` = 本期**仍在名单、但持有超配** ⇒ **减回等权** ✓（同一处 ✓）
#:   · `roll_in`   = 本期**仍在名单、但持有低配** ⇒ **补到等权** ✓（同一处 ✓）
#:   · `sell_signal` / `sell_expiry` / `exit_signal` = 引擎侧：卖出信号 / 持有到期 / **离场信号** ✓
#:   · `sell_trim_units` / `rebalance_sell` / `rebalance_buy` = 其余资金方案（`batch_even` / 事件驱动）✓
REASON_LABELS: Dict[str, str] = {
    "buy_signal": "买入（进场信号）",
    "buy_roll_in": "买入（加仓）",
    "buy_rebalance": "买入（补低配）",
    "sell_signal": "卖出（卖出信号 / 离场矩阵）",
    "sell_expiry": "卖出（持有到期）",
    # ⚠ 措辞要**通用** ✓：`exit_signal` 既用于"离场矩阵"（换手率/市值这类 ✓）也用于
    #   "含入场价的规则"（止损/止盈 ✓）⇒ 别写死成"入场价" ✗（我第一版就这么写了，当场改掉 ✓）。
    "exit_signal": "★离场卖出（离场信号触发）",
    "sell_trim_units": "卖出（超配减仓）",
    "sell_trim": "卖出（减仓）",
    "sell_rebalance": "卖出（再平衡减仓）",
    "sell_roll_trim": "卖出（减仓）",
    # ---- 「每期全仓滚动」调仓日的常规动作（**不是离场** ✗）----
    "roll_out": "调仓卖出（本期不在名单 ⇒ 清仓）",
    "roll_trim": "调仓减仓（仍在名单但超配 ⇒ 减回等权）",
    "roll_in": "调仓买入（仍在名单但低配 ⇒ 补到等权）",
    # ---- 事件驱动（0/1 信号）再平衡 ----
    "rebalance_sell": "再平衡卖出（超配减仓）",
    "rebalance_buy": "再平衡买入（补低配）",
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
        label = REASON_LABELS.get(reason)
        # ★ 有中文就写「中文（原码）」✓（可搜可对账 ✓）；没收录的**照原样暴露并打标** ✗（不假装认识 ✓）
        shown = ("%s（%s）" % (label, reason)) if label else ((reason or "-") + "（未收录原因码）")
        try:
            out.append([str(r.get("date") or ""), str(r.get("code") or ""),
                        str(r.get("side") or ""), int(r.get("shares") or 0),
                        round(float(r.get("price") or 0.0), 4),
                        round(float(r.get("amount") or 0.0), 2),
                        round(float(r.get("fee") or 0.0), 2), shown])
        except Exception as e:  # noqa: BLE001 —— 单行坏不拖垮整页 ✓
            _log_swallowed("exit_curve.py", e)
            continue
    return out
