# -*- coding: utf-8 -*-
"""公式翻译器：语法分析（Parser）。

把 token 流解析成 AST。支持：
- 赋值语句：变量 := 表达式 ;  或  变量 = 表达式 ;
- 输出线：  因子名 : 表达式 ;
- 表达式优先级：or < and < 比较 < 加减 < 乘除 < 一元
"""
from __future__ import annotations

from typing import List

from .ast import (
    Formula, Assign, Output, Expr, Num, Field, Var, BinOp, UnaryOp, FuncCall,
)
from .lexer import (
    Lexer, Token, TT_NUM, TT_IDENT, TT_OP, TT_LPAREN, TT_RPAREN,
    TT_SEMI, TT_COMMA, TT_COLON, TT_ASSIGN, TT_EOF, LexerError,
)

# 行情字段（大写）→ qlib $field
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
    "MARKET_CAP": "$market_cap",
    # 资金流向字段（moneyflow bin，tools/dump_moneyflow.py 生成）
    "MF_AMOUNT_MAIN": "$mf_amount_main", "MF_PCT_MAIN": "$mf_pct_main",
    "MF_AMOUNT_XL": "$mf_amount_xl", "MF_PCT_XL": "$mf_pct_xl",
    "MF_AMOUNT_L": "$mf_amount_l", "MF_PCT_L": "$mf_pct_l",
    "MF_AMOUNT_M": "$mf_amount_m", "MF_PCT_M": "$mf_pct_m",
    "MF_AMOUNT_S": "$mf_amount_s", "MF_PCT_S": "$mf_pct_s",
}


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, text: str):
        self.tokens = Lexer(text).tokenize()
        self.idx = 0

    def error(self, msg: str):
        t = self.tokens[self.idx]
        pos = t.pos if t.pos >= 0 else "EOF"
        raise ParseError(f"{msg} (位置 {pos})")

    def peek(self, k: int = 0) -> Token:
        i = min(self.idx + k, len(self.tokens) - 1)
        return self.tokens[i]

    def advance(self) -> Token:
        t = self.tokens[self.idx]
        if t.type != TT_EOF:
            self.idx += 1
        return t

    def match_type(self, ttype: str) -> bool:
        if self.peek().type == ttype:
            self.advance()
            return True
        return False

    def expect(self, ttype: str, what: str) -> Token:
        if self.peek().type == ttype:
            return self.advance()
        self.error(f"期望 {what}")

    # ---- 顶层 ----
    def parse(self) -> Formula:
        formula = Formula()
        while self.peek().type != TT_EOF:
            stmt = self._parse_statement()
            if isinstance(stmt, Assign):
                formula.assigns.append(stmt)
            elif isinstance(stmt, Output):
                formula.outputs.append(stmt)
            # 表达式单独作为一条？(如 "A+B;" 无赋值) — 忽略，因为要求单输出
        return formula

    def _parse_statement(self):
        # 语句开头必须是标识符（变量/输出名）
        name_tok = self.peek()
        if name_tok.type != TT_IDENT:
            self.error("语句必须以标识符（变量或输出名）开头")
        name = name_tok.raw or name_tok.value
        # 前瞻：:= 赋值，= 赋值，: 输出线，其它报错
        nxt = self.peek(1)
        if nxt.type == TT_ASSIGN:          # := 或 =（lexer 对 "=" 单字符是否给了 ASSIGN?）
            self.advance()                 # name
            self.advance()                 # :=
            self._check_field_name(name)
            expr = self._parse_expr()
            self._expect_semi()
            return Assign(name, expr)
        if nxt.type == TT_OP and nxt.value == "EQ":
            # 单个 "=" 赋值（lexer 会把 = 标成 EQ，这里在赋值上下文视为赋值）
            self.advance()                 # name
            self.advance()                 # =
            self._check_field_name(name)
            expr = self._parse_expr()
            self._expect_semi()
            return Assign(name, expr)
        if nxt.type == TT_COLON:           # :
            self.advance()                 # name
            self.advance()                 # :
            self._check_field_name(name)
            expr = self._parse_expr()
            self._expect_semi()
            return Output(name, expr)
        self.error("赋值需用 := 或 =，输出需用 :")

    def _expect_semi(self):
        if not self.match_type(TT_SEMI):
            self.error("语句应以分号 ; 结尾")

    # 字段缩写 → 中文名（错误提示用）
    _FIELD_CN = {
        "$close": "收盘价", "$high": "最高价", "$low": "最低价",
        "$open": "开盘价", "$volume": "成交量", "$amount": "成交额",
        "$turn": "换手率", "$vwap": "均价",
    }

    def _check_field_name(self, name: str):
        """字段缩写（C/H/L/O/V/CLOSE 等）不允许作为赋值变量名或输出线名。

        否则表达式里引用 C 会被解析成行情字段而非变量，产生语义混淆。
        这里直接报错，引导用户换用其他变量名。
        """
        upper = name.upper()
        if upper in FIELD_MAP:
            q = FIELD_MAP[upper]
            cn = self._FIELD_CN.get(q, q)
            raise ParseError(
                f"`{name}` 是行情字段（{cn}，{q}），不允许作为变量名或输出线名，请换用其他名称")

    # ---- 表达式（优先级：or < and < 比较 < 加减 < 乘除 < 一元）----
    def _parse_expr(self) -> Expr:
        return self._parse_or()

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        while self.peek().type == TT_OP and self.peek().value == "OR":
            self.advance()
            right = self._parse_and()
            left = BinOp("OR", left, right)
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_comparison()
        while self.peek().type == TT_OP and self.peek().value == "AND":
            self.advance()
            right = self._parse_comparison()
            left = BinOp("AND", left, right)
        return left

    def _parse_comparison(self) -> Expr:
        left = self._parse_addsub()
        t = self.peek()
        if t.type == TT_OP and t.value in ("GT", "GE", "LT", "LE", "EQ", "NE"):
            self.advance()
            right = self._parse_addsub()
            return BinOp(t.value, left, right)
        return left

    def _parse_addsub(self) -> Expr:
        left = self._parse_muldiv()
        while self.peek().type == TT_OP and self.peek().value in ("ADD", "SUB"):
            op = self.advance().value
            right = self._parse_muldiv()
            left = BinOp(op, left, right)
        return left

    def _parse_muldiv(self) -> Expr:
        left = self._parse_unary()
        while self.peek().type == TT_OP and self.peek().value in ("MUL", "DIV"):
            op = self.advance().value
            right = self._parse_unary()
            left = BinOp(op, left, right)
        return left

    def _parse_unary(self) -> Expr:
        t = self.peek()
        if t.type == TT_OP and t.value == "SUB":
            self.advance()
            return UnaryOp("NEG", self._parse_unary())
        return self._parse_atom()

    def _parse_atom(self) -> Expr:
        t = self.peek()
        if t.type == TT_NUM:
            self.advance()
            return Num(float(t.value))
        if t.type == TT_IDENT:
            self.advance()
            upper = t.value
            if upper in FIELD_MAP:
                return Field(upper)
            # 可能是函数调用
            if self.peek().type == TT_LPAREN:
                args = self._parse_args()
                return FuncCall(upper, args)
            return Var(t.raw or t.value)
        if t.type == TT_LPAREN:
            self.advance()
            e = self._parse_expr()
            self.expect(TT_RPAREN, ")")
            return e
        self.error("期望数字、字段、变量或函数")

    def _parse_args(self) -> List[Expr]:
        self.advance()  # (
        args: List[Expr] = []
        if self.match_type(TT_RPAREN):
            return args
        while True:
            args.append(self._parse_expr())
            if self.match_type(TT_RPAREN):
                break
            if not self.match_type(TT_COMMA):
                self.error("函数参数需用逗号分隔")
        return args


def parse_formula(text: str) -> Formula:
    """把公式文本解析成 AST。"""
    return Parser(text).parse()
