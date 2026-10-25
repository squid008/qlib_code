# -*- coding: utf-8 -*-
"""`DATE` 字面量折叠的**两个边界**（★ v2.11.79）—— 都是"跨公式内联后才暴露"的错。

## 背景
`DATE` 是**行情字段**（日序号，float32 存不下 YYYYMMDD），所以用户写 `DATE<=20070901`
时由编译器把 `20070901` 折成日序号 `13757` ✓（见 `codegen.fold_date_literals`）。

折叠逻辑靠一个"**这个子树里含日期值吗**"的标记来决定要不要折另一侧 ✓。
标记传错就会**反过来要求另一侧必须是 8 位日期** ✗ —— 而它往往只是普通数字或布尔 ✓。

## 两个实测 bug（2026-10-25，由启动日志那 48 条 `__RECUR_OUT` 顺着挖出来的）
| bug | 现象 | 修法 |
|---|---|---|
| ① `DATE(x)` 折出来的日序号被标成"含日期值" ✗ | `DATE(20070901)>0` ⇒ 报「与 DATE 比较的这一侧算出来是 0」✗ | 折完标 **False**（它是**普通数值** ✓）|
| ② **比较/逻辑的结果**（布尔）被继续标成"含日期值" ✗ | `KK:DATE<=20070901;` 被调用后 `AAA:KK>0.5;` ⇒ `Gt(Le($date,13757),0.5)` ⇒ 报「…算出来是 0.5」✗ | 布尔算子（`_BOOL_OPS`）**截断**该标记 ✓ |

② 才是真库 11 条常用公式（`步步高`/`B点`/`量王`…）卡住的最后一道 ✓ —— 它们都把
`DATE<=…` 的**布尔结果**再拿去比较 ✓。
"""
from __future__ import annotations

import pytest

from app.factors.parser import build_library, translate_formula
from app.factors.parser.codegen import CodeGenError


def _lib(*texts: str) -> dict:
    return build_library(list(texts))


class TestBug1DateCallIsPlainNumber:
    def test_date_call_result_is_not_a_date_field(self):
        """① `DATE(x)` 折出的是**日序号（普通数值）** ⇒ 外层比较不该要求日期字面量 ✓。

        ⚠ 这条公式本身因为"恒为常量"不能当因子 ✓ —— 但它必须报**那个**错，
        而不是「与 DATE 比较的这一侧算出来是 0」✗。
        """
        with pytest.raises(CodeGenError) as ei:
            translate_formula("AAA:DATE(20070901)>0;")
        assert "与 DATE 比较" not in str(ei.value), str(ei.value)
        # ⚠ 又是"字符串里嵌 ASCII 双引号"那个坑 ✗ —— 中文引号才安全 ✓
        assert "常量" in str(ei.value), "应报「恒为常量」✓：%s" % ei.value

    def test_date_call_arithmetic_allowed_with_field(self):
        """带真实字段时，`DATE(x)` 的减法仍要算得出来（真实天数 ✓）。"""
        r = translate_formula("AAA:DATE(20071008)-DATE(20070901)+CLOSE;")
        assert "Add(" in r.expression or "Sub(" in r.expression, r.expression


class TestBug2BoolResultStopsTheFlag:
    def test_bool_result_compared_again_is_fine(self):
        """⭐⭐ 核心回归 ②：`KK` 里是 `DATE<=字面量`（布尔），外层再比较**必须可以** ✓。

        修复前：`Gt(Le($date,13757), 0.5)` ⇒ 报「与 DATE 比较的这一侧算出来是 0.5」✗。
        """
        lib = _lib("KK:DATE<=20070901;")
        r = translate_formula("AAA:KK>0.5;", library=lib)
        assert "13757" in r.expression, "日期仍要折成日序号 ✓：%s" % r.expression
        assert r.expression.startswith("Gt("), r.expression

    def test_and_or_also_stop_the_flag(self):
        lib = _lib("KK:DATE<=20070901;")
        r = translate_formula("AAA:(KK AND CLOSE>0)+1;", library=lib)
        assert "13757" in r.expression

    def test_bool_result_arithmetic(self):
        lib = _lib("KK:DATE<=20070901;")
        r = translate_formula("AAA:KK*CLOSE;", library=lib)
        assert "13757" in r.expression


class TestPlainDateFoldingUnchanged:
    """修 bug 不能把**主功能**弄丢 ✓（这两条是原有行为 ✓）。"""

    @pytest.mark.parametrize("src", ["AAA:DATE<=20070901;", "AAA:CLOSE>0 AND DATE<20070901;"])
    def test_still_folds(self, src):
        r = translate_formula(src)
        assert "13757" in r.expression, "日期必须折成日序号 ✓：%s" % r.expression
        assert "20070901" not in r.expression, "不能把 8 位字面量原样留进 qlib 表达式 ✗"

    def test_non_constant_side_not_folded(self):
        """另一侧**不是常量** ⇒ 原样 ✓（不能为了折而报错 ✓）。

        ⚠ 我第一版把 `DATE<20070901+CLOSE*0` 也列进"必须折"⇒ 当场红 ✓ ——
        那侧含 `$close` ⇒ 本来就不该折 ✓（错的是**测试**，不是实现 ✓）。
        """
        r = translate_formula("AAA:DATE<20070901+CLOSE*0;")
        assert "13757" not in r.expression
        assert "20070901" in r.expression, r.expression

    def test_dashed_or_too_small_date_still_rejected(self):
        """⚠ **保护不能丢** ✗ —— `DATE<=1997`（用户把 `2007-09-01` 写成减法）必须报错 ✓，
        否则会**静默按 1997 比**（比错而且不报）✗✗。"""
        with pytest.raises(CodeGenError) as ei:
            translate_formula("AAA:DATE<=1997;")
        assert "与 DATE 比较" in str(ei.value), str(ei.value)

    def test_both_sides_are_dates_left_alone(self):
        """两侧都是日期值（`DATE<=REF(DATE,1)`）⇒ 原样交给运行期 ✓。"""
        r = translate_formula("AAA:DATE<=REF(DATE,1);")
        assert "$date" in r.expression and "13757" not in r.expression
