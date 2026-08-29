# -*- coding: utf-8 -*-
"""公式翻译器：词法分析（Lexer）。

把益盟/通达信公式文本拆成 token 流。大小写不敏感（标识符统一按大写规范化，
但保留原始文本用于因子命名）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


# ---- Token 类型 ----
TT_NUM = "NUM"          # 数字
TT_IDENT = "IDENT"      # 标识符 / 函数名 / 变量名（可能含中文）
TT_OP = "OP"            # 运算符
TT_LPAREN = "LPAREN"    # (
TT_RPAREN = "RPAREN"    # )
TT_SEMI = "SEMI"        # ;
TT_COMMA = "COMMA"      # ,
TT_COLON = "COLON"      # : （输出线）
TT_ASSIGN = "ASSIGN"    # := 或 = （赋值）
TT_EQ = "EQ"            # = 或 == （相等比较，上下文决定）
TT_EOF = "EOF"

# 一元/二元运算符
OPS = {
    "+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV",
    ">": "GT", "<": "LT", ">=": "GE", "<=": "LE",
    "=": "EQ", "==": "EQ", "<>": "NE", "!=": "NE",
    ">=": "GE", "<=": "LE",
}
# 逻辑关键字（映射到二元逻辑运算，Parser 层再决定合并方式）
LOGIC_OPS = {"AND": "AND", "OR": "OR"}


@dataclass
class Token:
    type: str
    value: str
    # 原始文本（用于变量名/因子名保留原始大小写与中文）
    raw: Optional[str] = None
    pos: int = -1          # 在原始文本中的起始位置


class LexerError(Exception):
    pass


class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.n = len(text)

    def error(self, msg: str):
        raise LexerError(f"{msg} (位置 {self.pos})")

    def peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.text[i] if i < self.n else ""

    def advance(self) -> str:
        c = self.text[self.pos]
        self.pos += 1
        return c

    def _skip_ws(self):
        while self.pos < self.n and self.text[self.pos].isspace():
            self.pos += 1

    def _is_ident_start(self, c: str) -> bool:
        # 标识符开头：字母、下划线、中文等非 ASCII 字符
        return c.isalpha() or c == "_" or ord(c) > 127

    def _is_ident_char(self, c: str) -> bool:
        return c.isalnum() or c == "_" or ord(c) > 127

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        while self.pos < self.n:
            self._skip_ws()
            if self.pos >= self.n:
                break
            start = self.pos
            c = self.peek()

            if c.isdigit() or (c == "." and self.peek(1).isdigit()):
                # 数字开头：若数字后紧跟字母/下划线/中文 → 整体作为标识符（如 30均线涨，益盟命名习惯）
                if self._is_digit_ident_continuation():
                    raw = self._read_ident()
                    upper = raw.upper()
                    tokens.append(Token(TT_IDENT, upper, raw, start))
                    continue
                # 纯数字（含科学计数法，如 1e-12）
                num = self._read_number()
                tokens.append(Token(TT_NUM, num, num, start))
                continue

            if self._is_ident_start(c):
                raw = self._read_ident()
                upper = raw.upper()
                # 逻辑关键字
                if upper in LOGIC_OPS:
                    tokens.append(Token(TT_OP, LOGIC_OPS[upper], raw, start))
                else:
                    tokens.append(Token(TT_IDENT, upper, raw, start))
                continue

            # 运算符
            two = c + self.peek(1)
            if two in OPS:
                self.pos += 2
                tokens.append(Token(TT_OP, OPS[two], two, start))
                continue
            if c == ":=" and False:  # placeholder (never)
                pass
            if c == ":":
                if self.peek(1) == "=":
                    self.pos += 2
                    tokens.append(Token(TT_ASSIGN, ":=", ":=", start))
                else:
                    self.pos += 1
                    tokens.append(Token(TT_COLON, ":", ":", start))
                continue
            if c in OPS:
                self.pos += 1
                tokens.append(Token(TT_OP, OPS[c], c, start))
                continue

            if c == "(":
                self.pos += 1
                tokens.append(Token(TT_LPAREN, "(", "(", start)); continue
            if c == ")":
                self.pos += 1
                tokens.append(Token(TT_RPAREN, ")", ")", start)); continue
            if c == ";":
                self.pos += 1
                tokens.append(Token(TT_SEMI, ";", ";", start)); continue
            if c == ",":
                self.pos += 1
                tokens.append(Token(TT_COMMA, ",", ",", start)); continue

            self.error(f"无法识别的字符 '{c}'")

        tokens.append(Token(TT_EOF, "", "", self.n))
        return tokens

    def _is_digit_ident_continuation(self) -> bool:
        """数字开头但后面紧跟字母/下划线/中文 → 整体应作为标识符（如 30均线涨）。"""
        i = self.pos
        # 跳过数字与小数点
        while i < self.n and (self.text[i].isdigit() or self.text[i] == "."):
            i += 1
        # 跳过指数部分（e+10 / E-3 之类），避免把 1e5 误判为标识符
        if i < self.n and self.text[i] in ("e", "E"):
            nxt = i + 1
            if nxt < self.n and (
                self.text[nxt].isdigit()
                or (self.text[nxt] in ("+", "-") and nxt + 1 < self.n and self.text[nxt + 1].isdigit())
            ):
                i = nxt + 1
                while i < self.n and self.text[i].isdigit():
                    i += 1
        # 数字串结束后的下一个字符是字母/下划线/中文 → 整体是标识符
        if i < self.n:
            c = self.text[i]
            return c.isalpha() or c == "_" or ord(c) > 127
        return False

    def _read_number(self) -> str:
        """读取数字字面量，支持小数和科学计数法（如 1.5、1e-12、2E3）。"""
        start = self.pos
        # 整数/小数部分
        while self.pos < self.n and (self.peek().isdigit() or self.peek() == "."):
            self.pos += 1
        # 指数部分 e/E [+/-] 数字
        if self.pos < self.n and self.peek() in ("e", "E"):
            nxt = self.pos + 1
            if nxt < self.n and (self.text[nxt].isdigit()
                                 or (self.text[nxt] in ("+", "-") and nxt + 1 < self.n
                                     and self.text[nxt + 1].isdigit())):
                self.pos += 1  # e/E
                if self.pos < self.n and self.peek() in ("+", "-"):
                    self.pos += 1
                while self.pos < self.n and self.peek().isdigit():
                    self.pos += 1
        return self.text[start:self.pos]

    def _read_ident(self) -> str:
        start = self.pos
        while self.pos < self.n and self._is_ident_char(self.peek()):
            self.pos += 1
        return self.text[start:self.pos]
