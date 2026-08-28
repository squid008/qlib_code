# -*- coding: utf-8 -*-
"""公式翻译器：代码生成（AST → qlib 表达式字符串 / 外挂算子标记）。

把已内联的表达式树翻译成 qlib 可加载的表达式字符串。
不支持的算子（Level2 深度、需外挂的有状态算子）抛出 CodeGenError 并给出清晰提示。
"""
from __future__ import annotations

from typing import List

from .ast import Expr, Num, Field, Var, BinOp, UnaryOp, FuncCall
from .lexer import LexerError


class CodeGenError(Exception):
    pass


# ---- 行情字段映射（大写 → qlib $field）----
FIELD_MAP = {
    "CLOSE": "$close", "C": "$close",
    "HIGH": "$high", "H": "$high",
    "LOW": "$low", "L": "$low",
    "OPEN": "$open", "O": "$open",
    "VOL": "$volume", "V": "$volume",
    "VOLUME": "$volume",
    "AMOUNT": "$amount",
    "TURNOVERRATE": "$turn",
    "VWAP": "$vwap",
}

# ---- 二元运算 → qlib 表达式 ----
BINOP_MAP = {
    "ADD": "Add", "SUB": "Sub", "MUL": "Mul", "DIV": "Div",
    "GT": "Gt", "GE": "Ge", "LT": "Lt", "LE": "Le",
    "EQ": "Eq", "NE": "Ne",
    "AND": "And", "OR": "Or",
}

# ---- 一元运算 ----
UNARY_MAP = {"NEG": "Neg"}


def _const_fold(e: Expr):
    """对纯常量子树做常量折叠求值；子树含行情字段/变量/函数时返回 None。

    用于在编译期检测恒等于 0 的除数（如 OUT:CLOSE/0 或 OUT:CLOSE/(5-5)）。
    """
    if isinstance(e, Num):
        return float(e.value)
    if isinstance(e, UnaryOp):
        v = _const_fold(e.operand)
        if v is None:
            return None
        return -v if e.op == "NEG" else None
    if isinstance(e, BinOp):
        lv = _const_fold(e.left)
        rv = _const_fold(e.right)
        if lv is None or rv is None:
            return None
        if e.op == "ADD":
            return lv + rv
        if e.op == "SUB":
            return lv - rv
        if e.op == "MUL":
            return lv * rv
        if e.op == "DIV":
            # 除法结果继续折叠会传播 inf/nan，这里直接返回 None 交给外层除零检测
            return None
        return None
    return None

# ---- 函数 → qlib 表达式（直接映射）----
FUNC_QLIB = {
    "MA": "Mean", "EMA": "EMA", "WMA": "WMA",
    "HHV": "Max", "LLV": "Min",
    "SUM": "Sum", "COUNT": "Count",
    "ABS": "Abs", "SQRT": "Sqrt", "LOG": "Log", "LN": "Log",
    "POW": "Pow", "MAX": "Max", "MIN": "Min", "MOD": "Mod",
    "STD": "Std", "VAR": "Var", "SLOPE": "Slope",
    "REF": "Ref", "DELTA": "Delta", "MEAN": "Mean", "MED": "Med",
    "IF": "If", "IFS": "If",
}

# ---- 函数 → 组合表达式（用已有算子展开）----
def _expand_cross(args: List[Expr], code) -> str:
    """CROSS(A,B) = And(Gt(A,B), Le(Ref(A,1),Ref(B,1)))"""
    if len(args) != 2:
        raise CodeGenError("CROSS 需要 2 个参数：CROSS(A,B)")
    a, b = code(args[0]), code(args[1])
    return f"And(Gt({a},{b}),Le(Ref({a},1),Ref({b},1)))"


def _expand_abs(args: List[Expr], code) -> str:
    return f"Abs({code(args[0])})"


def _expand_sign(args: List[Expr], code) -> str:
    return f"Sign({code(args[0])})"


# ---- 需外挂的有状态算子（M3 再实现，这里给出明确提示）----
PATCHED_OPS = {
    "FILTER": "FILTER（信号过滤）",
    "SMA": "SMA（通达信递归均线）",
    "BARSLAST": "BARSLAST（距上次满足条件的周期数）",
    "BARSSINCE": "BARSSINCE",
    "BARSCOUNT": "BARSCOUNT",
    "BACKSET": "BACKSET（未来函数）",
    "ZIG": "ZIG（摆动指标）",
    "PEAK": "PEAK", "TROUGH": "TROUGH", "SAR": "SAR",
    "HHVBARS": "HHVBARS", "LLVBARS": "LLVBARS",
}

