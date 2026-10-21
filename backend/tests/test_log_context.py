# -*- coding: utf-8 -*-
"""日志上下文（`task=` / `stage=`）测试（★ v2.10.60，2026-10-21 ✓）。

## 为什么用 `ContextVar` 而不是给每个函数加参数

全仓有 **352 处** `_log_swallowed(...)` 调用、分布在 **50 个文件**里 ——
挨个加 `task_id` 参数既不可能（改动面太大）也不该（日志字段不是业务参数）。
`ContextVar` 是 per-thread 隐式上下文 ⇒ **调用点一行不改**，只要任务开跑时包一层
`with task_log_context(task_id)` 就能让**整条调用链**的日志都带上任务号 ✓。

## 这组测试钉的是什么

不是"日志能打印"（那太弱 ✗），而是：
① **进入/退出/嵌套** 的作用域语义 ✓；② **线程之间不串味** ✓（多任务并行时最要命 ✗）；
③ `_log_swallowed` **真的把字段拼进日志文本** ✓（否则加了等于没加 ✗）；
④ 显式传参**优先于**上下文 ✓；⑤ 日志本身**永不抛** ✓（项目 2.9.26 事故教训）。
"""
from __future__ import annotations

import logging
import threading

import pytest

from app.logger import (
    _log_swallowed,
    current_stage,
    current_task_id,
    task_log_context,
)

LOGGER_NAME = "app.logger"


def _capture(caplog):
    """让 `app.logger` 能被 caplog 抓到。

    ⚠ 它的正式 handler 是直连 stdout 的（`get_logger` 里 `propagate = False` ✗）⇒
      **caplog 默认抓不到** ✓；这里只在本测试内临时打开 propagate ✓（不动生产行为 ✓）。
    """
    lg = logging.getLogger(LOGGER_NAME)
    lg.propagate = True
    lg.setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    return lg


def test_default_is_empty_not_none():
    """上下文外：**空串**而不是 `None` ✓（省得调用方到处判空 ✗）。"""
    assert current_task_id() == ""
    assert current_stage() == ""


def test_enter_and_leave_restores():
    with task_log_context("T-1", "train"):
        assert current_task_id() == "T-1"
        assert current_stage() == "train"
    assert current_task_id() == ""
    assert current_stage() == ""


def test_nested_restores_outer():
    """⭐ 嵌套安全：内层结束 ⇒ **恢复外层** ✓（不是清空 ✗）。"""
    with task_log_context("OUT", "outer"):
        with task_log_context("IN", "inner"):
            assert current_task_id() == "IN"
        assert current_task_id() == "OUT", "内层退出必须回到外层 ✗"
        assert current_stage() == "outer"


def test_thread_isolation():
    """⭐⭐ 线程隔离：别的线程里读不到这里的任务号 ✓。

    多任务并行时这是**最关键**的一条 ✗ —— 若用全局变量实现，
    两个任务的日志会互相串味 ⇒ 排查时反而更乱 ✓。
    """
    seen: list[str] = []
    with task_log_context("MAIN"):
        def worker():
            seen.append(current_task_id())      # 新线程 ⇒ 应为空 ✓

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert current_task_id() == "MAIN"
    assert seen == [""], "另一个线程不该看到 MAIN 的任务号 ✗"


def test_log_swallowed_includes_task_and_stage(caplog):
    """⭐⭐ 字段必须**真的进到日志文本**里 ✓ —— 否则"加了上下文"等于没加 ✗。"""
    lg = _capture(caplog)
    try:
        with task_log_context("T-42", "compose"):
            _log_swallowed("engine/x.py", ValueError("boom"))
    finally:
        lg.propagate = False

    text = caplog.text
    assert "[吞]" in text
    assert "task=T-42" in text, "任务号没进日志 ✗（那按任务 grep 就无从谈起）"
    assert "stage=compose" in text
    assert "engine/x.py" in text and "boom" in text


def test_log_swallowed_without_context_has_no_task_field(caplog):
    """没上下文时**不许**打印 `task=` ✓（别输出 `task=` 空值混淆 grep ✗）。"""
    lg = _capture(caplog)
    try:
        _log_swallowed("engine/y.py", RuntimeError("no-ctx"))
    finally:
        lg.propagate = False

    text = caplog.text
    assert "[吞]" in text and "no-ctx" in text
    assert "task=" not in text


def test_explicit_arg_beats_context(caplog):
    """显式 `task_id=` **优先于**上下文 ✓（跨任务排查时想标注"这是谁引起的" ✓）。"""
    lg = _capture(caplog)
    try:
        with task_log_context("CTX"):
            _log_swallowed("engine/z.py", ValueError("x"), task_id="EXPLICIT", stage="s2")
    finally:
        lg.propagate = False

    assert "task=EXPLICIT" in caplog.text
    assert "task=CTX" not in caplog.text


@pytest.mark.parametrize("bad", [object(), None, 123, {"k": "v"}])
def test_log_swallowed_never_raises(bad):
    """⭐ **日志自身失败绝不许抛** ✓（2.9.26 事故教训：日志把回测搞崩 ✗）。"""
    with task_log_context("T", "s"):
        _log_swallowed("engine/w.py", bad)      # 不抛即通过 ✓
