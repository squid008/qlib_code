# -*- coding: utf-8 -*-
"""线程上下文工具（contextvars）的隔离性测试。

阶段一修复的核心：_artifact_dir/_progress_cb/_cancel_check 必须用 contextvars
按线程隔离，否则并行回测会互相污染（A 任务覆盖 B 任务的产物目录/取消标志）。
"""
import threading

from app.engine.context import (
    set_artifact_dir,
    set_progress_callback,
    set_cancel_check,
    get_artifact_dir,
    report,
    check_cancel,
)


class TestContextIsolation:
    def test_artifact_dir_thread_isolation(self):
        """主线程设置目录 A，子线程设置目录 B，互不干扰。"""
        set_artifact_dir("main_dir")
        captured = {}

        def worker():
            set_artifact_dir("worker_dir")
            captured["worker"] = get_artifact_dir()
            # 子线程内读到的是自己的
            captured["worker_inner"] = get_artifact_dir()

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert captured["worker"] == "worker_dir"
        # 主线程不受子线程影响
        assert get_artifact_dir() == "main_dir"

    def test_artifact_dir_reset(self):
        set_artifact_dir("x")
        set_artifact_dir(None)
        assert get_artifact_dir() is None
        set_artifact_dir("y")  # 复原，避免影响其他用例

    def test_progress_callback_isolation(self):
        calls = []
        set_progress_callback(lambda p, m: calls.append((p, m)))
        report(50, "half")
        report(100, "done")
        assert calls == [(50, "half"), (100, "done")]
        set_progress_callback(None)
        # 无回调时 report 不抛错
        report(1, "noop")
        assert len(calls) == 2

    def test_cancel_check(self):
        """check_cancel 在设置取消检查且返回 True 时抛出 TaskCancelledError。"""
        from app.engine.task_manager import TaskCancelledError
        set_cancel_check(lambda: True)
        try:
            check_cancel()
            raised = False
        except TaskCancelledError:
            raised = True
        finally:
            set_cancel_check(None)
        assert raised

    def test_cancel_check_false(self):
        """取消检查返回 False 时，check_cancel 不抛错。"""
        set_cancel_check(lambda: False)
        try:
            check_cancel()
            raised = False
        except Exception:
            raised = True
        finally:
            set_cancel_check(None)
        assert not raised
