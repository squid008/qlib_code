# -*- coding: utf-8 -*-
"""Meta-Gate 因子归因测试（v1.19.72）：gain 占比 + 单因子 ablation 的语义正确性 + 落盘。

背景（用户 2026-09-16）：「我勾了 Meta-Gate 风控，训练产物里没有这块结果？不知道机器最后觉得
用哪个风控因子更好？」⇒ 归因必须真的"能指出哪个因子有用"，所以这里不只断言键存在，
而是**构造一个带信号的因子 + 两个噪声因子**，断言带信号的那个 ΔAUC 排第一。

实现：桩 dataset/model + 合成数据跑**真实** `train_gate`（真 LightGBM）；
monkeypatch `_extra_cols` 以避开 qlib 数据依赖（无需数据环境即可验证整条归因链路）。
"""
import json
import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lightgbm")

from app.engine import signal_compose as sc          # noqa: E402
from app.engine.artifacts import _save_compose_attr  # noqa: E402


class _StubDataset:
    """最小 dataset 桩：只需 prepare(seg, col_set=..., data_key=...) 返回 DataFrame。"""

    def __init__(self, feat, label):
        self._feat, self._label = feat, label

    def prepare(self, seg, col_set="feature", data_key=None):   # noqa: ARG002
        return self._feat if col_set == "feature" else self._label


class _StubModel:
    def __init__(self, p):
        self._p = p

    def predict(self, dataset, segment=None):                   # noqa: ARG002
        return self._p


def _synth(n_dates=140, n_inst=60, seed=7):
    """合成数据：标签由 extra_feature_0 主导 ⇒ 它必须是最有用的那个附加因子。"""
    rng = np.random.default_rng(seed)
    dts = pd.date_range("2021-01-04", periods=n_dates, freq="B")
    insts = ["S%04d" % i for i in range(n_inst)]
    idx = pd.MultiIndex.from_product([dts, insts], names=["datetime", "instrument"])
    n = len(idx)
    extra_sig = rng.normal(size=n)
    lab = extra_sig + rng.normal(size=n) * 0.8           # "未来收益"：符号由 extra_feature_0 主导
    feat = pd.DataFrame({"f0": rng.normal(size=n), "f1": rng.normal(size=n)}, index=idx)
    label = pd.DataFrame({"label": lab}, index=idx)
    prim = pd.Series(rng.normal(size=n) * 0.3, index=idx)  # 主模型分（弱）
    extras = pd.DataFrame({"extra_feature_0": extra_sig,
                           "extra_feature_1": rng.normal(size=n),
                           "extra_feature_2": rng.normal(size=n)}, index=idx)
    return _StubDataset(feat, label), _StubModel(prim), extras


def test_attribution_ranks_informative_factor_first(monkeypatch):
    ds, mdl, extras = _synth()
    # 桩：像真实 `_extra_cols` 那样"一条表达式一列"，顺序即请求顺序
    monkeypatch.setattr(sc, "_extra_cols",
                        lambda dataset, exprs, idx, **kw: extras.reindex(idx).iloc[:, :len(exprs)])
    _, gi = sc.train_gate(ds, mdl,
                          {"extra_features": ["e0", "e1", "e2"], "attribution": True})
    at = gi["attribution"]

    # 基线（不含附加因子）与"全用上"都算出来了
    assert at["primary_only_auc"] is not None
    assert at["full_auc"] is not None
    assert abs(at["full_delta_auc"] - (at["full_auc"] - at["primary_only_auc"])) < 1e-12

    rows = at["extras"]
    assert [r["i"] for r in rows] == [0, 1, 2]
    assert all(r["delta_auc"] is not None for r in rows)

    # ★ 语义断言：带信号的那条必须 ΔAUC 最大且为正（这才是"哪个因子更好"的答案）
    best = max(rows, key=lambda r: r["delta_auc"])
    assert best["i"] == 0, "带信号的因子应排第一，实际 %s" % [(r["i"], r["delta_auc"]) for r in rows]
    assert best["delta_auc"] > 0

    # gain 占比在这批附加因子内归一
    assert abs(sum(r["gain_share"] for r in rows) - 1.0) < 1e-6
    assert 0.0 <= at["extras_gain_share"] <= 1.0
    assert 0.0 <= at["primary_p_gain_share"] <= 1.0


