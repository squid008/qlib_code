# -*- coding: utf-8 -*-
"""artifacts service 层单元测试：扫描/加载/删除逻辑。"""
import json
import os

import pytest

from app.services import artifacts_service


class TestArtifactService:
    def _make_dir(self, tmp_path, name, task_id):
        """造一个含 params/result/meta/model_artifacts/图片/段目录的产物目录。"""
        base = tmp_path / name
        base.mkdir(parents=True, exist_ok=True)
        with open(base / "params.json", "w", encoding="utf-8") as f:
            json.dump({"model": "LightGBM", "universe": "csi300"}, f)
        with open(base / "result.json", "w", encoding="utf-8") as f:
            json.dump({"total_return": 0.1, "nav": [{"date": "x", "value": 1.0}]}, f)
        with open(base / "meta.json", "w", encoding="utf-8") as f:
            json.dump({"模型": "LightGBM", "股票池": "csi300", "起始日期": "2023-01-01"}, f)
        with open(base / "model_artifacts.json", "w", encoding="utf-8") as f:
            json.dump({"model_info": {"model": "LightGBM"}, "feature_names": ["f1"]}, f)
        (base / "nav_curve.png").write_bytes(b"\x89PNG")
        (base / "segment_1").mkdir()
        with open(base / "segment_1" / "model_artifacts.json", "w", encoding="utf-8") as f:
            json.dump({"model_info": {"segment": "seg1"}}, f)
        return base

    def test_find_artifact_dir(self, tmp_path, monkeypatch):
        d = self._make_dir(tmp_path, "x_ab12cd34ef56", "ab12cd34ef56")
        monkeypatch.setattr(artifacts_service, "artifacts_root", lambda: str(tmp_path))
        found = artifacts_service.find_artifact_dir("ab12cd34ef56")
        assert found == str(d)

    def test_scan_history(self, tmp_path, monkeypatch):
        self._make_dir(tmp_path, "x_ab12cd34ef56", "ab12cd34ef56")
        monkeypatch.setattr(artifacts_service, "artifacts_root", lambda: str(tmp_path))
        res = artifacts_service.scan_history()
        assert len(res["items"]) == 1
        item = res["items"][0]
        assert item["task_id"] == "ab12cd34ef56"
        assert item["has_params"] and item["has_result"] and item["has_artifacts"]
        assert "nav_curve.png" in item["images"]
        assert item["segments"] == ["segment_1"]

    def test_load_snapshot(self, tmp_path, monkeypatch):
        self._make_dir(tmp_path, "x_ab12cd34ef56", "ab12cd34ef56")
        monkeypatch.setattr(artifacts_service, "artifacts_root", lambda: str(tmp_path))
        snap = artifacts_service.load_snapshot("ab12cd34ef56")
        assert snap["params"]["model"] == "LightGBM"
        assert "nav_curve.png" in snap["images"]
        assert snap["segments"] == ["segment_1"]

    def test_load_model_artifacts_single(self, tmp_path, monkeypatch):
        self._make_dir(tmp_path, "x_ab12cd34ef56", "ab12cd34ef56")
        monkeypatch.setattr(artifacts_service, "artifacts_root", lambda: str(tmp_path))
        data = artifacts_service.load_model_artifacts("ab12cd34ef56")
        # 有 segment_1，所以返回 segments 形式
        assert "segments" in data
        assert len(data["segments"]) == 1

    def test_delete_artifacts(self, tmp_path, monkeypatch):
        d = self._make_dir(tmp_path, "x_ab12cd34ef56", "ab12cd34ef56")
        monkeypatch.setattr(artifacts_service, "artifacts_root", lambda: str(tmp_path))
        deleted = artifacts_service.delete_artifacts("ab12cd34ef56")
        assert deleted == "x_ab12cd34ef56"
        assert not d.exists()

    def test_delete_missing_raises(self, monkeypatch):
        monkeypatch.setattr(artifacts_service, "artifacts_root", lambda: "NONEXIST")
        with pytest.raises(artifacts_service.ArtifactNotFoundError):
            artifacts_service.delete_artifacts("zzz")

    def test_load_result_missing_raises(self, tmp_path, monkeypatch):
        self._make_dir(tmp_path, "x_ab12cd34ef56", "ab12cd34ef56")
        os.remove(os.path.join(str(tmp_path), "x_ab12cd34ef56", "result.json"))
        monkeypatch.setattr(artifacts_service, "artifacts_root", lambda: str(tmp_path))
        with pytest.raises(artifacts_service.ArtifactNotFoundError):
            artifacts_service.load_result("ab12cd34ef56")
