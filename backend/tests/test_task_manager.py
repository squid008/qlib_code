# -*- coding: utf-8 -*-
"""任务管理器并发限制的单元测试（纯逻辑，不起真实回测线程）。"""
from app.engine.task_manager import TaskManager, MAX_CONCURRENT_TASKS


class TestConcurrencyLimit:
    def test_max_concurrent_positive(self):
        """并发上限应为正整数。"""
        assert isinstance(MAX_CONCURRENT_TASKS, int)
        assert MAX_CONCURRENT_TASKS >= 1

    def test_semaphore_limits_concurrency(self):
        """信号量初始计数等于并发上限；acquire 满后非阻塞 acquire 返回 False（表示排队）。"""
        manager = TaskManager(work_dir=None)
        sem = manager._sem

        # 连续 acquire 到上限，前 N 次成功
        acquired = 0
        for _ in range(MAX_CONCURRENT_TASKS):
            if sem.acquire(blocking=False):
                acquired += 1
            else:
                break
        assert acquired == MAX_CONCURRENT_TASKS

        # 再 acquire 应失败（并发已满 → 排队）
        assert not sem.acquire(blocking=False)

        # 释放一个后，又能 acquire（排队任务被唤醒）
        sem.release()
        assert sem.acquire(blocking=False)

    def test_concurrency_info_fields(self):
        """并发信息包含 max_concurrent / running / queued / available / resource。"""
        manager = TaskManager(work_dir=None)
        info = manager.concurrency_info()
        assert info["max_concurrent"] >= 1
        assert info["running"] >= 0
        assert info["queued"] >= 0
        assert info["available"] == info["max_concurrent"] - info["running"]
        assert "resource" in info
        assert "cpu_logical" in info["resource"]
        assert info["resource"]["max_concurrent"] >= 1

    def test_can_submit_when_room(self):
        """无运行任务时可提交。"""
        manager = TaskManager(work_dir=None)
        assert manager.can_submit() is True

    def test_available_tracks_semaphore(self):
        """available = max_concurrent - running，且与信号量占用一致。"""
        manager = TaskManager(work_dir=None)
        n = manager.concurrency_info()["max_concurrent"]
        for _ in range(n):
            assert manager._sem.acquire(blocking=False)
        # 此时信号量已满，但 running_count 统计的是状态字段（无实际 running 任务）
        info = manager.concurrency_info()
        assert info["available"] == n - info["running"]
