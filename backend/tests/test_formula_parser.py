# -*- coding: utf-8 -*-
"""公式翻译器（FormulaParser）单元测试。

覆盖：单输出、中间变量内联、:= 与 = 等效、大小写不敏感、
CROSS 展开、外挂算子占位、Level2 留接口、错误分支。
"""
import pytest

from app.factors.parser import translate_formula, TranslatedFactor
from app.factors.parser.lexer import LexerError
from app.factors.parser.parser import ParseError
from app.factors.parser.semantic import SemanticError
from app.factors.parser.codegen import CodeGenError


class TestTranslateBasics:
    def test_single_output_with_intermediate_vars(self):
        """多中间变量 + 单输出：变量被内联展开。"""
        text = """
A:=MA(CLOSE,5);
B:=A+100;
OUT:B;
"""
        r = translate_formula(text)
        assert isinstance(r, TranslatedFactor)
        assert r.name == "OUT"
        assert r.expression == "Add(Mean($close,5),100)"

    def test_assign_equal_equiv(self):
        """:= 与 = 赋值等效。"""
        r1 = translate_formula("A:=MA(CLOSE,5); B:=A+100; OUT:B;")
        r2 = translate_formula("A=MA(CLOSE,5); B=A+100; OUT:B;")
        assert r1.expression == r2.expression == "Add(Mean($close,5),100)"

    def test_case_insensitive(self):
        """大小写不敏感。"""
        r = translate_formula("a:=ma(close,5); b:=a+1; out:b;")
        assert r.expression == "Add(Mean($close,5),1)"

    def test_output_name_kept(self):
        """输出因子名保留原始名。"""
        r = translate_formula("A:=MA(CLOSE,5); 长期线:A+100;")
        assert r.name == "长期线"

    def test_scientific_notation(self):
        """科学计数法（防除零写法）。"""
        r = translate_formula("OUT:100*(HIGH-LOW)/(HIGH+1e-12);")
        assert "1e-12" in r.expression

    def test_negative_literal(self):
        """负数常量直接生成，而非 Sub(0, n)。"""
        r = translate_formula("OUT:-100*(CLOSE-OPEN);")
        assert r.expression == "Mul(-100,Sub($close,$open))"
        assert "Sub(0," not in r.expression


class TestOutputs:
    def test_multiple_outputs_error(self):
        """多输出报错。"""
        with pytest.raises(SemanticError):
            translate_formula("A:=1; X:A+1; Y:A+2;")

    def test_no_output_error(self):
        """无输出报错。"""
        with pytest.raises(SemanticError):
            translate_formula("A:=1; B:=A+1;")

    def test_undefined_var_error(self):
        """未定义变量报错。"""
        with pytest.raises(SemanticError):
            translate_formula("OUT:XYZ+1;")

    def test_circular_ref_error(self):
        """循环引用报错。"""
        with pytest.raises(SemanticError):
            translate_formula("A:=B+1; B:=A+1; OUT:A;")

    def test_unknown_function_error(self):
        """未知函数报错。"""
        with pytest.raises(SemanticError):
            translate_formula("OUT:FOOBAR(1);")


class TestOperatorMapping:
    def test_cross_expansion(self):
        """CROSS 展开为 And(Gt, Le(Ref))。"""
        r = translate_formula("OUT:CROSS(MA(CLOSE,5),MA(CLOSE,10));")
        assert r.expression == (
            "And(Gt(Mean($close,5),Mean($close,10)),"
            "Le(Ref(Mean($close,5),1),Ref(Mean($close,10),1)))"
        )

    def test_hhv_llv_ema(self):
        """HHV/LLV/EMA 映射：默认走 qlib 内建 EMA（adjust=True，聚宽口径）。"""
        r = translate_formula("OUT:EMA(HHV(HIGH,20),5);")
        assert r.expression == "EMA(Max($high,20),5)"
        # 切换 EMA_SEMANTICS='tdx' 时应回退通达信递归外挂算子 EMA_TDX
        import app.factors.parser.codegen as cg
        old = cg.EMA_SEMANTICS
        try:
            cg.EMA_SEMANTICS = "tdx"
            r2 = translate_formula("OUT:EMA(HHV(HIGH,20),5);")
            assert r2.expression == "EMA_TDX(Max($high,20),5)"
        finally:
            cg.EMA_SEMANTICS = old

    def test_if_logic(self):
        """IF 条件。"""
        r = translate_formula("OUT:IF(CLOSE>OPEN,1,0);")
        assert r.expression == "If(Gt($close,$open),1,0)"

    def test_fields_extracted(self):
        """依赖行情字段收集。"""
        r = translate_formula("OUT:(HIGH-LOW)/(HIGH+1e-12);")
        assert set(r.inputs) == {"$high", "$low"}