def test_attribution_can_be_disabled(monkeypatch):
    ds, mdl, extras = _synth(n_dates=100, n_inst=60)   # ⚠ ≥5000 行（gate 有最小样本门槛）
    monkeypatch.setattr(sc, "_extra_cols",
                        lambda dataset, exprs, idx, **kw: extras.reindex(idx).iloc[:, :len(exprs)])
    _, gi = sc.train_gate(ds, mdl, {"extra_features": ["e0"], "attribution": False})
    assert "attribution" not in gi          # 关掉后不做 ablation（省 k+1 次训练）
    assert gi["extras"] == 1
    assert gi["valid_auc"] > 0


class TestAttrEnabled:
    """`attribution` 取值解析（默认每段 —— 实测 ablation 约 +20~30s/段、36 段 +12~18 分钟）。"""

    def test_default_is_all_segments(self):
        assert sc._attr_enabled(None, "段1") is True
        assert sc._attr_enabled(None, "段9") is True
        assert sc._attr_enabled(None, None) is True      # 一次性训练

    def test_seg1_setting_only_first(self):
        assert sc._attr_enabled("seg1", "段1") is True
        assert sc._attr_enabled("seg1", "段7") is False

    def test_all_and_off(self):
        for v in (True, "all", "on", "yes"):
            assert sc._attr_enabled(v, "段9") is True
        for v in (False, "off", "none", "0"):
            assert sc._attr_enabled(v, "段1") is False


def test_ablation_caps_factor_count(monkeypatch):
    """勾很多公式时只归因 gain 最高的前 _ATTR_MAX_FACTORS 个（防止训练时间失控）。"""
    n_f = sc._ATTR_MAX_FACTORS + 2
    ds, mdl, _ = _synth(n_dates=100, n_inst=60)
    idx = ds._feat.index
    rng = np.random.default_rng(11)
    extras = pd.DataFrame({"extra_feature_%d" % i: rng.normal(size=len(idx))
                           for i in range(n_f)}, index=idx)
    monkeypatch.setattr(sc, "_extra_cols",
                        lambda dataset, exprs, idx_, **kw: extras.reindex(idx_).iloc[:, :len(exprs)])
    _, gi = sc.train_gate(ds, mdl,
                          {"extra_features": ["e%d" % i for i in range(n_f)], "attribution": True})
    at = gi["attribution"]
    assert len(at["extras"]) == sc._ATTR_MAX_FACTORS
    assert at["ablation_capped"] is True
    assert "上限" in at["note"]
    assert gi["extras"] == n_f                   # 因子数照实记录（只是 ablate 了前 N 个）


def test_no_extra_features_keeps_old_interface():
    """不带附加因子时 info 结构与旧版一致（n/valid_auc/reject_ratio），且不触发归因。"""
    ds, mdl, _ = _synth(n_dates=100, n_inst=60)
    _, gi = sc.train_gate(ds, mdl, None)
    assert gi["extras"] == 0
    assert set(["n", "valid_auc", "reject_ratio"]).issubset(gi.keys())
    assert "attribution" not in gi


class TestSaveComposeAttr:
    def test_merges_segments(self, tmp_path):
        d = str(tmp_path)
        _save_compose_attr(d, "段1", {"gate": {"n": 10}})
        _save_compose_attr(d, "段2", {"gate": {"n": 20}})
        with open(os.path.join(d, "compose.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        assert set(data["segments"].keys()) == {"段1", "段2"}
        assert data["segments"]["段2"]["gate"]["n"] == 20
        assert data["updated_at"]

    def test_corrupt_file_rebuilt(self, tmp_path):
        d = str(tmp_path)
        with open(os.path.join(d, "compose.json"), "w", encoding="utf-8") as f:
            f.write("{ 坏文件")
        _save_compose_attr(d, "段1", {"gate": {"n": 1}})
        with open(os.path.join(d, "compose.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "段1" in data["segments"]

    def test_none_dir_is_noop(self):
        _save_compose_attr(None, "段1", {})      # 无产物目录（如未走 artifacts 的调用）不应报错
