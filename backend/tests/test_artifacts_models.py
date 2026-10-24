# -*- coding: utf-8 -*-
"""`artifacts.py` 的**模型交付物 / 复用校验**（★ v2.11.73）。

## 为什么这批比上一批更值钱
上一批测的是"写盘对不对" ✓；这一批测的是**"该挡住的时候有没有挡住"** ✓ ——
复用模型（跳过训练直接用旧权重）如果校验失效，后果是**净值整条被抬高** ✗：

· **训练口径不符**：v1.20.46 把 `valid` 改成独立验证集（原先 `valid == train` ✗ ⇒
  early_stopping 在训练集上做 ⇒ 模型选择失真）。旧口径模型混进新回测 ⇒ 一张图两套口径，
  而净值是**累乘**的 ⇒ 曲线被抬高 ✓（2026-09-22 真事故 ✓）。
· **特征顺序不符**：静默错位预测 ✗（模型还在跑、数字还在出，只是**对应错了列** ✓）。

⇒ 这两条都必须**抛错**；而"源任务目录都找不到"时**不能抛** ✗（否则"任务被删了"会被
误报成"口径不一致"✓ —— 那种错误提示会把人引到错误的方向）。
"""
from __future__ import annotations

import json
import pickle

import pytest

from app.engine import artifacts as A


@pytest.fixture
def work(tmp_path, monkeypatch):
    """把 `config.WORK_DIR` 指到 tmp ⇒ `artifacts` 根就在 `tmp/artifacts/` ✓。"""
    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    (tmp_path / "artifacts").mkdir()
    return tmp_path


