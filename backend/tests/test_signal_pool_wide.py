# -*- coding: utf-8 -*-
"""基准池快路径 `pricing.load_close_wide` 的**等价性**回归（需真实数据 ⇒ `datareq`）。

为什么必须有这个测试（2026-09-15）：全A 基准池走 `load_price_panel` 要对 6~7 个字段各做一次
long→wide，实测 **38s/次**、且每次重跑都重算（用户："纯信号也算这么慢吗"）。改成"只求 `$close`
+ 宽表缓存"后，**必须证明数值与旧路径逐位一致** —— 否则就是把"变快"建立在"改口径"上。
（手工版校验：`python ai_test/check_pool_wide.py`，全A 5588 只 × 2186 天口径。）
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.datareq
def test_close_wide_matches_price_panel_bitwise():
    """同一批标的：`load_close_wide` 与 `load_price_panel['CLOSE']` 必须**逐位相同**。"""
    from app.signals.pricing import load_close_wide, load_price_panel

    codes = ["SH600000", "SH600519", "SZ000001", "SZ300750", "SH601318",
             "SZ000651", "SH600036", "SZ002415", "SH688981", "SZ300059"]
    start, end = "2021-01-01", "2021-12-31"

    fast = load_close_wide(codes, start, end)
    slow = load_price_panel(codes, start, end, need_open=False).get("CLOSE")
    assert fast is not None and slow is not None

    # ⚠ 只允许**列顺序**不同：快路径把代码排序后取数，旧路径保持传入顺序。
    #   上下游都按**列名**取数组（`full.columns.get_indexer(ev_code)` 等）⇒ 顺序无关紧要；
    #   但数值/形状/日期索引必须严格一致 ⇒ 这里按列名对齐后再逐位比较。
    assert fast.shape == slow.shape
    assert set(fast.columns) == set(slow.columns)
    assert fast.index.equals(slow.index)
    slow = slow[[c for c in fast.columns]]

    a = slow.to_numpy(dtype=float)
    b = fast.to_numpy(dtype=float)
    assert int((np.isnan(a) != np.isnan(b)).sum()) == 0, "NaN 分布必须一致（停牌口径）"
    both = np.isfinite(a) & np.isfinite(b)
    if both.any():
        assert float(np.max(np.abs(a[both] - b[both]))) == 0.0, "数值必须逐位相同"


@pytest.mark.datareq
def test_close_wide_cache_hit_is_fast_and_equal():
    """第二次调用走宽表缓存：**快**且结果完全一致（缓存不能改数）。"""
    import time

    from app.signals.pricing import load_close_wide

    codes = ["SH600000", "SZ000001", "SH600519"]
    start, end = "2021-01-01", "2021-06-30"
    a = load_close_wide(codes, start, end)
    t0 = time.perf_counter()
    b = load_close_wide(codes, start, end)
    dt = time.perf_counter() - t0
    assert a is not None and b is not None
    assert a.equals(b)
    assert dt < 2.0, "命中缓存应远快于首次（实测 ~0.03s；给 2s 余量防慢盘）"
