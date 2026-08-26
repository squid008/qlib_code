# -*- coding: utf-8 -*-
"""分层回测与 IC 计算纯函数的单元测试（锁住行为，防止重构破坏）。"""
import numpy as np
import pandas as pd
import pytest

from app.engine.qlib_engine import _compute_layers, _compute_ic


def _make_pred_label(n_instruments=10, dates=None, seed=42):
    """构造 (instrument, datetime) MultiIndex 的 pred_label DataFrame（columns: score, label）。"""
    if dates is None:
        dates = ["2022-01-04", "2022-01-05", "2022-01-06"]
    insts = ["SH%06d" % i for i in range(1, n_instruments + 1)]
    index = pd.MultiIndex.from_product([insts, dates], names=["instrument", "datetime"])
    rng = np.random.default_rng(seed)
    n = len(index)
    df = pd.DataFrame(
        {"score": rng.normal(size=n), "label": rng.normal(size=n) * 0.01},
        index=index,
    )
    # 让 label 与 score 正相关，使分层单调、IC 为正
    df["label"] = df["score"] * 0.02 + rng.normal(size=n) * 0.005
    return df


class TestComputeLayers:
    def test_returns_5_groups(self):
        pl = _make_pred_label()
        layers = _compute_layers(pl)
        assert layers is not None
        # 每天一个 point
        assert len(layers) == 3
        # 每个 point 包含 5 组 + long_short + long_average
        pt = layers[0]
        for k in ["Group1", "Group2", "Group3", "Group4", "Group5", "long_short", "long_average"]:
            assert k in pt

    def test_group_monotonic(self):
        """分组应单调：Group1(最强) 累计收益 >= Group5(最弱)。"""
        pl = _make_pred_label()
        layers = _compute_layers(pl)
        # 最后一期的累计收益，Group1 应 >= Group5
        last = layers[-1]
        assert last["Group1"] >= last["Group5"]

    def test_none_on_empty(self):
        assert _compute_layers(None) is None
        assert _compute_layers(pd.DataFrame()) is None

    def test_benchmark_attached(self):
        pl = _make_pred_label()
        bench = {"2022-01-04": 0.0, "2022-01-05": 0.01, "2022-01-06": 0.02}
        layers = _compute_layers(pl, benchmark_ret=bench)
        assert layers[0]["benchmark"] == 0.0
        assert layers[2]["benchmark"] == 0.02


class TestComputeIc:
    def test_returns_ic_series(self):
        pl = _make_pred_label()
        ic = _compute_ic(pl)
        assert ic is not None
        assert "points" in ic and "mean_ic" in ic and "icir" in ic
        # 正相关 → 平均 IC 为正
        assert ic["mean_ic"] > 0
        assert len(ic["points"]) == 3

    def test_none_on_empty(self):
        assert _compute_ic(None) is None
        assert _compute_ic(pd.DataFrame()) is None
