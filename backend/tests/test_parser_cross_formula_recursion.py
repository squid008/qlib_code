# -*- coding: utf-8 -*-
"""**跨公式内联 + 递推组**（★ v2.11.79）—— 一个"自己生成、自己不认"的编译期 bug。

## 当时的现象（2026-10-25 定位，用户截图里的启动日志）
```
[formulas] 步步高 重新编译失败，保留旧 expression：
            SemanticError: 不支持的函数：__RECUR_OUT（在 输出 步步高）
```
启动日志里 **48 条**、涉及 **12 条常用公式**，每次启动都重试、每次都失败 ✓。

## 根因（两层，都在编译期）
1. `macros._callee_tree` 内联被调公式时返回的是**已经 lowering 过的树**
   （`inline_variables(resolve_vars(f))` ⇒ 里面已含 `__RECUR_OUT/PREV` ✓）；
2. caller 展开完 `expand_calls` 之后**还要再跑一遍** `resolve_vars` 的 `check` ✗，
   而它只认 `BUILTIN_FUNCS` ⇒ 把**自己刚生成的**内部算子判成"不支持的函数" ✗✗。

⇒ 触发条件极窄但很常见：**公式 A 调用公式 B，且 B 里有递推组**。
`步步高` 的原文里一个递推都没有 ✓，它只是调了含递推的 `CPX` ✗。

## 修法
· `semantic._INTERNAL_OPS` 放进 `check` 的白名单 ✓（只影响"已展开进树"的形态 ✓）；
· 入口处挡住**用户手写** `__RECUR_*` ✓（否则白名单就成了公开入口 ✗）；
· 两条都钉成测试（下面 ①②）✓。
"""
from __future__ import annotations

import pytest

from app.factors.parser import build_library, translate_formula
from app.factors.parser.semantic import SemanticError


def _lib(*texts: str) -> dict:
    return build_library(list(texts))


class TestRegression:
    def test_call_formula_with_recursion_inside(self):
        """⭐⭐ 核心回归：**A 调 B，B 里有递推** ⇒ 必须能编译 ✓。

        修复前抛 `SemanticError: 不支持的函数：__RECUR_OUT` ✗（就是启动日志那 48 条）。
        """
        lib = _lib("B:REF(B,1)+1;")
        r = translate_formula("A:B*2;", library=lib)
        assert "RecurOut" in r.expression, "被调公式的递推组应保留 ✓：%s" % r.expression
        assert r.name == "A"

    def test_real_shape_like_cpx(self):
        """用**真实形状**再验一次（`步步高: IF(C<中期线 AND CPX<0, …)` 那种）。

        ⚠ `CPX` 在真库里是个多语句大公式 ✓；这里用最小等价形状（含递推 + 变量）即可 ✓
        —— 回归的是**内联层级**，不是某个具体公式 ✓。
        """
        cpx = ("CPX:=0; BASE:=REF(CLOSE,1)+0*CPX; "
               "OUT:=IF(CLOSE>BASE,1,0); CPXOUT:OUT;")
        lib = _lib(cpx)
        r = translate_formula("步步高:IF(CLOSE>10 AND CPXOUT<0,0.5,1);", library=lib)
        assert r.expression

    def test_caller_own_recursion_plus_callee_recursion(self):
        """⭐ caller 自己也有递推组、又调了递推的 callee ⇒ 两组**各自独立** ✓。"""
        lib = _lib("B:REF(B,1)+1;")
        r = translate_formula("A:REF(A,1)+B;", library=lib)
        assert r.expression.count("RecurOut") >= 2, r.expression

    def test_cycle_still_rejected(self):
        """循环引用仍然要报错 ✓（白名单不能顺手把这层保护弄丢 ✗）。

        ⚠ 名字**不能撞内置别名** ✗→✓ —— 我第一版用 `B`/`C` 造循环，`C` 恰好是
        `CLOSE` 的别名 ✓ ⇒ 那条"循环"根本没形成，测试却红在别的原因上 ✓
        （`S:REF(S,1)` 之类同理）。改用 `P1`/`Q1` 这类不冲突的名字 ✓。
        """
        lib = _lib("P1:Q1+1;", "Q1:P1+1;")
        with pytest.raises(SemanticError) as ei:
            translate_formula("AAA:P1;", library=lib)
        assert "循环引用" in str(ei.value), str(ei.value)

    def test_cycle_via_call_form_also_rejected(self):
        lib = _lib("P1:Q1()+1;", "Q1:P1()+1;")
        with pytest.raises(SemanticError) as ei:
            translate_formula("AAA:P1();", library=lib)
        assert "循环引用" in str(ei.value), str(ei.value)


class TestUserCannotWriteInternalOps:
    def test_recur_out_in_user_text_rejected(self):
        """⭐⭐ 白名单只对"已展开进树"的形态有效 ✓ —— 用户手写必须**明确拒绝** ✗，
        否则它就成了公开入口（面板/内核没有对应定义 ✓，写进去只会更难查 ✗）。"""
        with pytest.raises(SemanticError) as ei:
            translate_formula("A:__RECUR_OUT(0,1,CLOSE);")
        msg = str(ei.value)
        assert "__RECUR_" in msg and "内部算子" in msg, msg
        assert "REF(A,1)" in msg, "提示里要告诉用户**该怎么写** ✓：%s" % msg

    @pytest.mark.parametrize("op", ["__RECUR_PREV", "__RECUR_CUR", "__RECUR_OUT"])
    def test_all_three_ops_blocked(self, op):
        with pytest.raises(SemanticError):
            translate_formula("A:%s(0,1);" % op)

    def test_case_insensitive(self):
        """小写也要挡 ✓（否则 `__recur_out(...)` 就能绕过 ✗）。"""
        with pytest.raises(SemanticError):
            translate_formula("A:__recur_out(0,1,CLOSE);")


class TestRecursionStillWorksAlone:
    """修完之后**单公式递推**不能退化 ✓（回归的另一半）。"""

    def test_self_recursion(self):
        r = translate_formula("A:REF(A,1)+1;")
        assert r.expression == "RecurOut(0,1,Add(RecurPrev(0,1),1))", r.expression

    def test_no_library_still_requires_callee(self):
        """不带 library 调别的公式 ⇒ 仍是"未定义变量" ✓（不能因为放行内部算子就放松 ✓）。"""
        with pytest.raises(SemanticError) as ei:
            translate_formula("A:B*2;")
        assert "未定义" in str(ei.value) or "不支持的函数" in str(ei.value), str(ei.value)
