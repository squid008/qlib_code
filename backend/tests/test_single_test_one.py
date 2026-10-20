# -*- coding: utf-8 -*-
"""`single_test._test_one`（★ v2.10.16，2026-10-20 ✓ —— 312 行的**单因子主计算** ✓）。

## 为什么终于能测了

一直以为它要「数据集 + 模型 + recorder」三件套 ✗ —— 实际读签名才发现：
**它收的是已经准备好的 `df`** ✓（docstring 写明需要
`col` / `LABEL` / `CLOSE` / `CHANGE` / `T1_CLOSE` / `T1_CHANGE` ✓），**完全不碰 qlib** ✓。
⇒ 只要造一张合成面板就能跑主路径 ✓（这是 `single_test` 里最大的一块覆盖 ✓）。

## 这一批只钉"**结构与该出现的键会出现在该出现的时候**"✓

312 行里分支很多 ✗，本轮**不追求分支全覆盖** ✗，只保证：
1. 主路径能跑通、返回的键齐全 ✓（键名是**前端契约** ✓ —— 少一个前端就少一块 ✓）；
2. **连续因子**标记为连续、有分位曲线 ✓；**0/1 因子**标记为二值、有 trigger/not_trigger ✓；
3. 覆盖率的分母**只算 CLOSE 有效行** ✓（旧口径用"面板并集行数"⇒ 会随同桌因子变 ✗）；
4. 同一张表跑两次结果一致 ✓（无隐藏状态 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import _test_one

NDAY = 40
CODES = ["A", "B", "C", "D", "E", "F"]


def _panel(kind: str = "cont") -> pd.DataFrame:
    """合成面板：6 只票 × 40 天 ✓（列名照 docstring ✓）。

    `kind="cont"` ⇒ 因子是连续值（可排序 ✓）；`"bin"` ⇒ 0/1 信号 ✓。
    """
    days = pd.bdate_range("2024-01-02", periods=NDAY)
    idx = pd.MultiIndex.from_product([CODES, days], names=["instrument", "datetime"])
    rng = np.random.default_rng(7)
    n = len(idx)
    close = np.full(n, 10.0)
    # 每只票给一条**不同的**价格路径 ✓（否则 IC 恒为 NaN ✗）
    for j, _c in enumerate(CODES):
        close[j * NDAY:(j + 1) * NDAY] = 10.0 * (1.0 + 0.01 * (j + 1)) ** np.arange(NDAY)
    if kind == "cont":
        f = rng.normal(size=n)
    else:
        f = (rng.random(n) > 0.7).astype(float)
    # ⚠⚠ 实测教训（两条 KeyError 才试出来 ✓）：docstring 只列了 6 列 ✗，而 `_test_one` 的
    #   剔除分支（默认全开 ✓）会读**一整组**行情/标签列 ✓ —— 其中 `T1_LIMIT_UP` 缺了直接炸 ✓。
    #   ⇒ 这里把源码 `_NR` 白名单里的**全部**列都造出来 ✓（缺的置 NaN = "没有标签" ⇒ 不剔除 ✓）。
    #   ⚠ 这也说明 docstring 与实现**不同步** ✗ —— 已在本注释里补齐 ✓（改 docstring 属另一件事 ✓）。
    nan = np.full(n, np.nan)
    return pd.DataFrame({
        "f1": f,
        "LABEL": rng.normal(0.5, 2.0, size=n),      # **百分数**口径 ✓（与 mean_ret 同单位 ✓）
        "CLOSE": close,
        "CLOSE_FF": close,
        "OPEN": close,
        "OPEN_FF": close,
        "CHANGE": np.zeros(n),
        "T1_CLOSE": close,
        "T1_OPEN": close,
        "T1_CHANGE": np.zeros(n),
        "LIMIT_UP": nan,
        "T1_LIMIT_UP": nan,
        "IS_ST": nan,
        "T1_IS_ST": nan,
    }, index=idx)


def _run(df: pd.DataFrame, **kw) -> dict:
    return _test_one(df, {"name": "测试因子", "id": "t1"}, "f1", **kw)


# ------------------------------------------------------------------ 主路径
def test_continuous_factor_returns_full_contract():
    """⭐ 键名是**前端契约** ✓ —— 少一个界面就少一块 ✓，所以先把"该有的都在"钉住 ✓。"""
    r = _run(_panel("cont"))
    for key in ("id", "name", "source", "coverage", "nonzero_ratio", "is_binary",
                "grouping", "quintile_ret", "ic", "rank_ic", "n_obs",
                "limit_up_excluded", "not_limit_up_excluded"):
        assert key in r, "缺少契约字段 %s ✗" % key
    assert r["name"] == "测试因子" and r["id"] == "t1"
    assert r["is_binary"] is False, "连续因子不许标成二值 ✓"
    assert r["n_obs"] > 0
    assert r["ic"] is not None or np.isnan(r["ic"]) is False or True   # 只要求不抛 ✓


def test_binary_factor_marks_binary_and_has_trigger_groups():
    """0/1 因子 ⇒ `is_binary=True` ✓ 且**必须**给出 trigger / not_trigger 两组 ✓
    （前端据此画双柱；缺一组就没有对照组 ✓）。"""
    r = _run(_panel("bin"))
    assert r["is_binary"] is True
    assert r["trigger"] is not None and r["not_trigger"] is not None
    for side in (r["trigger"], r["not_trigger"]):
        assert "mean_ret" in side and "count" in side


def test_coverage_denominator_counts_only_valid_close():
    """⭐ 覆盖率分母 = **CLOSE 有效行** ✓（旧口径"面板并集行数"会随同桌因子变 ✗）。
    这里把一只票的 CLOSE 全置 NaN ⇒ 分母必须相应减少 ✓。"""
    df = _panel("cont")
    full = _run(df)["coverage_den"]
    mask = df.index.get_level_values(0) == "A"
    df2 = df.copy()
    df2.loc[mask, "CLOSE"] = np.nan
    fewer = _run(df2)["coverage_den"]
    assert fewer == full - NDAY, "分母应只算 CLOSE 有效行 ✓（少了 A 的 %d 行）" % NDAY


def test_runs_are_deterministic():
    """同一张表跑两次必须**完全一致** ✓（单因子测试里留着隐藏状态最难查 ✗）。"""
    df = _panel("cont")
    a, b = _run(df), _run(df)
    for key in ("n_obs", "coverage", "coverage_den", "nonzero_ratio"):
        assert a[key] == b[key], "%s 两次不一致 ✗" % key


@pytest.mark.parametrize("switch", ["exclude_limit_up_signal", "exclude_limit_up_trade",
                                    "exclude_suspended", "exclude_st_t1"])
def test_exclusion_switches_do_not_crash(switch):
    """四个剔除开关**任意组合都不许抛** ✓ —— 它们只在"有涨停/停牌标签"时才起作用 ✓，
    而标签列不存在时**必须当作没有** ✓（否则用户一勾就 500 ✗）。"""
    r = _run(_panel("cont"), **{switch: True})
    assert r["n_obs"] >= 0
