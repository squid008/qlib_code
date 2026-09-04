# -*- coding: utf-8 -*-
"""硬件资源检测与并发能力估算（阶段一：多任务并行）。

自动检测本机 CPU / 内存，结合"单回测任务的内存占用估算"，计算本机能安全
并发运行多少个回测任务，避免：
  1. 并发过多 → CPU 满载互相拖慢、内存被吃爆 OOM
  2. 硬编码上限 → 家里小机 / 公司大机 / 服务器 无法自适应

核心能力：
  - cpu_logical(): 逻辑核数
  - memory_total_gb() / memory_available_gb(): 总内存 / 可用内存
  - estimated_task_memory_gb(): 单个回测任务的估算内存（可配置，按需细化）
  - max_concurrent(): 建议并发上限 = min(CPU 核数, 内存可容纳数)，并留系统余量
  - estimate_memory_for(n): 并发 n 个任务需要的内存

估算依据（经验值，可调）：
  - 单个回测任务 = qlib 数据 + 特征 + 模型训练/预测 的峰值内存。
  - A 股全市场日线数据约 1~2 GB 底层 + Alpha158 特征展开后约 2~4 GB，
    模型训练（LightGBM/XGBoost）额外 1~2 GB。
  - 默认按 3.0 GB / 任务估算，可按环境变量 QLIB_TASK_MEM_GB 覆盖。
"""
from __future__ import annotations

import os
import threading
from typing import Optional

logger = None


def _log():
    global logger
    if logger is None:
        from ..logger import get_logger
        logger = get_logger(__name__)
    return logger


# ----------------------------------------------------------------------
# CPU
# ----------------------------------------------------------------------
def cpu_logical() -> int:
    """逻辑 CPU 核数（线程数）。"""
    return os.cpu_count() or 1


# ----------------------------------------------------------------------
# 内存
# ----------------------------------------------------------------------
def _sys_mem_info():
    """跨平台获取内存信息。返回 (total_bytes, available_bytes)。"""
    try:
        # Windows
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys), int(stat.ullAvailPhys)
    except Exception:
        pass

    try:
        # Linux / macOS
        with open("/proc/meminfo", "r") as f:
            total = avail = None
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
            if total:
                return total, (avail or total)
    except Exception:
        pass

    # 兜底：给一个保守默认（8GB 总量 / 6GB 可用）
    return 8 * 1024 ** 3, 6 * 1024 ** 3


def memory_total_gb() -> float:
    total, _ = _sys_mem_info()
    return round(total / 1024 ** 3, 1)


def memory_available_gb() -> float:
    _, avail = _sys_mem_info()
    return round(avail / 1024 ** 3, 1)


# ----------------------------------------------------------------------
# 单任务内存估算
# ----------------------------------------------------------------------
# 单回测任务峰值内存估算（GB）。可通过环境变量 QLIB_TASK_MEM_GB 覆盖。
# 经验值：全 A 日线数据 + Alpha158 特征 + 模型训练 ≈ 3 GB。
DEFAULT_TASK_MEM_GB = 3.0
_TASK_MEM_GB = float(os.environ.get("QLIB_TASK_MEM_GB", str(DEFAULT_TASK_MEM_GB)))


def estimated_task_memory_gb() -> float:
    """单个回测任务的峰值内存估算（GB）。"""
    return _TASK_MEM_GB


def set_task_memory_gb(gb: float):
    """运行时覆盖单任务内存估算（测试/调优用）。"""
    global _TASK_MEM_GB
    _TASK_MEM_GB = max(0.1, float(gb))


# ----------------------------------------------------------------------
# 并发能力估算
# ----------------------------------------------------------------------
# 系统保留内存比例（不用于回测，避免 OOM）：默认 30%
SYSTEM_HEADROOM_RATIO = float(os.environ.get("QLIB_MEM_HEADROOM", "0.3"))


