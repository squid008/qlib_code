# -*- coding: utf-8 -*-
"""`.bin` 读盘缓存的**记账与淘汰**（★ v2.10.61，2026-10-21 ✓）。

## 为什么测这块（P2-6 的第一刀）

`panel_expr` 未覆盖的行里，最大一坨是"逐个算子的求值分支"✗（要一个个读语义 + 造数据 ✓，
一轮吃不掉 ✓）。但文件顶部这组**缓存管理**是**纯内存逻辑** ✓，而且错了很隐蔽：

- **记账不平** ⇒ 字节数虚高 ⇒ 缓存**提前空转淘汰** ✓（读盘变慢，却查不出原因 ✗）；
- **淘汰顺序错**（不是最旧先出）⇒ **频繁用的字段反而被踢** ✓ 同上；
- **同 key 重复 put 不扣旧字节** ⇒ 记账只增不减 ✓ ⇒ 跑久了**缓存形同不存在** ✗。

⇒ 这些都不会报错、也不会算错数 ✓，只会"**莫名其妙变慢**" ✗ —— 正是最该钉住的那类。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.factors import panel_cache as PC
from app.factors import panel_expr as P


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后都复原 ✓（模块级全局状态，会互相污染 ✗）。"""
    P.clear_bin_cache()
    old_max = PC.bin_cache_max_bytes()
    yield
    P.clear_bin_cache()
    PC.set_bin_cache_mb(max(1, old_max // (1024 * 1024)))


def _vals(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.float64)


def test_entry_bytes_counts_payload_plus_overhead():
    """单条字节 = 数组 nbytes + **固定 64 字节开销** ✓（键/元组等 ✓）。"""
    assert PC._entry_bytes((0, 0, _vals(10))) == 10 * 8 + 64


def test_put_keeps_accounting_consistent():
    """⭐⭐ 记账必须等于**实际各条之和** ✓ —— 虚高会提前淘汰、虚低会撑爆内存 ✗。"""
    PC._put_bin_cache(("A", "close"), 1, 0, _vals(100))
    PC._put_bin_cache(("B", "close"), 1, 0, _vals(50))
    expect = sum(PC._entry_bytes(v) for v in PC._BIN_CACHE.values())
    assert PC.bin_cache_bytes() == expect
    assert set(PC._BIN_CACHE) == {("A", "close"), ("B", "close")}


def test_put_same_key_replaces_and_refunds_old_bytes():
    """⭐⭐ **同 key 覆盖时必须先扣旧字节** ✓ —— 不扣就只增不减 ✓
    ⇒ 跑久了 `_BIN_CACHE_BYTES` 虚高 ⇒ **缓存几乎永远命中不了** ✗。"""
    PC._put_bin_cache(("A", "close"), 1, 0, _vals(1000))
    first = PC.bin_cache_bytes()
    PC._put_bin_cache(("A", "close"), 2, 0, _vals(10))     # 换成更小的
    assert len(PC._BIN_CACHE) == 1, "同 key 不该变成两条 ✗"
    assert PC.bin_cache_bytes() < first, "换了更小的值，记账必须变小 ✓"
    assert PC.bin_cache_bytes() == PC._entry_bytes((2, 0, _vals(10)))
    assert PC._BIN_CACHE[("A", "close")][0] == 2, "mtime 必须更新为新值 ✓"


def test_evicts_least_recently_used_first():
    """⭐⭐ 超上限 ⇒ **最旧（最久未用）先出** ✓（LRU ✓）。

    若顺序错成"最新先出" ✓ ⇒ 刚读过的字段立刻被踢 ⇒ **每次都重新读盘** ✗
    （表现就是"越跑越慢"✓，但没有任何报错 ✗）。
    """
    PC.set_bin_cache_mb(10)
    for i in range(5):
        PC._put_bin_cache(("X%d" % i, "close"), 1, 0, _vals(256 * 1024))  # 每条 2MB
    assert PC.bin_cache_bytes() <= PC.bin_cache_max_bytes(), "淘汰后不许超限 ✓"
    assert ("X0", "close") not in PC._BIN_CACHE, "最旧的必须先被淘汰 ✓"
    assert ("X4", "close") in PC._BIN_CACHE, "最新的一条必须在 ✓"


def test_set_cache_mb_shrinks_immediately():
    """⭐⭐ 调小上限 ⇒ **立刻逐出** ✓（等到下次 put 才生效 ✗ ⇒ 用户以为调了没用 ✓）。"""
    PC._put_bin_cache(("A", "close"), 1, 0, _vals(256 * 1024))   # 2MB
    PC._put_bin_cache(("B", "close"), 1, 0, _vals(256 * 1024))
    assert PC.bin_cache_bytes() > 0
    P.set_bin_cache_mb(1)
    assert PC.bin_cache_max_bytes() == 1 * 1024 * 1024
    assert PC.bin_cache_bytes() <= PC.bin_cache_max_bytes()


def test_set_cache_mb_has_floor_of_one_mb():
    """`set_bin_cache_mb(0)` ⇒ **至少 1MB** ✓（0 会让缓存彻底失效且每次 put 都立即逐出 ✗）。"""
    P.set_bin_cache_mb(0)
    assert PC.bin_cache_max_bytes() == 1024 * 1024


def test_clear_resets_everything():
    """`clear_bin_cache()` ⇒ 表空 + **字节归零** ✓（只清表不清字节 ⇒ 下次直接过量淘汰 ✗）。"""
    PC._put_bin_cache(("A", "close"), 1, 0, _vals(100))
    P.clear_bin_cache()
    assert len(PC._BIN_CACHE) == 0
    assert PC.bin_cache_bytes() == 0
