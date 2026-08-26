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
