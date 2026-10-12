# -*- coding: utf-8 -*-
"""openpyxl **薄封装**：标题 / KV 块 / 表格 / KPI 卡 / **贴图** + 样式。

边界（**请勿破坏**）：这里**零业务语义** —— 不认识"因子""持仓""成本档"，只提供
「按行游标写东西、把 PNG 贴到指定位置、并报告写在哪/要留多少白」的能力 ✓。
业务组装（复刻哪个面板、填什么文案）在 `topk.py` / `topk_doc.py` ✓。

★ 为什么图表用**图片**而不是 openpyxl 原生图表（2026-10-11 用户反馈后改）：
  ① 原生图表在部分 Excel/WPS 版本里**渲染不出来**（用户实测：文件里那两块是空白 ✗）；
  ② 用户的要求是"**所见即所得**"——"我参数选到那档就截图那档" ⇒ 直接把**前端渲染的
     recharts SVG 抓成 PNG** 贴进文件，像素与页面完全一致 ✓（抓图在前端做，见
     `frontend/src/captureChart.ts`；后端只负责贴 ✓）；
  ③ 图片还有一个好处：**不会**因为阅读者改了单元格/删了数据页而变形 ✓。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Tuple

from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook

# ---------------------------------------------------------------- 样式（统一，避免各处手搓 ✗）
TITLE_FONT = Font(bold=True, size=14)
SUB_FONT = Font(size=10, color="475569")
HINT_FONT = Font(size=9, color="64748B")
SECTION_FONT = Font(bold=True, size=11, color="1F4E79")
HEAD_FONT = Font(bold=True, color="FFFFFF")
KEY_FONT = Font(bold=True)
WARN_FONT = Font(size=9, color="B45309")
NOTE_FILL = PatternFill("solid", fgColor="FFFBEB")      # 黄底注释（与页面 `bg-amber-50` 对应 ✓）
HEAD_FILL = PatternFill("solid", fgColor="4472C4")
SECTION_FILL = PatternFill("solid", fgColor="D9E1F2")
KPI_FILL = PatternFill("solid", fgColor="F1F5F9")
CURRENT_FILL = PatternFill("solid", fgColor="DBEAFE")    # 当前选中的档位/按钮（与页面高亮对应 ✓）
_THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
PCT = "0.00%"
PCT1 = "0.0%"
NUM4 = "0.0000"
RATIO = "0.00"

WRAP_TOP = Alignment(wrap_text=True, vertical="top")
EMU_PER_CM = 360000                # Excel 单位（1 cm = 360000 EMU ✓）
PX_PER_CM = 96 / 2.54              # 96 dpi ⇒ 1 cm ≈ 37.8 px ✓
ROW_PX = 20.0                      # 默认行高 15pt ≈ 20px（图片留白按此折算 ✓）


@dataclass(frozen=True)
class TableRef:
    """一张表的位置（1-based，含表头行 ✓）—— 只做"记录位置"用（贴图/后续引用 ✓）。"""

    first_row: int
    last_row: int
    first_col: int
    last_col: int


class SheetWriter:
    """按行游标写 sheet：`title/section/kv/table/kpi/image` 依次往下写，**不用手算行号** ✓。"""

    def __init__(self, ws, start_row: int = 1):
        self.ws = ws
        self.row = int(start_row)

    # ------------------------------------------------------------ 基础
    def blank(self, n: int = 1) -> "SheetWriter":
        self.row += max(0, int(n))
        return self

    def title(self, text: str, subtitle: Optional[str] = None) -> "SheetWriter":
        self.ws.cell(row=self.row, column=1, value=text).font = TITLE_FONT
        self.row += 1
        if subtitle:
            self.subtitle(subtitle)
        return self

    def subtitle(self, text: str) -> "SheetWriter":
        """标题下的摘要行（与页面标题栏右侧那串灰字对应 ✓）。"""
        self.ws.cell(row=self.row, column=1, value=text).font = SUB_FONT
        self.row += 1
        return self

    def hint(self, text: str) -> "SheetWriter":
        self.ws.cell(row=self.row, column=1, value=text).font = HINT_FONT
        self.row += 1
        return self

    def section(self, text: str, wrap_cols: int = 12) -> "SheetWriter":
        """分区标题（浅蓝底纹 + 跨列合并，视觉上把"块"分开 ✓）。"""
        c = self.ws.cell(row=self.row, column=1, value=text)
        c.font = SECTION_FONT
        c.fill = SECTION_FILL
        if wrap_cols > 1:
            self.ws.merge_cells(start_row=self.row, start_column=1,
                                end_row=self.row, end_column=wrap_cols)
        self.row += 1
        return self

    def note(self, text: str) -> "SheetWriter":
        self.ws.cell(row=self.row, column=1, value=text).font = HINT_FONT
        self.row += 1
        return self

    def note_block(self, text: str, cols: int = 10, per_line: int = 58,
                   box: bool = True) -> "SheetWriter":
        """**跨列合并 + 自动换行**的注释块（对应页面那块黄底"口径说明" ✓）。

        ⚠ 行高按字数估：不合并 + 不设行高的话，长句会**溢出到右边空白列**（看着像乱码 ✗）。
        `cols` 应与该页实际使用的列数一致（本工具第 1 页用 ~11 列 ⇒ 合并 A:K ✓）。
        """
        c = self.ws.cell(row=self.row, column=1, value=text)
        c.font = WARN_FONT if box else HINT_FONT
        c.alignment = WRAP_TOP
        if cols > 1:
            self.ws.merge_cells(start_row=self.row, start_column=1, end_row=self.row, end_column=cols)
            for j in range(1, cols + 1):
                if box:
                    self.ws.cell(row=self.row, column=j).fill = NOTE_FILL
        lines = max(1, -(-len(str(text)) // max(10, int(per_line))))
        self.ws.row_dimensions[self.row].height = 13.5 * min(lines, 20)
        self.row += 1
        return self

    def caption(self, text: str) -> "SheetWriter":
        """加粗小标题（如绩效卡的"组合：最强分位组 Q10"这一行 ✓）。"""
        self.ws.cell(row=self.row, column=1, value=text).font = Font(bold=True, size=10)
        self.row += 1
        return self

    # ------------------------------------------------------------ KV 块
    def kv(self, pairs: Iterable[Tuple[str, Any, Optional[str]]],
           per_row: int = 2, key_width: float = 20) -> "SheetWriter":
        """`(键, 值, 数字格式)` → 每行 `per_row` 组（默认 2 组，省一半行数 ✓）。"""
        buf: list = []
        for label, val, fmt in pairs:
            buf.append((label, val, fmt))
            if len(buf) >= max(1, int(per_row)):
                self._kv_row(buf)
                buf = []
        if buf:
            self._kv_row(buf)
        self._widen(key_width)
        return self

    def _kv_row(self, items: Sequence[Tuple[str, Any, Optional[str]]]) -> None:
        col = 1
        for label, val, fmt in items:
            self.ws.cell(row=self.row, column=col, value=label).font = KEY_FONT
            vc = self.ws.cell(row=self.row, column=col + 1, value=val)
            if fmt:
                vc.number_format = fmt
            col += 3
        self.row += 1

    # ------------------------------------------------------------ KPI 卡
    def kpi_row(self, pairs: Sequence[Tuple[str, Optional[float], Optional[str]]],
                mark: Optional[str] = None) -> "SheetWriter":
        """一行 KPI 卡（标签在上、数值在下，浅底 + 细框 ⇒ 与页面的指标格对应 ✓）。"""
        for i, (label, value, fmt) in enumerate(pairs, start=1):
            kc = self.ws.cell(row=self.row, column=i, value=label)
            kc.font = Font(size=9, color="64748B")
            kc.alignment = Alignment(horizontal="center")
            vc = self.ws.cell(row=self.row + 1, column=i, value=value)
            vc.font = Font(bold=True, size=11)
            vc.alignment = Alignment(horizontal="center")
            if fmt:
                vc.number_format = fmt
            for r in (self.row, self.row + 1):
                cell = self.ws.cell(row=r, column=i)
                cell.border = BOX
                cell.fill = CURRENT_FILL if (mark is not None and label == mark) else KPI_FILL
        self.row += 2
        return self

    # ------------------------------------------------------------ 表格
    def table(self, headers: Sequence[str], rows: Sequence[Sequence[Any]],
              num_fmts: Optional[dict] = None, *, head: bool = True,
              freeze: bool = False) -> TableRef:
        """写表（默认含表头 ✓）并返回位置。`num_fmts` = 列下标 → 格式（如 `{2: PCT}` ✓）。

        ⚠ `freeze` 必须**显式开启**（默认关 ✗）：早先"写表就冻表头"，而第 1 页的成本敏感度表
          在 100 多行 ⇒ `freeze_panes="A105"` 等于把上面 105 行全冻住，用户滚不动（2026-10-11
          用户实测："为什么我滚动条往下滚动，它不下去呢" ✗）。只有"表头在第 1 行、数据几千行"的
          页（持仓明细）才值得冻 ✓。
        """
        first_row = self.row
        if head:
            for j, h in enumerate(headers, start=1):
                c = self.ws.cell(row=self.row, column=j, value=h)
                c.font = HEAD_FONT
                c.fill = HEAD_FILL
                c.alignment = Alignment(horizontal="center", wrap_text=True)
                c.border = BOX
            self.row += 1
        fmts = num_fmts or {}
        for r in rows:
            for j, v in enumerate(r, start=1):
                c = self.ws.cell(row=self.row, column=j, value=v)
                if (j - 1) in fmts:
                    c.number_format = fmts[j - 1]
            self.row += 1
        last_row = self.row - 1
        ref = TableRef(first_row=first_row, last_row=last_row,
                       first_col=1, last_col=len(headers))
        if head and freeze:
            self.freeze(first_row)
        return ref

    def freeze(self, header_row: int) -> None:
        """冻结到表头行（表头恒在第 `header_row` 行 ✓）。"""
        if header_row >= 1:
            self.ws.freeze_panes = "A%d" % (header_row + 1)

    # ------------------------------------------------------------ 贴图
    def image_block(self, png: bytes, width_cm: float = 24.0, name: str = "panel",
                    caption: Optional[str] = None) -> int:
        """把 PNG 贴到**当前行 A 列**，返回"该留白的行数"（调用方 `blank(rows)` ✓）。

        ⚠ 图片是"浮在单元格上"的对象 ✗ ⇒ 不留白的话下面的文字会被图压住（第一版踩到 ✓）。
        图片按 `width_cm` 等比缩放（高度由原图宽高比推出 ✓）；行数按默认行高 15pt ≈ 20px 折算 ✓。
        """
        if caption:
            self.caption(caption)
        from io import BytesIO

        img = XLImage(BytesIO(png))
        w0, h0 = float(img.width or 1.0), float(img.height or 1.0)
        w_px = float(width_cm) * PX_PER_CM
        h_px = w_px * (h0 / w0 if w0 else 1.0)
        img.width, img.height = w_px, h_px
        ws = self.ws
        img.anchor = "A%d" % self.row
        ws.add_image(img)                       # 图片名重复会互相覆盖 ⇒ 交给 openpyxl 自增 ✓
        rows = int(h_px / ROW_PX) + 2            # +2 行余量（缩放/字体差异 ✓）
        self.blank(rows)
        return rows

    # ------------------------------------------------------------ 列宽
    def widths(self, widths: dict) -> "SheetWriter":
        """列宽：`{列号(1-based): 宽度}`（写小表时按内容给个观感 ✓）。"""
        for col, w in (widths or {}).items():
            self.ws.column_dimensions[get_column_letter(int(col))].width = float(w)
        return self

    def _widen(self, width: float, col: int = 1) -> None:
        cur = self.ws.column_dimensions[get_column_letter(col)].width or 0
        if width > (cur or 0):
            self.ws.column_dimensions[get_column_letter(col)].width = float(width)


def new_workbook(first_sheet_title: str) -> Tuple[Workbook, Any]:
    """新建工作簿（去掉默认空 sheet ⇒ 不留一张空白页 ✗）并写第一页标题。"""
    wb = Workbook()
    ws = wb.active
    ws.title = first_sheet_title[:31]
    return wb, ws


def workbook_bytes(wb: Workbook) -> bytes:
    """工作簿 → bytes（走内存 ⇒ 不落临时文件、天然并发安全 ✓）。"""
    from io import BytesIO

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = [
    "BOX", "CURRENT_FILL", "HINT_FONT", "KPI_FILL", "NOTE_FILL", "NUM4", "PCT", "PCT1",
    "RATIO", "SECTION_FILL", "SheetWriter", "TableRef", "WRAP_TOP",
    "new_workbook", "workbook_bytes",
]
