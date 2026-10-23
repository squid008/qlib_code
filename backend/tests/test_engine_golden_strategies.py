# -*- coding: utf-8 -*-
"""`period_roll` / `batch_even` 的**语义冻结**（★ v2.11.18）—— 补上"没有数值基线"的缺口。

## 为什么补这条
`tests/test_engine_golden.py` 只冻结了 **`run_backtest`** ✓ —— 另外两个策略
（`run_period_roll` / `run_batch_backtest`）**一个指纹都没有** ✗。
而我 v2.11.7 / 2.11.12 / 2.11.13 恰好动过它们的**内部结构**（抽 `build_targets`、
`build_batch_execs`、`init_batch_state` ✓）—— 当时只有**单元测试**护着 ✓，
**没有任何东西能证明"整条链的数字没变"** ✗。

> 单元测试证明"那块逻辑对" ✓；**指纹证明"跑出来的净值一模一样"** ✓ —— 两件事 ✗。

## ⚡⚡ 关键：指纹只算 **NAV**，**不含成交明细行序**（这是 CI 教我的）
第一版我直接复用 `bench_engine.checksum`（NAV + 成交明细 `to_csv` ✓）⇒
**本地 Windows 绿、CI ubuntu 红** ✗ —— 而 `trades` / `rejects` / `nav_last` **全都一致** ✓。
⇒ 结论：**成交明细的"行序"跨平台不稳定** ✗（NAV 逐位相同 ✓）。
⇒ 所以这里**只冻 NAV 序列的 sha1** ✓（与行序无关 ✓），另外把成交/被拒的**条数**冻住 ✓
（条数也是行序无关的 ✓）。
⚠ 想要"整条明细逐位一致"得先让引擎**按稳定键排序** trades ✓ —— 那是另一件事 ✓（未做 ✗）。

## 数据从哪来（**无真数据依赖** ✓）
`tools/bench_engine.build(120, 260, 3)` ✓：120 只 × 260 日、固定种子 `20261018` ✓
⇒ 面板逐位可复现 ✓；再按调仓日编 `bucket`（**多批次**路径 ✓，正是被重构过的那条 ✓）。

## ⚠ 换黄金值的唯一正当理由
**有意改变撮合/账目语义** ✓ —— 那就连 `md/change_log.md` 一起写清楚 ✓。
"""
import hashlib
import os
import sys

import numpy as np
import pytest

BE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BE)
sys.path.insert(0, os.path.join(BE, "tools"))

from bench_engine import build  # noqa: E402

from app.signals.engine import run_batch_backtest  # noqa: E402
from app.signals.period_roll import run_period_roll  # noqa: E402

#: 黄金值（2026-10-22 实测 ✓ v2.11.17 之后）
GOLD = {
    "period_roll": dict(trades=9183, rejects=0, nav_last=1.0551722828,
                        sha="b8cec07074b4ff6c", trades_sha="b19a7c2f1a9bdd1e"),
    "batch_even": dict(trades=4000, rejects=49, nav_last=1.0161231221,
                       sha="a2497ed9268d77ea", trades_sha="0c67399e2de1e17f"),
}

KW = dict(cost=0.004, capital=1e9, fill="t1_open", strict_limit=True, lot=100)


def nav_sha1(nav) -> str:
    """**只**对 NAV 数值 + 列名做指纹 ✓（**不含成交明细行序** ✗，见模块 docstring）。"""
    h = hashlib.sha1()
    h.update(np.asarray(nav.to_numpy(), dtype="float64").tobytes())
    h.update("|".join(map(str, nav.columns)).encode("utf-8"))
    return h.hexdigest()[:16]


def trades_multiset_sha1(tr) -> str:
    """成交明细的**行序无关**哈希 ✓ —— 把每行转成字符串、**排序后**再哈希。

    ⚠ 为什么不直接用 `tr.to_csv()`（`bench_engine.checksum` 那样）✗：
    v2.11.18 实测**本地 Windows 绿、CI ubuntu 红** ✓，而同一用例里的
    `trades`/`rejects`/`nav_last` **全一致** ✓ ⇒ 差异只在**行序** ✗。
    ⇒ 排序后哈希既能抓住"某笔的数量/价格/费用变了"✓，又不依赖平台相关的行序 ✓。
    """
    if tr is None or not len(tr):
        return "empty"
    rows = tr.astype(str).apply(lambda r: "|".join(r.tolist()), axis=1)
    h = hashlib.sha1()
    for line in sorted(rows.tolist()):
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:16]


@pytest.fixture(scope="module")
def panel_sig():
    """合成面板 + **带 bucket 的信号** ✓（固定种子 ⇒ 逐位可复现 ✓》。"""
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
    """⭐ 成交笔数 / 被拒笔数 / 末值净值 / **NAV 序列整条 sha1** 全部冻结 ✓。

    任何"改了撮合或账目语义"的改动都会让它红 ✓ —— 而且报错直接说"哪个策略的哪个数变了" ✓，
    不需要人眼比对两条净值曲线 ✗。
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

    got = nav_sha1(nav)
    assert got == g["sha"], (
        "%s 的 NAV 指纹变了 ✗（%s ≠ %s）\n"
        "  ⇒ 若这是**有意**改口径：请同时更新 GOLD 与 md/change_log.md ✓\n"
        "  ⇒ 若不是：说明某处撮合/成本/整手/权重算错了 ✓" % (name, got, g["sha"]))

    got_tr = trades_multiset_sha1(r.trades)
    assert got_tr == g["trades_sha"], (
        "%s 的成交明细内容变了 ✗（行序无关哈希 %s ≠ %s）\n"
        "  ⇒ 相比 v2.11.19 只数条数，这一条能抓住\"条数没变但某笔数量/价格/费用变了\" ✓\n"
        "  ⇒ 有意改口径就同步更新 GOLD ✓" % (name, got_tr, g["trades_sha"]))


@pytest.mark.parametrize("name,runner", [
    ("period_roll", run_period_roll),
    ("batch_even", run_batch_backtest),
])
def test_trades_are_sorted_for_determinism(panel_sig, name, runner):
    """⭐⭐ **技术债 P2-#21 已清偿（v2.11.21）**：成交明细**必须已按全列排序** ✓。

    为什么（v2.11.18 跨平台指纹实测 ✓）：原来返回引擎**追加顺序** ⇒
    **同一策略、同一份数据，Windows 与 ubuntu 的明细行序不同** ✗
    （NAV 逐位相同 ✓、行序无关哈希也相同 ✓ ⇒ 内容一样、只是顺序 ✗）。
    影响：导出的 XLSX「成交明细」页**行序跨机器不同** ✓ ——
    数值全对 ✓，但"逐位对比两份导出文件"会失败 ✗（对账/回归都受影响 ✓）。

    这里断言"`trades` 已经是有序的" ✓（拿它自己再排一遍必须不变 ✓）——
    这样**换任何平台跑都是同一个顺序** ✓。
    """
    panel, sig = panel_sig
    tr = runner(sig, panel, **KW).trades
    assert tr is not None and len(tr), "%s 没有成交 ✗" % name
    again = tr.sort_values(by=list(tr.columns), kind="stable").reset_index(drop=True)
    assert tr.equals(again), (
        "%s 的成交明细**没有排序** ✗ ⇒ 跨机器行序会不同 ✓（见技术债 P2-#21）" % name)
    # 主键单调：日期非递减 ✓（人读导出时也顺 ✓）
    assert tr["date"].is_monotonic_increasing, "%s 的成交明细应按日期非递减 ✓" % name
