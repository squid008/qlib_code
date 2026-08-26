# -*- coding: utf-8 -*-
"""回测引擎的线程上下文工具（从 qlib_engine.py 拆分而来）。

用 contextvars 而非模块级全局变量：每个线程有独立上下文，
从而支持并行回测时各任务互不污染（避免 A 任务覆盖 B 任务的产物目录/取消标志）。

集中管理：
  - 进度回调（进度百分比 + 阶段消息）
  - 当前任务产物目录（按 task_id 组织）
  - 取消检查函数（返回 True 表示任务被取消）
"""
from typing import Optional

import contextvars

_progress_cb: contextvars.ContextVar = contextvars.ContextVar("_progress_cb", default=None)
_artifact_dir: contextvars.ContextVar = contextvars.ContextVar("_artifact_dir", default=None)
_cancel_check: contextvars.ContextVar = contextvars.ContextVar("_cancel_check", default=None)
# 记录本任务已上报的最大进度，保证进度条单调递增（不倒退）
_max_progress: contextvars.ContextVar = contextvars.ContextVar("_max_progress", default=0.0)


def set_progress_callback(cb):
    _progress_cb.set(cb)


def set_artifact_dir(path: Optional[str]):
    """设置当前任务的模型产物保存目录（按 task_id 组织）。"""
    _artifact_dir.set(path)


def set_cancel_check(fn):
    """设置当前任务的取消检查函数（返回 True 表示任务被取消）。"""
    _cancel_check.set(fn)


def get_artifact_dir() -> Optional[str]:
    """获取当前任务的产物保存目录。"""
    return _artifact_dir.get()


def check_cancel():
    """在关键检查点调用：若用户已点停止，抛 TaskCancelledError 让上层捕获并终止。"""
    cancel_check = _cancel_check.get()
    if cancel_check is not None:
        try:
            if cancel_check():
                from .task_manager import TaskCancelledError
                raise TaskCancelledError("cancelled by user")
        except Exception as e:
            # 只传递取消异常，其他异常忽略
            if type(e).__name__ == "TaskCancelledError":
                raise
            # 非取消异常，吞掉以免误终止


def report(p, msg):
    """上报进度。进度值保持单调递增（不倒退），回调异常时静默忽略，不中断回测。"""
    cb = _progress_cb.get()
    if cb is None:
        return
    try:
        max_p = _max_progress.get()
        if p < max_p:
            return  # 进度倒退，忽略（避免多段滚动时各段公式区间导致的回调）
        _max_progress.set(p)
        cb(p, msg)
    except Exception:
        pass


def reset_progress():
    """任务开始时重置本任务的最大进度记录（使新任务从头累计，不继承旧值）。"""
    _max_progress.set(0.0)
