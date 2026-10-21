# -*- coding: utf-8 -*-
"""进程级 `.day.bin` 读盘 LRU 缓存（★ v2.10.83 —— 从 `panel_expr.py` 抽出来 ✓）。

## 为什么抽出来

`panel_expr.py` 已经 **2,555 行** ✓（技术债 P1-#3 的六个巨型文件之一 ✓）。
这一层是**唯一自包含、且已有 7 条测试护着**的块 ✓（`tests/test_panel_bin_cache.py` ✓）
⇒ 是"拆巨型文件"里**风险最低的一刀** ✓。

## 缓存的是什么

`(inst, field)` → `(mtime_ns, start_idx, 值数组)` ✓。命中就跳过 `np.fromfile`
（实测 profile：`np.fromfile` 占整段求值 **≈52%** ✓ ⇒ 这是最值钱的一层缓存 ✓）。

## 三条容易错、且**只表现为"莫名变慢"**的约束（测试里逐条钉住 ✓）

1. **记账必须与内容一致** ✓（虚高 ⇒ 缓存过早空转 ✗；虚低 ⇒ 实际内存超预算 ✗）；
2. **同 key 覆盖要先扣旧字节** ✓（否则记账只增不减 ⇒ 跑久了缓存形同不存在 ✗）；
3. **超限淘汰必须"最旧先出"** ✓（按访问序 LRU ✓，不是插入序 ✗）。

## ⚠ 为什么用函数取字节数，而不是让调用方读模块变量

`_BIN_CACHE_BYTES` 是个**可变的整型** ✓ —— 跨模块 `from ... import _BIN_CACHE_BYTES`
拿到的是**当时的副本** ✗（之后变化看不到 ✓）。所以这里只提供
`bin_cache_bytes()` / `bin_cache_max_bytes()` 两个**函数** ✓；
⚠ 同理**不要**在外部给 `_BIN_CACHE_MAX_BYTES` 赋值 ✗ —— 要改上限请调 `set_bin_cache_mb()` ✓。
"""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Tuple

import numpy as np

#: 容量默认 768MB（`QLIB_PANEL_BIN_CACHE_MB` 可调 ✓，子进程 worker 各自独立 ✓）
_BIN_CACHE_MAX_BYTES = int(os.environ.get("QLIB_PANEL_BIN_CACHE_MB", "768")) * 1024 * 1024

#: `(inst, field)` → `(mtime_ns, start_idx, 值数组)` ✓（`OrderedDict` 是为了 LRU 顺序 ✓）
_BIN_CACHE: "OrderedDict[Tuple[str, str], Tuple[int, int, np.ndarray]]" = OrderedDict()

_BIN_CACHE_BYTES = 0


def _entry_bytes(entry) -> int:
    """单条占用的估算字节（数组本身 + 64B 的元组/键开销 ✓）。"""
    return entry[2].nbytes + 64


def _evict_bin_cache(need: int) -> None:
    """按 LRU（最旧先出 ✓）腾到能放下 `need` 字节为止。"""
    global _BIN_CACHE_BYTES
    while _BIN_CACHE_BYTES + need > _BIN_CACHE_MAX_BYTES and _BIN_CACHE:
        _BIN_CACHE_BYTES -= _entry_bytes(_BIN_CACHE.popitem(last=False)[1])


def _put_bin_cache(key: Tuple[str, str], mtime: int, start: int, vals: np.ndarray) -> None:
    """写入一条（**同 key 先扣旧字节** ✓，再按需淘汰 ✓）。"""
    global _BIN_CACHE_BYTES
    old = _BIN_CACHE.pop(key, None)
    if old is not None:
        _BIN_CACHE_BYTES -= _entry_bytes(old)
    entry = (mtime, start, vals)
    _evict_bin_cache(_entry_bytes(entry))
    _BIN_CACHE[key] = entry
    _BIN_CACHE_BYTES += _entry_bytes(entry)


def bin_cache_bytes() -> int:
    """当前已占字节 ✓（外部**只读**用 ✓ —— 见模块头部说明）。"""
    return _BIN_CACHE_BYTES


def bin_cache_max_bytes() -> int:
    """当前上限字节 ✓（要改请调 `set_bin_cache_mb()` ✓）。"""
    return _BIN_CACHE_MAX_BYTES


def clear_bin_entries() -> None:
    """只清 `.bin` 这一层（**表 + 字节计数** ✓）。

    ⚠ 字节计数必须一起归零 ✗→✓ —— 只清表不归零会让下一次 `put` 立刻过量淘汰 ✓
    （表现：刚清过缓存，接下来每次读盘都白读 ✓）。
    """
    global _BIN_CACHE_BYTES
    _BIN_CACHE.clear()
    _BIN_CACHE_BYTES = 0


def set_bin_cache_mb(mb: int) -> None:
    """动态调整上限（MB）；**至少 1MB** ✓（0 会让缓存彻底失效且每次 put 都立即逐出 ✗）。"""
    global _BIN_CACHE_MAX_BYTES
    _BIN_CACHE_MAX_BYTES = max(1, int(mb)) * 1024 * 1024
    _evict_bin_cache(0)
