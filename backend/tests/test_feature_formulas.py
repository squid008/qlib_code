# -*- coding: utf-8 -*-
"""特征名 ↔ 公式对照测试（v1.19.75）。

用户 2026-09-16：「特征列表里都是特征名称，下载也只是名称，这不对吧？应该还有对应公式才对，
应该是下载 CSV：第一列特征名、第二列 QLIB 公式；如果是用户自定义的公式，第二列是**用户前端保存的
公式（不是转译后的公式）**」⇒ 这里锁住三条语义：
  ① Alpha158/360（含混合模式 A158_/A360_ 前缀）⇒ 第二列 = 因子目录里的 qlib 表达式；
  ② 自定义公式（特征名 = `translate_formula(原文).name`）⇒ 第二列 = **用户原文**，转译结果在 `qlib_expr`；
  ③ 查不到的 ⇒ 明确 `kind="未知"` + 空公式（**不瞎猜**）。
"""
import pytest

from app.services.artifacts_service import build_feature_formulas


@pytest.fixture(scope="module")
def alpha_names():
    from app.factors.catalog import get_catalog
    flat = get_catalog("Alpha158")["flat"]
    assert flat, "因子目录为空，测试前提不成立"
    return {r["name"]: r["expression"] for r in flat}


class TestAlphaSources:
    def test_alpha158_plain(self, alpha_names):
        name = next(iter(alpha_names))
        out = build_feature_formulas({}, [name])
        assert out[0]["kind"] == "Alpha158"
        assert out[0]["formula"] == alpha_names[name]        # 第二列 = 目录里的 qlib 表达式
        assert out[0]["qlib_expr"] == alpha_names[name]

    def test_mixed_prefix(self, alpha_names):
        name = next(iter(alpha_names))
        out = build_feature_formulas({"feature": "mixed"}, ["A158_" + name])
        assert out[0]["kind"] == "Alpha158"
        assert out[0]["formula"] == alpha_names[name]
        assert out[0]["name"] == "A158_" + name               # 第一列保留训练时的前缀名

    def test_alpha360_prefix(self):
        from app.factors.catalog import _alpha360_provider
        rec = _alpha360_provider()[0]
        out = build_feature_formulas({"feature": "mixed"}, ["A360_" + rec["name"]])
        assert out[0]["kind"] == "Alpha360"
        assert out[0]["formula"] == rec["expression"]


class TestCustomFormula:
    def test_second_column_is_user_text_not_translation(self):
        """★ 用户明确要求：自定义公式的第二列是**用户保存的公式原文**，不是转译后的表达式。"""
        from app.factors.parser import translate_formula
        text = "A:=MA(CLOSE,5); 输出:A+100;"
        tname = translate_formula(text).name
        out = build_feature_formulas({"custom_formulas": [text]}, [tname])
        assert out[0]["kind"] == "自定义公式"
        assert out[0]["formula"] == text                       # 用户原文
        assert out[0]["qlib_expr"] == translate_formula(text).expression
        assert out[0]["formula"] != out[0]["qlib_expr"]        # 两者确实不同，别拿转译结果糊弄

    def test_translate_failure_does_not_break_others(self, alpha_names):
        name = next(iter(alpha_names))
        out = build_feature_formulas({"custom_formulas": ["这不是一条公式((( ]]"]}, [name, "XG"])
        assert out[0]["kind"] == "Alpha158"                    # 正常列不受影响
        assert out[1]["kind"] in ("未知", "自定义公式")


class TestUnknown:
    def test_unknown_name_is_blank(self):
        out = build_feature_formulas({}, ["完全不存在的特征名"])
        assert out[0]["kind"] == "未知"
        assert out[0]["formula"] == ""

    def test_empty_input(self):
        assert build_feature_formulas({}, []) == []
        assert build_feature_formulas({}, None) == []
