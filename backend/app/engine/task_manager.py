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


# 最大并发回测任务数（避免多个 LightGBM/XGBoost 任务同时占满 CPU 互抢，
# 导致单任务变慢。超过则排队执行。可用环境变量 QLIB_MAX_CONCURRENT 覆盖）
MAX_CONCURRENT_TASKS = int(os.environ.get("QLIB_MAX_CONCURRENT", "2"))

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

        # 后台线程执行
        t = threading.Thread(target=self._run, args=(task_id, req), daemon=True)
        t.start()
        return task_id

    def _run(self, task_id: str, req: BacktestRequest):
        task = self._get(task_id)
        if task is None:
            return
        # 限制并发：超过 MAX_CONCURRENT_TASKS 的任务在此等待（保持 pending 排队状态）
        self._update(task_id, status="pending", message=f"排队中（并发回测上限 {MAX_CONCURRENT_TASKS}）", progress=1.0)
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
            qlib_engine.set_progress_callback(None)
            qlib_engine.set_artifact_dir(None)
            qlib_engine.set_cancel_check(None)

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


# 全局单例
_manager: Optional[TaskManager] = None


def get_task_manager(work_dir: Optional[str] = None) -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager(work_dir=work_dir)
    return _manager