def _max_by_memory() -> int:
    """按可用内存能容纳的并发任务数（留出系统余量后）。"""
    avail = memory_available_gb()
    usable = avail * (1.0 - SYSTEM_HEADROOM_RATIO)
    mem_per_task = estimated_task_memory_gb()
    if mem_per_task <= 0:
        return 1
    n = int(usable // mem_per_task)
    return max(1, n)


def max_concurrent() -> int:
    """建议的并发回测上限。

    取「内存可容纳数」和「CPU 可承载数」的较小者。
    CPU 侧：LightGBM/XGBoost 训练是 CPU 密集，通常每任务吃 1~N 核；
    为避免互抢拖慢，默认保守地按「逻辑核数/2」估算可承载任务数，
    但仍以内存为主要瓶颈。
    """
    cpu_based = max(1, cpu_logical() // 2)
    mem_based = _max_by_memory()
    return max(1, min(cpu_based, mem_based))


def estimate_memory_for(n: int) -> float:
    """并发 n 个回测任务需要的总内存（含系统余量）。"""
    return round(n * estimated_task_memory_gb() / (1.0 - SYSTEM_HEADROOM_RATIO), 1)


def resource_summary() -> dict:
    """返回资源摘要（供 API / 前端展示）。"""
    return {
        "cpu_logical": cpu_logical(),
        "memory_total_gb": memory_total_gb(),
        "memory_available_gb": memory_available_gb(),
        "task_mem_gb": estimated_task_memory_gb(),
        "max_concurrent": max_concurrent(),
        "estimated_total_mem_for_max_gb": estimate_memory_for(max_concurrent()),
        "memory_headroom_ratio": SYSTEM_HEADROOM_RATIO,
        "task_jobs": task_jobs_for_active(1),
    }


# ----------------------------------------------------------------------
# 任务级并行核数协调
#
# 后端多任务在同一进程共享 qlib 全局配置 C["kernels"]（决定 D.features 的
# dataset_processor 用多少个 worker 并行取数）。若每个任务都用全核，多任务
# 并发会互相抢占 CPU。方案：按"当前同时运行任务数"动态分配每任务核数
#   per-task jobs = max(1, 逻辑核数 // 运行任务数)
# 由 task_manager 在拿到并发许可时 acquire、结束时 release；qlib.init 之后
# （init 会 reset 配置）再 apply 一次，避免被 init 覆盖。
# 环境变量 QLIB_TASK_JOBS 可显式指定单任务核数（如服务器想限制为 4）。
# ----------------------------------------------------------------------
_jobs_lock = threading.Lock()
_active_jobs = 0


def task_jobs_for_active(active: int) -> int:
    """给定当前运行任务数，返回每任务允许的并行核数。"""
    env = os.environ.get("QLIB_TASK_JOBS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, cpu_logical() // max(1, active))


def _apply_kernels(jobs: int) -> None:
    """把 qlib 的并行 worker 数设置为 jobs（失败静默：未 init/低版本均容忍）。"""
    try:
        from qlib.config import C

        C["kernels"] = jobs
    except Exception:
        pass


def acquire_task_jobs() -> int:
    """任务开始：登记一个运行任务，并按当前并发数分配 qlib 并行核数。返回核数。"""
    global _active_jobs
    with _jobs_lock:
        _active_jobs += 1
        jobs = task_jobs_for_active(_active_jobs)
        _apply_kernels(jobs)
        return jobs


def release_task_jobs() -> None:
    """任务结束：撤销登记并重算 qlib 并行核数（回到剩余任务可用的核数）。"""
    global _active_jobs
    with _jobs_lock:
        _active_jobs = max(0, _active_jobs - 1)
        _apply_kernels(task_jobs_for_active(max(1, _active_jobs)))


def apply_active_jobs() -> None:
    """在 qlib.init() 之后调用：init 会 reset 全局配置，需按当前并发重设 kernels。"""
    with _jobs_lock:
        jobs = task_jobs_for_active(max(1, _active_jobs))
        _apply_kernels(jobs)
