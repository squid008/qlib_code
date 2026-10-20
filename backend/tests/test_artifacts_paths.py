# -*- coding: utf-8 -*-
"""`engine/artifacts.py` 的**目录定位 / 口径戳读取**（★ v2.10.8，2026-10-20 ✓）。

## 这些函数决定"复用时读到的是谁的产物"

`_find_artifact_base` 是**所有复用/续测的入口** ✓ —— 定位错目录就会：

* 拿**别的任务**的模型去预测 ✗（结果全错但界面正常 ✓）；
* 读**别的任务**的训练口径戳 ✓ ⇒ 守卫比对的是错的对象 ✗（该挡的没挡 ✓）。

`_read_train_semantics` 则决定"能不能证明口径一致" ✓ ——
读不到就必须返回 `None` ✓（调用方据此**挡住复用** ✓ 见 v1.20.48 ✓），
**不能**猜一个默认值 ✗（猜了就等于偷偷放行 ✓）。
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from app.engine import artifacts as A


@pytest.fixture()
def work(tmp_path, monkeypatch):
    """把 `WORK_DIR/artifacts` 指到临时目录 ✓（函数里是 `from ..config import WORK_DIR` ✓）。"""
    from app import config as cfg

    monkeypatch.setattr(cfg, "WORK_DIR", str(tmp_path), raising=False)
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


def _task(root, name: str, **files):
    d = root / name
    d.mkdir()
    for fn, obj in files.items():
        (d / fn).write_text(json.dumps(obj), encoding="utf-8")
    return d


# ------------------------------------------------------- _find_artifact_base
def test_find_base_prefers_prefixed_dir(work):
    _task(work, "20240101_T1")
    got = A._find_artifact_base("T1")
    assert got and os.path.basename(got) == "20240101_T1"


def test_find_base_accepts_bare_task_id(work):
    _task(work, "T2")
    got = A._find_artifact_base("T2")
    assert got and os.path.basename(got) == "T2"


def test_find_base_returns_none_when_missing(work):
    """⭐ 找不到 ⇒ `None` ✓（调用方据此"不挡"✓ —— 那是"任务被删了"✗，不是"口径不一致"✗）。"""
    assert A._find_artifact_base("nope") is None


def test_find_base_does_not_match_other_task_prefix(work):
    """⭐ `T1` **不能**匹配到 `T10` ✓ —— 否则会读**别的任务**的模型 ✗。"""
    _task(work, "20240101_T10")
    assert A._find_artifact_base("T1") is None


# ---------------------------------------------------- _read_train_semantics
def test_read_semantics_missing_file_returns_none(work):
    _task(work, "20240101_T3")
    assert A._read_train_semantics("T3") is None


def test_read_semantics_returns_stamp(work):
    _task(work, "20240101_T4", **{"model_artifacts.json": {"train_semantics": "v7"}})
    assert A._read_train_semantics("T4") == "v7"


def test_read_semantics_segment_first_then_falls_back(work):
    """⭐ 分段复用先读 `segment_N/` ✓，那里没有就退回主目录 ✓（不是"读不到就算没有"✗）。"""
    d = _task(work, "20240101_T5", **{"model_artifacts.json": {"train_semantics": "main"}})
    (d / "segment_2").mkdir()
    (d / "segment_2" / "model_artifacts.json").write_text(
        json.dumps({"train_semantics": "seg"}), encoding="utf-8")
    assert A._read_train_semantics("T5", seg_no=2) == "seg"
    assert A._read_train_semantics("T5", seg_no=9) == "main"      # 该段没有 ⇒ 退主目录 ✓


def test_read_semantics_broken_json_returns_none(work):
    """坏 JSON ⇒ `None` ✓（吞异常 + 留痕 ✓），**不许抛** ✗ —— 否则复用检查本身会让回测崩 ✗。"""
    d = _task(work, "20240101_T6")
    (d / "model_artifacts.json").write_text("{not json", encoding="utf-8")
    assert A._read_train_semantics("T6") is None


def test_read_semantics_empty_stamp_is_none(work):
    """戳是空串/缺字段 ⇒ `None` ✓（"有文件"不等于"有戳"✗）。"""
    _task(work, "20240101_T7", **{"model_artifacts.json": {"train_semantics": ""}})
    assert A._read_train_semantics("T7") is None


# --------------------------------------------------- _task_has_segment_models
def test_has_segment_models_detects_rolling_layout(work):
    d = _task(work, "20240101_T8")
    assert A._task_has_segment_models("T8") is False
    (d / "segment_1").mkdir()
    assert A._task_has_segment_models("T8") is True


# ------------------------------------------------------- _train_semantics_info
def test_train_semantics_info_shapes_non_scalars(work):
    """非标量值要**转成字符串** ✓（否则写 JSON 会炸 ✗），并统计特征/公式**个数** ✓。"""
    req = SimpleNamespace(model="lgb", feature="Alpha158", split_mode="single",
                          label_horizon=2, price_adjust="none", universe="csi300",
                          selected_features=["a", "b"], custom_formulas=[{"x": 1}])
    info = A._train_semantics_info(req)
    assert info["model"] == "lgb" and info["universe"] == "csi300"
    assert info["n_selected_features"] == 2
    assert info["n_custom_formulas"] == 1
    assert isinstance(info["semantics"], str) and info["semantics"]
    json.dumps(info)          # 必须可序列化 ✓
