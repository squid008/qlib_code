# -*- coding: utf-8 -*-
"""训练口径戳（v1.20.48）单测。

背景（2026-09-22 实测事故）：`_verify_reuse_feature_order` 只比对**特征顺序** ✗ ⇒
源任务（旧口径：`valid == train`，early_stopping 在训练集上做 ✓）的模型被新回测**静默复用** ✗
⇒ 同一张净值图混两套训练口径，而净值是**累乘**的 ⇒ 整条曲线被抬高 ✗。

本测试锁住两条底线：
  1. 复用模型时**必须**比对 `train_semantics`（不一致 / 没戳 ⇒ 报错 ✗，不许静默复用 ✗）；
  2. **段结果缓存**（断点续跑 / 续测）同样要验口径 ⇒ 旧口径缓存视作无效 ⇒ 调用方重算 ✓。
"""
import json
import os


def _mk_artifacts(tmp_path, task_id, stamp, seg_no=None):
    """造一个 artifacts 目录（`*_<task_id>` ✓）并写 `model_artifacts.json`。"""
    base = tmp_path / "artifacts" / ("20260922-000000_Test_" + task_id)
    sub = (base / ("segment_%s" % seg_no)) if seg_no is not None else base
    sub.mkdir(parents=True, exist_ok=True)
    meta = {"feature_names": ["f1", "f2"]}
    if stamp is not None:
        meta["train_semantics"] = stamp
    (sub / "model_artifacts.json").write_text(json.dumps(meta), encoding="utf-8")
    return base


def test_reuse_ok_when_stamp_matches(tmp_path, monkeypatch):
    from app.engine import artifacts as art

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    _mk_artifacts(tmp_path, "taskok", art._current_train_semantics())
    art._verify_reuse_train_semantics("taskok")          # 不应抛
    _mk_artifacts(tmp_path, "taskok2", art._current_train_semantics(), seg_no=1)
    art._verify_reuse_train_semantics("taskok2", seg_no=1)


def test_reuse_raises_when_stamp_stale(tmp_path, monkeypatch):
    from app.engine import artifacts as art

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    _mk_artifacts(tmp_path, "taskstale", "1.20.45")
    try:
        art._verify_reuse_train_semantics("taskstale")
    except ValueError as e:
        assert "训练口径不一致" in str(e)
    else:
        raise AssertionError("口径不一致时必须报错（不能静默复用）")


def test_reuse_raises_when_stamp_missing(tmp_path, monkeypatch):
    """v1.20.48 之前的产物没有戳 ⇒ 无法证明一致 ⇒ 也必须报错（宁可重新训练）。"""
    from app.engine import artifacts as art

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    _mk_artifacts(tmp_path, "tasknostamp", None)
    try:
        art._verify_reuse_train_semantics("tasknostamp")
    except ValueError as e:
        assert "没有训练口径戳" in str(e)
    else:
        raise AssertionError("没有戳时必须报错")


def test_reuse_skips_check_when_task_dir_missing(tmp_path, monkeypatch):
    """任务目录都没有 ⇒ 不在这里报错（交给 `_load_model_object` 报"未找到可复用权重"）。"""
    from app.engine import artifacts as art

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    art._verify_reuse_train_semantics("nosuchtask")      # 不应抛


def test_segment_cache_stale_is_invalid(tmp_path):
    """段结果缓存：口径不符 / 无戳 ⇒ 视为无效（返回 None）⇒ 调用方重算。"""
    from app.engine import qlib_engine as qe

    art_dir = str(tmp_path / "segcache")
    os.makedirs(art_dir, exist_ok=True)
    qe._save_segment_result(art_dir, 1, [{"date": "2021-01-04", "value": 1.0}], [], 0.0,
                            0.0, 0.0, None, "2021-01-01", "2021-01-31")

    got = qe._load_segment_result(art_dir, 1)
    assert got is not None, "刚写入的缓存必须可读"
    assert got[0]["train_semantics"] == qe.TRAIN_SEMANTICS

    path = os.path.join(art_dir, "segment_1", "seg_result.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["train_semantics"] = "1.20.45"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    assert qe._load_segment_result(art_dir, 1) is None, "旧口径缓存必须视为无效"

    data.pop("train_semantics")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    assert qe._load_segment_result(art_dir, 1) is None, "无戳（v1.20.48 之前）缓存必须视为无效"
