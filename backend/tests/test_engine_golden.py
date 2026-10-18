# -*- coding: utf-8 -*-
"""`run_backtest` **语义冻结**（★ P2-C，2026-10-18 ✓）。

## 为什么要有它

P2-C 是"提速但**数字一位都不许变**"✓。这类改动的风险很阴 ✗：
优化完跑一遍看着对 ✓、隔天改另一处时**悄悄**变了 0.001% ✗ —— 没有任何测试会红 ✓。

⇒ 这里把一个小规模（120 只 × 260 日 / 480 笔成交 ✓，**0.2 秒** ✓ 无数据依赖 ✓）的
**NAV 全序列指纹**钉死 ✓：任何"改了撮合/账目语义"的改动都会立刻红 ✓，
而且报错信息能直接说"是 NAV 变了"✓（不是靠人眼看 ✓）。

⚠ 换成新黄金值的唯一正当理由：**有意改变口径** ✓ —— 那就连 `md/change_log.md` 一起写清楚 ✓。
"""
import sys
import os

import numpy as np
import pytest

BE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BE)
sys.path.insert(0, os.path.join(BE, "tools"))

from app.signals.engine import run_backtest          # noqa: E402
from bench_engine import build                      # noqa: E402

#: 黄金值（2026-10-18 实测 ✓ v2.9.34 之后）
GOLD = {
    "trades": 480,
    "rejects": 0,
    "nav_last": 1.0759942421,
    "nav_first": 1.0000000000,
    "nav_sha1": "d2b30ed3ab2f1ee4",
}


@pytest.fixture(scope="module")
def result():
    panel, sig = build(120, 260, 3)
    return run_backtest(sig, panel, hold_days=10, fill="t1_open", cost=0.004,
                        capital=1e9, strict_limit=True,
                        alloc_modes=("cash_even", "event_even"))


def test_trade_and_reject_counts_are_frozen(result):
    assert len(result.trades) == GOLD["trades"], (
        "成交笔数变了 ✗（%d ≠ %d）⇒ 撮合语义被改动 ✓" % (len(result.trades), GOLD["trades"]))
    assert len(result.rejects) == GOLD["rejects"]


def test_nav_series_fingerprint_is_frozen(result):
    nav = result.nav
    sha = __import__("hashlib").sha1(
        np.asarray(nav.to_numpy(), dtype="float64").tobytes()).hexdigest()[:16]
    assert sha == GOLD["nav_sha1"], (
        "NAV 整条序列的指纹变了 ✗（%s ≠ %s）⇒ 撮合/账目语义被改动 ✓\n"
        "  ⚠ 若是有意改口径：请同时改 GOLD 与 md/change_log.md ✓" % (sha, GOLD["nav_sha1"]))


def test_nav_endpoints_are_frozen(result):
    """端点值 ✓（容差 **1e-9** ✓ —— 黄金值在文件里只写到 10 位小数 ✓，
    再收紧就变成"测字符串精度"而不是"测语义"✗；真正的严格守门是上面的**整条 sha1** ✓）。"""
    nav = result.nav
    assert abs(float(nav.iloc[0, 0]) - GOLD["nav_first"]) < 1e-9
    assert abs(float(nav.iloc[-1, 0]) - GOLD["nav_last"]) < 1e-9, (
        "末值 NAV 变了 ✗（%.10f ≠ %.10f）" % (float(nav.iloc[-1, 0]), GOLD["nav_last"]))
