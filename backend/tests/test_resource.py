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
