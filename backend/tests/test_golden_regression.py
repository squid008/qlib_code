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

    def test_forward_backward_keep_original(self):
        from app.engine.adjust import adjust_expr

        expr = "Mean($close, 20)"
        assert adjust_expr(expr, "forward") == expr
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
        """资金流 bin 数值 golden：平安银行 2024-01-02 主力净占比 -14.20、超大单净额 -18361。"""
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
        assert df["$mf_pct_main"].iloc[0] == pytest.approx(-14.20, abs=0.02)
        assert df["$mf_amount_xl"].iloc[0] == pytest.approx(-18361.23, abs=1.0)
