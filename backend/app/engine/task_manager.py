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


def _is_pool_killed_error(e: Exception) -> bool:
    """异常是否来自「取数 worker 被强制杀掉」（joblib/loky 的中断异常）。

    用于把「用户按了强制停止」与「真的回测失败」区分开（`_execute` 里据此收尾成
    `cancelled` 而不是 `failed`）。
    """
    # ⚠ 别把 `FileNotFoundError` 之类通用异常也算进来 —— 那会把真实的取数错误误判成"强制停止"
    if type(e).__name__ in ("BrokenProcessPool", "TerminatedWorkerError", "WorkerInterrupted",
                            "WorkerCrashError", "ProcessExpired"):
        return True
    msg = str(e).lower()
    return any(k in msg for k in ("brokenprocesspool", "terminatedworker", "a worker process",
                                  "loky", "was unexpectedly terminated"))


class TaskManager:
    def __init__(self, work_dir: Optional[str] = None):
        self._tasks: Dict[str, BacktestTask] = {}
        self._cancel_flags: set = set()
        # 走了「强制停止」（已杀取数 worker）的任务：收尾时记为「已强制停止」而非「失败」，
        # 且**抢占终态** —— 线程若永久卡在 joblib 里，这个集合还会阻止它事后覆盖状态。
        self._forced: set = set()
        # 当前**持有**并发配额的任务（用于强制停止时代卡死的线程归还配额，且幂等不重复归还）。
        self._held_by: set = set()
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

    def _kill_loky_pool(self) -> list:
        """杀本进程的 loky worker 并**重置 joblib 池缓存**，返回被杀的 worker PID。

        实现放在 `patches/serial_load.py`（池的"杀死/重置"是 joblib/loky 的领域知识，
        与"串行化取数"的根治补丁同处一模块）；这里包一层方法是为了让调用点稳定、
        也便于单测打桩（见 `tests/test_force_cancel.py`）。
        """
        from .patches.serial_load import kill_and_reset_loky_pool
        return kill_and_reset_loky_pool()

    def _release_hold_if_held(self, task_id: str) -> bool:
        """归还**该任务**占用的并发配额（幂等：只有仍持有才归还，返回是否真的归还了）。

        为什么需要按任务归还（2026-09-14）：强制停止时线程可能**永久卡在 joblib 内部**
        （`Parallel._retrieve` 等一个已死 worker 的管道，杀 worker 也解不开），它的
        `finally` 永远不执行 ⇒ 配额不会归还 ⇒ 攒够几次之后新任务全部排队饿死。
        故强制停止要**代它归还**；而线程若后来醒过来又会走 `finally` ⇒ 必须幂等，
        否则会多归还、把并发放超过上限。
        """
        with self._lock:
            if task_id not in self._held_by:
                return False
            self._held_by.discard(task_id)
            self._hold_slots = max(0, self._hold_slots - 1)
            self._cond.notify_all()
            return True

    def _has_pending_backtests_locked(self) -> bool:
        """（调用方需已持有 self._lock）是否存在排队等待配额的回测任务。"""
        return any(t.status == "pending" for t in self._tasks.values())

    def has_pending_backtests(self) -> bool:
        """是否存在排队等待配额的回测任务（pending）。

        并发协调用：回测排队中时，外部任务（单因子等）不再新增占用配额，
        把并发让给回测，避免单因子并行把排队回测饿死。
        """
        with self._lock:
            return self._has_pending_backtests_locked()

    def _reserve_blocking(self, task_id: str) -> bool:
        """回测任务阻塞式申请配额：配额满时在此等待（保持 pending 排队）。

        返回 False 表示排队期间被取消（不再执行）。
        """
        with self._lock:
            while self._hold_slots >= self.current_limit():
                if task_id in self._cancel_flags:
                    # 排队期间被取消：cancel() 已 notify_all 唤醒本等待者，
                    # 这里直接退出（不再占配额），由 _run 统一标记 cancelled。
                    return False
                self._cond.wait()
            self._hold_slots += 1
            self._held_by.add(task_id)          # 记名：强制停止时可代其归还（幂等）
        if self.is_cancelled(task_id):
            self._release_hold_if_held(task_id)
            return False
        return True

    def cancel(self, task_id: str) -> bool:
        """请求取消任务。返回是否成功标记（任务存在且未结束）。

        若任务正排队等待配额（pending），notify_all 唤醒其等待线程，
        使其立刻退出排队（此前 cancel 后要等到有槽释放才会响应）。
        """
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.status in ("success", "failed", "cancelled"):
                return False
            self._cancel_flags.add(task_id)
            t.status = "cancelling"  # 正在取消中
            self._cond.notify_all()  # 唤醒排队中的等待者（含 pending 回测自身）
            return True

    def is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cancel_flags

    def force_cancel(self, task_id: str) -> Optional[dict]:
        """强制停止：协作式标志 + 杀/重置 loky 池 + **立刻落终态**（不再等线程响应）。

        与 `cancel` 的差别（2026-09-14「取消的回测卡住了」之后加，同日再修）：
          · `cancel` 只置标志 ⇒ 卡在 joblib 内部时**永远等不到检查点**（状态停在 `cancelling`）；
          · `force_cancel` 额外调 `_kill_loky_pool()`（杀 worker **并重置 joblib 池缓存**）。
        ⚠⚠ **杀 worker 并不总能唤醒调用线程**：实测线程会永远卡在 `Parallel._retrieve`
        等一个已死 worker 的管道（此时 0 个 worker、CPU 0，状态永远 `cancelling`，用户
        「取消不了、强制停止也不行」）⇒ 故这里**不依赖线程配合**：
          ① 立刻把任务置为 `cancelled`（`_forced` 同时保证线程事后不能覆盖，见 `_finish`）；
          ② **代卡死的线程归还并发配额**（否则几次之后新任务全被饿死，见 `_release_hold_if_held`）；
          ③ 重置池缓存 ⇒ 下一个任务不会复用已损坏的池。
        返回 None 表示任务不存在或已结束。
        ⚠ 池是进程内共享的 ⇒ 会**同时中断同进程内其它正在取数的回测**（死锁场景的另一半）。
        """
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.status in ("success", "failed", "cancelled"):
                return None
            self._cancel_flags.add(task_id)
            self._forced.add(task_id)
            t.status = "cancelling"
            t.message = "强制停止中（正在终止取数进程并重置进程池）"
            self._cond.notify_all()
        killed = self._kill_loky_pool()
        # 线程可能已经永久卡住（见 docstring）⇒ 立刻落终态 + 代它归还配额（都是幂等的）
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None and t.status not in ("success", "failed", "cancelled"):
                t.status = "cancelled"
                t.progress = 100.0
                t.message = "已强制停止（取数进程已终止，进程池已重置）"
                t.result = None
            self._reqs.pop(task_id, None)
        released = self._release_hold_if_held(task_id)
        logger.warning("强制停止 %s：杀 %d 个取数 worker（PID %s）、归还配额=%s、已落终态 cancelled",
                       task_id, len(killed), killed, released)
        return {"task_id": task_id, "killed_workers": killed, "slot_released": released}

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
        _stop_mem_sampler = None
        try:
            # 按当前并发任务数分配 qlib 并行核数（每任务 = 逻辑核/运行任务数）
            resource.acquire_task_jobs()
            # ★ v1.20.63：起**内存采样器**（后端进程树 RSS 峰值 ✓）—— 任务结束时用它校正
            #   "单任务内存估算"，让并发上限跟着**实测**走 ✓（不再只靠 2026 年的 3GB 经验值 ✗）
            _stop_mem_sampler = resource.start_peak_sampler()
            task = self._get(task_id)
            if task is None:
                return
            if self.is_cancelled(task_id):
                self._update(task_id, status="cancelled", progress=100.0, message="已停止")
                return
            self._execute(task_id, req)
        finally:
            # ★ v1.20.63：先收采样 ⇒ 用**实测**峰值校正单任务内存估算 ✓
            #   ⚠ 只上报"上调"（见 `resource.note_task_peak_memory` ✓）；失败绝不影响收尾 ✗
            if _stop_mem_sampler is not None:
                try:
                    _peak_gb, _samples = _stop_mem_sampler()
                    if _samples:
                        logger.info("任务 %s 内存采样：进程树峰值归因 %.2fGB/任务（%d 次采样）",
                                    task_id, _peak_gb, _samples)
                        resource.note_task_peak_memory(_peak_gb, task_id)
                except Exception:                                # noqa: BLE001
                    pass
            resource.release_task_jobs()
            # 按任务归还（幂等）：强制停止可能已代本任务归还过（线程永久卡死时）
            self._release_hold_if_held(task_id)

    def _finish(self, task_id: str, status: str, message: str, result=None) -> bool:
        """写终态；若该任务已被「强制停止」**抢先落了终态**则忽略（返回 False）。

        必要性（2026-09-14）：强制停止会**立刻**把任务标成 `cancelled`（因为线程可能永久卡在
        joblib 内部、杀 worker 也解不开 —— 见 `patches/serial_load.py`），线程若事后醒过来
        （例如池被重置后抛 BrokenProcessPool、或居然跑完了）**不得覆盖**这个终态。
        """
        with self._lock:
            if task_id in self._forced:
                return False
            t = self._tasks.get(task_id)
            if t is None:
                return False
            t.status = status
            t.progress = 100.0
            t.message = message
            t.result = result
            return True

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
            if not self._finish(task_id, "success", "完成", result=result):
                logger.warning("回测任务 %s 已被强制停止，忽略其完成结果", task_id)
        except TaskCancelledError:
            self._finish(task_id, "cancelled", "已停止")
        except Exception as e:
            if task_id in self._forced or _is_pool_killed_error(e):
                # 强制停止（或同进程 loky 池被强杀时的连带中断）⇒ 收尾成「已强制停止」，
                # **不记失败**：这是用户主动操作的结果（见 force_cancel / _kill_loky_pool）。
                logger.warning("回测任务 %s 已被强制停止（%s）", task_id, type(e).__name__)
                self._finish(task_id, "cancelled", "已强制停止（取数进程已终止）")
                return
            # 堆栈只打到服务端日志；前端只显示友好错误信息，避免暴露内部堆栈
            import traceback
            logger.error("回测任务 %s 失败: %s\n%s", task_id, e, traceback.format_exc())
            self._finish(task_id, "failed", f"失败: {e}")
        finally:
            with self._lock:
                self._cancel_flags.discard(task_id)
                self._forced.discard(task_id)
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

        协调规则（回测优先）：存在排队等待配额的回测任务（pending）时，
        外部任务不再新增占用——把释放出来的槽优先让给排队回测，避免单因子
        并行（每周期一槽）把用户提交的回测长期饿在队列里。
        """
        with self._lock:
            if external_id:
                # 回测在排队：外部不抢新槽（已持有的槽继续跑，释放后回测先得）
                if self._has_pending_backtests_locked():
                    return False
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

    def external_wait_slot(self, external_id: str, cancel_check=None) -> bool:
        """外部任务（单因子）阻塞式等待一个配额（取代 sleep 忙轮询）。

        与 try_acquire_slot 同条件；拿不到时在条件变量上挂起，由释放/取消方
        notify_all 唤醒后重新竞争。cancel_check 可传可调用对象，每次被唤醒时
        检查：返回 True 表示已请求取消 → 立即放弃返回 False（不再排队空等）。

        返回 True=已占用一个槽（调用方需在结束时 release_slot(external_id)）；
        False=排队期间被取消。
        """
        with self._lock:
            while True:
                if cancel_check is not None and cancel_check():
                    return False
                ok = True
                if self._has_pending_backtests_locked():
                    ok = False  # 回测在排队：让位（不抢新槽）
                if ok:
                    ext = sum(self._external.values())
                    if ext >= self.external_soft_cap():
                        ok = False
                if ok and self._hold_slots >= self.current_limit():
                    ok = False
                if ok:
                    self._hold_slots += 1
                    break
                # 挂起等待（最长 1s 自醒一次，兜底感知取消/上限变化）：
                # 释放槽 / cancel() / wake_external_waiters() 会 notify_all 提前唤醒
                self._cond.wait(timeout=1.0)
        resource.acquire_task_jobs()
        with self._lock:
            self._external[external_id] = self._external.get(external_id, 0) + 1
        return True

    def wake_external_waiters(self) -> None:
        """唤醒全部排队等待配额的外部任务（如取消单因子测试时调用，使排队中的
        worker 立刻醒来感知取消退出，不必等到下一次自醒/释放）。"""
        with self._lock:
            self._cond.notify_all()

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
