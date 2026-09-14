# -*- coding: utf-8 -*-
"""「强制停止」语义的单元测试（纯逻辑，不杀真实进程、不起真实回测）。

对应 2026-09-14 用户报的两个问题：
  ① 任务卡在 `cancelling`、点「取消」无效 —— 因为取消是**协作式**的；
  ② 点「强制停止」也停不掉 —— 实测**杀 worker 并不总能唤醒卡在 `Parallel._retrieve`
     里的线程**（0 个 worker、CPU 0，状态永远 `cancelling`）。
⇒ 故 `force_cancel` 必须**不依赖线程配合**：立刻落终态 + 代卡死的线程归还并发配额（幂等），
   且线程事后醒过来**不得覆盖**这个终态。
"""
import pytest

from app.engine.task_manager import TaskManager
from app.models.backtest import BacktestTask


def _running_task(manager: TaskManager, task_id: str = "t1", slot: bool = True):
    """造一个"正在跑（且占着并发配额）"的任务。"""
    with manager._lock:
        manager._tasks[task_id] = BacktestTask(
            task_id=task_id, status="running", progress=42.0, message="跑着", created_at="2026-09-14T00:00:00")
        if slot:
            manager._hold_slots += 1
            manager._held_by.add(task_id)
    return manager


class TestForceCancel:
    def test_marks_terminal_immediately_and_releases_slot(self, monkeypatch):
        """强制停止：立刻 `cancelled`（不等线程）+ 代其归还配额。"""
        m = _running_task(TaskManager(work_dir=None))
        monkeypatch.setattr(m, "_kill_loky_pool", lambda: [111, 222])
        info = m.force_cancel("t1")
        assert info["killed_workers"] == [111, 222]
        assert info["slot_released"] is True
        t = m.get("t1")
        assert t.status == "cancelled", "必须立刻落终态（否则界面永远停在 cancelling）"
        assert t.progress == 100.0
        assert "强制停止" in t.message
        assert m._hold_slots == 0, "卡死线程不会走 finally ⇒ 必须代它归还配额"
        assert "t1" in m._forced, "_forced 要留在集合里，才能拦住线程事后覆盖状态"

    def test_slot_release_is_idempotent(self, monkeypatch):
        """线程若事后醒过来又走 finally ⇒ 不能重复归还（否则并发放超过上限）。"""
        m = _running_task(TaskManager(work_dir=None))
        monkeypatch.setattr(m, "_kill_loky_pool", lambda: [])
        assert m.force_cancel("t1")["slot_released"] is True
        assert m._release_hold_if_held("t1") is False        # 第二次不归还
        assert m._hold_slots == 0
        assert m._release_hold_if_held("t1") is False

    def test_second_force_cancel_returns_none(self, monkeypatch):
        """已终态的任务再点强制停止 ⇒ None（路由层据此返回 400）。"""
        m = _running_task(TaskManager(work_dir=None))
        monkeypatch.setattr(m, "_kill_loky_pool", lambda: [])
        assert m.force_cancel("t1") is not None
        assert m.force_cancel("t1") is None
        assert m.force_cancel("不存在") is None

    def test_late_thread_cannot_override_terminal_state(self, monkeypatch):
        """线程卡了很久之后"居然跑完了"/抛异常 ⇒ 不得把 `cancelled` 覆盖成 success/failed。"""
        m = _running_task(TaskManager(work_dir=None))
        monkeypatch.setattr(m, "_kill_loky_pool", lambda: [])
        m.force_cancel("t1")
        assert m._finish("t1", "success", "完成", result={}) is False
        assert m._finish("t1", "failed", "失败: boom") is False
        assert m.get("t1").status == "cancelled"

    def test_finish_updates_normally_when_not_forced(self):
        """没有强制停止时，`_finish` 照常写终态（含 result/progress）。"""
        m = _running_task(TaskManager(work_dir=None), slot=False)
        assert m._finish("t1", "success", "完成", result={"ok": 1}) is True
        t = m.get("t1")
        assert (t.status, t.progress, t.message) == ("success", 100.0, "完成")
        assert t.result == {"ok": 1}
        assert m._finish("不存在", "cancelled", "已停止") is False

    def test_force_cancel_wakes_queued_waiter(self):
        """排队（pending）任务被强制停止：wake 掉等待者，且任务落终态。"""
        import threading

        m = TaskManager(work_dir=None)
        with m._lock:
            m._tasks["q1"] = BacktestTask(task_id="q1", status="pending", progress=1.0,
                                          message="排队中", created_at="x")
        woke = []

        def _waiter():
            # 模拟 _reserve_blocking：等条件变量被 notify
            with m._lock:
                m._cond.wait(timeout=5)
                woke.append(True)

        th = threading.Thread(target=_waiter)
        th.start()
        import time

        time.sleep(0.1)
        m.force_cancel("q1")
        th.join(timeout=5)
        assert woke == [True], "force_cancel 必须 notify_all 唤醒排队者"
        assert m.get("q1").status == "cancelled"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
