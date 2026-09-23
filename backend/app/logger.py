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

import logging
import os
import sys

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
            except Exception:                                    # noqa: BLE001
                pass
        try:
            return self._stream.write(text)
        except Exception:                                        # noqa: BLE001
            return len(text)          # ⚠ 日志绝不许把回测搞崩 ✗ ⇒ 吞掉并假装写成功 ✓

    def flush(self):
        for f in (self._buffer, self._stream):
            try:
                if f is not None:
                    f.flush()
            except Exception:                                    # noqa: BLE001
                pass

    def isatty(self):
        try:
            return bool(self._stream.isatty())
        except Exception:                                        # noqa: BLE001
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
