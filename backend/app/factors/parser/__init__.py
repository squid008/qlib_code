# -*- coding: utf-8 -*-
"""公式翻译器（FormulaParser）对外入口。

把益盟/通达信公式文本翻译成 qlib 表达式字符串。
用法：
    from app.factors.parser import translate_formula
    result = translate_formula("A:=MA(CLOSE,5); B:=A+100; 输出:B;")
    # => {"name": "输出", "expression": "Add(Mean($close,5),100)", ...}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .parser import parse_formula, ParseError
from .lexer import LexerError
from .semantic import resolve, SemanticError
from .codegen import generate, CodeGenError


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
        patchable: 是否允许生成"待接入外挂算子"占位（PATCH:XXX）。默认 False 会对外挂算子报错。

    Returns:
        TranslatedFactor（name + expression + 依赖字段）。

    Raises:
        LexerError / ParseError / SemanticError / CodeGenError：带清晰中文提示。
    """
    formula = parse_formula(text)
    expr = resolve(formula)          # 校验单输出 + 变量内联
    qlib_expr = generate(expr, patchable=patchable)

    # 收集依赖的行情字段（从 qlib 表达式里提取 $xxx）
    inputs = _extract_fields(qlib_expr)
    output = formula.outputs[0]

    return TranslatedFactor(
        name=output.name,
        expression=qlib_expr,
        inputs=inputs,
        has_patch="PATCH:" in qlib_expr,
        source_formula=text,
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
