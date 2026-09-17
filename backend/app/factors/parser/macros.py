# -*- coding: utf-8 -*-
"""公式间调用（**编译期宏展开**）：把"引用其它已保存公式"的节点换成被调公式的表达式树。

用户 2026-09-17：「我先编辑一个 `CPX` 函数…保存之后，在别的公式去调用它：
`基础:CPX>0 AND C>=REF(C,1) …` 这样支持吗？」

用法（调用方）::

    CPX                  # 用被调公式的**默认参数**
    CPX(2)  / CPX(2,30)  # 按**位置**传参
    基础:CPX>0 AND C>=REF(C,1) AND C>MA(C,5);

被调公式里声明参数（一行，逗号分隔，可省）::

    参数 K=1, N=20;
    A:=MA(CLOSE,5); 输出:A*K;

设计取舍（为什么用"内联"而不是"独立列"）：
- **只在编译期发生**（纯 AST 替换）⇒ **运行期零额外开销** ✓；每条因子仍然只占**一列**，
  读盘次数不变 ✓；
- 代价：① 表达式**变大**；② **跨列没有公共子式缓存** ⇒ 20 个公式都调 `CPX` 时 `CPX` 那部分
  会被各算一遍（等价于把那 10 行代码手工粘贴 20 遍）。几十个公式互相调用的量级可忽略 ✓；
  真要"上百个公式都调同一个大公式"，再考虑"被调公式编译成独立因子列、调用方引用该列"
  （收益共享，但要引擎支持列间引用，复杂度高得多）。
- ⚠ **循环引用**（`A→B→A`）直接报错；嵌套深度上限 `MAX_DEPTH` 兜底 ✓。
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .ast import (
    Formula, Assign, Output, Expr, Num, Var, BinOp, UnaryOp, FuncCall,
)
from .lexer import LexerError
from .parser import parse_formula, ParseError
from .semantic import SemanticError, resolve_vars, inline_variables

# `参数 K=1, N=20;` 声明行（整行；可写"参数"/"PARAM"/"PARAMS"，大小写不敏感）
_PARAM_LINE_RE = re.compile(r"^[ \t]*(?:参数|PARAMS?)[ \t]*([^;]*);[ \t]*$",
                            re.IGNORECASE | re.MULTILINE)

MAX_DEPTH = 16


def _split_top_commas(s: str) -> List[str]:
    """按**括号深度为 0** 的逗号切分（默认值里可能有函数调用，如 `N=MA(CLOSE,5)`）。

    ⚠ 不能直接 `s.split(",")` —— 实测（2026-09-17 单测抓到）：`参数 a=1, B=MA(CLOSE,5);`
      会被切成 `B=MA(CLOSE` 和 `5)` 两半 ✗。
    """
    out: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def extract_params(text: str) -> Tuple[str, Dict[str, str]]:
    """剥离 `参数 a=1, b=2;` 声明行 ⇒（去掉声明行的文本，{参数名: 默认值表达式文本}）。

    ⚠ 声明行必须在**解析之前**摘掉（我们的语法只认 `名:=表达式;` / `名:表达式;`，
      `参数 K=1;` 这种写法本身不是合法语句）✓。
    """
    params: Dict[str, str] = {}

    def _grab(m: re.Match) -> str:
        guts = m.group(1)
        for piece in _split_top_commas(guts):
            piece = piece.strip()
            if not piece:
                continue
            if "=" not in piece:
                raise SemanticError(
                    f"参数声明格式应为 `参数 名=默认值, 名2=默认值;`，当前片段：{piece}")
            k, v = piece.split("=", 1)
            k, v = k.strip(), v.strip()
            if not k or not v:
                raise SemanticError(f"参数声明的名/默认值不能为空：{piece}")
            params[k.upper()] = v
        return ""                       # 声明行整体去掉

    text2 = _PARAM_LINE_RE.sub(_grab, text)
    return text2, params


def parse_param_expr(expr_text: str) -> Expr:
    """把参数默认值的**表达式文本**解析成 AST（借道解析器：包成一条输出线）。"""
    f = parse_formula("__PARAM__:" + expr_text.strip() + ";")
    return f.outputs[0].value


def build_library(texts: List[str]) -> Dict[str, str]:
    """从一批公式**原文**建"公式名 → 原文"索引（供 `translate_formula(library=...)` 用）。

    只**解析取输出名**、不做完整编译 ⇒ **不依赖库自身**（不会循环依赖 ✓）。
    解析失败的条目直接跳过（历史坏公式不该阻塞别人 ✓）。
    """
    lib: Dict[str, str] = {}
    for t in texts or []:
        if not t or not t.strip():
            continue
        try:
            body, _params = extract_params(t)
            f = parse_formula(body)
            # ⚠ 必须**恰好 1 条输出线**才登记：多输出公式（我们只支持单输出 ✗）若硬取
            #   `outputs[0]`，库键会变成一个"中间变量名"⇒ 调用方拿到莫名其妙的报错
            #   （实测：`MA_:MA(CLOSE,N); 输出:MA_*K;` 两条 `:` ⇒ 键成了 `MA_` ✗）。
            if len(f.outputs) != 1:
                continue
            lib[str(f.outputs[0].name).upper()] = t
        except (LexerError, ParseError, SemanticError):
            continue
    return lib


def _subst(expr: Expr, binding: Dict[str, Expr], locals_: set) -> Expr:
    """把表达式里的 `Var(参数名)` 换成绑定的表达式（`locals_` 里的是本公式变量，不替换）。"""
    if isinstance(expr, Var):
        key = expr.name.upper()
        if key in binding and key not in locals_:
            return binding[key]
        return expr
    if isinstance(expr, BinOp):
        return BinOp(expr.op, _subst(expr.left, binding, locals_),
                     _subst(expr.right, binding, locals_))
    if isinstance(expr, UnaryOp):
        return UnaryOp(expr.op, _subst(expr.operand, binding, locals_))
    if isinstance(expr, FuncCall):
        return FuncCall(expr.name, [_subst(a, binding, locals_) for a in expr.args])
    return expr


def _callee_tree(name: str, args: List[Expr], library: Dict[str, str],
                 stack: Tuple[str, ...]) -> Expr:
    """编译被调公式 ⇒ 返回它的**输出表达式单棵树**（参数已绑定、内部调用已展开）。"""
    if name in stack:
        raise SemanticError(
            "公式间调用存在循环引用：%s" % " → ".join(list(stack) + [name]))
    if len(stack) >= MAX_DEPTH:
        raise SemanticError(
            "公式间调用嵌套过深（超过 %d 层）：%s" % (MAX_DEPTH, " → ".join(list(stack) + [name])))

    body, params = extract_params(library[name])
    f = parse_formula(body)                       # 语法错误 ⇒ 带被调公式名的提示（下方包一层）
    names = list(params.keys())
    if len(args) > len(names):
        raise SemanticError(
            f"调用 `{name}(...)` 传了 {len(args)} 个参数，但该公式只声明了 {len(names)} 个"
            f"（{'、'.join(names) or '无'}）—— 参数需在公式里用 `参数 K=1;` 声明")
    # ① 位置传参 / 未传则用默认值
    binding: Dict[str, Expr] = {}
    for i, pn in enumerate(names):
        binding[pn] = args[i] if i < len(args) else parse_param_expr(params[pn])
    # ② 先替换参数（**在** resolve 之前：否则参数名会被判成"未定义变量"✗）
    locals_ = {a.name.upper() for a in f.assigns} | {o.name.upper() for o in f.outputs}
    f = Formula(assigns=[Assign(a.name, _subst(a.value, binding, locals_)) for a in f.assigns],
                outputs=[Output(o.name, _subst(o.value, binding, locals_)) for o in f.outputs])
    # ③ 递归展开被调公式内部**对其它公式的调用**
    f = expand_calls(f, library, stack=stack + (name,))
    # ④ 校验（单输出等）+ 变量内联 ⇒ 单棵树（里面已无 Var，除了"自引用"那种不支持的情形）
    return inline_variables(resolve_vars(f))


def expand_calls(formula: Formula, library: Dict[str, str],
                 stack: Tuple[str, ...] = ()) -> Formula:
    """把公式里"命中 `library` 名字"的 `Var` / `FuncCall` 就地替换为被调公式的输出树。

    ⚠ 本公式**自己定义的变量名/输出名**不参与替换（局部优先，避免"同名遮蔽"被吃掉）✓。
    """
    if not library:
        return formula
    locals_ = {a.name.upper() for a in formula.assigns} | {o.name.upper() for o in formula.outputs}

    def walk(expr: Expr) -> Expr:
        if isinstance(expr, Var):
            key = expr.name.upper()
            if key in library and key not in locals_:
                return _callee_tree(key, [], library, stack)
            return expr
        if isinstance(expr, FuncCall) and expr.name.upper() in library \
                and expr.name.upper() not in locals_:
            args = [walk(a) for a in expr.args]
            return _callee_tree(expr.name.upper(), args, library, stack)
        if isinstance(expr, BinOp):
            return BinOp(expr.op, walk(expr.left), walk(expr.right))
        if isinstance(expr, UnaryOp):
            return UnaryOp(expr.op, walk(expr.operand))
        if isinstance(expr, FuncCall):
            return FuncCall(expr.name, [walk(a) for a in expr.args])
        return expr

    return Formula(assigns=[Assign(a.name, walk(a.value)) for a in formula.assigns],
                   outputs=[Output(o.name, walk(o.value)) for o in formula.outputs])


__all__ = ["extract_params", "parse_param_expr", "build_library", "expand_calls", "MAX_DEPTH"]
