# -*- coding: utf-8 -*-
"""横向统计（`BLOCKSETNUM` / `INSUM`）守卫（2026-10-09）。

守护三块：
1. **公式语言**：字符串字面量（`'全部Ａ股'`）+ 两个函数 → 市场级物化字段名（`$mkt_*`）；
2. **报错可自助**：板块名/参数/计算类型写错时，提示里要能直接看到可用清单；
3. **聚合口径**：`_aggregate` 的四种计算类型 + "停牌股算分母、指标按 0 计"的既定口径
   （数值口径错了会**静默**产出看似合理的偏小/偏大值 ✗ ⇒ 必须钉住 ✓）。
"""
import numpy as np
import pandas as pd
import pytest

from app.factors.market_stat import Spec, _aggregate, specs_from_text
from app.factors.parser import translate_formula
from app.factors.parser.codegen import CodeGenError
from app.factors.parser.lexer import LexerError
from app.factors.parser.semantic import SemanticError


class TestCompile:
    def test_blocksetnum(self):
        assert translate_formula("OUT:BLOCKSETNUM('全部A股');").expression == "$mkt_num_all"

    def test_blocksetnum_fullwidth_and_aliases(self):
        # 用户写的是全角 `Ａ`（截图里就是全角）⇒ NFKC 归一后必须命中
        assert translate_formula("OUT:BLOCKSETNUM('全部Ａ股');").expression == "$mkt_num_all"
        assert translate_formula("OUT:BLOCKSETNUM('沪深300');").expression == "$mkt_num_csi300"
        assert translate_formula("OUT:BLOCKSETNUM('中证1000');").expression == "$mkt_num_csi1000"
        assert translate_formula("OUT:BLOCKSETNUM('沪深A股');").expression == "$mkt_num_hsa"

    def test_insum_field_name(self):
        r = translate_formula("OUT:INSUM('全部A股','IS_GOLD_PIT',1,0);")
        # 字段名 = 板块键 + 公式短键(含名字哈希) + 输出号 + 计算类型
        assert r.expression.startswith("$mkt_insum_all_is_gold_pit_")
        assert r.expression.endswith("_1_0")

    def test_insum_in_real_formula(self):
        """用户那条"纯度"公式的骨架（坑数量 / 总股票数 × 100，>40 截断）。"""
        text = ("坑数量:=INSUM('全部A股','IS_GOLD_PIT',1,0);\n"
                "总股票数:=BLOCKSETNUM('全部A股');\n"
                "纯度:坑数量/总股票数*100;\n")
        r = translate_formula(text)
        assert r.name == "纯度"
        assert "Div(" in r.expression and "Mul(" in r.expression
        assert "$mkt_num_all" in r.expression
        assert "$mkt_insum_all_is_gold_pit_" in r.expression

    def test_double_quotes_equivalent(self):
        assert translate_formula('OUT:BLOCKSETNUM("沪深300");').expression == "$mkt_num_csi300"


class TestErrors:
    def test_unknown_block(self):
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:BLOCKSETNUM('创业板');")
        assert "不认识的板块名" in str(e.value)
        assert "全部A股" in str(e.value)            # 可用清单要在提示里

    def test_blocksetnum_needs_string(self):
        with pytest.raises(CodeGenError):
            translate_formula("OUT:BLOCKSETNUM(CLOSE);")

    def test_insum_needs_four_args(self):
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:INSUM('全部A股','IS_GOLD_PIT',1);")
        assert "4 个参数" in str(e.value)

    def test_insum_out_index_must_be_one(self):
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:INSUM('全部A股','IS_GOLD_PIT',2,0);")
        assert "只能是" in str(e.value) and "1" in str(e.value)

    def test_insum_calc_type_4_not_supported(self):
        with pytest.raises(CodeGenError) as e:
            translate_formula("OUT:INSUM('全部A股','IS_GOLD_PIT',1,4);")
        assert "计算类型" in str(e.value)

    def test_unterminated_string(self):
        with pytest.raises((LexerError, SemanticError)):
            translate_formula("OUT:BLOCKSETNUM('全部A股);")

    def test_bare_string_is_not_generatable(self):
        """字符串只能被这两个函数消费；裸字符串要**明确报错**（不静默）✓。"""
        with pytest.raises(CodeGenError):
            translate_formula("OUT:'全部A股';")


