# -*- coding: utf-8 -*-
"""CSV → 统一中间结构的公共部分（编码嗅探、分隔符/表头识别、列名别名、诊断收集）。

设计要点：
  · **编码**：utf-8-sig → utf-8 → gbk → gb18030 依次试（实测用户给的两个文件都是 **GBK**）；
  · **分隔符**：逗号 / 制表符 / 分号自动嗅探；
  · **表头**：有表头就按中文/英文别名匹配；**没表头**（第一行就是数据）也能吃 —— 按位置认列；
  · **诊断**：任何被丢掉的行都要留痕（原行内容 + 原因），界面上可展开核对，绝不静默吞。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import pandas as pd

from ..codes import normalize_code, parse_date, parse_time, CodeInfo

# ---- 列名别名（小写去空格后匹配）----
DATE_ALIASES: Sequence[str] = (
    "日期", "交易日期", "成交日期", "委托日期", "时间", "交易日", "dt", "date", "day",
)
TIME_ALIASES: Sequence[str] = (
    "委托时间", "成交时间", "时间", "time", "成交时刻", "委托时刻",
)
CODE_ALIASES: Sequence[str] = (
    "标的", "股票代码", "证券代码", "代码", "证券", "股票", "code", "symbol", "ticker",
    "stock", "stockcode", "secid", "wind代码", "instrument",
)
SIDE_ALIASES: Sequence[str] = (
    "交易类型", "买卖", "方向", "买卖方向", "操作", "side", "direction", "trade_type",
)
QTY_ALIASES: Sequence[str] = ("成交数量", "数量", "成交量", "qty", "volume", "shares", "股数")
PRICE_ALIASES: Sequence[str] = ("成交价", "成交均价", "价格", "price", "avg_price")
AMOUNT_ALIASES: Sequence[str] = ("成交额", "成交金额", "金额", "amount", "turnover", "成交总值")
FEE_ALIASES: Sequence[str] = ("手续费", "佣金", "费用", "fee", "commission", "总费用")
STATUS_ALIASES: Sequence[str] = ("状态", "委托状态", "status", "订单状态")

_BUY_WORDS = ("买", "buy", "b", "证券买入", "融资买入", "申购")
_SELL_WORDS = ("卖", "sell", "s", "证券卖出", "融券卖出", "赎回")


@dataclass
class ParseResult:
    """解析结果（三种模式共用外壳；具体明细放 signals / trades / perf 里）。"""
    kind: str = ""                       # "signal_list" | "jq_trades" | "jq_perf"
    encoding: str = ""
    sep: str = ","
    headers: List[str] = field(default_factory=list)
    signals: Optional[pd.DataFrame] = None   # 信号清单模式：date, code, side, raw...
    trades: Optional[pd.DataFrame] = None    # 流水模式：date, time, code, side, qty, price…
    perf: Optional[pd.DataFrame] = None      # 聚宽《收益概述》：date, strat_cum, bench_cum, nav…
    stats: Dict = field(default_factory=dict)
    issues: List[Dict] = field(default_factory=list)

    def add_issue(self, row_no, raw, reason: str, cap: int = 300) -> None:
        if len(self.issues) < cap:
            self.issues.append({"row": int(row_no), "raw": str(raw)[:160], "reason": reason})

    @property
    def ok(self) -> bool:
        n = 0
        for df in (self.signals, self.trades, self.perf):
            if df is not None:
                n += len(df)
        return n > 0


def decode_bytes(raw: bytes) -> tuple:
    """字节 → 文本（返回 (text, encoding)）。顺序：utf-8-sig → utf-8 → gbk → gb18030。"""
    if isinstance(raw, str):
        return raw, "str"
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _sniff_sep(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    n_tab, n_semi, n_comma = first.count("\t"), first.count(";"), first.count(",")
    if n_tab and n_tab >= max(n_comma, n_semi):
        return "\t"
    if n_semi and n_semi > n_comma:
        return ";"
    return ","


def _norm_col(c) -> str:
    return re.sub(r"[\s_\-（）()]+", "", str(c or "")).lstrip("\ufeff").lower()


def pick_col(df: pd.DataFrame, aliases: Sequence[str]) -> Optional[str]:
    """按别名找列（大小写/空格无关；先精确后包含）。"""
    norm = {_norm_col(c): c for c in df.columns}
    for a in aliases:
        if _norm_col(a) in norm:
            return norm[_norm_col(a)]
    for a in aliases:
        na = _norm_col(a)
        for nc, c in norm.items():
            if na and na in nc:
                return c
    return None


def read_table(text: str) -> tuple:
    """文本 → (DataFrame, sep, had_header)。无表头时按位置命名列。

    ⚠ "无表头"的判据是**列名本身像不像数据**（列名能解析成日期 ⇒ 原文件没表头行）。
      绝不能拿"第一行数据"去判（那永远像数据 ⇒ 会把正常表头当数据行吃掉 —— 2026-09-15
      实测把 `日期,标的` 这行标题记成 1 条 dropped 记录、`had_header=False`）。
    """
    sep = _sniff_sep(text)
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, skip_blank_lines=True,
                     engine="python")
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    had_header = True
    if len(df.columns) >= 2 and parse_date(str(df.columns[0])) is not None:
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, header=None,
                         skip_blank_lines=True, engine="python")
        df.columns = ["col%d" % (i + 1) for i in range(df.shape[1])]
        had_header = False
    return df, sep, had_header


def num(v) -> Optional[float]:
    """从 `60200股` / `1,247,946` / `-796300股` / `-` 里取数（失败 None）。"""
    s = str(v or "").strip().replace(",", "").replace("，", "")
    if not s or s in ("-", "--", "nan", "None", "null"):
        return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def side_of(v) -> Optional[int]:
    """买卖方向 → +1 / -1（认不出来 None）。"""
    s = str(v or "").strip().lower()
    if not s:
        return None
    for w in _BUY_WORDS:
        if w in s:
            return 1
    for w in _SELL_WORDS:
        if w in s:
            return -1
    return None


def detect_kind(df: pd.DataFrame) -> str:
    """按列名判断格式（三种）：

    · **聚宽《收益概述》**：有 `策略收益` + `基准收益`（逐日绩效序列）；
    · **聚宽成交明细**：有 `委托时间/成交价` 或 `交易类型+成交数量`；
    · 其余按**信号清单**（第 1 列日期、第 2 列标的）。
    """
    cols = [_norm_col(c) for c in df.columns]
    joined = "|".join(cols)
    if "策略收益" in joined and "基准收益" in joined:
        return "jq_perf"
    has_jq = all(k in joined for k in ("委托时间", "成交价")) or \
        ("交易类型" in joined and "成交数量" in joined)
    if has_jq:
        return "jq_trades"
    if any(_norm_col(a) in cols for a in DATE_ALIASES) or len(df.columns) >= 2:
        return "signal_list"
    return "signal_list"


def parse_csv(raw, filename: str = "") -> ParseResult:
    """统一入口：自动识别格式并解析。"""
    text, enc = decode_bytes(raw)
    if not text.strip():
        res = ParseResult(kind="", encoding=enc)
        res.add_issue(0, "", "文件内容为空")
        return res
    df, sep, had_header = read_table(text)
    kind = detect_kind(df)
    # ⚠ 延迟 import：解析器都 import 本模块 ⇒ 模块级互相 import 会成环
    if kind == "signal_list":
        from .signal_list import parse_signal_list as _parse
    elif kind == "jq_perf":
        from .jq_perf import parse_jq_perf as _parse
    else:
        from .jq_trades import parse_jq_trades as _parse
    res = _parse(raw, filename)
    res.encoding = enc
    res.sep = sep
    res.headers = [str(c) for c in df.columns]
    res.stats["had_header"] = bool(had_header)
    res.stats["filename"] = filename
    return res
