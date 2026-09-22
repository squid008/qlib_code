# -*- coding: utf-8 -*-
"""`find_artifact_dir` 的**续测回退**单测（v1.20.49）。

背景（2026-09-22 用户实测）：**断点续跑复用源任务目录**（目录名后缀是**源** task_id）
⇒ 用**续测任务自己的 id** 查目录落空 ⇒ `/artifacts`、`/features`、`/snapshot`、`/result`、
`/image/*` 在**续测运行期间全部 404**（而 `/backtest/{id}` 因为单独做了回退仍 200）。
本测试锁住两条回退：**内存态**（TaskManager 的 `resume_task_id`）+ **磁盘标记**
（`resume_tasks.json`，重启后仍有效）。
"""
import json

import pytest


def _mk(tmp_path, name):
    d = tmp_path / "artifacts" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_direct_and_legacy_dir(tmp_path, monkeypatch):
    from app.services import artifacts_service as svc

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    d1 = _mk(tmp_path, "20260922-000000_Test_AAA")
    d2 = _mk(tmp_path, "BBB")
    assert svc.find_artifact_dir("AAA") == str(d1)
    assert svc.find_artifact_dir("BBB") == str(d2)          # 旧命名（裸 id）✓
    assert svc.find_artifact_dir("NOPE") is None


def test_resume_marker_fallback(tmp_path, monkeypatch):
    """续测任务 NEW 复用源目录 SRC ⇒ 用 NEW 也必须能查到 SRC ✓。"""
    from app.services import artifacts_service as svc

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    src = _mk(tmp_path, "20260922-000000_Test_SRC")
    assert svc.find_artifact_dir("NEW") is None             # 未登记 ⇒ 查不到 ✓
    svc.note_resume_task(str(src), "NEW")
    assert svc.find_artifact_dir("NEW") == str(src)
    svc.note_resume_task(str(src), "NEW")                   # 幂等 ✓
    ids = json.loads((src / "resume_tasks.json").read_text(encoding="utf-8"))["task_ids"]
    assert ids == ["NEW"]


def test_memory_fallback(tmp_path, monkeypatch):
    """内存态回退：TaskManager 里的 `resume_task_id` 能解析到源目录 ✓。"""
    from app.services import artifacts_service as svc

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    src = _mk(tmp_path, "20260922-000000_Test_SRC2")
    monkeypatch.setattr(svc, "_resume_of",
                        lambda tid: "SRC2" if str(tid) == "NEW2" else None)
    assert svc.find_artifact_dir("NEW2") == str(src)
    monkeypatch.setattr(svc, "_resume_of",
                        lambda tid: "NOPE" if str(tid) == "NEW3" else None)
    assert svc.find_artifact_dir("NEW3") is None


def test_resume_chain_and_cycle_guard(tmp_path, monkeypatch):
    """续测可套续测（链 ✓），但不能因环而无限递归 ✓。"""
    from app.services import artifacts_service as svc

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    base = _mk(tmp_path, "20260922-000000_Test_BASE")
    chain = {"C": "B", "B": "A", "A": "BASE", "X": "Y", "Y": "X"}
    monkeypatch.setattr(svc, "_resume_of", lambda tid: chain.get(str(tid)))
    assert svc.find_artifact_dir("C") == str(base)
    # 环（X↔Y）⇒ 不能死循环，返回 None ✓
    assert svc.find_artifact_dir("X") is None


@pytest.mark.parametrize("raw", [["ZZ"], {"task_ids": ["ZZ"]}])
def test_marker_accepts_list_or_dict(tmp_path, monkeypatch, raw):
    from app.services import artifacts_service as svc

    monkeypatch.setattr("app.config.WORK_DIR", str(tmp_path))
    src = _mk(tmp_path, "20260922-000000_Test_SRC3")
    (src / "resume_tasks.json").write_text(json.dumps(raw), encoding="utf-8")
    assert svc.find_artifact_dir("ZZ") == str(src)
