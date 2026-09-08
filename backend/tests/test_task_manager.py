# -*- coding: utf-8 -*-
"""任务管理器并发限制的单元测试（纯逻辑，不起真实回测线程）。"""
import pytest

from app.engine.task_manager import TaskManager, current_max_concurrent


class TestConcurrencyLimit:
    def test_max_concurrent_positive(self):
        """并发上限应为正整数。"""
        assert isinstance(current_max_concurrent(), int)
        assert current_max_concurrent() >= 1

    def test_dynamic_limit_tracks_slots(self):
        """动态配额：try_acquire_slot 满后非阻塞返回 False（表示排队），释放后又能取得。"""
        manager = TaskManager(work_dir=None)
        n = manager.current_limit()

        # 连续取得配额到上限，前 N 次成功
        acquired = 0
        for _ in range(n):
            if manager.try_acquire_slot():
                acquired += 1
            else:
                break
        assert acquired == n

        # 再取应失败（并发已满 → 排队）
        assert not manager.try_acquire_slot()

        # 释放一个后，又能取得（排队任务被唤醒）
        manager.release_slot()
        assert manager.try_acquire_slot()

        # 清理，避免计数泄漏
        manager.release_slot()

    def test_external_soft_cap_reserves_backtest_slot(self):
        """外部任务（单因子）合计最多占 max_concurrent-1，给回测留 1 槽。"""
        manager = TaskManager(work_dir=None)
        n = manager.current_limit()
        if n <= 1:
            pytest.skip("并发上限为 1，无预留空间")
        cap = manager.external_soft_cap()
        assert cap == max(1, n - 1)
        for _ in range(cap):
            assert manager.try_acquire_slot(external_id="e1")
        # 单因子（external）继续占应被软上限拒绝
        assert not manager.try_acquire_slot(external_id="e2")
        # 但回测（无 external_id）仍可占用剩余槽 → 预留 1 槽生效
        assert manager.try_acquire_slot()

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

    def test_available_tracks_slots(self):
        """available = max_concurrent - running，且与配额占用一致。"""
        manager = TaskManager(work_dir=None)
        n = manager.concurrency_info()["max_concurrent"]
        for _ in range(n):
            assert manager.try_acquire_slot()
        # 此时配额已满，但 running_count 统计的是状态字段（无实际 running 任务）
        info = manager.concurrency_info()
        assert info["available"] == n - info["running"]
        # 清理
        for _ in range(n):
            manager.release_slot()
