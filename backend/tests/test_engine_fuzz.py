# -*- coding: utf-8 -*-
"""撮合引擎**随机配置 fuzz**（★ 2026-10-19 ✓ —— 不写黄金值，只守"不变量" ✓）。

## 和 `test_engine_golden` 的分工（别重复 ✗）

| | `test_engine_golden` | 本文件 |
|---|---|---|
| 配置来源 | **写死** 6 组 ✓ | **固定种子随机** 12 组 ✓ |
| 断言 | 精确 NAV 指纹 ✓ | **不变量**（两次一致 / 首值 1.0 / 有限值 / 列齐 / 计数自洽 ✓） |
| 能抓什么 | 数字被悄悄改动 ✗ | **崩溃**、**NaN/Inf**、**隐藏随机性**、某组合下的**自相矛盾** ✗ |

⚠ 为什么不用黄金值：12 组 × 每组 4 个指纹 = 48 个数字 ✗ —— 一旦有意改口径就要手工改 48 处 ✗，
维护成本远超收益 ✓。这里只钉**不可能变的东西** ✓。

⚠ 种子写死（`20261019` ✓）⇒ 配置**每次完全一样** ✓，失败可复现 ✓（真随机只会制造 flaky ✗）。
⚠ 面板也小（80 只 × 200 日 ✓）⇒ 12 组跑两遍 ≈ **几秒** ✓，能进 CI ✓。
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

from app.signals.engine import run_backtest  # noqa: E402

N_CASES = 12
SEED = 20261019

#: 参数取值域（都是**真实用户会填的**范围 ✓ —— 刻意避开荒谬值 ✗，那种不算 fuzz 算噪声 ✓）
HOLD = [1, 3, 5, 10, 20, 40]
COST = [0.0, 0.0005, 0.004, 0.01]
CAPITAL = [1e6, 1e7, 1e9]
STRICT = [True, False]
JUDGE = ["legacy", "open_only"]
LOT = [100, 200, 1000]
BAND = [0.0, 0.01, 0.05]
MAXPOS = [0, 10, 30]
ALLOC = [("event_even", "cash_even"), ("event_even",), ("cash_even",)]


def _cases():
    rng = np.random.default_rng(SEED)
    out = []
    for _i in range(N_CASES):
        out.append(dict(
            hold_days=int(rng.choice(HOLD)),
            cost=float(rng.choice(COST)),
            capital=float(rng.choice(CAPITAL)),
            strict_limit=bool(rng.choice(STRICT)),
            limit_judge=str(rng.choice(JUDGE)),
            lot=int(rng.choice(LOT)),
            rebal_band=float(rng.choice(BAND)),
            max_positions=int(rng.choice(MAXPOS)),
            # ⚠ 不能用 `rng.choice(ALLOC)` ✗ —— 里面是**不等长**元组 ✓，
            #   numpy 会报 "inhomogeneous shape" ✓（实测抓到 ✓）⇒ 按下标取 ✓。
            alloc_modes=ALLOC[int(rng.integers(len(ALLOC)))],
            fill="t1_open",
        ))
    return out


CASES = _cases()


def _ids(c):
    return "h%d_c%g_cap%g_%s_%s_lot%d_band%g_pos%d" % (
        c["hold_days"], c["cost"], c["capital"], "S" if c["strict_limit"] else "I",
        c["limit_judge"][:5], c["lot"], c["rebal_band"], c["max_positions"])


@pytest.fixture(scope="module")
def panel_sig():
    return build(80, 200, 3)


def _fp(res):
    nav = np.asarray(res.nav.to_numpy(), dtype="float64")
    return (len(res.trades), len(res.rejects),
            hashlib.sha1(nav.tobytes()).hexdigest()[:16])


@pytest.mark.parametrize("cfg", CASES, ids=[_ids(c) for c in CASES])
def test_random_config_invariants(panel_sig, cfg):
    """⭐ 一组合里同时守四件事 ✓（任何一件不成立都是真问题 ✓）。"""
    panel, sig = panel_sig
    res = run_backtest(sig, panel, **cfg)
    nav = res.nav

    # ① 净值列数与请求的方案数一致 ✓（静默丢一个方案 = 前端少一条曲线 ✗）
    assert len(nav.columns) == len(cfg["alloc_modes"]), (
        "净值列数 %d ≠ 请求的方案数 %d ✗（%s）" % (
            len(nav.columns), len(cfg["alloc_modes"]), cfg))

    # ② 首值必须是 1.0 ✓（初始净值 ✓ —— 不是 1.0 说明"起手就亏/赚"了 ✗）
    first = nav.iloc[0].to_numpy(dtype="float64")
    assert np.allclose(first, 1.0, atol=1e-12), "首值不是 1.0 ✗：%s（%s）" % (first, cfg)

    # ③ 不许出现 NaN / Inf ✓（一旦出现，整条曲线和统计全废 ✗）
    arr = nav.to_numpy(dtype="float64")
    assert np.isfinite(arr).all(), "净值出现 NaN/Inf ✗（%s）" % cfg

    # ④ 计数自洽 ✓：拒单不能是负的、成交不能超过"信号数"的量级 ✓
    assert res.rejects is not None and len(res.rejects) >= 0
    assert 0 <= len(res.trades) <= 10 * len(sig) + 1000, (
        "成交笔数离谱（%d，信号 %d 行）⇒ 可能重复记账 ✗（%s）"
        % (len(res.trades), len(sig), cfg))


@pytest.mark.parametrize("cfg", CASES, ids=[_ids(c) for c in CASES])
def test_random_config_is_deterministic(panel_sig, cfg):
    """⭐ 同一组参数跑两遍必须**逐位一致** ✓ —— 否则所有性能对比都不可信 ✗。"""
    panel, sig = panel_sig
    a = _fp(run_backtest(sig, panel, **cfg))
    b = _fp(run_backtest(sig, panel, **cfg))
    assert a == b, "两次运行不一致 ✗ ⇒ 引擎有隐藏随机性（%s）" % cfg


def test_no_cost_beats_with_cost_on_same_config(panel_sig):
    """常识不变量 ✓：同一配置下 **零成本** 的最终净值 ≥ **有成本** 的 ✓。

    （⚠ 只比"同参、仅成本不同"的两组 ✓ —— 跨配置比净值大小是没有意义的 ✗，
      参数不同、持仓不同、根本不可比 ✓。）
    """
    panel, sig = panel_sig
    base = dict(hold_days=5, fill="t1_open", capital=1e9, strict_limit=True,
                limit_judge="legacy", lot=100, rebal_band=0.0, max_positions=0,
                alloc_modes=("event_even",))
    free = run_backtest(sig, panel, cost=0.0, **base)
    costly = run_backtest(sig, panel, cost=0.004, **base)
    assert float(free.nav.iloc[-1, 0]) >= float(costly.nav.iloc[-1, 0]) - 1e-12, (
        "零成本净值 %.10f 反而低于有成本 %.10f ✗ ⇒ 成本符号搞反或费用被重复扣 ✗"
        % (float(free.nav.iloc[-1, 0]), float(costly.nav.iloc[-1, 0])))
