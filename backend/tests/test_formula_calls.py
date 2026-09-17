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
    """本公式里的局部变量**优先**（同名不展开），避免"遮蔽"被吃掉。"""
    lib = build_library(["K:MA(CLOSE,5);"])
    t = translate_formula("K:=99;\n用:K+1;", library=lib)
    assert "Mean" not in t.expression


def test_unknown_name_still_reports_undefined():
    lib = build_library([CPX])
    with pytest.raises(SemanticError) as ei:
        translate_formula("用:没有这个公式;", library=lib)
    assert "未定义" in str(ei.value)


def test_without_library_behaviour_unchanged():
    """不给 library ⇒ 与以前完全一致（老调用点不受影响）。"""
    t = translate_formula("A:MA(CLOSE,5);")
    assert t.name == "A" and "Mean($close,5)" in t.expression
