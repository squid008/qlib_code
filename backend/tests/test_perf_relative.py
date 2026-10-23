# -*- coding: utf-8 -*-
"""**相对性能门**（★ v2.10.42，2026-10-21 ✓）—— 防"性能改写被悄悄退回 python 循环" ✗。

## 为什么不用"墙钟阈值"（"这个函数必须在 200ms 内跑完"）

因为**那在 CI 上必然 flaky** ✗：GitHub 的 runner 与本机差 2~3 倍 ✓，
一旦定了绝对阈值，就会变成"改一行注释都可能红"✗ ⇒ 最后一定被人 `skip` 掉 ✓。
**绝对时间不是契约**，能当契约的只有——**"向量化版本必须显著快于朴素版本"** ✓。

## 所以这里测的是**比值**（同一台机器、同一个进程内比）

    比值 = 朴素实现耗时 / 向量化耗时   必须 ≥ THRESHOLD

这台机器快，两个都变快 ✓；慢，两个都变慢 ✓ ⇒ **比值稳定** ✓，
而"有人把向量化改回 for 循环"✗ 会让比值塌到 1 附近 ✓ ⇒ 立刻红 ✓。

⚠ 阈值故意**留几十倍余量** ✓（本机实测：`DYN_WINDOW_SUM` **373~542×** ✓、
`BARSLAST` **61~83×** ✓；阈值只要求 ≥ **3 倍** ✓）——
性能断言的目的不是"跑分"✓，而是"**别退化一个数量级**"✓。
"""
from __future__ import annotations

from time import perf_counter

import numpy as np
import pytest

from app.factors.ops_ext import barslast_vec, dyn_window_sum_vec

# 规模：够大到让比值稳定 ✓，又不至于让测试跑几秒 ✗
# ⚠ 120k 时朴素实现要 **2.4 秒** ✓（比值 373×）⇒ 缩到 40k ✓（比值仍在百倍量级 ✓，本文件 ~1.5 秒 ✓）
ROWS = 40_000
THRESHOLD = 3.0     # 实测 61~542 倍，阈值只卡"是否退化一个数量级" ✓


def _naive_window_sum(vals: np.ndarray, nvals: np.ndarray) -> np.ndarray:
    """朴素实现：逐行切片求和 ✓（就是向量化要替代的那个写法 ✓）。"""
    out = []
    for i in range(len(vals)):
        w = 1 if np.isnan(nvals[i]) else max(1, int(np.trunc(nvals[i])))
        lo = max(0, i - w + 1)
        out.append(float(np.nan_to_num(vals[lo:i + 1], nan=0.0).sum()))
    return np.array(out)


def _naive_barslast(vals: np.ndarray) -> np.ndarray:
    """朴素实现：一趟循环记住"上次命中位置" ✓。"""
    out, last = [], -1
    for i in range(len(vals)):
        v = vals[i]
        if not np.isnan(v) and v != 0:
            last = i
        out.append(0.0 if last < 0 else float(i - last))
    return np.array(out)


@pytest.fixture(scope="module")
def big_panel() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    vals = rng.normal(size=ROWS)
    vals[::17] = np.nan                       # 掺点停牌 ✓
    nvals = np.full(ROWS, 20.0)               # 固定 20 期窗口 ✓（模拟 `Mean(x,20)`）
    return vals, nvals


def test_dyn_window_sum_is_not_degraded_to_python_loop(big_panel):
    """⭐⭐ `DYN_WINDOW_SUM` 必须**显著快于**逐行切片求和 ✓。

    若有人把 `dyn_window_sum_vec` 改回 for 循环 ✗ ⇒ 比值塌到 1 附近 ⇒ 这里红 ✓。
    """
    vals, nvals = big_panel

    t0 = perf_counter()
    got = dyn_window_sum_vec(vals, nvals)
    t_vec = perf_counter() - t0

    t0 = perf_counter()
    exp = _naive_window_sum(vals, nvals)
    t_naive = perf_counter() - t0

    np.testing.assert_allclose(got, exp, rtol=1e-9, atol=1e-9)   # 正确性先钉 ✓
    ratio = t_naive / max(t_vec, 1e-9)
    assert ratio >= THRESHOLD, (
        "向量化退化 ✗：朴素 %.1fms vs 向量化 %.1fms（仅 %.1f 倍，要求 ≥ %.1f 倍）"
        % (t_naive * 1000, t_vec * 1000, ratio, THRESHOLD)
    )


def test_barslast_is_not_degraded_to_python_loop(big_panel):
    """同上 ✓：`BARSLAST` 的向量实现不许退化成逐元素循环 ✗。"""
    vals, _ = big_panel

    t0 = perf_counter()
    got = barslast_vec(vals)
    t_vec = perf_counter() - t0

    t0 = perf_counter()
    exp = _naive_barslast(vals)
    t_naive = perf_counter() - t0

    np.testing.assert_allclose(got, exp)
    ratio = t_naive / max(t_vec, 1e-9)
    assert ratio >= THRESHOLD, (
        "向量化退化 ✗：朴素 %.1fms vs 向量化 %.1fms（仅 %.1f 倍，要求 ≥ %.1f 倍）"
        % (t_naive * 1000, t_vec * 1000, ratio, THRESHOLD)
    )
