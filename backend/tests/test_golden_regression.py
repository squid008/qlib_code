# -*- coding: utf-8 -*-
"""golden 回归测试：锁住关键口径/语义，防止静默回退（最高优先）。

分层：
- 纯逻辑（无行情数据依赖，CI 可跑）：复权表达式、SR 算子逻辑、SR 叶子包装、
  L2 翻译、缓存 key 敏感性
- datareq（需本机 cn_data / moneyflow，缺失自动 skip）：万科复牌 SR 语义、
  资金流字段数值
"""
import os

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# 1. 复权表达式（字符串 golden，锁 adjust_expr 语义）
# ---------------------------------------------------------------------------
class TestAdjustExprGolden:
    def test_none_substitutes_price_fields(self):
        from app.engine.adjust import adjust_expr

        assert adjust_expr("Mean($close, 20)", "none") == "Mean(($close/$factor), 20)"
        assert adjust_expr("$close/$open", "none") == "($close/$factor)/($open/$factor)"
        # 非价格字段不替换（除权不变）
        assert adjust_expr("Ref($volume,1)", "none") == "Ref($volume,1)"
        assert adjust_expr("Log($market_cap)", "none") == "Log($market_cap)"
        # 算子名不受影响（不做 Ref/Mean 名替换）
        assert "($close/$factor)" in adjust_expr("Ref($close,1)-Mean($close,5)", "none")

    def test_forward_substitutes_backward_keeps(self):
        """⚠ v1.19.96 语义变更：`forward` **不再保持原样** ✗（原断言的假设已不成立）。

        原假设是"前/后复权**对比率类**表达式等价"✓ —— 但**价格量纲**表达式（LLT/close 这类要拿
        价格**互相比较、排序**的因子 ✗）在该假设下会退化成"按**后复权**价排序" ✗ ⇒ 与米筐 rqalpha
        （前复权/真实价）对不上：K=20 年化 **+9.03%** vs 米筐 **−5.24%**、回撤 −54.16% vs **−81.51%**
        ✗；修复后 **−3.86% / −81.55%** ✓。⇒ `forward` 现在与 `none` 一样替换价格字段 ✓，
        `backward`（后复权 = 收益率口径）保持原样 ✓。
        """
        from app.engine.adjust import adjust_expr

        expr = "Mean($close, 20)"
        assert adjust_expr(expr, "forward") == "Mean(Add($preclose,0), 20)"
        assert adjust_expr(expr, "backward") == expr

    def test_invalid_mode_falls_back_none(self):
        from app.engine.adjust import adjust_expr, normalize_mode

        assert normalize_mode("xxx") == "none"
        assert adjust_expr("$close", "xxx") == "($close/$factor)"


# ---------------------------------------------------------------------------
# 2. SR（停牌删行）算子逻辑（用 fake feature，无需行情数据）
# ---------------------------------------------------------------------------
class _FakeField:
    """模拟 qlib Expression：load() 返回固定 Series。"""

    def __init__(self, s: pd.Series):
        self._s = s

    def load(self, *args, **kwargs):
        return self._s.copy()


class TestSRStrRoundTrip:
    """★ v1.19.86：`str(SR(...))` 必须**可被重新解析**成等价对象（round-trip）。

    背景（用户 2026-09-17 实测）：`SR.__str__` 在无掩码时输出 `SR($close,250)`，其中的 `250`
    是**内部 lookback**、不是掩码；而 qlib 有些路径会**先把表达式 str、再 parse**
    （缓存 key / 错误信息文本 / 部分 driver）⇒ 重新解析时 `250` 落到 `mask` 位置 ⇒
    `AttributeError: 'int' object has no attribute 'load'`（任何含 SR 的表达式都可能中招）。
    """

    class _F:
        """带 `__str__` 的假字段（模拟 qlib Feature，便于断言字符串形态）。"""

        def __init__(self, name):
            self.name = name

        def load(self, *a, **k):
            return pd.Series([1.0, 2.0])

        def __str__(self):
            return self.name

    def test_bare_lookback_string_parses_back_as_lookback(self):
        from app.factors.ops_ext import SR

        a = SR(self._F("$close"), lookback=250)
        assert str(a) == "SR($close,250)"
        # 重新解析：第 2 个位置参数（250）必须还原成 lookback，而不是被当成掩码
        b = SR(self._F("$close"), 250)
        assert b._mask is None
        assert b._lookback == 250
        assert str(b) == str(a)                    # 字符串稳定（缓存 key 不会漂）

    def test_masked_form_round_trips(self):
        from app.factors.ops_ext import SR

        a = SR(self._F("$factor"), self._F("$close"), 250)
        assert str(a) == "SR($factor,$close,250)"
        b = SR(self._F("$factor"), self._F("$close"), 250)
        assert b._mask is not None and b._lookback == 250

    def test_numeric_string_lookback(self):
        """解析路径把数字给成字符串时也要认。"""
        from app.factors.ops_ext import SR

        b = SR(self._F("$close"), "250")
        assert b._mask is None and b._lookback == 250

    def test_expression_mask_still_works(self):
        """真掩码（有 load）不能被误认成 lookback。"""
        from app.factors.ops_ext import SR

        m = self._F("$close")
        b = SR(self._F("$factor"), m)
        assert b._mask is m
        assert b._lookback == SR.LOOKBACK_DAYS

    def test_default_lookback_unchanged(self):
        from app.factors.ops_ext import SR

        b = SR(self._F("$close"))
        assert b._mask is None and b._lookback == SR.LOOKBACK_DAYS


