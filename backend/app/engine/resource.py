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


def _joblib_backend() -> str:
    """qlib D.features 的并行后端。

    默认 `loky`：joblib 会【复用进程池】，连续多次 D.features 只有首次付进程池启动
    开销（实测 简单字段：第1次 ~8s、第2次起 ~0.4s），而 qlib 默认 `multiprocessing`
    每次调用都新建进程池（Windows spawn + joblib 轮询 ≈ 每批固定 16s+ 固定开销，
    与数据量无关，是"加载特征慢"的头号元凶）。

    loky 与 multiprocessing 数值一致（实测 CWH 大公式全列 allclose），仅调度实现不同。
    如需回退用环境变量 QLIB_JOBLIB_BACKEND=multiprocessing（如多进程隔离的服务器）。
    """
    env = os.environ.get("QLIB_JOBLIB_BACKEND")
    if env:
        return env
    return "loky"


_loky_patched = False
_CREATE_NO_WINDOW = 0x08000000


def _patch_loky_nowin() -> None:
    """loky 子进程免弹黑色控制台窗口（Windows）。

    后端若以无控制台方式运行（DETACHED_PROCESS），loky spawn 的 python.exe
    （console 子系统）子进程会被 Windows 分配各自新 console → 用户看到一堆黑窗。
    loky 子进程通过匿名管道通信，不能切 pythonw（stdio 句柄无效会崩 worker，已实测）；
    正确解法 = monkey-patch loky 的 Popen.__init__：复制原实现、仅把 CreateProcess
    的 flags 0 → CREATE_NO_WINDOW（0x08000000），worker 正常跑但不弹窗。
    只做一次（幂等）。与 multiprocessing spawn（_nowin_spawn）互不影响。
    """
    global _loky_patched
    if _loky_patched or os.name != "nt":
        return
    try:
        import _winapi

        # 绝对 import loky 内部（避免复制逻辑时相对路径错误）
        from joblib.externals.loky.backend import popen_loky_win32 as _lp
        from joblib.externals.loky.backend import spawn as _lspawn
        from joblib.externals.loky.backend.popen_loky_win32 import (
            _close_handles, _path_eq, get_command_line, WINENV,
        )

        _orig_init = _lp.Popen.__init__

        def _patched_init(self, process_obj):
            # 与 loky 原实现逐行一致，仅 CreateProcess flags=0 → _CREATE_NO_WINDOW
            import msvcrt
            import sys as _sys
            from multiprocessing import util as _mp_util
            from multiprocessing.context import set_spawning_popen
            from joblib.externals.loky.backend import reduction as _lred

            prep_data = _lspawn.get_preparation_data(
                process_obj._name, getattr(process_obj, "init_main_module", True)
            )
            rhandle, whandle = _winapi.CreatePipe(None, 0)
            wfd = msvcrt.open_osfhandle(whandle, 0)
            cmd = get_command_line(parent_pid=os.getpid(), pipe_handle=rhandle)
            child_env = {**os.environ, **process_obj.env}
            python_exe = _lspawn.get_executable()
            if WINENV and _path_eq(python_exe, _sys.executable):
                cmd[0] = python_exe = _sys._base_executable
                child_env["__PYVENV_LAUNCHER__"] = _sys.executable
            cmd = " ".join(f'"{x}"' for x in cmd)
            with open(wfd, "wb") as to_child:
                try:
                    hp, ht, pid, _ = _winapi.CreateProcess(
                        python_exe, cmd, None, None, False,
                        _CREATE_NO_WINDOW, child_env, None, None,
                    )
                    _winapi.CloseHandle(ht)
                except BaseException:
                    _winapi.CloseHandle(rhandle)
                    raise
                self.pid = pid
                self.returncode = None
                self._handle = hp
                self.sentinel = int(hp)
                self.finalizer = _mp_util.Finalize(
                    self, _close_handles, (self.sentinel, int(rhandle))
                )
                set_spawning_popen(self)
                try:
                    _lred.dump(prep_data, to_child)
                    _lred.dump(process_obj, to_child)
                finally:
                    set_spawning_popen(None)

        _lp.Popen.__init__ = _patched_init

        # loky 的 resource_tracker 进程走 resource_tracker.spawnv_passfds（标准库 util
        # 的 win32 分支，flags 硬编码 0）——不 patch 的话它每次弹一个黑窗（实测 7 个
        # worker 中该 1 个有可见窗口）。替换为带 CREATE_NO_WINDOW 的实现。
        from joblib.externals.loky.backend import resource_tracker as _rt

        _orig_spawnv = _rt.spawnv_passfds

        def _patched_spawnv(path, args, passfds):
            passfds = sorted(passfds)
            cmd = " ".join(f'"{x}"' for x in args)
            try:
                _, ht, pid, _ = _winapi.CreateProcess(
                    path, cmd, None, None, True, _CREATE_NO_WINDOW, None, None, None,
                )
                _winapi.CloseHandle(ht)
            except BaseException:
                return 0
            return pid

        _rt.spawnv_passfds = _patched_spawnv
        _loky_patched = True
        _log().info("loky 子进程 CreateProcess 已加 CREATE_NO_WINDOW（免弹黑色命令行窗口）")
    except Exception:
        pass


def _apply_kernels(jobs: int) -> None:
    """把 qlib 的并行 worker 数与并行后端设置为 jobs / loky（失败静默容忍）。"""
    try:
        from qlib.config import C

        C["kernels"] = jobs
        # loky 自动复用进程池，消掉 D.features 每批新建进程池的固定开销
        C["joblib_backend"] = _joblib_backend()
        # 设置 loky 后端后立刻打免窗补丁（在首次 D.features spawn 之前）
        if C["joblib_backend"] == "loky":
            _patch_loky_nowin()
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
