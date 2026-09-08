# -*- coding: utf-8 -*-
"""
异步任务管理器。

职责：
- 提交回测任务到后台线程池执行，不阻塞 FastAPI 主线程
- 维护每个任务的状态（pending/running/success/failed）和进度
- 提供查询接口给 API 层

说明：Qlib 的回测较重且 qlib.init() 与全局状态相关，因此在独立线程中执行。
更稳妥的做法是子进程隔离，这里先用线程 + 全局进度字典，简单可靠。

并发上限是"动态配额"而非启动期固定值：
- 每次准入排队时按当前可用内存 / CPU 实时探测（resource.max_concurrent），
  因此测试进程退出释放内存后，无需重启后端即可自动放宽并发上限；
  也可用环境变量 QLIB_MAX_CONCURRENT 固定为常数（人工指定，如服务器固定 4）。
- 配额（持有计数）与任务状态解耦：排队任务不计配额，获批后才占用；
  释放时通过条件变量唤醒排队中的回测任务线程。
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


def current_max_concurrent() -> int:
    """当前允许的最大并发回测数（实时探测，可随时间变化）。

    - 优先读环境变量 QLIB_MAX_CONCURRENT（人工显式指定，如服务器上固定 4）
    - 未指定则按硬件自动检测（CPU 核数 + 当前可用内存）计算
    """
    env = os.environ.get("QLIB_MAX_CONCURRENT")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, resource.max_concurrent())


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
        self._cond = threading.Condition(self._lock)
        self._work_dir = work_dir
        # 任务请求快照：task_id -> req（用于判断续测占用源目录等场景；任务结束即清理）
        self._reqs: Dict[str, BacktestRequest] = {}
        # 已批准但尚未完结的并发配额数（回测任务 + 外部任务 slot 共用）。
        # 用"持有计数"而非固定信号量：上限是动态的（当前内存/CPU），
        # 运行中任务数超过新上限时新任务排队等待，不打断已在跑的任务。
        self._hold_slots = 0
        # 持有并发配额的外部任务（如单因子测试）与排队等待配额的外部任务。
        # external：task_id -> 当前持有 slot 数——一个外部任务可同时占多个并发单元
        # （单因子测试"并行测试"模式下每个预测周期各占一个，与回测/训练共享同一配额）。
        self._external: Dict[str, int] = {}
        self._external_queued: set = set()

    def current_limit(self) -> int:
        """当前并发上限（现算，供展示与准入判断共用）。"""
        return current_max_concurrent()

    def _release_hold(self) -> None:
        """归还一个并发配额并唤醒排队任务。"""
        with self._lock:
            self._hold_slots = max(0, self._hold_slots - 1)
            self._cond.notify_all()

    def _reserve_blocking(self, task_id: str) -> bool:
        """回测任务阻塞式申请配额：配额满时在此等待（保持 pending 排队）。

        返回 False 表示排队期间被取消（不再执行）。
        """
        with self._lock:
            while self._hold_slots >= self.current_limit():
                self._cond.wait()
            self._hold_slots += 1
        if self.is_cancelled(task_id):
            self._release_hold()
            return False
        return True

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
        # 限制并发：超出当前上限的任务在此等待（保持 pending 排队状态）
        limit = self.current_limit()
        full = self.running_count() >= limit
        hint = "（已达并发上限，排队等待）" if full else f"（并发上限 {limit}）"
        self._update(task_id, status="pending", message=f"排队中{hint}", progress=1.0)
        if not self._reserve_blocking(task_id):
            # 排队期间被取消
            self._update(task_id, status="cancelled", progress=100.0, message="已停止")
            return
        try:
            # 按当前并发任务数分配 qlib 并行核数（每任务 = 逻辑核/运行任务数）
            resource.acquire_task_jobs()
            task = self._get(task_id)
            if task is None:
                return
            if self.is_cancelled(task_id):
                self._update(task_id, status="cancelled", progress=100.0, message="已停止")
                return
            self._execute(task_id, req)
        finally:
            resource.release_task_jobs()
            self._release_hold()

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
        """返回并发能力信息（供前端提示：已达上限则无法再增加回测）。

        注意：running/queued 包含外部任务（单因子测试）占用的并发配额，
        使"并发: x/y"的显示与实际占用的配额一致。
        """
        running = self.running_count() + self.external_running()
        limit = self.current_limit()
        return {
            "max_concurrent": limit,
            "running": running,
            "queued": self.queued_count() + self.external_queued(),
            "available": max(0, limit - running),
            "resource": resource.resource_summary(),
        }

    def can_submit(self) -> bool:
        """是否还能提交新回测（running 数未达到当前上限）。

        注意：这里"还能提交"指的是不会立即排队等不到 CPU；即使达到上限，
        新任务仍会进入 pending 排队，只是通过 available=0 提示用户已达并发上限。
        """
        return self.running_count() + self.external_running() < self.current_limit()

    def external_soft_cap(self) -> int:
        """单因子等外部(共享配额)任务合计可占用的最大槽位数。

        默认 = 当前最大并发 - 1：始终为回测保留至少 1 个槽，避免单因子并行测试
        （每个预测周期各占一个槽）一次吃光全部并发导致回测长期排队。
        可通过环境变量 QLIB_RESERVED_BACKTEST_SLOTS 调整"预留给回测的槽数"。
        """
        reserve = 1
        env = os.environ.get("QLIB_RESERVED_BACKTEST_SLOTS")
        if env:
            try:
                reserve = max(0, int(env))
            except ValueError:
                pass
        return max(1, self.current_limit() - reserve)

    def try_acquire_slot(self, external_id: Optional[str] = None) -> bool:
        """尝试占用一个并发配额（不阻塞，按当前动态上限判断）。

        供单因子测试等共享回测并发资源的任务使用：与回测共用同一并发池，
        配额满时返回 False（需排队等待），配合 release_slot() 成对使用。
        external_id 非空时登记"持有配额"（计数 +1，同一任务可多次 acquire 占多个
        slot，如单因子测试并行模式每个预测周期各占一个），计入并发统计（running）。
        带 external_id 的占用还受 external_soft_cap() 限制（默认留 1 槽给回测）。
        """
        with self._lock:
            if external_id:
                ext = sum(self._external.values())
                if ext >= self.external_soft_cap():
                    return False
            if self._hold_slots >= self.current_limit():
                return False
            self._hold_slots += 1
        resource.acquire_task_jobs()
        if external_id:
            with self._lock:
                self._external[external_id] = self._external.get(external_id, 0) + 1
        return True

    def release_slot(self, external_id: Optional[str] = None) -> None:
        """归还一个并发配额（与 try_acquire_slot 成对），并注销持有登记（计数 -1，归零移除）。"""
        resource.release_task_jobs()
        with self._lock:
            self._hold_slots = max(0, self._hold_slots - 1)
            if external_id:
                n = self._external.get(external_id, 0)
                if n > 1:
                    self._external[external_id] = n - 1
                else:
                    self._external.pop(external_id, None)
            self._cond.notify_all()

    def register_external_queued(self, external_id: str) -> None:
        """登记一个排队等待配额的外部任务（计入并发统计 queued）。"""
        with self._lock:
            self._external_queued.add(external_id)

    def unregister_external_queued(self, external_id: str) -> None:
        """取消排队登记（任务拿到配额或结束/取消时调用）。"""
        with self._lock:
            self._external_queued.discard(external_id)

    def external_running(self) -> int:
        """外部任务当前持有的并发配额总数（如单因子测试并行模式 = 各任务已占 slot 之和）。"""
        with self._lock:
            return sum(self._external.values())

    def external_queued(self) -> int:
        """排队等待配额的外部任务数。"""
        with self._lock:
            return len(self._external_queued)


# 全局单例
_manager: Optional[TaskManager] = None


def get_task_manager(work_dir: Optional[str] = None) -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager(work_dir=work_dir)
    return _manager