class TestSROperator:
    def test_dropna_self_mode(self):
        from app.factors.ops_ext import SR

        s = pd.Series([1.0, np.nan, 3.0, np.nan, 5.0], index=[0, 1, 2, 3, 4])
        out = SR(_FakeField(s))._load_internal("X", 0, 5)
        assert list(out.index) == [0, 2, 4]
        assert list(out.values) == [1.0, 3.0, 5.0]

    def test_mask_mode_removes_by_mask_not_self_nan(self):
        from app.factors.ops_ext import SR

        # 字段自身无 NaN，但按 $close 掩码删行（factor 停牌日仍有值场景）
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=[0, 1, 2, 3, 4])
        mask = pd.Series([np.nan, 1.0, 1.0, np.nan, 1.0], index=[0, 1, 2, 3, 4])
        out = SR(_FakeField(s), mask=_FakeField(mask))._load_internal("X", 0, 5)
        assert list(out.index) == [1, 2, 4]
        assert list(out.values) == [2.0, 3.0, 5.0]

    def test_lookback_configured(self):
        from app.factors.ops_ext import SR

        # 读取必须向前扩展足够历史跨停牌段（否则复牌首日 Ref 取不到停牌前值）
        assert SR.LOOKBACK_DAYS >= 200
        assert SR(_FakeField(pd.Series([1.0])))._lookback == SR.LOOKBACK_DAYS

    def test_mask_reindex_align(self):
        """mask 与 feature index 不同长度时也能正确对齐（reindex 后再删）。"""
        from app.factors.ops_ext import SR

        s = pd.Series([1.0, 2.0, 3.0], index=[1, 2, 3])
        # mask 在 feature 的 index {1,3} 上有值、{2} 为 NaN → 删 2，保留 1/3
        mask = pd.Series([np.nan, 1.0, np.nan, 1.0], index=[0, 1, 2, 3])
        out = SR(_FakeField(s), mask=_FakeField(mask))._load_internal("X", 0, 4)
        assert list(out.index) == [1, 3]
        assert list(out.values) == [1.0, 3.0]


# ---------------------------------------------------------------------------
# 3. SR 叶子包装（loader 侧字符串重写 golden）
# ---------------------------------------------------------------------------
class TestSRWrapExpr:
    def test_wrap_golden(self):
        from app.engine.feature_cache import _sr_wrap_expr

        cases = {
            "Mean($close,5)": "Mean(SR($close),5)",
            "Ref($close,1)": "Ref(SR($close),1)",
            "Div($close,$factor)": "Div(SR($close),SR($factor,$close))",
            "Mean(Sub($high,Ref($close,1)),30)": (
                "Mean(Sub(SR($high,$close),Ref(SR($close),1)),30)"
            ),
            "Log($market_cap)": "Log(SR($market_cap,$close))",
            "$volume": "SR($volume,$close)",
            "$close": "SR($close)",
        }
        for expr, expect in cases.items():
            assert _sr_wrap_expr(expr) == expect, f"wrap 回退: {expr}"

