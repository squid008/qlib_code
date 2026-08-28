# -*- coding: utf-8 -*-
"""公式翻译器：AST（抽象语法树）节点定义。

把益盟/通达信公式解析成 AST，再翻译成 qlib 表达式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union


class Expr:
    """表达式基类。"""


@dataclass
class Num(Expr):
    """数字字面量。"""
    value: float


@dataclass
class Field(Expr):
    """行情字段（CLOSE/HIGH/LOW/OPEN/VOL/AMOUNT...）。"""
    name: str          # 规范化后的大写字段名


@dataclass
class Var(Expr):
    """变量引用（中间变量或输出线名）。"""
    name: str          # 原始名（保留用于因子命名/去重）


@dataclass
class BinOp(Expr):
    """二元运算（+ - * / > < >= <= = and or）。"""
    op: str            # ADD/SUB/MUL/DIV/GT/GE/LT/LE/EQ/NE/AND/OR
    left: Expr
    right: Expr


@dataclass
class UnaryOp(Expr):
    """一元运算（-）。"""
    op: str
    operand: Expr


@dataclass
class FuncCall(Expr):
    """函数调用（MA/EMA/HHV/LLV/REF/CROSS/SUM/COUNT/ABS...）。"""
    name: str          # 规范化后的大写函数名
    args: List[Expr]


@dataclass
class Assign:
    """赋值语句：变量 := / = 表达式。"""
    name: str          # 变量名（原始名）
    value: Expr


@dataclass
class Output:
    """输出线：因子名 : 表达式。"""
    name: str          # 输出因子名（原始名）
    value: Expr


@dataclass
class Formula:
    """整段公式。"""
    assigns: List[Assign] = field(default_factory=list)
    outputs: List[Output] = field(default_factory=list)

    def output_names(self) -> List[str]:
        return [o.name for o in self.outputs]
