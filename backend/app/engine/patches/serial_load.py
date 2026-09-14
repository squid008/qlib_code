# -*- coding: utf-8 -*-
"""串行化 joblib/loky 取数（进程级锁）+ 池的「杀死 / 重置」—— 根治「多任务共享同一 loky 池」的死锁。

【为什么要这个】（2026-09-14 实测定位）
  qlib 的 `D.features` / `Dataset` 走 `joblib.Parallel` + **loky 复用池**（`resource._joblib_backend()`
  故意选 loky：复用进程池，省掉每批 ~16s 的 spawn 固定开销）。而 loky 的 reusable executor 是
  **进程级单例**（`joblib.externals.loky.reusable_executor._executor`），`ExecutorManagerThread` /
  `QueueFeederThread` 的调度状态也是**每进程一份** ⇒ 两个回测**同时取数**时各自 `n_jobs=12`，
  却共用同一个池、同时往同一结果队列投递/取结果 ⇒ 协调状态打架 ⇒ **死锁**：
    调用线程停在 `Parallel._retrieve`、`ExecutorManagerThread` 停在 `wait_result_broken_or_wakeup`、
    worker 全空闲、**CPU 与磁盘双 0**（py-spy 实证；用户当场看到"取消的回测卡住了"）。
  ⚠ 更糟：**杀掉 worker 解不开** —— `_retrieve` 会永远等一个已死 worker 的管道 ⇒ 任务永远停在
    `cancelling`（2026-09-14 用户报「取消不了，强制停止也不行」）。故本模块同时提供
    `kill_and_reset_loky_pool()`：杀 worker **并丢掉缓存的池**，让下一次取数 spawn 全新池
    （否则下一个任务会复用已损坏的池、继续挂）。

【方案】
  `install_serial_load()`：在 `joblib.parallel.Parallel.__call__` 外面套一把**进程级锁** ⇒
  同一时刻只允许一个 Parallel 调用进入 loky 池。代价只是"取数排队"（多任务下各自取数串行），
  其它阶段（训练/回测/分析/画图）照旧并行 —— 相比"限制用户提交任务"（🚫 用户 2026-09-14 否决：
  将来要上服务器、多人会同时请求任务）这是**并发安全**的做法。
  · 同线程嵌套（Parallel 内部再调 Parallel）直接放行（线程本地 depth 计数），避免自锁；
  · **等锁期间仍可被取消**：每 1 秒醒来查一次 `context.check_cancel()`（否则排队中的任务点取消无反应）；
  · 开关：`QLIB_SERIAL_LOAD=0` 关闭（默认开启）；幂等，可重复调用。
  ⚠ 与单因子/事件研究的 `panel_features` 无关（它走 `multiprocessing`，不经过 joblib）。
"""
from __future__ import annotations

import os
import threading
from typing import List

from ...logger import get_logger

logger = get_logger(__name__)

# 进程级"取数闸门"：同一时刻只允许一个 joblib Parallel 调用进入 loky 池。
_LOAD_LOCK = threading.Lock()
# 线程本地嵌套深度：同一线程内层 Parallel（如 load → features → load）直接放行，避免自锁。
_depth = threading.local()
_install_lock = threading.Lock()
_installed = False
# 等锁的轮询间隔（秒）：每秒醒来查一次取消标志 ⇒ 排队中的任务也能被取消。
_POLL_SEC = 1.0


def serial_enabled() -> bool:
    """是否启用取数串行化（`QLIB_SERIAL_LOAD=0` 可关）。"""
    return os.environ.get("QLIB_SERIAL_LOAD", "1").strip().lower() not in ("0", "false", "no", "off")


def is_installed() -> bool:
    return _installed


