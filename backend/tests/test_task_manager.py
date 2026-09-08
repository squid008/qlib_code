# -*- coding: utf-8 -*-
"""任务管理器并发限制的单元测试（纯逻辑，不起真实回测线程）。"""
import threading
import time

import pytest

from app.engine.task_manager import TaskManager, current_max_concurrent


def _hold_all(manager, external_id=None):
    """占满当前全部配额；返回占到的槽数。"""
    n = manager.current_limit()
    got = 0
    for _ in range(n + 1):
        if manager.try_acquire_slot(external_id=external_id):
            got += 1
        else:
            break
    return got


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

    def test_external_yields_when_backtest_queued(self):
        """回测优先契约：有排队回测（pending）时，外部任务不再抢占新配额。"""
        manager = TaskManager(work_dir=None)
        n = manager.current_limit()
        if n <= 1:
            pytest.skip("并发上限为 1，无预留空间")

        # 注入一个 pending 回测任务（模拟排队等待配额）
        task = type(
            "FakeTask",
            (),
            {"task_id": "bt1", "status": "pending"},
        )()
        manager._tasks["bt1"] = task  # 白盒注入，仅测试协调语义
        try:
            # 回测在排队 → 外部新 acquire 一律让位（即便还有空槽）
            for _ in range(n):
                assert not manager.try_acquire_slot(external_id="sf1")
            # 但回测自身仍可拿槽（无 external_id 不受让位约束）
            assert manager.try_acquire_slot()
        finally:
            manager.release_slot()
            manager._tasks.pop("bt1", None)

    def test_cancel_wakes_queued_backtest(self):
        """排队中的回测被 cancel 后应立刻退出排队（_reserve_blocking 返回 False）。"""
        manager = TaskManager(work_dir=None)
        n = manager.current_limit()
        # 占满配额，让新任务必须排队
        got = _hold_all(manager)
        assert got == n

        task = type(
            "FakeTask",
            (),
            {"task_id": "bt2", "status": "pending"},
        )()
        manager._tasks["bt2"] = task
        result: dict = {}

        def waiter():
            # 模拟任务线程：先登记再排队等待
            manager._update("bt2", status="pending", message="排队中", progress=1.0)
            result["ok"] = manager._reserve_blocking("bt2")

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.2)  # 确保已进入排队
        assert manager.cancel("bt2") is True
        t.join(timeout=2)
        assert not t.is_alive(), "排队中的回测 cancel 后应被唤醒退出，而非死等"
        assert result.get("ok") is False
        # 清理占用的槽
        for _ in range(got):
            manager.release_slot()

    def test_external_wait_slot_blocking_and_cancel(self):
        """external_wait_slot：无槽时阻塞；有回测排队时让位；取消回调为真时立即返回 False。"""
        manager = TaskManager(work_dir=None)
        n = manager.current_limit()
        got = _hold_all(manager)  # 占满

        results: dict = {}

        def worker():
            results["acquired"] = manager.external_wait_slot(
                "sfX", cancel_check=lambda: results.get("want_cancel", False)
            )

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        time.sleep(0.3)
        # 尚未拿到槽：释放一个 → 拿到
        manager.release_slot()
        t.join(timeout=2)
        assert not t.is_alive()
        assert results.get("acquired") is True

        # 再测取消：先占满，让新 worker 阻塞，随后置取消回调
        _hold_all(manager)
        results2: dict = {}

        def worker2():
            results2["acquired"] = manager.external_wait_slot(
                "sfY", cancel_check=lambda: results2.get("want_cancel", False)
            )

        t2 = threading.Thread(target=worker2, daemon=True)
        t2.start()
        time.sleep(0.3)
        results2["want_cancel"] = True
        manager.wake_external_waiters()
        t2.join(timeout=2)
        assert not t2.is_alive()
        assert results2.get("acquired") is False
        # 清理：此刻配额仍被第一个测试占满（n 个），精确释放 n 次
        for _ in range(n):
            manager.release_slot()
