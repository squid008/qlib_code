# -*- coding: utf-8 -*-
"""
异步任务管理器。

职责：
- 提交回测任务到后台线程池执行，不阻塞 FastAPI 主线程
- 维护每个任务的状态（pending/running/success/failed）和进度
- 提供查询接口给 API 层

说明：Qlib 的回测较重且 qlib.init() 与全局状态相关，因此在独立线程中执行。
更稳妥的做法是子进程隔离，这里先用线程 + 全局进度字典，简单可靠。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

from ..logger import get_logger
from ..models.backtest import BacktestRequest, BacktestTask, BacktestResult
from . import resource


# 最大并发回测任务数：
#   - 优先读环境变量 QLIB_MAX_CONCURRENT（人工显式指定，如服务器上固定 4）
#   - 未指定则按硬件自动检测（CPU 核数 + 可用内存）计算，家里小机 / 公司大机 / 服务器自适应
#   - 避免多个 LightGBM/XGBoost 任务同时占满 CPU 互抢，或把内存吃爆 OOM
def _default_concurrent() -> int:
    env = os.environ.get("QLIB_MAX_CONCURRENT")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return resource.max_concurrent()


MAX_CONCURRENT_TASKS = _default_concurrent()

logger = get_logger(__name__)


class TaskCancelledError(Exception):
    """任务被用户取消。"""

    def __init__(self, task_id: str):
        super().__init__(f"任务 {task_id} 已被用户停止")
        self.task_id = task_id


class TaskManager:
    def __init__(self, work_dir: Optional[str] = None):
        self._tasks: Dict[str, BacktestTask] = {}
        self._cancel_flags: set = set()
        self._lock = threading.Lock()
        self._work_dir = work_dir
        # 任务请求快照：task_id -> req（用于判断续测占用源目录等场景；任务结束即清理）
        self._reqs: Dict[str, BacktestRequest] = {}
        # 限制并发回测数量，避免多任务同时训练占满 CPU
        self._sem = threading.BoundedSemaphore(MAX_CONCURRENT_TASKS)

    def cancel(self, task_id: str) -> bool:
        """请求取消任务。返回是否成功标记（任务存在且未结束）。"""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.status in ("success", "failed", "cancelled"):
                return False
            self._cancel_flags.add(task_id)
            t.status = "cancelling"  # 正在取消中
            return True

    def is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cancel_flags

    def submit(self, req: BacktestRequest) -> str:
        """提交回测任务，返回 task_id"""
        task_id = uuid.uuid4().hex[:12]
        task = BacktestTask(
            task_id=task_id,
            status="pending",
            progress=0.0,
            message="已提交",
            created_at=datetime.now().isoformat(),
        )
        with self._lock:
            self._tasks[task_id] = task
            self._reqs[task_id] = req

        # 后台线程执行
        t = threading.Thread(target=self._run, args=(task_id, req), daemon=True)
        t.start()
        return task_id

    def _run(self, task_id: str, req: BacktestRequest):
        task = self._get(task_id)
        if task is None:
            return
        # 限制并发：超过 MAX_CONCURRENT_TASKS 的任务在此等待（保持 pending 排队状态）
        full = self.running_count() >= MAX_CONCURRENT_TASKS
        hint = "（已达并发上限，排队等待）" if full else f"（并发上限 {MAX_CONCURRENT_TASKS}）"
        self._update(task_id, status="pending", message=f"排队中{hint}", progress=1.0)
        self._sem.acquire()
        try:
            task = self._get(task_id)
            if task is None:
                return
            if self.is_cancelled(task_id):
                self._update(task_id, status="cancelled", progress=100.0, message="已停止")
                return
            self._execute(task_id, req)
        finally:
            self._sem.release()

    def _execute(self, task_id: str, req: BacktestRequest):
        """真正执行回测（已获得并发许可）。"""
        self._update(task_id, status="running", message="开始执行", progress=2.0)

        # 应用 qlib 外挂补丁（monkey-patch，不改 qlib 内核）
        try:
            from .patches import patch_qlib_parallel, patch_cancel_callbacks
            # 1) 多线程并行补丁：把全局 R 替换为线程本地版本，使多任务并发不冲突
            patch_qlib_parallel(base_dir=self._work_dir if self._work_dir else None)
            # 2) 训练中途可取消补丁：monkey-patch lightgbm/xgboost.train，注入每 N 轮取消检查
            patch_cancel_callbacks()
        except Exception:
            # 补丁失败不阻塞回测（退回单任务可靠运行）
            pass

        # 注入进度回调（每次汇报进度时检查是否被取消）
        from . import qlib_engine

        def cb(p, msg):
            self._update(task_id, progress=p, message=msg)
            if self.is_cancelled(task_id):
                raise TaskCancelledError(task_id)

        qlib_engine.set_progress_callback(cb)
        # 重置本任务的最大进度记录，保证进度从新任务开始累计（不继承旧任务值）
        qlib_engine.reset_progress()
        qlib_engine.set_artifact_dir(
            os.path.join(self._work_dir, "artifacts", task_id) if self._work_dir else None
        )
        # 注入取消检查：每个关键检查点会调用这个 lambda 检查是否被取消
        qlib_engine.set_cancel_check(lambda: self.is_cancelled(task_id))
        try:
            result = qlib_engine.run_backtest(req, work_dir=self._work_dir, task_id=task_id)
            self._update(task_id, status="success", progress=100.0, message="完成", result=result)
        except TaskCancelledError:
            self._update(task_id, status="cancelled", progress=100.0, message="已停止", result=None)
        except Exception as e:
            # 堆栈只打到服务端日志；前端只显示友好错误信息，避免暴露内部堆栈
            import traceback
            logger.error("回测任务 %s 失败: %s\n%s", task_id, e, traceback.format_exc())
            self._update(
                task_id,
                status="failed",
                progress=100.0,
                message=f"失败: {e}",
            )
        finally:
            with self._lock:
                self._cancel_flags.discard(task_id)
                # 任务已结束，清理请求快照（running_resume_sources 只关心运行中的）
                self._reqs.pop(task_id, None)
            qlib_engine.set_progress_callback(None)
            qlib_engine.set_artifact_dir(None)
            qlib_engine.set_cancel_check(None)

    def set_display_name(self, task_id: str, display_name: str) -> None:
        """为任务设置可读名称（如续测时沿用源任务目录名），用于任务状态区展示。"""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.display_name = display_name

    def get_req(self, task_id: str) -> Optional[BacktestRequest]:
        """返回任务提交时的请求快照（用于续测源目录判断等；任务结束后已清理则返回 None）。"""
        with self._lock:
            return self._reqs.get(task_id)

    def running_resume_sources(self) -> set:
        """返回运行/排队/取消中任务所复用的源 task_id 集合（用于历史列表判断"续测占用中"）。

        续测任务会复用源任务的 artifacts 目录：目录名后缀是源 task_id，
        此时源目录对应的历史行也应视为"运行中"，禁止删除。
        """
        with self._lock:
            active = {
                tid for tid, t in self._tasks.items()
                if t.status in ("running", "pending", "cancelling")
            }
            return {
                self._reqs[tid].resume_task_id
                for tid in active
                if tid in self._reqs and self._reqs[tid].resume_task_id
            }

    def get(self, task_id: str) -> Optional[BacktestTask]:
        with self._lock:
            t = self._tasks.get(task_id)
            return t.model_copy(deep=True) if t else None

    def _get(self, task_id: str) -> Optional[BacktestTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def _update(self, task_id: str, **kwargs):
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return
            for k, v in kwargs.items():
                setattr(t, k, v)

    def list(self) -> Dict[str, BacktestTask]:
        with self._lock:
            return {k: v.model_copy(deep=True) for k, v in self._tasks.items()}

    def running_count(self) -> int:
        """当前正在运行（非 pending/非结束）的任务数。"""
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status in ("running", "cancelling"))

    def queued_count(self) -> int:
        """当前排队等待（pending）的任务数。"""
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status == "pending")

    def concurrency_info(self) -> dict:
        """返回并发能力信息（供前端提示：已达上限则无法再增加回测）。"""
        return {
            "max_concurrent": MAX_CONCURRENT_TASKS,
            "running": self.running_count(),
            "queued": self.queued_count(),
            "available": max(0, MAX_CONCURRENT_TASKS - self.running_count()),
            "resource": resource.resource_summary(),
        }

    def can_submit(self) -> bool:
        """是否还能提交新回测（running 数未达到上限）。

        注意：这里"还能提交"指的是不会立即排队等不到 CPU；即使达到上限，
        新任务仍会进入 pending 排队，只是通过 available=0 提示用户已达并发上限。
        """
        return self.running_count() < MAX_CONCURRENT_TASKS


# 全局单例
_manager: Optional[TaskManager] = None


def get_task_manager(work_dir: Optional[str] = None) -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager(work_dir=work_dir)
    return _manager
