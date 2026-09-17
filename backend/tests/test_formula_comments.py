# -*- coding: utf-8 -*-
"""`{...}` 注释支持（v1.19.86）。

用户 2026-09-17：「给公式编辑区加上注释符号 `{}` 识别功能吧，`{}` 里的都是注释，
比如 `{的点点滴滴}` 这样」。以前 Lexer 遇到 `{` 直接抛「无法识别的字符 '{'」✗。
"""
import pytest

from app.factors.parser import translate_formula
from app.factors.parser.lexer import LexerError


def test_inline_comment_is_ignored():
    """行内注释不参与编译，前后代码照常工作。"""
    t = translate_formula("A:=MA(CLOSE,5); {五日均线} 输出:A+100;")
    assert t.name == "输出"
    assert "Mean($close,5)" in t.expression
    assert "五日均线" not in t.expression          # 注释不会漏进表达式


def test_full_line_and_trailing_comments():
    """整行注释 + 行尾注释都能吃掉。"""
    t = translate_formula("{整行注释}\nA:=CLOSE;\nB:=A*2; {行尾注释}\n输出:B;")
    assert "整行注释" not in t.expression and "行尾注释" not in t.expression
    assert t.expression


def test_multiline_comment():
    """注释可跨行（通达信一致）。"""
    t = translate_formula("A:=MA(CLOSE,5); {第一行\n第二行\n第三行}\n输出:A;")
    assert "Mean($close,5)" in t.expression


def test_full_width_braces_work():
    """全角 `｛｝` 由 normalize_source 的 NFKC 折成半角 ⇒ 同样识别。"""
    t = translate_formula("A:=CLOSE; ｛全角注释｝ 输出:A;")
    assert t.name == "输出"


def test_comment_only_formula_reports_missing_output():
    """整段都是注释 ⇒ 报「没有输出线」（而不是「无法识别的字符 '{'」）。"""
    with pytest.raises(Exception) as ei:
        translate_formula("{只有注释}")
    assert "输出" in str(ei.value)


def test_unclosed_comment_reports_line_and_col():
    """注释未闭合 ⇒ 明确说缺 `}`，并且带「第 N 行第 M 列」（位置信息仍准）。"""
    with pytest.raises(LexerError) as ei:
        translate_formula("A:=CLOSE;\nB:=A+1; {忘了关闭")
    msg = str(ei.value)
    assert "注释未闭合" in msg
    assert "第 2 行" in msg


def test_positions_stay_accurate_after_comment():
    """注释只是"什么都不输出"，**位置记账不变** ⇒ 后面的语法错误仍报对行。"""
    with pytest.raises(Exception) as ei:
        # 第 2 行故意少写分号
        translate_formula("A:=CLOSE; {注释}{注释}\nB:=A+1\n输出:B;")
    assert "第 3 行" not in str(ei.value) or "分号" in str(ei.value)
