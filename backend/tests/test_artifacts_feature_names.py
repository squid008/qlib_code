# -*- coding: utf-8 -*-
"""`artifacts` 的**特征名读取**（★ v2.11.83）—— 复用模型时的"逐位对齐"依据。

## 为什么这两个小函数值钱
复用模型时要比对**特征顺序** ✓（错位 = 静默错位预测 ✗，见 `_verify_reuse_feature_order`）。
顺序从哪来？就是这两个：

· `_dataset_feature_names(dataset)` —— **当前**这次回测实际喂给模型的列（handler 侧 ✓）；
· `_read_train_feature_names(task_id, seg_no)` —— **上次训练**时存的 `feature_names` ✓。

两边取错任何一个 ⇒ 比对结果没有意义 ✗；而失败时**必须返回 `None`** ✓
（调用方据此"无法校验 ⇒ 跳过"，不是"当作不一致"✗，也不是"当作一致"✗）。
"""
from __future__ import annotations

import json

from app.engine import artifacts as A


def _mk(work, task_id="t1", seg=None, payload=None, name="model_artifacts.json"):
    d = work / "artifacts" / ("20260101_%s" % task_id)
    d.mkdir(parents=True, exist_ok=True)
    target = d / ("segment_%s" % seg) if seg is not None else d
    target.mkdir(exist_ok=True)
    if payload is not None:
        (target / name).write_text(json.dumps(payload), encoding="utf-8")
    return d


class _Handler:
    def __init__(self, cols=None, fail=False):
        self.cols = cols
        self.fail = fail

    def get_cols(self, kind):
        if self.fail:
            raise RuntimeError("handler 没有列")
        return self.cols


class _NoCols:
    """没有 `get_cols` 的 handler ✓ —— ⚠ 不能用 `del self.get_cols` 模拟 ✗：
    那是**类**上的方法，`del` 会抛 `AttributeError: get_cols` ✓（我第一版就这么红的 ✓）。"""


class _Dataset:
    def __init__(self, handler):
        self.handler = handler


class TestDatasetFeatureNames:
    def test_reads_handler_cols_as_str(self):
        assert A._dataset_feature_names(_Dataset(_Handler(["a", "b"]))) == ["a", "b"]

    def test_none_when_no_handler(self):
        assert A._dataset_feature_names(object()) is None

    def test_none_when_empty_cols(self):
        assert A._dataset_feature_names(_Dataset(_Handler([]))) is None

    def test_none_when_handler_raises(self):
        """⚠ 抛异常也只能给 `None` ✓ —— 它是**校验前置**，自己不能变成新的失败源 ✗。"""
        assert A._dataset_feature_names(_Dataset(_Handler(fail=True))) is None

    def test_none_without_get_cols(self):
        assert A._dataset_feature_names(_Dataset(_NoCols())) is None


class TestReadTrainFeatureNames:
    def test_reads_base_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        _mk(tmp_path, payload={"feature_names": ["f1", "f2"]})
        assert A._read_train_feature_names("t1") == ["f1", "f2"]

    def test_segment_file_preferred(self, tmp_path, monkeypatch):
        """⭐⭐ 指定段号 ⇒ **优先** `segment_N/` ✓（滚动训练每段一套特征顺序 ✓）。"""
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        _mk(tmp_path, payload={"feature_names": ["base"]})
        _mk(tmp_path, seg=2, payload={"feature_names": ["seg2"]})
        assert A._read_train_feature_names("t1", seg_no=2) == ["seg2"]
        assert A._read_train_feature_names("t1") == ["base"]

    def test_falls_back_to_base_when_segment_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        _mk(tmp_path, payload={"feature_names": ["base"]})
        assert A._read_train_feature_names("t1", seg_no=9) == ["base"]

    def test_none_when_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        (tmp_path / "artifacts").mkdir()
        assert A._read_train_feature_names("不存在") is None

    def test_none_when_json_corrupt(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        d = _mk(tmp_path)
        (d / "model_artifacts.json").write_text("{坏 json", encoding="utf-8")
        assert A._read_train_feature_names("t1") is None

    def test_none_when_feature_names_empty(self, tmp_path, monkeypatch):
        """空列表也算"没有" ✓ —— 空列表拿去比对会**永远不一致** ✗。"""
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        _mk(tmp_path, payload={"feature_names": []})
        assert A._read_train_feature_names("t1") is None

    def test_exact_name_dir_also_accepted(self, tmp_path, monkeypatch):
        """不带日期前缀的 `<task_id>` 目录也认 ✓（glob 的第二条回退 ✓）。"""
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        d = tmp_path / "artifacts" / "t2"
        d.mkdir(parents=True)
        (d / "model_artifacts.json").write_text(json.dumps({"feature_names": ["x"]}), encoding="utf-8")
        assert A._read_train_feature_names("t2") == ["x"]

    def test_prefers_last_glob_match(self, tmp_path, monkeypatch):
        """同一个 task_id 有多份 ⇒ 取 glob 的**最后**一个 ✓（与 `_find_artifact_base` 同口径 ✓）。"""
        monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
        _mk(tmp_path, payload={"feature_names": ["old"]})
        d2 = tmp_path / "artifacts" / "20260102_t1"
        d2.mkdir(parents=True)
        (d2 / "model_artifacts.json").write_text(json.dumps({"feature_names": ["new"]}), encoding="utf-8")
        assert A._read_train_feature_names("t1") == ["new"]