def _mk_task(work, task_id: str = "t1", **meta):
    """造一个 `<日期>_<task_id>` 产物目录（真实命名就带前缀 ✓）。"""
    d = work / "artifacts" / ("20260101_0930_%s" % task_id)
    d.mkdir(parents=True, exist_ok=True)
    if meta:
        (d / "model_artifacts.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return d


class _Recorder:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list = []

    def save_objects(self, **kw):
        if self.fail:
            raise RuntimeError("mlflow 挂了")
        self.calls.append(kw)


class TestModelObject:
    def test_roundtrip(self, work):
        """⭐⭐ 存 → 读：**复用模型**的全部依赖就在这一对函数上 ✓。"""
        d = _mk_task(work)
        obj = {"w": [1, 2, 3], "b": 0.5}
        A._save_model_object(obj, str(d))
        assert (d / "model.pkl").is_file()
        assert A._load_model_object("t1", "model") == obj

    def test_segment_model_preferred(self, work):
        """指定段号时**优先**读 `segment_N/model.pkl` ✓（滚动训练每段一个模型 ✓）。"""
        d = _mk_task(work)
        (d / "segment_1").mkdir()
        A._save_model_object("main-model", str(d))
        A._save_model_object("seg1-model", str(d / "segment_1"))
        assert A._load_model_object("t1", "model", seg_no=1) == "seg1-model"
        assert A._load_model_object("t1", "model") == "main-model"

    def test_missing_task_is_none(self, work):
        assert A._load_model_object("不存在", "model") is None

    def test_corrupt_pkl_is_none(self, work):
        """坏 pkl ⇒ `None`（调用方据此"没有可复用权重"✓，不能抛 ✗）。"""
        d = _mk_task(work)
        (d / "model.pkl").write_bytes(b"not-a-pickle")
        assert A._load_model_object("t1", "model") is None

    def test_empty_dir_path_is_safe(self):
        A._save_model_object({"a": 1}, "")   # 不应抛 ✓


class TestSegmentModels:
    def test_detects_segment_dir(self, work):
        d = _mk_task(work)
        assert A._task_has_segment_models("t1") is False
        (d / "segment_1").mkdir()
        assert A._task_has_segment_models("t1") is True

    def test_missing_task(self, work):
        assert A._task_has_segment_models("没有这个") is False


class TestSaveModelArtifacts:
    def test_mlflow_failure_still_writes_local(self, work, monkeypatch):
        """⭐ mlflow 挂了**不能**让本地交付物也丢 ✗（旁路记录：两边都要尽力写 ✓）。"""
        art = work / "art"
        art.mkdir()
        monkeypatch.setattr(A, "get_artifact_dir", lambda: str(art))
        A._save_model_artifacts(_Recorder(fail=True), {"feature_names": ["a", "b"]})
        p = art / "model_artifacts.json"
        assert p.is_file(), "mlflow 失败后本地也没写 ✗"
        assert json.loads(p.read_text(encoding="utf-8"))["feature_names"] == ["a", "b"]

    def test_model_file_goes_to_txt_and_not_json(self, work, monkeypatch):
        art = work / "art"
        art.mkdir()
        monkeypatch.setattr(A, "get_artifact_dir", lambda: str(art))
        rec = _Recorder()
        A._save_model_artifacts(rec, {"model_file": "tree-structure", "feature_names": ["a"]})
        assert (art / "model.txt").read_text(encoding="utf-8") == "tree-structure"
        meta = json.loads((art / "model_artifacts.json").read_text(encoding="utf-8"))
        assert "model_file" not in meta, "树结构不该同时塞进 JSON（体积 ✗）"
        assert rec.calls, "mlflow 那边也该收到一份 ✓"

    def test_segment_subdir(self, work, monkeypatch):
        """滚动训练按段写 `segment_N/` ✓ —— 写错就会**段之间互相覆盖** ✗。"""
        art = work / "art"
        art.mkdir()
        monkeypatch.setattr(A, "get_artifact_dir", lambda: str(art))
        A._save_model_artifacts(_Recorder(), {"model_info": {"segment": "seg2"},
                                              "feature_names": ["x"]})
        assert (art / "segment_2" / "model_artifacts.json").is_file(), \
            "段名 `seg2` 应落到 `segment_2/`（不是 `segment_seg2` ✗）"

    def test_no_artifact_dir_returns(self, monkeypatch):
        monkeypatch.setattr(A, "get_artifact_dir", lambda: None)
        A._save_model_artifacts(_Recorder(), {"a": 1})   # 不应抛 ✓


class TestReadStamps:
    def test_train_semantics_read(self, work):
        _mk_task(work, train_semantics="vX")
        assert A._read_train_semantics("t1") == "vX"

    def test_train_semantics_segment(self, work):
        d = _mk_task(work, train_semantics="main-v")
        (d / "segment_1").mkdir()
        (d / "segment_1" / "model_artifacts.json").write_text(
            json.dumps({"train_semantics": "seg-v"}), encoding="utf-8")
        assert A._read_train_semantics("t1", seg_no=1) == "seg-v"

    def test_train_semantics_missing(self, work):
        _mk_task(work)                       # 有文件但没那个字段
        assert A._read_train_semantics("t1") is None
        assert A._read_train_semantics("不存在") is None

    def test_feature_names_read(self, work):
        _mk_task(work, feature_names=["f1", "f2"])
        assert A._read_train_feature_names("t1") == ["f1", "f2"]
        assert A._read_train_feature_names("不存在") is None


class TestVerifyReuse:
    def test_same_semantics_passes(self, work):
        _mk_task(work, train_semantics=A._current_train_semantics())
        A._verify_reuse_train_semantics("t1")      # 不应抛 ✓

    def test_different_semantics_raises(self, work):
        """⭐⭐ 口径不符必须**挡住** ✓（净值累乘 ⇒ 混口径会把整条曲线抬高 ✗）。"""
        _mk_task(work, train_semantics="2026-01-01-旧口径")
        with pytest.raises(ValueError) as ei:
            A._verify_reuse_train_semantics("t1")
        msg = str(ei.value)
        assert "口径不一致" in msg
        assert "2026-01-01-旧口径" in msg, "错误信息要写明**源口径是什么** ✓（否则用户没法判断 ✗）"
        assert A._current_train_semantics() in msg, "也要写明当前口径 ✓"

    def test_missing_stamp_also_raises(self, work):
        """⭐⭐ **没有戳也要挡** ✓ —— "无法证明一致" ≠ "一致" ✗（v1.20.48 立的就是这条）。"""
        _mk_task(work)                              # 有产物目录，但没有 train_semantics
        with pytest.raises(ValueError) as ei:
            A._verify_reuse_train_semantics("t1")
        assert "没有训练口径戳" in str(ei.value)
        assert "重新训练" in str(ei.value), "要给可操作的下一步 ✓"

    def test_missing_task_does_not_raise(self, work):
        """⭐ 源任务被删 ⇒ **不报口径错** ✓（否则"任务没了"被误报成"口径不一致"✗）。"""
        A._verify_reuse_train_semantics("早就删了")

    def test_feature_order_same_passes(self, work):
        _mk_task(work, feature_names=["a", "b"])
        A._verify_reuse_feature_order(["a", "b"], "t1")

    def test_feature_order_diff_raises(self, work):
        """⭐⭐ 特征顺序不同必须挡 ✓ —— 否则**静默错位预测** ✗（数字照出，对应错了列 ✓）。"""
        _mk_task(work, feature_names=["a", "b", "c"])
        with pytest.raises(ValueError) as ei:
            A._verify_reuse_feature_order(["a", "c", "b"], "t1")
        assert "特征" in str(ei.value)

    def test_feature_order_unknown_is_skipped(self, work):
        """读不到训练特征名 ⇒ **跳过校验** ✓（不阻断；否则老产物永远不能复用 ✗）。"""
        _mk_task(work)
        A._verify_reuse_feature_order(["a"], "t1")
        A._verify_reuse_feature_order(None, "t1")
