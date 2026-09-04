# -*- coding: utf-8 -*-
"""资源检测与并发能力估算的单元测试（阶段一）。"""
import pytest

from app.engine import resource


class TestResourceDetection:
    def test_cpu_logical_positive(self):
        assert resource.cpu_logical() >= 1

    def test_memory_total_positive(self):
        assert resource.memory_total_gb() > 0

    def test_memory_available_positive(self):
        assert resource.memory_available_gb() > 0

    def test_task_memory_default(self):
        # 默认单任务内存估算为正
        assert resource.estimated_task_memory_gb() > 0

    def test_set_task_memory_clamps(self):
        resource.set_task_memory_gb(0.01)  # 极小值被 clamp 到 0.1
        assert resource.estimated_task_memory_gb() == 0.1
        resource.set_task_memory_gb(5.0)
        assert resource.estimated_task_memory_gb() == 5.0
        resource.set_task_memory_gb(3.0)  # 复原

    def test_max_concurrent_at_least_one(self):
        assert resource.max_concurrent() >= 1

    def test_max_concurrent_bounded_by_cpu(self):
        # 并发上限不应超过逻辑核数的一半以上（CPU 保守估计）
        assert resource.max_concurrent() <= max(1, resource.cpu_logical())

    def test_estimate_memory_positive(self):
        assert resource.estimate_memory_for(resource.max_concurrent()) > 0

    def test_resource_summary_complete(self):
        s = resource.resource_summary()
        for k in ["cpu_logical", "memory_total_gb", "memory_available_gb",
                  "task_mem_gb", "max_concurrent", "estimated_total_mem_for_max_gb",
                  "memory_headroom_ratio"]:
            assert k in s
        assert s["max_concurrent"] >= 1


class TestTaskJobsCoordination:
    """任务级并行核数协调（⑤：多任务共享 qlib C["kernels"]）。"""

    def test_jobs_share_cpu(self, monkeypatch):
        # 16 核：1 任务用满 16，2 任务各 8，4 任务各 4
        monkeypatch.setenv("QLIB_TASK_JOBS", "")  # 不启用显式覆盖
        monkeypatch.delenv("QLIB_TASK_JOBS", raising=False)
        monkeypatch.setattr(resource, "cpu_logical", lambda: 16)
        assert resource.task_jobs_for_active(1) == 16
        assert resource.task_jobs_for_active(2) == 8
        assert resource.task_jobs_for_active(4) == 4
        assert resource.task_jobs_for_active(20) == 1  # 不出现 0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("QLIB_TASK_JOBS", "4")
        assert resource.task_jobs_for_active(1) == 4
        assert resource.task_jobs_for_active(3) == 4

    def test_acquire_release_counting(self, monkeypatch):
        monkeypatch.setattr(resource, "_apply_kernels", lambda n: None)
        resource._active_jobs = 0
        try:
            n1 = resource.acquire_task_jobs()
            n2 = resource.acquire_task_jobs()
            assert resource._active_jobs == 2
            assert n1 == resource.task_jobs_for_active(1)
            assert n2 == resource.task_jobs_for_active(2)
            resource.release_task_jobs()
            assert resource._active_jobs == 1
            resource.release_task_jobs()
            assert resource._active_jobs == 0
            resource.release_task_jobs()  # 不会降到负数
            assert resource._active_jobs == 0
        finally:
            resource._active_jobs = 0
