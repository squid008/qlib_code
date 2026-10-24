# -*- coding: utf-8 -*-
"""`validate_test_request`（★ v2.11.74）—— 单因子测试的**入参闸门**。

## 为什么值得逐条钉 400 文案
这些文案是用户**唯一能看到的线索** ✓。写错的代价不是崩，而是**把人引向错误的方向** ✗ ——
「预测周期需在 1~250 之间」（范围）vs「必须为正整数」（类型）合成一句的话，
用户把 `abc` 改小一点还是过不去 ✓。

## 两处历史事故（都写成回归条）
1. **`rebalance_period=0` 被 `or 5` 静默当成 5** ✓（v1.18.45）⇒ 非法参数**绕过校验** ✓，
   而且结果"看起来正常" ✗ ⇒ 所以校验必须 `is None` 判空 ✓。
2. **K 上限曾是 8，而前端有 10 个预设** ✓（v1.18.64）⇒ 用户勾到第 9 个必报 400 ✗，
   而计算层早为"全选"预留了 12 ✓ —— 两处口径不一致 ✓。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.factors_single_validate import NormalizedReq, validate_test_request


def _f(expr: str = "CLOSE>1", name: str = "f"):
    return SimpleNamespace(expression=expr, name=name, id="1")


def _req(**kw):
    base = dict(
        factors=[_f()],
        exit_factors=None, exit_factor=None, period_roll=False, exit_threshold=0.0,
        start_date="2024-01-01", end_date="2024-12-31",
        label_horizons=[1], label_horizon=None,
        parallel=False, quantiles=None, rebalance_period=None, topk_list=None,
        exclude_st_t1=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _bad(req) -> tuple:
    with pytest.raises(HTTPException) as ei:
        validate_test_request(req)
    assert ei.value.status_code == 400
    return ei.value.detail


class TestHappyPath:
    def test_normalized_fields(self):
        got = validate_test_request(_req(label_horizons=[5, 1, 5], parallel=True))
        assert isinstance(got, NormalizedReq)
        assert got.horizons == [1, 5], "要去重并**升序** ✓（结果按周期排列 ✓）"
        assert got.n_h == 2
        assert got.quantiles == 10, "None ⇒ 默认 10 组 ✓"
        assert got.parallel is True

    def test_parallel_needs_multiple_horizons(self):
        """⭐ 单周期给 `parallel=True` 也只算 **False** ✓（一个周期没什么可并行的 ✓）。"""
        assert validate_test_request(_req(label_horizons=[3], parallel=True)).parallel is False

    def test_label_horizons_wins_over_single(self):
        """`label_horizons` 优先；只给单数 `label_horizon` ⇒ 退化为单周期 ✓（历史行为 ✓）。"""
        assert validate_test_request(_req(label_horizons=[2], label_horizon=9)).horizons == [2]
        assert validate_test_request(_req(label_horizons=None, label_horizon=7)).horizons == [7]

    def test_topk_list_floatified(self):
        got = validate_test_request(_req(topk_list=[5, "10.5", 20]))
        assert got.topk_list == [5.0, 10.5, 20.0]


class TestBasicRejections:
    def test_no_factors(self):
        assert "至少勾选一个因子" in _bad(_req(factors=[]))

    def test_missing_dates(self):
        assert "请填写测试区间" in _bad(_req(start_date=""))
        assert "请填写测试区间" in _bad(_req(end_date=""))

    def test_illegal_date(self):
        """`2026-06-31` 这种前端挡得住、API 直连挡不住的 ⇒ 必须**这里**给可读文案 ✓。"""
        detail = _bad(_req(start_date="2026-06-31"))
        assert detail and "range" not in detail.lower(), \
            "不该把 pandas 的英文报错透给用户 ✗：%s" % detail

    def test_empty_horizons(self):
        assert "预测周期不能为空" in _bad(_req(label_horizons=None, label_horizon=None))

    def test_horizon_not_a_number(self):
        # ⚠ 文案是「必须**为**正整数」（不是我凭印象写的「必须是」✗ —— 断言按实际文案写 ✓）
        detail = _bad(_req(label_horizons=["abc"]))
        assert "必须为正整数" in detail, detail
        assert "'abc'" in detail, "要把**用户填的原值**带出来 ✓（否则他不知道是哪一项错 ✗）"

    @pytest.mark.parametrize("h", [0, -1, 251])
    def test_horizon_out_of_range(self, h):
        assert "1~250" in _bad(_req(label_horizons=[h]))


class TestQuantiles:
    @pytest.mark.parametrize("q", [2, 20])
    def test_bounds_ok(self, q):
        assert validate_test_request(_req(quantiles=q)).quantiles == q

    @pytest.mark.parametrize("q", [1, 21, 0, -3])
    def test_out_of_range(self, q):
        assert "2~20" in _bad(_req(quantiles=q))

    def test_not_an_int(self):
        assert "分位组数必须为整数" in _bad(_req(quantiles="abc"))


class TestRebalancePeriod:
    def test_none_means_follow_horizon(self):
        assert validate_test_request(_req(rebalance_period=None)).rebalance_period is None

    def test_zero_is_rejected_not_defaulted(self):
        """⭐⭐ v1.18.45 的回归：`0` 曾被 `or 5` 静默当成 5 ⇒ **非法参数绕过校验** ✗。
        现在必须**报范围错**（而不是悄悄变成 5 继续跑 ✓）。"""
        assert "1~250" in _bad(_req(rebalance_period=0))

    def test_out_of_range(self):
        assert "1~250" in _bad(_req(rebalance_period=251))

    def test_not_an_int(self):
        assert "调仓期必须为整数" in _bad(_req(rebalance_period="x"))


class TestTopkList:
    def test_over_twelve_rejected(self):
        """⭐⭐ v1.18.64 的回归：上限曾是 8、前端有 10 个预设 ⇒ 勾到第 9 个必报 400 ✗。
        现在 12 = 默认档 + 10 个预设，与计算层 `_ks[:12]` 一致 ✓。"""
        assert "最多点选 12 个 K" in _bad(_req(topk_list=list(range(1, 14))))

    def test_twelve_is_allowed(self):
        assert len(validate_test_request(_req(topk_list=list(range(1, 13)))).topk_list) == 12

    @pytest.mark.parametrize("bad", ["abc", None, [None]])
    def test_not_numeric(self, bad):
        v = bad if isinstance(bad, list) else [bad]
        assert "K 必须为数值" in _bad(_req(topk_list=v))

    @pytest.mark.parametrize("v", [0, -1])
    def test_not_positive(self, v):
        assert "K 必须为正数" in _bad(_req(topk_list=[v]))


class TestStExclusion:
    def test_without_is_st_bin_is_rejected_with_fix_hint(self, monkeypatch):
        """⭐ ST 剔除依赖 `is_st` 标签 ⇒ 本机没 dump 时必须**明确报错并给出修法** ✓，
        否则用户只看到"勾了却没剔除" ⇒ 会以为平台坏了 ✗。"""
        monkeypatch.setattr("app.engine.limits.field_bin_available", lambda f: False)
        detail = _bad(_req(exclude_st_t1=True))
        assert "dump_states.py" in detail, "必须告诉用户**怎么修** ✓：%s" % detail
        assert "取消勾选" in detail, "也要给**不做这一步**的出路 ✓"

    def test_with_is_st_bin_passes(self, monkeypatch):
        monkeypatch.setattr("app.engine.limits.field_bin_available", lambda f: True)
        validate_test_request(_req(exclude_st_t1=True))      # 不应抛 ✓

    def test_not_requested_never_checks_bin(self, monkeypatch):
        """没勾就不该查 bin ✓（否则没数据的机器连普通测试都提交不了 ✗）。"""

        def _boom(_f):
            raise AssertionError("不该查 is_st ✗")

        monkeypatch.setattr("app.engine.limits.field_bin_available", _boom)
        validate_test_request(_req(exclude_st_t1=False))
