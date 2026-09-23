# -*- coding: utf-8 -*-
"""★ v1.20.63：`engine/resource` 的**实测自校正**单任务内存估算。

背景（2026-09-23 用户报「两个回测卡在段 1 的并行取数、CPU/磁盘双 0」）：
    机器上另有别的项目占着 ~18GB ✗ ⇒ 可用内存只剩 13GB，而估算仍是 **3.0GB/任务** ✗
    ⇒ 仍放行 3 个并发 ⇒ 内存被吃穿 ⇒ 取数阶段卡死 ✓。
⚠⚠ 纪律：那 5~7GB 是**别的项目**的数字 ✗，**不能**拿来当我们任务的估算 ✗（猜 ✗）
    ⇒ 改成**让程序自己测**：回测结束时采样"后端进程树"RSS 峰值并上报 ✓（见 `note_task_peak_memory` ✓）。
"""
from __future__ import annotations

import pytest

from app.engine import resource


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """复位模块级实测状态 + 清掉环境变量干扰 ✓（否则用例间互相污染 ✗）。"""
    monkeypatch.delenv("QLIB_TASK_MEM_GB", raising=False)
    monkeypatch.setattr(resource, "_measured_peak_gb", 0.0)
    monkeypatch.setattr(resource, "_measured_at", None)
    monkeypatch.setattr(resource, "_TASK_MEM_GB", resource.DEFAULT_TASK_MEM_GB)


def test_default_used_when_nothing_measured():
    """没测到过 ⇒ 用默认值 ✓，来源标注 `default` ✓。"""
    assert resource.measured_task_memory_gb() == 0.0
    assert resource.effective_task_memory_gb() == pytest.approx(resource.DEFAULT_TASK_MEM_GB)
    assert resource._task_mem_source() == "default"


def test_measured_raises_estimate():
    """实测峰值 ⇒ 估算上调 ✓，来源变 `measured` ✓。"""
    assert resource.note_task_peak_memory(6.5, "t1") == pytest.approx(6.5)
    assert resource.effective_task_memory_gb() == pytest.approx(6.5)
    assert resource._task_mem_source() == "measured"
    assert resource.measured_task_memory_at()            # 有时间戳 ✓


def test_measured_only_goes_up():
    """★ **只上调不下调** ✓ —— 否则并发上限会虚高、重新踩内存 ✗（要下调用 env 显式指定 ✓）。"""
    resource.note_task_peak_memory(6.5)
    resource.note_task_peak_memory(2.0)                  # 更小的实测值：**不得**把它拉下来 ✗
    assert resource.measured_task_memory_gb() == pytest.approx(6.5)
    assert resource.effective_task_memory_gb() == pytest.approx(6.5)


def test_env_has_highest_priority(monkeypatch):
    """`QLIB_TASK_MEM_GB` **优先级最高** ✓（人工显式指定 > 实测 > 默认 ✓）。"""
    resource.note_task_peak_memory(6.5)
    monkeypatch.setenv("QLIB_TASK_MEM_GB", "1.5")
    assert resource.effective_task_memory_gb() == pytest.approx(1.5)
    assert resource._task_mem_source() == "env"


def test_config_value_untouched_by_measurement():
    """`estimated_task_memory_gb()` 仍是**配置值** ✓（展示/单测语义不变 ✓）—— 只有 `effective_` 会变 ✓。"""
    resource.note_task_peak_memory(6.5)
    assert resource.estimated_task_memory_gb() == pytest.approx(resource.DEFAULT_TASK_MEM_GB)
    assert resource.effective_task_memory_gb() == pytest.approx(6.5)


def test_max_concurrent_follows_measurement(monkeypatch):
    """★★ 决定性用例：同一份"可用内存 32GB"，**实测 10GB/任务** 时只能放 2 个 ✓
    （旧口径按 3GB 会放 7 个 ✗ —— 这正是 2026-09-23 那次把内存吃穿的原因 ✓）。"""
    monkeypatch.setattr(resource, "memory_available_gb", lambda: 32.0)
    monkeypatch.setattr(resource, "cpu_logical", lambda: 64)      # 别让 CPU 侧成为瓶颈 ✓
    assert resource.max_concurrent() == 7                          # 3GB 口径：32*0.7/3 = 7 ✓
    resource.note_task_peak_memory(10.0)
    assert resource.max_concurrent() == 2                          # ★ 实测 10GB ⇒ 22.4/10 = 2 ✓✓


def test_summary_exposes_new_fields():
    """摘要要能一眼看出"估算从哪来" ✓（排查时很关键 ✓）。"""
    resource.note_task_peak_memory(4.25)
    s = resource.resource_summary()
    assert s["task_mem_gb"] == pytest.approx(4.25)
    assert s["task_mem_source"] == "measured"
    assert s["task_mem_measured_gb"] == pytest.approx(4.25)


def test_sampler_starts_and_stops():
    """采样器：有 psutil ⇒ 返回可调用 `stop()`，`stop()` ⇒ `(峰值GB, 采样次数)` ✓；没有 ⇒ None ✓。"""
    stop = resource.start_peak_sampler(interval=0.01)
    if stop is None:
        pytest.skip("本机无 psutil（回测里会静默不采样 ✓）")
    peak, samples = stop()
    assert peak >= 0.0 and samples >= 0


def test_note_ignores_bad_input():
    """脏输入（非数字/负数）⇒ 不抛、不动估算 ✓（这是回测收尾路径，绝不许炸 ✗）。"""
    resource.note_task_peak_memory("not-a-number")       # type: ignore[arg-type]
    resource.note_task_peak_memory(-5.0)
    assert resource.measured_task_memory_gb() == 0.0
    assert resource.effective_task_memory_gb() == pytest.approx(resource.DEFAULT_TASK_MEM_GB)
