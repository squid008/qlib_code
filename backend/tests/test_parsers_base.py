# -*- coding: utf-8 -*-
"""`signals/parsers/base.py` 的统一解析入口（★ v2.9.94，2026-10-19 ✓ —— `signal_test` 路由 9.5% 的主干 ✓）。

## `parse_csv` 是"用户拖进来一个文件"的第一站

它要在一堆**格式各异**的导出文件里认出三种模式 ✓：
`signal_list`（信号清单）/ `jq_trades`（聚宽成交明细）/ `jq_perf`（聚宽《收益概述》）✓，
还要处理 **编码（UTF-8 / GBK）**、**分隔符（逗号 / 制表符）**、**有没有表头** ✓。

## 三条"错了就白忙一场"的

1. **认错模式** ✗ ⇒ 后面全用错解析器 ⇒ 用户看到"识别出 0 条信号"却不知道原因 ✓
   ⇒ 这里钉住每种模式的**触发列名**（`委托时间 + 成交价` ⇒ 流水 ✓；`策略收益 + 基准收益` ⇒ 绩效 ✓）；
2. **二进制/加密 xlsx 当文本 decode** ✗ ⇒ 变成乱码再让 pandas 抛 `ParserError`（用户完全看不懂 ✓）
   ⇒ 现在必须抛**人话**：「这不是文本文件（含二进制内容）…请给 CSV/TXT 或完整的 XLSX/XLS」✓
   （2026-09-15 单测抓到 ✓）；
3. **空文件** ⇒ 不许抛 ✗，要返回 `kind=""` + issue「文件内容为空」✓（前端据此提示 ✓）。
"""
from __future__ import annotations

import pytest

from app.signals.parsers.base import excel_to_csv_text, parse_csv


def _csv(text: str, enc: str = "utf-8") -> bytes:
    return text.encode(enc)


# ------------------------------------------------------------------ 三种模式识别
def test_signal_list_detected():
    raw = _csv("日期,代码\n2024-01-02,SH600000\n2024-01-03,SZ000001\n")
    res = parse_csv(raw, "signals.csv")
    assert res.kind == "signal_list"
    assert res.signals is not None and len(res.signals) == 2
    assert res.stats["filename"] == "signals.csv"
    assert isinstance(res.stats["had_header"], bool)


def test_jq_trades_detected_by_commission_time_and_price():
    """⭐ `委托时间 + 成交价` ⇒ 流水模式 ✓（认错就会去当"信号清单"解析 ⇒ 0 条 ✗）。"""
    raw = _csv("委托时间,成交价,交易类型,成交数量,证券代码\n"
               "2024-01-02 09:31:00,10.5,买,100,600000\n")
    res = parse_csv(raw, "trades.csv")
    assert res.kind == "jq_trades"
    assert res.trades is not None


def test_jq_perf_detected_by_strategy_and_benchmark_returns():
    """⭐ `策略收益 + 基准收益` ⇒ 绩效序列 ✓。"""
    raw = _csv("日期,策略收益,基准收益\n2024-01-02,0.01,0.005\n2024-01-03,0.02,0.01\n")
    res = parse_csv(raw, "perf.csv")
    assert res.kind == "jq_perf"
    assert res.perf is not None


# ---------------------------------------------------------------------- 编码/分隔符
def test_gbk_encoding_is_decoded():
    """⭐ 中文导出常见 GBK ✓ —— 用 UTF-8 硬解会变乱码 ⇒ 表头认不出 ⇒ 模式判错 ✗。"""
    raw = "日期,代码\n2024-01-02,SH600000\n".encode("gbk")
    res = parse_csv(raw, "gbk.csv")
    assert res.kind == "signal_list"
    assert any("日" in str(h) for h in res.headers), "表头应能正确解出中文 ✓"


def test_tab_separator_detected():
    raw = _csv("日期\t代码\n2024-01-02\tSH600000\n")
    res = parse_csv(raw, "tab.txt")
    assert res.sep == "\t"
    assert res.kind == "signal_list"


# ------------------------------------------------------------------------ 边界
def test_empty_file_returns_issue_not_exception():
    """⭐ 空文件 ⇒ `kind=""` + issue「文件内容为空」✓（不许抛 ✗）。"""
    res = parse_csv(b"", "empty.csv")
    assert res.kind == ""
    assert res.issues and "为空" in res.issues[0]["reason"]


def test_whitespace_only_file_also_reported():
    res = parse_csv(b"   \n\n  \n", "blank.csv")
    assert res.kind == ""
    assert res.issues and "为空" in res.issues[0]["reason"]


def test_binary_content_raises_human_error():
    """⭐ 2026-09-15：含 NUL 的二进制（加密/损坏 xlsx）必须给人话 ✓，不许 decode 成乱码 ✗。"""
    raw = b"PK\x03\x04" + b"\x00" * 64          # 有魔数 ⇒ 会去 read_excel ✗；
    # 用"非 Excel 魔数但含 NUL"更贴近当时那个用例 ✓
    raw2 = b"\x01\x02\x03\x04\x05\x06\x07\x08" + b"\x00" * 64
    with pytest.raises(ValueError) as ei:
        excel_to_csv_text(raw2)
    assert "不是文本文件" in str(ei.value)
    assert "请给 CSV/TXT" in str(ei.value)


def test_excel_to_csv_text_returns_none_for_plain_text():
    assert excel_to_csv_text("日期,代码\n".encode("utf-8")) is None
    assert excel_to_csv_text(None) is None
    assert excel_to_csv_text(b"short") is None       # < 8 字节 ✓


def test_issues_cap_is_300():
    """`add_issue` 的 **300 条上限** ✓（脏文件上万行时不会把响应撑爆 ✗）。"""
    from app.signals.parsers.base import ParseResult

    res = ParseResult()
    for i in range(500):
        res.add_issue(i, "raw", "reason")
    assert len(res.issues) == 300
    assert res.issues[0]["row"] == 0
