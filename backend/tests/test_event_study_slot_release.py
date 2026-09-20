# -*- coding: utf-8 -*-
"""事件研究任务**必须归还并发配额**（回归测试，纯逻辑、不碰 qlib 数据）。

对应 2026-09-20 用户报「事件研究卡死」：
  事件研究端点（`routers/factors.py::event_study`）用 `external_wait_slot()` 抢占一个并发配额，
  但 `finally` 里**只调了 `_est_trim()`、漏了 `manager.release_slot(task_id)`**
  ⇒ 每做一次事件研究就**永久泄漏 1 个槽**；泄漏数攒到并发上限（`max_concurrent`，本机 3）之后，
  **后续所有事件研究 / 单因子测试永远排队**（message 停在「已提交」、CPU 0）——
  表现完全像"卡死"，且点取消也未必能唤醒。

诊断特征（记下来，下次一眼可认）：`GET /api/backtest/capacity` 的 `running`
**大于实际在跑的任务数**（例如 `running=3` 但没有任何任务在跑）⇒ 配额泄漏。

本测试把 `run_event_study` 打桩成秒回，验证：
  ① 任务能正常跑完（success）；
  ② 结束后 `_hold_slots` 回到 0（配额已归还）。
"""
import time

import pytest

import app.routers.factors as F
from app.engine.task_manager import TaskManager


@pytest.fixture()
def patched(monkeypatch):
    tm = TaskManager(work_dir=None)
    monkeypatch.setattr(F, "get_task_manager", lambda *a, **k: tm)
    monkeypatch.setattr("app.factors.single_test._ensure_qlib_init", lambda: None)
    monkeypatch.setattr(F, "run_event_study",
                        lambda **kw: {"error": None, "n_events": 3, "n_short": 0, "_ev": None,
                                      "cached": True,
                                      "timings": {"total_s": 1.5, "cached": True, "stages": []}})
    return tm


def _req():
    return F.EventStudyRequest(
        universe="csi300", start_date="2021-01-01", end_date="2021-12-31",
        factor=F.SingleFactorTestFactor(id="t", name="t", expression="Gt($close,0)",
                                        source="custom", source_formula=""),
        max_k=20,
    )


def _wait_done(tid, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = F._est_get(tid) or {}
        if st.get("status") in ("success", "failed", "cancelled"):
            return st
        time.sleep(0.02)
    return F._est_get(tid) or {}


def test_event_study_releases_its_concurrency_slot(patched):
    """跑完一次事件研究后，`_hold_slots` 必须归零（否则会累积泄漏、最终全部任务饿死）。"""
    tm = patched
    tid = F.event_study(_req())["task_id"]
    st = _wait_done(tid)
    assert st.get("status") == "success", st
    assert tm._hold_slots == 0, "事件研究必须归还并发配额（漏了 release_slot 会攒满上限饿死所有任务）"


def test_repeated_event_studies_do_not_leak(patched):
    """连做 3 次仍不得残留占用（修复前第 4 次起会永久排队）。"""
    tm = patched
    for _ in range(3):
        tid = F.event_study(_req())["task_id"]
        assert _wait_done(tid).get("status") == "success"
    assert tm._hold_slots == 0, "多次事件研究后配额仍应归零"
    assert tm.external_queued() == 0


def test_progress_exposes_timings_and_cached(patched):
    """v1.20.36：进度接口必须透出 `timings` / `cached`（前端据此自适应自动/手工）。"""
    tid = F.event_study(_req())["task_id"]
    assert _wait_done(tid).get("status") == "success"
    p = F.event_study_progress(tid)
    assert p["cached"] is True
    assert p["timings"]["total_s"] == 1.5
    assert isinstance(p["timings"]["stages"], list)
