# -*- coding: utf-8 -*-
"""公式翻译器：语义分析 + 变量内联。

- 校验单输出（每个公式只允许 1 个输出因子）
- 把中间变量（Assign）递归内联展开进输出表达式，得到无变量的单棵表达式树
- 检测未定义变量 / 循环引用
"""
from __future__ import annotations

from typing import Dict

from .ast import (
    Formula, Expr, Var, BinOp, UnaryOp, FuncCall, Num, Field,
)

# 公式可用的内置函数名（用于区分"函数调用"与"变量引用"；未在此表内的标识符视为变量）
BUILTIN_FUNCS = {
    "MA", "SMA", "EMA", "WMA", "MEMA", "REF", "HHV", "LLV", "SUM", "COUNT",
    "FILTER", "HHVALL", "LLVALL", "HHVBARS", "LLVBARS", "BARSLAST",
    "BARSSINCE", "BARSSINCEN", "BARSCOUNT", "DMA", "ALL", "ANY", "LAST",
    # 自定义外挂算子（app/factors/ops_ext.py），需同时在 codegen 中映射
    "DYN_MIN", "DYN_MAX", "DYN_COUNT", "DYN_REF", "DYN_SUM",
    "CROSS", "IF", "IFS", "BETWEEN", "RANGE", "LONGCROSS",
    "ABS", "SQRT", "LOG", "LN", "EXP", "POW", "MAX", "MIN", "MOD",
    "INT", "CEILING", "FLOOR", "SGN", "SIGN",
    "STD", "STDP", "VAR", "VARP", "AVEDEV", "SLOPE", "FORCAST", "DEVSQ",
    # Level2（留接口：语法支持，计算层待数据）
    "BIGORDER", "ORDER", "ORDERAMT", "ORDERNUM", "ORDERNWP", "ORDERVOL",
    "TRANSACTNUM", "TRANSACTVOL", "ALLASKVOL", "ALLBIDVOL",
    # 资金流向（moneyflow）：L2_PCT(n)/L2_AMO(n)，n=0主力/1超大/2大/3中/4小
    "L2_PCT", "L2_AMO",
}


class SemanticError(Exception):
    pass


def resolve_vars(formula: Formula) -> Formula:
    """语义分析：校验单输出，并检查变量定义。返回原 Formula（变量内联在 codegen 阶段做）。"""
    if len(formula.outputs) == 0:
        raise SemanticError("公式没有输出线（需要至少一条 `因子名:表达式;`）")
    if len(formula.outputs) > 1:
        names = "、".join(o.name for o in formula.outputs)
        raise SemanticError(f"公式只允许 1 个输出因子，当前有 {len(formula.outputs)} 个：{names}")

    # 检查变量是否被重复定义（大小写不敏感）
    seen_assigns = {}
    for a in formula.assigns:
        key = a.name.upper()
        if key in seen_assigns:
            raise SemanticError(
                f"变量 `{a.name}` 被重复定义（前面已定义 `{seen_assigns[key]}`），请删除其中一行或改名")
        seen_assigns[key] = a.name

    # 检查中间变量是否被定义 / 是否有未定义引用
    assign_names = {a.name.upper(): a for a in formula.assigns}
    output_name = formula.outputs[0].name.upper()

    def check(expr: Expr, ctx: str):
        if isinstance(expr, Var):
            if expr.name.upper() not in assign_names and expr.name.upper() != output_name:
                raise SemanticError(f"引用了未定义的变量或函数：{expr.name}（在 {ctx}）")
        elif isinstance(expr, BinOp):
            check(expr.left, ctx)
            check(expr.right, ctx)
        elif isinstance(expr, UnaryOp):
            check(expr.operand, ctx)
        elif isinstance(expr, FuncCall):
            if expr.name not in BUILTIN_FUNCS:
                raise SemanticError(f"不支持的函数：{expr.name}（在 {ctx}）")
            for a in expr.args:
                check(a, ctx)

    for a in formula.assigns:
        check(a.value, f"变量 {a.name}")
    for o in formula.outputs:
        check(o.value, f"输出 {o.name}")

    return formula


def inline_variables(formula: Formula) -> Expr:
    """变量内联：把中间变量递归展开，返回输出表达式的单棵树（无 Var 引用）。"""
    assign_map: Dict[str, Expr] = {a.name.upper(): a.value for a in formula.assigns}
    output = formula.outputs[0]

    # 展开一个表达式（先展开其内部变量引用）
    def expand(expr: Expr, stack) -> Expr:
        if isinstance(expr, Var):
            name = expr.name.upper()
            if name not in assign_map:
                return expr  # 保留（可能是输出名自引用，后面 codegen 会处理）
            if name in stack:
                raise SemanticError(f"中间变量存在循环引用：{' -> '.join(stack + [name])}")
            return expand(assign_map[name], stack + [name])
        if isinstance(expr, BinOp):
            return BinOp(expr.op, expand(expr.left, stack), expand(expr.right, stack))
        if isinstance(expr, UnaryOp):
            return UnaryOp(expr.op, expand(expr.operand, stack))
        if isinstance(expr, FuncCall):
            return FuncCall(expr.name, [expand(a, stack) for a in expr.args])
        return expr

    return expand(output.value, [])


def resolve(formula: Formula) -> Expr:
    """一步完成：校验单输出 + 变量内联，返回输出表达式的单棵树。"""
    formula = resolve_vars(formula)
    return inline_variables(formula)