class TestPatchedOps:
    def test_filter_default_error(self):
        """FILTER 默认报错（需外挂）。"""
        with pytest.raises(CodeGenError):
            translate_formula("OUT:FILTER(CLOSE>OPEN,5);")

    def test_filter_patchable_placeholder(self):
        """FILTER patchable=True 时生成占位。"""
        r = translate_formula("OUT:FILTER(CLOSE>OPEN,5);", patchable=True)
        assert r.expression.startswith("PATCH:FILTER(")
        assert r.has_patch is True


class TestLevel2:
    def test_level2_error(self):
        """Level2 深度函数报错（留接口）。"""
        with pytest.raises(CodeGenError):
            translate_formula("OUT:BIGORDER(1,2);")


class TestMoneyflowL2:
    """L2_PCT/L2_AMO 档位 + 可选买卖方向参数（moneyflow3 派生字段）。"""

    def test_l2_amo_net(self):
        """L2_AMO(n) 无方向：净额字段（向后兼容）。"""
        assert translate_formula("OUT:L2_AMO(0);").expression == "$mf_amount_main"
        assert translate_formula("OUT:L2_AMO(3);").expression == "$mf_amount_m"

    def test_l2_amo_direction(self):
        """L2_AMO(n,b|s) 大小写不敏感 → 买入/卖出字段。"""
        assert translate_formula("OUT:L2_AMO(0,B);").expression == "$mf_amount_main_b"
        assert translate_formula("OUT:L2_AMO(0,b);").expression == "$mf_amount_main_b"
        assert translate_formula("OUT:L2_AMO(0,s);").expression == "$mf_amount_main_s"
        assert translate_formula("OUT:L2_AMO(1,S);").expression == "$mf_amount_xl_s"

    def test_l2_pct(self):
        """L2_PCT(n) 净占比 / 方向占比。"""
        assert translate_formula("OUT:L2_PCT(0);").expression == "$mf_pct_main"
        assert translate_formula("OUT:L2_PCT(2,b);").expression == "$mf_pct_l_b"
        assert translate_formula("OUT:L2_PCT(4,s);").expression == "$mf_pct_s_s"

    def test_l2_direct_field(self):
        """方向字段也可在公式中直接引用。"""
        assert translate_formula("OUT:MF_AMOUNT_MAIN_B;").expression == "$mf_amount_main_b"
        assert translate_formula("OUT:MF_PCT_MAIN_B;").expression == "$mf_pct_main_b"

    def test_l2_errors(self):
        """参数错误分支。"""
        with pytest.raises(CodeGenError):
            translate_formula("OUT:L2_AMO(5);")          # 档位越界
        with pytest.raises(CodeGenError):
            translate_formula("OUT:L2_AMO(0,X);")        # 方向非法
        with pytest.raises(CodeGenError):
            translate_formula("OUT:L2_AMO(0,B,1);")      # 参数过多
        with pytest.raises(CodeGenError):
            translate_formula("OUT:L2_AMO();")           # 缺档位


class TestSyntax:
    def test_parse_error(self):
        """语法错误报 ParseError。"""
        with pytest.raises(ParseError):
            translate_formula("OUT:(1+2;")

    def test_lexer_error(self):
        """非法字符报 LexerError。"""
        with pytest.raises(LexerError):
            translate_formula("OUT:@#$;")
