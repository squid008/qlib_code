# -*- coding: utf-8 -*-
"""公式间调用（编译期宏展开，v1.19.87）。

用户 2026-09-17：保存一个 `CPX` 公式后，在别的公式里 `基础:CPX>0 AND C>=REF(C,1) …` 调用它。
"""
import pytest

from app.factors.parser import build_library, translate_formula
from app.factors.parser.macros import extract_params
from app.factors.parser.semantic import SemanticError

# 用户给的 CPX（去掉 K 的"未声明"写法，按新语法声明参数）
CPX = (
    "参数 K=1;\n"
    "买线:=EMA(C,3);\n"
    "卖线:=EMA(SLOPE(C,21)*20+C,42);\n"
    "B_RAW:=CROSS(买线,卖线);\n"
    "S_RAW:=CROSS(卖线,买线);\n"
    "B_FILTER:=FILTER(B_RAW,K);\n"
    "S_FILTER:=FILTER(S_RAW,K);\n"
    "LAST_B:=BARSLAST(B_FILTER);\n"
    "LAST_S:=BARSLAST(S_FILTER);\n"
    "CPX:IF(LAST_B=0,1,IF(LAST_S=0,-1,IF(LAST_B<LAST_S,1,-1)));"
)


def test_parameter_line_is_extracted():
    body, p = extract_params("参数 a=1, B=MA(CLOSE,5);\nX:a+B;")
    assert set(p) == {"A", "B"} and p["A"] == "1"
    assert "参数" not in body and "X:a+B;" in body


def test_build_library_indexes_by_output_name():
    lib = build_library([CPX, "别的:MA(CLOSE,10);"])
    assert set(lib) == {"CPX", "别的"}
    assert "参数" in lib["CPX"]          # 库里存**原文**（前端展示/编辑要用原文）


def test_user_example_calls_another_formula():
    """用户原例：基础:CPX>0 AND C>=REF(C,1) AND C>=O AND C>MA(C,5)。"""
    lib = build_library([CPX])
    t = translate_formula(
        "基础:CPX>0 AND C>=REF(C,1) AND C>=O AND C>MA(C,5);", library=lib)
    assert t.name == "基础"
    # 被调公式的树确实内联进来了（它能算：含 SLOPE / EMA / FILTER / BARSLAST 等）
    for op in ("Slope", "EMA", "FILTER", "BARSLAST", "Mean($close,5)"):
        assert op in t.expression, f"应当内联 CPX 里的 {op}"


def test_default_params_and_positional_args():
    lib = build_library(["参数 K=3, N=5;\nMA_:=MA(CLOSE,N);\n输出:MA_*K;"])
    # 不传参 ⇒ 用默认值 K=3, N=5
    t1 = translate_formula("用默认:输出;", library=lib)
    assert "Mean($close,5)" in t1.expression and "3" in t1.expression
    # 位置传参 ⇒ K=2, N=10
    t2 = translate_formula("传参:输出(2,10);", library=lib)
    assert "Mean($close,10)" in t2.expression and "2" in t2.expression


def test_nested_calls():
    lib = build_library(["内:MA(CLOSE,5);", "外:内*2;"])
    t = translate_formula("用:外+1;", library=lib)
    assert "Mean($close,5)" in t.expression


def test_cycle_is_rejected():
    lib = build_library(["A:B+1;", "B:A+1;"])
    with pytest.raises(SemanticError) as ei:
        translate_formula("用:A;", library=lib)
    assert "循环引用" in str(ei.value)


def test_too_many_args_has_clear_message():
    lib = build_library(["M:MA(CLOSE,5);"])       # 没声明参数
    with pytest.raises(SemanticError) as ei:
        translate_formula("用:M(3);", library=lib)
    assert "只声明了 0 个" in str(ei.value)


def test_local_variable_shadows_library_name():
    """本公式里的局部变量**优先**（同名不展开），避免"遮蔽"被吃掉。

    ⚠ 这里必须夹一个行情字段（`K+CLOSE`）：纯常量的公式在 v1.19.90 之后会被明确拦下
      （"公式不含任何行情字段"，qlib 加载纯常量列会崩）⇒ 只用 `K+1` 测不出遮蔽 ✗。
    """
    lib = build_library(["K:MA(CLOSE,5);"])
    t = translate_formula("K:=99;\n用:K+CLOSE;", library=lib)
    assert "Mean" not in t.expression        # 没把库里的 K=MA(CLOSE,5) 展开进来 ✓
    assert "$close" in t.expression


