# -*- coding: utf-8 -*-
"""`period_roll` / `batch_even` 的**语义冻结**（★ v2.11.18）—— 补上"没有数值基线"的缺口。

## 为什么补这条
`tests/test_engine_golden.py` 只冻结了 **`run_backtest`** ✓ —— 另外两个策略
（`run_period_roll` / `run_batch_backtest`）**一个指纹都没有** ✗。
而我 v2.11.7 / 2.11.12 / 2.11.13 恰好动过它们的**内部结构**（抽 `build_targets`、
`build_batch_execs`、`init_batch_state` ✓）—— 当时只有**单元测试**护着 ✓，
**没有任何东西能证明"整条链的数字没变"** ✗。

> 单元测试证明"那块逻辑对" ✓；**指纹证明"跑出来的净值一模一样"** ✓ —— 两件事 ✗。

## 数据从哪来（**无真数据依赖** ✓ ⇒ 能跨机器）
用 `tools/bench_engine.build(120, 260, 3)` ✓：120 只 × 260 日、**固定种子 `20261018`** ✓
⇒ 面板逐位可复现 ✓；再按调仓日编 `bucket`（**多批次**路径 ✓，正是我重构过的那条 ✓）。
本地 **Windows** 与 CI **ubuntu-latest** 跑出同一个 sha1 ⇒ 这就是**跨平台基线** ✓
（同一份 `test_engine_golden.py` 已用这种方式跑了一年多 ✓）。

## ⚠ 换黄金值的唯一正当理由
**有意改变撮合/账目语义** ✓ —— 那就连 `md/change_log.md` 一起写清楚 ✓。
"""
import os
import sys

import pytest

BE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BE)
sys.path.insert(0, os.path.join(BE, "tools"))

from bench_engine import build, checksum                       # noqa: E402
from app.signals.engine import run_batch_backtest              # noqa: E402
from app.signals.period_roll import run_period_roll            # noqa: E402

#: 黄金值（2026-10-22 实测 ✓ v2.11.17 之后）
GOLD = {
    "period_roll": dict(trades=9183, rejects=0, nav_last=1.0551722828, sha="a5f1aa1ec7f05a17"),
    "batch_even": dict(trades=4000, rejects=49, nav_last=1.0161231221, sha="50439002e40802a3"),
}

KW = dict(cost=0.004, capital=1e9, fill="t1_open", strict_limit=True, lot=100)


@pytest.fixture(scope="module")
def panel_sig():
    """合成面板 + **带 bucket 的信号** ✓（固定种子 ⇒ 逐位可复现 ✓）。"""
    panel, sig = build(120, 260, 3)
    sig = sig.copy()
    ranks = {d: i for i, d in enumerate(sorted(sig["date"].unique()))}
    # 每批 = 一个调仓日 ⇒ 走**多批次**路径 ✓（v2.11.7 的 `build_targets` 就在这条上 ✓）
    sig["bucket"] = sig["date"].map(lambda d: "b%03d" % ranks[d])
    return panel, sig


@pytest.mark.parametrize("name,runner", [
    ("period_roll", run_period_roll),
    ("batch_even", run_batch_backtest),
])
def test_strategy_numbers_are_frozen(panel_sig, name, runner):
    """⭐ 成交笔数 / 被拒笔数 / 末值净值 / **NAV+成交明细整条 sha1** 全部冻结 ✓。

    任何"改了撮合或账目语义"的改动都会让它红 ✓ —— 而且报错直接说"是哪个策略的哪个数变了" ✓，
    不需要人眼比对两张净值曲线 ✗。
    """
    panel, sig = panel_sig
    r = runner(sig, panel, **KW)
    g = GOLD[name]

    assert len(r.trades) == g["trades"], (
        "%s 成交笔数变了 ✗（%d ≠ %d）⇒ 撮合语义被改动 ✓" % (name, len(r.trades), g["trades"]))
    assert len(r.rejects) == g["rejects"], (
        "%s 被拒笔数变了 ✗（%d ≠ %d）⇒ 涨跌停/停牌判定被改动 ✓"
        % (name, len(r.rejects), g["rejects"]))

    nav = r.nav
    assert nav is not None and len(nav), "%s 没有产出净值 ✗" % name
    col = nav.columns[-1]
    assert float(nav[col].iloc[-1]) == pytest.approx(g["nav_last"], abs=1e-9), (
        "%s 末值净值变了 ✗（%.10f ≠ %.10f）⇒ 账目语义被改动 ✓"
        % (name, float(nav[col].iloc[-1]), g["nav_last"]))

    got = checksum(r)[:16]
    assert got == g["sha"], (
        "%s 的 NAV+成交明细指纹变了 ✗（%s ≠ %s）\n"
        "  ⇒ 若这是**有意**改口径：请同时更新 GOLD 与 md/change_log.md ✓\n"
        "  ⇒ 若不是：说明某处撮合/成本/整手/权重算错了 ✓" % (name, got, g["sha"]))