# ---- Level2 深度函数（留接口，暂不可用）----
LEVEL2_OPS = {
    "BIGORDER", "ORDER", "ORDERAMT", "ORDERNUM", "ORDERNWP", "ORDERVOL",
    "TRANSACTNUM", "TRANSACTVOL", "ALLASKVOL", "ALLBIDVOL",
}

# ---- 绘图/颜色函数（忽略，不生成因子）----
IGNORED_OPS = {
    "STICKLINE", "DRAWICON", "DRAWTEXT", "DRAWLINE", "DRAWGBK", "DRAWFLAGTEXT",
    "PARTLINE", "POLYLINE", "FILLRGN", "VERTLINE", "DRAWBMP", "RGB",
    "COLORRED", "COLORBLUE", "COLORGREEN", "COLORSTICK", "DOTLINE",
    "CIRCLEDOT", "DASHLINE",
}


class CodeGen:
    def __init__(self, patchable: bool = False):
        # patchable=True 时，遇到外挂算子返回特殊占位（形如 PATCH:FILTER(...)），供后续 M3 接入
        self.patchable = patchable

    def gen(self, expr: Expr) -> str:
        return self._g(expr)

    def _g(self, e: Expr) -> str:
        if isinstance(e, Num):
            v = e.value
            if float(v).is_integer():
                return str(int(v))
            return repr(v)
        if isinstance(e, Field):
            if e.name not in FIELD_MAP:
                raise CodeGenError(f"不支持的行情字段：{e.name}")
            return FIELD_MAP[e.name]
        if isinstance(e, Var):
            raise CodeGenError(f"未展开的变量引用：{e.name}")
        if isinstance(e, UnaryOp):
            op = UNARY_MAP.get(e.op)
            if op is None:
                raise CodeGenError(f"不支持的一元运算：{e.op}")
            if op == "Neg":
                # 负数：若作用于数字字面量 → 直接输出负数常量（如 -100）；
                # 作用于复杂表达式 → 用 Mul(-1, expr)（不能用 Sub(0,expr)，裸 0 常量 qlib 无法加载）
                if isinstance(e.operand, Num):
                    v = -e.operand.value
                    return str(int(v)) if float(v).is_integer() else repr(v)
                return f"Mul(-1,{self._g(e.operand)})"
            return f"{op}({self._g(e.operand)})"
        if isinstance(e, BinOp):
            op = BINOP_MAP.get(e.op)
            if op is None:
                raise CodeGenError(f"不支持的运算：{e.op}")
            if e.op == "DIV":
                # 编译期拦截"分母恒等于 0"的公式，避免回测时整列出现 inf/NaN
                denom = _const_fold(e.right)
                if denom is not None and denom == 0:
                    raise CodeGenError("除数为 0：公式分母恒等于 0，请修改公式")
            return f"{op}({self._g(e.left)},{self._g(e.right)})"
        if isinstance(e, FuncCall):
            return self._gen_func(e)
        raise CodeGenError(f"无法生成的表达式节点：{type(e).__name__}")

    def _gen_func(self, e: FuncCall) -> str:
        name = e.name
        # 忽略绘图/颜色
        if name in IGNORED_OPS:
            raise CodeGenError(f"函数 {name} 是绘图/颜色指令，不生成因子（已忽略）")
        # Level2
        if name in LEVEL2_OPS:
            raise CodeGenError(
                f"函数 {name} 需要 Level2 深度数据，当前数据源未提供，暂不可用")
        # 需外挂算子
        if name in PATCHED_OPS:
            if not self.patchable:
                raise CodeGenError(
                    f"函数 {name} 属于有状态算子（{PATCHED_OPS[name]}），"
                    f"当前阶段未实现，请先实现外挂算子或改用其他函数")
            inner = ",".join(self._g(a) for a in e.args)
            return f"PATCH:{name}({inner})"
        # 直接映射
        if name in FUNC_QLIB:
            q = FUNC_QLIB[name]
            inner = ",".join(self._g(a) for a in e.args)
            return f"{q}({inner})"
        # 组合展开
        if name == "CROSS":
            return _expand_cross(e.args, self._g)
        raise CodeGenError(f"不支持的函数：{name}")


def generate(expr: Expr, patchable: bool = False) -> str:
    """把内联后的表达式树生成 qlib 表达式字符串。"""
    return CodeGen(patchable=patchable).gen(expr)
