# -*- coding: utf-8 -*-
"""
统一日志模块。

为整个后端提供统一的 logger 工厂，避免各模块各自裸打 print/裸吞异常。
用法：
    from ..logger import get_logger
    logger = get_logger(__name__)
    logger.warning("...", exc_info=True)
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from contextvars import ContextVar
from typing import Any, Iterator

#: 当前任务号 / 阶段（★ v2.10.60）——
#  ⚠ 为什么用 `ContextVar` 而不是"给每个函数加参数" ✗：
#    全仓有 **352 处** `_log_swallowed(...)` 调用、分布在 **50 个文件**里 ✓，
#    挨个加 `task_id` 参数既不可能（改动面太大）也不该（日志字段不是业务参数）✓。
#    `ContextVar` 是 **per-thread/per-task 隐式上下文** ✓ ⇒ **调用点一行都不用改** ✓，
#    只要在"任务开跑"的地方包一层 `with task_log_context(task_id)` ✓ 就有了 ✓。
#  ⚠ 它与 `threading.local` 的区别：协程/异步里也正确 ✓（本项目任务走线程 ✓，两者都行 ✓）。
_TASK_ID: ContextVar[str] = ContextVar("qlib_task_id", default="")
_STAGE: ContextVar[str] = ContextVar("qlib_stage", default="")


@contextlib.contextmanager
def task_log_context(task_id: str = "", stage: str = "") -> Iterator[None]:
    """在 `with` 块内给**所有**日志（含 `_log_swallowed`）打上 `task=` / `stage=` ✓。

    用法（一行 ✓）：`with task_log_context(task_id): ...干活...` ✓
    ⚠ **嵌套安全**：内层结束后自动恢复外层值 ✓（`ContextVar.reset` ✓）。
    """
    tok_id = _TASK_ID.set(str(task_id or ""))
    tok_stage = _STAGE.set(str(stage or ""))
    try:
        yield
    finally:
        _TASK_ID.reset(tok_id)
        _STAGE.reset(tok_stage)


def current_task_id() -> str:
    """当前日志上下文里的任务号（没有则空串 ✓ —— 别返回 None ✗，省得调用方判空）。"""
    return _TASK_ID.get()


def current_stage() -> str:
    """当前日志上下文里的阶段名（没有则空串 ✓）。"""
    return _STAGE.get()


def _log_swallowed(where, exc, **ctx: Any):
    """**留痕兜底** ✓（P2-A，2026-10-18）—— 吞异常后写一行 debug 日志。

    ⚠ logger 名在不同模块不一样（`logger` / `_LOGGER` / 没有 ✓），这里全兜住 ✓；
      全程 `contextlib.suppress` ✓：**日志自身失败绝不许抛** ✗（2.9.26 事故教训 ✓）。

    ★ v2.10.60：自动带上 `task=` / `stage=` ✓（来自 `task_log_context` ✓）——
      排查"某个任务为什么慢/失败"时，能在 `backend.log` 里**按任务号 grep** ✓，
      而不是在一堆交错的多任务日志里猜哪几行属于它 ✗。
      ⚠ 也可以显式传 `**ctx`（如 `_log_swallowed("x", e, task_id=...)` ✓），优先用它 ✓。
    """
    with contextlib.suppress(Exception):
        tid = str(ctx.pop("task_id", "") or _TASK_ID.get())
        stage = str(ctx.pop("stage", "") or _STAGE.get())
        prefix = "[吞]"
        if tid:
            prefix += " task=%s" % tid
        if stage:
            prefix += " stage=%s" % stage
        if ctx:                                   # 其它结构化字段（原样拼，不丢 ✓）
            prefix += " " + " ".join("%s=%s" % (k, v) for k, v in ctx.items())
        lg = globals().get("logger") or globals().get("_LOGGER") or logging.getLogger(__name__)
        lg.debug("%s %s: %r", prefix, where, exc)


_LEVEL = os.environ.get("QLIB_LOG_LEVEL", "INFO").upper()


class _Utf8SafeStream:
    """★ v1.20.63：把日志文本按 **UTF-8** 写到底层字节流，并且**永不抛异常** ✓。

    动机（2026-09-23 用户报「卡住了？很久都没有进度」，排查时发现）：后端由
    `restart_backend.ps1` 以 `-RedirectStandardOutput backend.log` 启动 ✓ ⇒ Windows 下
    `sys.stdout.encoding` 是 **GBK** ✗ ⇒ 只要日志里出现 `✓ ✗ → ⇒` 之类（**本项目日志到处是** ✓）
    就抛 `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'` ✗
    ⇒ `--- Logging error ---` ✗ ⇒ **引擎日志整条整条地丢** ✗✓
    （实测 `backend_err.log` 末条正是 `qlib_engine.py:424 _once_log` 的那句 ✓）。
    ⇒ 卡死时**看不到任何进度/阶段行** ✓，只能靠产物时间戳反推 ✗ —— 这就是"很久没进度"的一半原因 ✓。

    ⚠ 为什么不直接给 `logging.StreamHandler(sys.stdout)` 传 `encoding=` ✗：那是 `FileHandler` 的参数 ✗；
    ⚠ 也不 `TextIOWrapper(sys.stdout.buffer)` ✗ —— 它会把原 `sys.stdout` **detach** ✗，
      之后 uvicorn/print 写标准输出会 `ValueError` ✗。所以这里**只借用底层 buffer** ✓，
      **保留原 `sys.stdout` 不动** ✓；拿不到 buffer（如已被包过 ✓）则退回原流 + `errors="replace"` ✓。
    """

    def __init__(self, stream):
        self._stream = stream
        self._buffer = getattr(stream, "buffer", None)
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, text: str):
        if self._buffer is not None:
            try:
                self._buffer.write(text.encode("utf-8", "replace"))
                return len(text)
            except Exception as e:  # noqa: BLE001
                _log_swallowed("logger.py", e)
                pass
        try:
            return self._stream.write(text)
        except Exception as e:  # noqa: BLE001
            _log_swallowed("logger.py", e)
            return len(text)          # ⚠ 日志绝不许把回测搞崩 ✗ ⇒ 吞掉并假装写成功 ✓

    def flush(self):
        for f in (self._buffer, self._stream):
            try:
                if f is not None:
                    f.flush()
            except Exception as e:  # noqa: BLE001
                _log_swallowed("logger.py", e)
                pass

    def isatty(self):
        try:
            return bool(self._stream.isatty())
        except Exception as e:  # noqa: BLE001
            _log_swallowed("logger.py", e)
            return False


def _build_default_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(_Utf8SafeStream(sys.stdout))
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, _LEVEL, logging.INFO))
        # 避免重复打日志到父 logger
        logger.propagate = False
    return logger


def get_logger(name: str = "qlib") -> logging.Logger:
    """获取统一 logger。name 传模块名（如 __name__），用于定位来源。"""
    return _build_default_logger(name)