class TestSpecsFromText:
    def test_extract_both(self):
        text = ("坑数量:=INSUM('全部A股','IS_GOLD_PIT',1,0);\n"
                "总股票数:=BLOCKSETNUM('全部A股');\n")
        specs = specs_from_text(text)
        kinds = sorted(s.kind for s in specs)
        assert kinds == ["BLOCKSETNUM", "INSUM"]
        fields = {s.field for s in specs}
        assert fields == {"mkt_num_all", "mkt_insum_all_is_gold_pit_" +
                          Spec("全部A股", "IS_GOLD_PIT", 1, 0).field.split("is_gold_pit_")[1]}

    def test_fullwidth_normalized(self):
        specs = specs_from_text("A:=BLOCKSETNUM('全部Ａ股');")
        assert specs and specs[0].field == "mkt_num_all"

    def test_dedup_and_no_match(self):
        assert specs_from_text("OUT:CLOSE;") == []
        text = "A:=BLOCKSETNUM('全部A股');\nB:=BLOCKSETNUM('全部A股');\n"
        assert len({s.field for s in specs_from_text(text)}) == 1


class TestAggregate:
    """聚合口径（含停牌 NaN 的处理）。"""

    def setup_method(self):
        # (3 天 × 3 只)；第 2 天第 2 只停牌(NaN)；第 3 天第 3 只不在板块里
        self.mat = np.array([
            [1.0, 2.0, 3.0],
            [0.0, np.nan, 1.0],
            [1.0, 1.0, 0.0],
        ])
        self.mask = np.array([
            [True, True, True],
            [True, True, True],
            [True, True, False],
        ])

    def test_sum_nan_as_zero(self):
        np.testing.assert_allclose(_aggregate(self.mat, self.mask, 0), [6.0, 1.0, 2.0])

    def test_mean_denominator_is_member_count(self):
        # 第 2 天：成分 3 只、其中 1 只停牌(NaN→0) ⇒ (0+0+1)/3（**分母仍是 3** ✓）
        np.testing.assert_allclose(_aggregate(self.mat, self.mask, 1), [2.0, 1.0 / 3.0, 1.0])

    def test_max_min_ignore_nan(self):
        np.testing.assert_allclose(_aggregate(self.mat, self.mask, 2), [3.0, 1.0, 1.0])
        np.testing.assert_allclose(_aggregate(self.mat, self.mask, 3), [1.0, 0.0, 1.0])

    def test_mask_excludes_out_of_block(self):
        """第 3 天第 3 只不在板块 ⇒ 不参与统计（值 0 也不能被算进去）✓。"""
        mat = np.array([[1.0, 2.0, 999.0]])
        mask = np.array([[True, True, False]])
        np.testing.assert_allclose(_aggregate(mat, mask, 0), [3.0])

    def test_bad_calc_type(self):
        with pytest.raises(ValueError):
            _aggregate(self.mat, self.mask, 9)


class TestBroadcastWriteAxis:
    """★ 写盘日期轴：**窗口日历** 的值要铺到 **完整日历** 上再按 close 的 header 切片。

    2026-10-09 第一版把两者混了 ⇒ ① 2016 年后上市的股票整段被跳过（3011/6141 ✗，看着像"跑完了"）；
    ② 写得出来的那批**日期轴整体错位** ✗ —— 典型的静默错，必须用测试钉住 ✓。
    """

    def test_window_values_mapped_onto_full_calendar(self, tmp_path, monkeypatch):
        from app.factors import market_stat

        full = pd.date_range("2020-01-01", periods=20, freq="D")
        win = full[5:10]                      # 物化窗口 = 第 5~9 天
        vals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        monkeypatch.setattr(market_stat, "features_dir", lambda: str(tmp_path))

        code = "sh600000"
        (tmp_path / code).mkdir()
        # 该股 close 的日期轴：header=8、7 行（第 8~14 天）⇒ 与窗口只重叠第 8、9 天
        with open(tmp_path / code / "close.day.bin", "wb") as f:
            np.asarray([8.0], dtype="<f4").tofile(f)
            np.arange(7, dtype="<f4").tofile(f)

        n = market_stat._broadcast_write("mkt_num_all", full, win, vals, [code], True)
        assert n == 1
        a = np.fromfile(tmp_path / code / "mkt_num_all.day.bin", dtype="<f4")
        assert int(a[0]) == 8                 # header 必须跟 close 一致（否则多股加载会崩）
        body = a[1:]
        assert len(body) == 7
        np.testing.assert_allclose(body[:2], [4.0, 5.0])     # 第 8/9 天 = 窗口第 4/5 天
        assert np.isnan(body[2:]).all()                      # 窗口外为 NaN，不是错位的值

    def test_no_close_means_skip_not_silent_miswrite(self, tmp_path, monkeypatch):
        from app.factors import market_stat

        full = pd.date_range("2020-01-01", periods=5, freq="D")
        monkeypatch.setattr(market_stat, "features_dir", lambda: str(tmp_path))
        (tmp_path / "sh600000").mkdir()                        # 没有 close.day.bin
        assert market_stat._broadcast_write("mkt_num_all", full, full, np.ones(5),
                                            ["sh600000"], True) == 0