def install_serial_load() -> bool:
    """把 `joblib.parallel.Parallel.__call__` 包成"进程级串行"。幂等，返回是否已生效。"""
    global _installed
    with _install_lock:
        if _installed:
            return True
        if not serial_enabled():
            logger.info("取数串行化已按 QLIB_SERIAL_LOAD 关闭（多任务同时取数有死锁风险）")
            return False
        try:
            import joblib.parallel as _jp
        except Exception as e:                                  # joblib 缺失：不装，退回原行为
            logger.warning("取数串行化未安装（joblib 不可用）：%r", e)
            return False
        _orig_call = _jp.Parallel.__call__

        def _serial_call(self, *args, **kwargs):
            # 同线程嵌套（已持有闸门）⇒ 直接执行，避免自锁
            if getattr(_depth, "n", 0) > 0:
                return _orig_call(self, *args, **kwargs)
            from ..context import check_cancel                     # 延后 import：避免循环依赖
            while not _LOAD_LOCK.acquire(timeout=_POLL_SEC):
                # 排队等闸门期间检查取消：被取消就抛 TaskCancelledError（不干等）
                check_cancel()
            _depth.n = 1
            try:
                return _orig_call(self, *args, **kwargs)
            finally:
                _depth.n = 0
                _LOAD_LOCK.release()

        _serial_call._serial_load_patched = True                  # type: ignore[attr-defined]
        _jp.Parallel.__call__ = _serial_call
        _installed = True
        logger.info("取数串行化已安装：同一时刻只允许一个 joblib/loky 取数（其它阶段不受影响）")
        return True


def _kill_worker_pids() -> List[int]:
    """杀掉**本进程**的 loky worker 子进程（按命令行特征识别），返回被杀的 PID。"""
    killed: List[int] = []
    try:
        import psutil
    except Exception:
        logger.warning("强制停止：psutil 不可用，无法杀取数 worker（已置协作式取消标志）")
        return killed
    try:
        for ch in psutil.Process(os.getpid()).children(recursive=True):
            try:
                cl = " ".join(ch.cmdline() or [])
            except Exception:
                continue
            if "popen_loky" in cl or "reusable_executor" in cl:   # loky worker 的稳定特征
                try:
                    ch.kill()
                    killed.append(ch.pid)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("强制停止：枚举/终止取数 worker 失败：%r", e)
    return killed


def kill_and_reset_loky_pool() -> List[int]:
    """杀本进程的 loky worker **并重置 joblib 缓存的池**，返回被杀的 worker PID 列表。

    为什么还要"重置"：只杀 worker 的话，`_retrieve` 仍会**永远等一个死 worker 的管道**
    （2026-09-14 实测：0 个 worker 存活、后端 CPU 为 0，但任务永远停在 `cancelling`）。
    丢掉 `reusable_executor._executor` 缓存 ⇒ 下一次 `D.features` 会 spawn 全新池，
    避免下一个任务复用一个已被打断的池继续挂。

    ⚠ 池是**进程内共享**的 ⇒ 会一并中断同进程内其它正在取数的回测（这正是死锁的另一半）。
    ⚠ 单因子/事件研究的 `panel_features` 是 `multiprocessing` 池（命令行 `spawn_main`）⇒ 不受影响。
    """
    # 1) 先让 joblib 自己回收（kill_workers=True 会终止 worker 并让管理线程退出）
    try:
        from joblib.externals.loky import reusable_executor as _re
        ex = getattr(_re, "_executor", None)
        if ex is not None:
            try:
                ex.shutdown(wait=False, kill_workers=True)
            except Exception as e:
                logger.warning("强制停止：shutdown loky 池失败（继续按 PID 杀）：%r", e)
        with getattr(_re, "_executor_lock", threading.Lock()):
            _re._executor = None            # 丢缓存 ⇒ 下次取数 spawn 全新池
    except Exception as e:
        logger.warning("强制停止：重置 joblib 池缓存失败：%r", e)
    # 2) 兜底：把残留的 worker 进程按 PID 杀掉（shutdown 有时来不及/夹在死锁里）
    killed = _kill_worker_pids()
    logger.warning("强制停止：joblib/loky 池已重置，杀掉 %d 个取数 worker（PID %s）", len(killed), killed)
    return killed
