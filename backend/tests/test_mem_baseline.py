# -*- coding: utf-8 -*-
"""**内存基线闸门**（★ v2.11.57）—— 进程内 `.day.bin` LRU 缓存**不许无限长**。

## 为什么需要它（原先的状态）
「并发按 CPU ∩ 内存自适应」「bin 缓存 768MB」这些**只写在文档和注释里** ✓，
**没有任何闸门** ✗ ⇒ 谁把 `_evict_bin_cache()` 改坏（或把上限设成 `10**9` ✗），
CPU 上的表现**完全一样** ✓，只在**长时间跑大面板**时才 OOM ✗ —— 那时已经上线了 ✗。

## 测什么（三条性质，都可复核）
1. **上限有界** ✓：默认 768MB，且不允许超过 4GB（有人写 `10**9` 会被这条抓到 ✗）；
2. **写入永不越界** ✓：连续写入 12 条 1MB，任何时刻 `bin_cache_bytes() <= 上限` ✓；
3. **峰值内存不随写入次数增长** ✓：cap=2MB 时写 20 条 1MB ⇒ tracemalloc 峰值必须远低于 20MB
   （**这条才抓得到"逐出被改坏"** ✓ —— 只看计数可能被同步维护的计数"骗"过 ✓）。

## ⚠ 为什么还要一条"自检"
若 `_evict_bin_cache` 变成空操作 ✓，第 2/3 条**确实会红** ✓ —— 但那是我"以为"会红 ✗。
⇒ 用 `monkeypatch` 把逐出改成 no-op ✓，**证明**这条闸门真的会响 ✓
（与 `xlsxWriteOnly` 的守卫自检同一套路 ✓：**守卫自己也要被验证** ✗→✓）。
"""
from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

from app.factors import panel_cache as pc

MB = 1024 * 1024
_ORIG_MB = pc.bin_cache_max_bytes() // MB


def _vals(mb: int) -> np.ndarray:
    """造一个约 `mb` MB 的 float32 数组（1 个 float32 = 4 字节 ✓）。"""
    return np.zeros(int(mb * MB / 4), dtype=np.float32)


@pytest.fixture(autouse=True)
def _restore_cache():
    """每条用例后把上限与缓存**恢复原样** ✓ —— 否则会污染同进程的其它用例 ✗。"""
    yield
    pc.set_bin_cache_mb(_ORIG_MB)
    pc.clear_bin_entries()


def test_default_cap_is_bounded():
    """默认上限有界且不离谱（768MB 附近；`set_bin_cache_mb` 至少 1MB ✓）。"""
    cap = pc.bin_cache_max_bytes()
    assert 64 * MB <= cap <= 4 * 1024 * MB, "缓存上限异常：%d 字节（%d MB）" % (cap, cap // MB)


def test_cache_never_exceeds_cap():
    """连续写入 12 条 1MB（cap=2MB）⇒ **任何时刻**都不越界，且 LRU 保留最新的。"""
    pc.set_bin_cache_mb(2)
    pc.clear_bin_entries()
    for i in range(12):
        pc._put_bin_cache(("SH%06d" % i, "close"), 1, 0, _vals(1))
        assert pc.bin_cache_bytes() <= pc.bin_cache_max_bytes(), (
            "第 %d 次写入后越界：%d > %d" % (i, pc.bin_cache_bytes(), pc.bin_cache_max_bytes())
        )
    assert pc.bin_cache_bytes() > 0, "全被逐光了 ⇒ 上限算错了 ✗"
    assert ("SH000011", "close") in pc._BIN_CACHE, "最新写入的必须还在（LRU 保新 ✓）"
    assert ("SH000000", "close") not in pc._BIN_CACHE, "最旧的应该已经被逐出（LRU ✓）"


def test_peak_memory_does_not_grow_with_insert_count():
    """⭐⭐ cap=2MB 时写 20 条 1MB ⇒ 峰值 / 远低于 20MB。

    没有逐出时峰值 ≈ 20MB+ ✓ ⇒ 这条**抓得到"逐出失效"** ✓（只看字节计数抓不到 ✓）。
    """
    tracemalloc.start()
    try:
        pc.set_bin_cache_mb(2)
        pc.clear_bin_entries()
        for i in range(20):
            pc._put_bin_cache(("X%03d" % i, "close"), 1, 0, _vals(1))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 10 * MB, (
        "峰值 %.1f MB 偏高（cap=2MB 却在 20 次写入里堆到这么多）⇒ 逐出可能失效 ✗" % (peak / MB)
    )


def test_selfcheck_eviction_guard_actually_fires(monkeypatch):
    """**守卫自检** ✓：把逐出改成空操作 ⇒ 上面那两条性质**必然**被破坏。

    不写这条的话，"闸门有效"只是我的**假设** ✗（正则/断言写错就会静默失效 ✗）。
    """
    monkeypatch.setattr(pc, "_evict_bin_cache", lambda need: None)
    pc.set_bin_cache_mb(2)
    pc.clear_bin_entries()
    for i in range(6):
        pc._put_bin_cache(("Y%03d" % i, "close"), 1, 0, _vals(1))
    assert pc.bin_cache_bytes() > pc.bin_cache_max_bytes(), (
        "逐出被改成空操作后竟然没越界 —— 那说明 `test_cache_never_exceeds_cap` 是假闸门 ✗"
    )
