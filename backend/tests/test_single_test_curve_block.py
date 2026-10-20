# -*- coding: utf-8 -*-
"""`_quantile_curve_block`（597 行 ✓）与其调用方 `_continuous_stats`（★ v2.10.20，2026-10-20 ✓）。

## 为什么从"调用方"进，而不是手搓 23 个 kwarg

`_quantile_curve_block` 的 23 个形参全是 `_continuous_stats` 里的**自由变量**（AST 推导出来的 ✓）
⇒ 手工拼这 23 个值既脆又会**测出一个假的分位曲线** ✗。
所以这里从 `_test_one(df, ...)` 进 ✓ —— 真实代码替我们算好这些中间量 ✓，
断言落在它**产出的三个结构**上 ✓：

* `quintile_ret` —— 各分组的日截面收益（`quantiles` 决定组数 ✓）；
* `quantile_curves` —— 分组净值曲线（`n_groups` / `dates` / `monotonicity` / `best_quantile` ✓）；
* `topk_curves` + `topk_sensitivity` —— 按 K（只数或比例 ✓）的曲线与敏感性表 ✓。

## 一个必须先知道的门槛

`_continuous_stats` 开头是 `if not result["is_binary"] and len(sub) >= 100` ✓ ——
**样本不足 100 行就整段跳过** ✓（`quantile_curves` 保持 `None` ✓）。
这条也钉了（见最后一条用例 ✓），否则以后有人把门槛改小/改大都不会被发现 ✗。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import FactorTestCancelled, _test_one

NDAY, CODES = 60, [chr(65 + i) for i in range(12)]


def _panel(n_stocks: int = 12) -> pd.DataFrame:
    codes = CODES[:n_stocks]
    days = pd.bdate_range("2024-01-02", periods=NDAY)
    idx = pd.MultiIndex.from_product([codes, days], names=["instrument", "datetime"])
    rng = np.random.default_rng(11)
    n = len(idx)
    close = np.full(n, 10.0)
    for j in range(len(codes)):
        close[j * NDAY:(j + 1) * NDAY] = 10.0 * (1.0 + 0.002 * (j + 1)) ** np.arange(NDAY)
    nan = np.full(n, np.nan)
    return pd.DataFrame({
        "f1": rng.normal(size=n),
        "LABEL": rng.normal(0.4, 2.0, size=n),
        "CLOSE": close, "CLOSE_FF": close, "OPEN": close, "OPEN_FF": close,
        "CHANGE": np.zeros(n), "T1_CLOSE": close, "T1_OPEN": close, "T1_CHANGE": np.zeros(n),
        "LIMIT_UP": nan, "T1_LIMIT_UP": nan, "IS_ST": nan, "T1_IS_ST": nan,
    }, index=idx)


def _run(**kw) -> dict:
    return _test_one(_panel(), {"name": "t", "id": "t1"}, "f1", **kw)


# ------------------------------------------------------------------ 分组数
@pytest.mark.parametrize("q", [5, 10])
def test_quantile_groups_follow_quantiles_param(q):
    """`quantiles=5/10` ⇒ `quintile_ret` 的**条数**必须跟着变 ✓（写死 10 就说明参数被忽略 ✗）。"""
    r = _run(quantiles=q)
    assert r["grouping"] == "quantile"
    assert isinstance(r["quintile_ret"], list) and len(r["quintile_ret"]) == q
    qc = r["quantile_curves"]
    assert isinstance(qc, dict) and qc["n_groups"] == q


def test_quantile_curves_shape_and_monotonicity():
    """曲线结构：`dates` 与每条曲线的长度一致 ✓、`monotonicity ∈ [-1, 1]` ✓、
    `best_quantile` 落在合法组号内 ✓（它决定前端高亮哪一组 ✓）。"""
    r = _run(quantiles=5)
    qc = r["quantile_curves"]
    assert qc["n_groups"] == 5 and qc["n_periods"] >= 1
    assert len(qc["dates"]) == qc["n_periods"]
    m = qc["monotonicity"]
    assert m is None or -1.0 - 1e-9 <= float(m) <= 1.0 + 1e-9
    bq = qc["best_quantile"]
    assert bq is None or 1 <= int(bq) <= 5


# ------------------------------------------------------------------ topk
def test_topk_accepts_both_percent_and_count():
    """⭐ `topk_list` 里 **≤1 视为比例**（0.1 = 10% ✓）、**>1 视为只数**（10 ✓）——
    两种口径必须都能算 ✓（只认一种的话，用户切到"按百分比"就得到空图 ✗）。"""
    r = _run(topk_list=[0.1, 10], quantiles=5)
    tc = r["topk_curves"]
    tk = r["topk_sensitivity"]
    assert isinstance(tc, dict) and isinstance(tk, dict)
    for key in ("rebalance_period", "horizon", "n_days", "dates", "side", "default_quantile"):
        assert key in tc, "topk_curves 缺字段 %s ✗" % key
    for key in ("rebalance_period", "n_days", "n_periods", "default_k", "ks"):
        assert key in tk, "topk_sensitivity 缺字段 %s ✗" % key
    assert len(tk["ks"]) >= 2, "同时给了比例与只数 ⇒ 至少两档 ✓"


def test_rebalance_period_defaults_to_horizon_and_can_override():
    """⭐ 默认口径：**调仓期跟随预测周期** ✓（用户定的"用预测周期调仓换股"✓）；
    显式传 `rebalance_period` 时必须用它 ✓（否则设置项形同虚设 ✗）。"""
    default = _run(horizon=3, quantiles=5)
    assert default["topk_curves"]["rebalance_period"] == 3
    override = _run(horizon=3, rebalance_period=7, quantiles=5)
    assert override["topk_curves"]["rebalance_period"] == 7


# --------------------------------------------------------------- 取消 / 门槛
def test_cancelled_raises_factor_test_cancelled():
    """⭐ 取消必须**抛出** `FactorTestCancelled` ✓ —— 被吞掉就是"点了取消但继续算" ✗
    （`_continuous_stats` 在每步之间检查 ✓，这里让它第一次检查就返回 True ✓）。"""
    with pytest.raises(FactorTestCancelled):
        _run(cancelled=lambda: True, quantiles=5)


def test_small_sample_skips_continuous_block():
    """⭐ 样本 < 100 ⇒ 整段跳过 ✓（`quantile_curves` 保持 `None` ✓）。
    这条把"100 行门槛"本身钉住 ✓ —— 否则门槛被改动没人知道 ✗。"""
    r = _test_one(_panel(1), {"name": "t", "id": "t1"}, "f1", quantiles=5)
    assert r["n_obs"] == NDAY, "单只票 ⇒ 60 行 ✓（< 100 ✓）"
    # ⚠ 实测细节：跳过时这三个键**根本不存在** ✗（不是 `None` ✓）——
    #   所以断言要用 `.get(...)` ✓；前端读它们时**必须**判空 ✓（否则小样本因子会渲染崩 ✗）。
    assert r.get("quantile_curves") is None
    assert r.get("topk_curves") is None
    assert "quantile_curves" not in r, "（记录：跳过时是缺键，不是 None ✓）"