# ---------------------------------------------------------------------------
# 4. L2 资金流方言翻译 golden
# ---------------------------------------------------------------------------
class TestL2TranslateGolden:
    def test_pct_amo_mapping(self):
        from app.factors.parser import translate_formula

        assert translate_formula("主力比例:L2_PCT(0);").expression == "$mf_pct_main"
        assert translate_formula("超大单占比:L2_PCT(1);").expression == "$mf_pct_xl"
        assert translate_formula("大单占比:L2_PCT(2);").expression == "$mf_pct_l"
        assert translate_formula("中单占比:L2_PCT(3);").expression == "$mf_pct_m"
        assert translate_formula("小单占比:L2_PCT(4);").expression == "$mf_pct_s"
        assert translate_formula("主力净额:L2_AMO(0);").expression == "$mf_amount_main"
        assert translate_formula("超大单净额:L2_AMO(1);").expression == "$mf_amount_xl"
        assert translate_formula("小单净额:L2_AMO(4);").expression == "$mf_amount_s"

    def test_l2_in_expression(self):
        from app.factors.parser import translate_formula

        assert (
            translate_formula("主力均额:MA(L2_AMO(0),5);").expression
            == "Mean($mf_amount_main,5)"
        )
        assert (
            translate_formula("主力占比:mf_pct_main;").expression == "$mf_pct_main"
        )

    def test_l2_errors(self):
        from app.factors.parser import translate_formula
        from app.factors.parser.codegen import CodeGenError

        with pytest.raises(CodeGenError):
            translate_formula("越界:L2_PCT(5);")
        with pytest.raises(CodeGenError):
            translate_formula("无参:L2_AMO();")


# ---------------------------------------------------------------------------
# 5. 缓存 key 敏感性（feature_cache key = 表达式+列名+时间，缺一不可）
# ---------------------------------------------------------------------------
class TestCacheKeySensitivity:
    def test_key_changes_on_any_variation(self):
        from app.engine.feature_cache import _cache_path

        inst = ["SZ000001"]
        s, e = "2020-01-01", "2020-02-01"
        a = _cache_path(inst, ["Mean($close,5)"], ["F0"], s, e)
        assert a == _cache_path(inst, ["Mean($close,5)"], ["F0"], s, e)  # 幂等
        assert a != _cache_path(inst, ["Mean($close,10)"], ["F0"], s, e)  # 表达式
        assert a != _cache_path(inst, ["Mean($close,5)"], ["F1"], s, e)  # 列名
        assert a != _cache_path(inst, ["Mean($close,5)"], ["F0"], s, "2020-03-01")  # 时间


# ---------------------------------------------------------------------------
# 6. datareq：需真实数据（无数据自动 skip）
# ---------------------------------------------------------------------------
@pytest.mark.datareq
class TestDataGolden:
    def test_sr_vanke_resumption(self):
        """万科 2016 停牌半年：SR 语义下复牌首日 Ref(close,1)=停牌前收盘；
        官方语义（不包 SR）为 NaN。"""
        from qlib.data import D
        from app.services.qlib_runtime import ensure_qlib_init
        from app.engine.utils import _default_qlib_uri

        ensure_qlib_init(_default_qlib_uri())
        win = dict(instruments=["SZ000002"], start_time="2016-07-04", end_time="2016-07-04")
        sr = D.features(**win, fields=["Ref(SR($close),1)"])["Ref(SR($close),1)"].iloc[0]
        assert sr == pytest.approx(35.5143, abs=1e-3)  # 2015-12-18 停牌前收盘
        plain = D.features(**win, fields=["Ref($close,1)"])["Ref($close,1)"].iloc[0]
        assert np.isnan(plain)  # 官方补 NaN 行语义：复牌首日 Ref 仍断

    def test_moneyflow_field_value(self):
        """资金流 bin 数值 golden：平安银行 2024-01-02 主力净占比 -15.11、超大单净额 -19304.05。

        （2026-09-07 重刷：moneyflow 源更新后同一天值由 -14.20/-18361.23 变为当前值）
        """
        from app.config import QLIB_PROVIDER_URI

        mf_bin = os.path.join(QLIB_PROVIDER_URI, "features", "sz000001", "mf_pct_main.day.bin")
        if not os.path.isfile(mf_bin):
            pytest.skip("moneyflow bin 未生成（需先跑 tools/dump_moneyflow.py）")
        from qlib.data import D
        from app.services.qlib_runtime import ensure_qlib_init
        from app.engine.utils import _default_qlib_uri

        ensure_qlib_init(_default_qlib_uri())
        df = D.features(
            ["SZ000001"],
            ["$mf_pct_main", "$mf_amount_xl"],
            start_time="2024-01-02",
            end_time="2024-01-02",
        )
        assert df["$mf_pct_main"].iloc[0] == pytest.approx(-15.112272, abs=0.02)
        assert df["$mf_amount_xl"].iloc[0] == pytest.approx(-19304.050781, abs=1.0)