def test_unknown_name_still_reports_undefined():
    lib = build_library([CPX])
    with pytest.raises(SemanticError) as ei:
        translate_formula("用:没有这个公式;", library=lib)
    assert "未定义" in str(ei.value)


def test_without_library_behaviour_unchanged():
    """不给 library ⇒ 与以前完全一致（老调用点不受影响）。"""
    t = translate_formula("A:MA(CLOSE,5);")
    assert t.name == "A" and "Mean($close,5)" in t.expression


# ---------------- ★ v1.20.57：**顶层公式自己的 `参数` 默认值也要代入** ----------------
# 用户 2026-09-23：「`深跌10` 这种**带数字的**名不能做输出变量吗？这不科学吧，通达信都支持啊」✓
# 实测结论：**与数字毫无关系** ✗ —— `translate_formula` 原先只把 `参数 …;` 声明行**摘掉**、
# 把 `params` **整个丢掉** ✗ ⇒ 顶层公式凡引用参数名处都成"未定义变量" ✗（纯中文名同样失败 ✓）。

def test_toplevel_param_default_is_substituted():
    """用户原案：带数字的中文参数名 ⇒ 默认值必须代入 ✓。"""
    t = translate_formula("参数 深跌10=5;\n输出:CLOSE>MA(CLOSE,深跌10);")
    assert t.name == "输出"
    assert "Mean($close,5)" in t.expression
    assert "深跌10" not in t.expression


def test_toplevel_param_chinese_name_also_works():
    """对照组：**纯中文**参数名 ✓（证明原报错与"数字"无关 ✗）。"""
    t = translate_formula("参数 深跌=5;\n输出:CLOSE>MA(CLOSE,深跌);")
    assert "Mean($close,5)" in t.expression


def test_multiple_param_defaults_and_expression_default():
    """多个参数 + 默认值是**表达式**（`_split_top_commas` 支持 `B=MA(CLOSE,5)` 这类）。

    ⚠ `参数 N=2*3;` 代入后 AST 是 `BinOp` ✗ ⇒ v1.20.57 起用**常量折叠**判窗口 ✓
      ⇒ 仍走内建 `Mean($close,6)`（而不是慢路径 `DYN_MEAN` ✓）。
    """
    t = translate_formula("参数 A=3, B=5;\n输出:A*CLOSE+MA(CLOSE,B);")
    assert "Mul(3,$close)" in t.expression and "Mean($close,5)" in t.expression
    t2 = translate_formula("参数 N=2*3;\n输出:MA(CLOSE,N);")
    assert "Mean($close,6)" in t2.expression and "DYN_MEAN" not in t2.expression


def test_param_used_in_output_name_position_and_assign():
    """参数既能用在中间变量里、也能用在输出表达式里 ✓（且输出名可与参数无关 ✓）。"""
    t = translate_formula("参数 K=2;\n中期:=MA(CLOSE,K);\n强势:CLOSE>中期*K;")
    assert "Mean($close,2)" in t.expression and "Mul(Mean($close,2),2)" in t.expression


def test_local_variable_still_shadows_param():
    """⚠ 局部优先语义**不变** ✓：本公式里同名的 `:=` 变量仍然是它自己（不替换成参数默认值）✓。

    口径与 `macros._callee_tree`（被调公式传参）一致 ✓ —— 见 `apply_param_defaults` 的 `locals_` ✓。
    """
    t = translate_formula("参数 K=1;\nK:=99;\n用:K+CLOSE;")
    assert "99" in t.expression and "$close" in t.expression


def test_param_defaults_also_apply_when_library_present():
    """带公式库时同样要代入 ✓（原先会误报"引用了未定义的变量或函数"✗）。"""
    lib = build_library(["M:MA(CLOSE,5);"])
    t = translate_formula("参数 K=2;\n输出:CLOSE+M*K;", library=lib)
    assert "Mean($close,5)" in t.expression and "Mul(Mean($close,5),2)" in t.expression
