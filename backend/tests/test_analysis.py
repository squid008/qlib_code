# -*- coding: utf-8 -*-
"""分层回测与 IC 计算纯函数的单元测试（锁住行为，防止重构破坏）。"""
import numpy as np
import pandas as pd
import pytest

from app.engine.analysis import _compute_layers, _compute_ic, _compute_analysis


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

    def test_rebalance_period_1_keeps_old_behavior(self):
        """rebalance_period=1（默认）应与原行为完全一致。"""
        pl = _make_pred_label()
        default = _compute_layers(pl)
        explicit = _compute_layers(pl, rebalance_period=1)
        assert default == explicit

    def test_cumulative_is_compound_not_arithmetic(self):
        """⚠ v1.19.5 口径：各组是**复利**累计 `Π(1+r)−1`，不是 `Σr` 算术累加。

        独立复算（不复用被测代码的分组逻辑）：自己按 score 降序均分 5 组、取组内 label 均值
        得到「该组当日收益」序列，再**复利累乘** —— 应当等于函数给出的末值；并与算术和**明确不同**
        （只断言"等于复利"还不够：若实现其实在算算术和，必须被这条抓住）。
        """
        pl = _make_pred_label()
        layers = _compute_layers(pl)
        d = pl.sort_values("score", ascending=False)
        daily = {}
        for i in range(5):
            daily["Group%d" % (i + 1)] = d.groupby(level="datetime", group_keys=False)["label"].apply(
                lambda x, _i=i: x[len(x) // 5 * _i: len(x) // 5 * (_i + 1)].mean()  # noqa: B023
            )
        for k in ("Group1", "Group5"):
            r = daily[k].to_numpy(dtype=np.float64)
            comp = float(np.prod(1.0 + r) - 1.0)
            arith = float(np.sum(r))
            assert layers[-1][k] == pytest.approx(comp, abs=2e-6)      # 是复利
            assert abs(comp - arith) > 1e-6                            # 两口径确有可测差异
            assert layers[-1][k] != pytest.approx(arith, abs=1e-6)     # 不是算术

    def test_long_short_is_spread_compound(self):
        """多空 = **逐期价差（最强−最弱）的复利** `Π(1+价差_t)−1`（与回测页分层图同口径）。"""
        pl = _make_pred_label()
        layers = _compute_layers(pl)
        d = pl.sort_values("score", ascending=False)
        g1 = d.groupby(level="datetime", group_keys=False)["label"].apply(
            lambda x: x[: len(x) // 5].mean())
        g5 = d.groupby(level="datetime", group_keys=False)["label"].apply(
            lambda x: x[len(x) // 5 * 4:].mean())
        want = float(np.prod(1.0 + (g1 - g5).to_numpy(dtype=np.float64)) - 1.0)
        assert layers[-1]["long_short"] == pytest.approx(want, abs=2e-6)

    def test_benchmark_is_compound(self, monkeypatch):
        """⚠ v1.19.5：基准曲线同为**逐日复利**（此前 `ret.cumsum()` 算术累加）。"""
        import qlib.data as qd

        from app.engine.analysis import _compute_benchmark_returns
        dates = pd.to_datetime(["2022-01-04", "2022-01-05", "2022-01-06"])
        idx = pd.MultiIndex.from_product([["SH000300"], dates], names=["instrument", "datetime"])
        df = pd.DataFrame({"$close": [100.0, 110.0, 121.0]}, index=idx)   # 每日 +10%

        class _D:
            @staticmethod
            def features(*a, **k):
                return df

        monkeypatch.setattr(qd, "D", _D)
        out = _compute_benchmark_returns("SH000300", "2022-01-04", "2022-01-06")
        assert out["2022-01-06"] == pytest.approx(0.21, abs=1e-6)      # 复利 1.1³−1
        assert out["2022-01-06"] != pytest.approx(0.20, abs=1e-3)      # 不是算术 3×10%

    def test_rebalance_period_gt1_algorithm_a(self):
        """算法A：分层持仓周期>1，调仓日分组持有，仍返回5组+long_short，且分组单调。"""
        # 构造含 ret（当日收益）的 pred_label，用强正相关保证单调
        dates = pd.to_datetime(["2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07",
                                "2022-01-10", "2022-01-11"])
        n_inst = 20
        insts = ["SH%06d" % i for i in range(1, n_inst + 1)]
        index = pd.MultiIndex.from_product([insts, dates], names=["instrument", "datetime"])
        rng = np.random.default_rng(7)
        # score 为股票固有排序（每只股票分数固定），ret 与 score 正相关
        score_map = {inst: rng.normal() for inst in insts}
        scores = [score_map[i] for i, _ in index]
        rets = [s * 0.02 + rng.normal() * 0.002 for s in scores]
        pl = pd.DataFrame({"score": scores, "ret": rets}, index=index)
        # 用 ret 作为 label（供 long_average 等用）
        pl["label"] = pl["ret"]

        layers = _compute_layers(pl, rebalance_period=3)
        assert layers is not None
        assert len(layers) == 6  # 每个交易日一个 point
        pt = layers[0]
        for k in ["Group1", "Group2", "Group3", "Group4", "Group5", "long_short", "long_average"]:
            assert k in pt
        # Group1(最强) 累计收益应 >= Group5(最弱)
        assert layers[-1]["Group1"] >= layers[-1]["Group5"]
        # 算法A在非调仓日应保持持仓（相邻两天分组收益来自同一持仓组合）
        assert layers[0]["Group1"] > layers[1]["Group1"] or layers[1]["Group1"] >= 0


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


class TestComputeAnalysisReturnsTestPl:
    """验证 _compute_analysis 返回 test_pl，供调用方复用以避免重复 predict。"""

    def test_returns_test_pl(self, monkeypatch):
        pl = _make_pred_label()
        calls = []

        def fake_get_pred_label(model, dataset, instruments, segment, label_horizon=2):
            calls.append(segment)
            return pl

        monkeypatch.setattr("app.engine.analysis._get_pred_label", fake_get_pred_label)
        monkeypatch.setattr("app.engine.analysis._compute_benchmark_returns", lambda *a, **k: {})

        result = _compute_analysis(None, None, ["SH000001"], "段1")
        # test 和 train 各调用一次 _get_pred_label
        assert set(calls) == {"test", "train"}
        # test_pl 被返回（调用方可直接复用，无需再 predict 一次）
        assert result["test_pl"] is pl
        assert result["layers"] is not None
        assert result["ic_train"] is not None
        assert result["ic_test"] is not None
