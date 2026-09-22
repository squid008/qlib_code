# -*- coding: utf-8 -*-
"""段目录**排序**单测（v1.20.50）。

背景（2026-09-22 用户实测）：后端原先用 `sorted(glob("segment_*"))` = **字符串排序**
⇒ `segment_1, segment_10, segment_11, …, segment_2, segment_20, …`（错序）；
而前端段选择器显示「段 {数组下标+1}」⇒ **段号全错**（"下拉里的『段 3』其实是 seg11"）。
修复：后端按**数字段号**排序 + 前端改取 `model_info.segment` 真实段号（双保险）。
"""
import json
import os


def test_segment_dirs_numeric_order(tmp_path):
    from app.services import artifacts_service as svc

    for n in (1, 2, 10, 11, 20):
        (tmp_path / ("segment_%d" % n)).mkdir()
    names = [os.path.basename(p) for p in svc._sorted_segment_dirs(str(tmp_path))]
    assert names == ["segment_1", "segment_2", "segment_10", "segment_11", "segment_20"], names


def test_seg_no_of_dir(tmp_path):
    from app.services import artifacts_service as svc

    assert svc._seg_no_of_dir(os.path.join(str(tmp_path), "segment_7")) == 7
    assert svc._seg_no_of_dir("segment_12") == 12
    assert svc._seg_no_of_dir("not_a_segment") == 10 ** 9      # 解不出 ⇒ 排最后 ✓


def test_load_model_artifacts_segments_order(tmp_path, monkeypatch):
    """`/artifacts` 返回的 `segments` 必须按**数字段号**排列 ✓（前端段选择器据此显示 ✓）。"""
    from app.services import artifacts_service as svc

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    base = tmp_path / "artifacts" / "20260922-000000_Test_ID"
    for n in (1, 2, 10):
        d = base / ("segment_%d" % n)
        d.mkdir(parents=True)
        (d / "model_artifacts.json").write_text(
            json.dumps({"model_info": {"segment": "seg%d" % n}}), encoding="utf-8")

    out = svc.load_model_artifacts("ID")
    assert [s["model_info"]["segment"] for s in out["segments"]] == ["seg1", "seg2", "seg10"]
