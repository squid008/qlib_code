# -*- coding: utf-8 -*-
"""公式翻译器（FormulaParser）对外入口。

把益盟/通达信公式文本翻译成 qlib 表达式字符串。
用法：
    from app.factors.parser import translate_formula
    result = translate_formula("A:=MA(CLOSE,5); B:=A+100; 输出:B;")
    # => {"name": "输出", "expression": "Add(Mean($close,5),100)", ...}
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

from .parser import parse_formula, ParseError
from .lexer import LexerError
from .semantic import resolve, SemanticError
from .codegen import generate, CodeGenError

# ---------------------------------------------------------------------------
# 粘贴文本归一化（2026-09-16：用户贴「二浪加强」报"语法错误"，查实是**看不见/全角的字符**）
#
# 实测（`ai_test/fml_variants.py`：同一份公式逐变体编译）：
#   · 带 **BOM**（`\ufeff`，从记事本/网页复制极常见）⇒ 第一行 `TRX:=...` 定义失效 ⇒
#     报「引用了未定义的变量或函数：TRX（在 变量 ATX）」—— **指向完全错误的地方**；
#   · **全角标点**（`，（）；：`）⇒ 报「不支持的函数：L，MAX」/「赋值需用 := 或 =」；
#   · 零宽字符（`\u200b` 等，从网页复制）⇒ 同样莫名其妙；
#   · CRLF / 行尾空格 / 缩进空行 ⇒ **本来就没事**（不用处理）。
# 处理：去零宽字符 + **NFKC**（全角→半角、全角空格→空格）+ 少数数学符号。
# ⚠ NFKC 只改"兼容字符"，**不动汉字** ⇒ 变量名（`二浪加强`）不受影响。
#
# NFKC 实测**不管**这些（下面手工补，`ai_test/nfkc_demo*.py` 逐字符验过）：
#   · `≤ ≥ ≦ ≧` ⇒ `<= >=`；`≠` ⇒ `<>`（词法器支持 `<>`/`!=` 写法 ✓）
#   · 破折号 `— – ―` ⇒ 减号（从 Word/网页复制常见）；顿号 `、` ⇒ 逗号（中文列表手误）
# ⚠ 仍不支持（如实告知，别让人猜）：弯引号 `“ ”`（无字符串字面量）、
#   `And(a,b)` 这种**前缀**写法（TDX 标准是中缀 `A AND B`）。
# ---------------------------------------------------------------------------
_ZERO_WIDTH_RE = re.compile("[\ufeff\u200b\u200c\u200d\u2060]")
_POS_RE = re.compile(r"\(位置\s+(\d+)\)\s*$")
_MATH_SYM = {
    "≤": "<=", "≥": ">=", "≦": "<=", "≧": ">=", "≠": "<>",
    "—": "-", "–": "-", "―": "-", "−": "-",       # U+2014/2013/2015/2212
    "、": ",",
}


def normalize_source(text: str) -> str:
    """把"粘贴来的"公式文本归一化：去零宽字符 + 全角→半角（详见上方注释）。"""
    if not text:
        return text
    t = _ZERO_WIDTH_RE.sub("", text)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = unicodedata.normalize("NFKC", t)
    for k, v in _MATH_SYM.items():
        t = t.replace(k, v)
    return t


def _locate(text: str, pos: int, width: int = 60) -> str:
    """字符位置 → 「第 N 行第 M 列」+ 该行内容（报错时直接告诉用户看哪一行）。"""
    pos = max(0, min(int(pos), len(text)))
    line = text.count("\n", 0, pos) + 1
    start = text.rfind("\n", 0, pos) + 1
    col = pos - start + 1
    end = text.find("\n", pos)
    snippet = text[start:end if end >= 0 else len(text)].strip()
    if len(snippet) > width:
        snippet = snippet[:width] + "…"
    return "第 %d 行第 %d 列：%s" % (line, col, snippet or "（空行）")


def _prev_line(text: str, pos: int, width: int = 60) -> str:
    """`pos` 之前最后一条非空行 → 「第 N 行：内容」。"""
    lines = text[:max(0, pos)].split("\n")
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s:
            return "第 %d 行：%s" % (i + 1, s[:width] + ("…" if len(s) > width else ""))
    return "（前面没有内容行）"


def _with_location(e: Exception, text: str) -> str:
    """给 `LexerError`/`ParseError` 消息里的「(位置 N)」补上「第 N 行第 M 列 + 该行内容」。

    ⚠ 「缺分号」要**特判指向上一行**：解析器是在**下一行的行首**发现"上一条语句没结束"的，
      直接用位置 N 会指向下一行（2026-09-16 单测抓到：`B:=A+1` 少分号，却被报成第 3 行）——
      这跟 BOM 那个错一样属于"报错指错地方"，比没提示更糟。
    """
    raw = str(e)
    m = _POS_RE.search(raw)
    if not m:
        return raw
    pos = int(m.group(1))
    if "分号" in raw:
        return "%s｜上一行缺分号 —— %s" % (raw, _prev_line(text, pos))
    return "%s｜%s" % (raw, _locate(text, pos))


@dataclass
class TranslatedFactor:
    """翻译结果：一个输出因子。"""
    name: str                                  # 输出因子名（原始名）
    expression: str                            # qlib 表达式
    inputs: List[str] = field(default_factory=list)   # 依赖的行情字段
    has_patch: bool = False                    # 是否含待接入的外挂算子
    source_formula: str = ""                   # 原始公式


def translate_formula(text: str, patchable: bool = False) -> TranslatedFactor:
    """把一段益盟/通达信公式翻译成单个输出因子。

    Args:
        text: 公式文本（可能含多个 `:=` 中间变量，但只允许 1 条 `:` 输出线）。
              会自动归一化：去 BOM/零宽字符、全角标点→半角（见 `normalize_source`）。
        patchable: 是否允许生成"待接入外挂算子"占位（PATCH:XXX）。默认 False 会对外挂算子报错。

    Returns:
        TranslatedFactor（name + expression + 依赖字段）。

    Raises:
        LexerError / ParseError / SemanticError / CodeGenError：带清晰中文提示；
        `LexerError`/`ParseError` 的消息额外带「第 N 行第 M 列：该行内容」，省得用户瞎猜位置。
    """
    src = normalize_source(text)
    try:
        formula = parse_formula(src)
        expr = resolve(formula)          # 校验单输出 + 变量内联
        qlib_expr = generate(expr, patchable=patchable)
    except (LexerError, ParseError) as e:
        # 只补位置信息，**不改异常类型**（路由仍按 400 + detail 返回）
        raise type(e)(_with_location(e, src)) from None

    # 收集依赖的行情字段（从 qlib 表达式里提取 $xxx）
    inputs = _extract_fields(qlib_expr)
    output = formula.outputs[0]

    return TranslatedFactor(
        name=output.name,
        expression=qlib_expr,
        inputs=inputs,
        has_patch="PATCH:" in qlib_expr,
        # ⚠ 存**归一化后**的文本：否则"全角那份能编译、存回去再编译又失败"（用户会以为存坏了）
        source_formula=src,
    )


def _extract_fields(expr: str) -> List[str]:
    """从 qlib 表达式里提取 $xxx 行情字段名（去重、保序）。"""
    fields = []
    seen = set()
    parts = expr.split("$")
    for p in parts[1:]:
        name = p.split(",")[0].split(")")[0].strip()
        if name and name not in seen:
            seen.add(name)
            fields.append("$" + name)
    return fields


__all__ = [
    "translate_formula", "TranslatedFactor",
    "ParseError", "LexerError", "SemanticError", "CodeGenError",
]
