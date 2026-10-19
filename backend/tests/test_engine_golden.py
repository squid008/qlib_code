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


# ===========================================================================
# ★★ 2026-10-19：**多配置矩阵**（上面的单点冻结只守"默认口径"✗）
#
# 为什么加：v2.9.34~.63 这几十轮里，改的都是"别的分支"（成本、每手股数、资金约束、
#   调仓带、持仓上限 ✓）—— 单点冻结对它们**完全没有保护** ✗。
# 每个配置的指纹都是**两次运行一致**才收进来的 ✓（见 `test_matrix_is_deterministic` ✓，
#   它同时证明"引擎没有隐藏随机性"✓ —— 否则这种冻结测试就是定时炸弹 ✗）。
#
# ⚠ 有意改口径时：**同时**改这里的 GOLDEN 与 `md/change_log.md` ✓。
# ⚠ `limit_judge="open_only"` **没有**进矩阵 ✓：实测它的指纹与 `base` **完全相同** ✓
#   （合成行情里涨跌停档从不触发 ⇒ 区分不出来 ✗）⇒ 放进来只是多跑一遍、骗自己 ✓。
# ===========================================================================
GOLDEN = {
    # ↑ 默认配置（与上面旧的 GOLD 一致 ✓）
    "base": dict(trades=480, rejects=0, nav_sha1="d2b30ed3ab2f1ee4",
                 nav_last=[1.0759942421, 1.0759942421]),
    # ↑ hold_days=5（更高换手 ✓；注意 rejects 从 0 变 2 ✓ —— 这段逻辑以前没被任何数字守过 ✓）
    "hold5": dict(trades=480, rejects=2, nav_sha1="765fe534f396b6ab",
                  nav_last=[1.0763598249, 1.0763598249]),
    # ↑ cost=0.0（无成本档 ✓）
    "no_cost": dict(trades=480, rejects=0, nav_sha1="899f614c6b8e3f02",
                    nav_last=[1.0803066751, 1.0803066751]),
    # ↑ capital=1e6（资金约束档 ✓ —— 小资金下买入会被摊薄 ✓）
    "small_capital": dict(trades=480, rejects=0, nav_sha1="1b10955d03d76e58",
                          nav_last=[1.0726224963, 1.0726224963]),
    # ↑ lot=200（非 100 的整数倍 ✓ 每手股数口径 ✓）
    "lot200": dict(trades=480, rejects=0, nav_sha1="d21cd1ce2cc9e393",
                   nav_last=[1.0759886583, 1.0759886583]),
    # ↑ rebal_band=0.02 + max_positions=20（调仓带 + 持仓上限 ✓：
    #   成交骤降到 80 笔、拒单 15,400 笔 ✓ —— 这是**拒单路径**唯一被数字守住的配置 ✓）
    "band_pos": dict(trades=80, rejects=15400, nav_sha1="8d443207c0067a24",
                     nav_last=[1.1213528655, 1.1213528655]),
}

#: 每个配置相对 base 的关键字覆盖 ✓（base 自身不覆盖任何东西 ✓）
MATRIX_KW = {
    "base": {},
    "hold5": {"hold_days": 5},
    "no_cost": {"cost": 0.0},
    "small_capital": {"capital": 1e6},
    "lot200": {"lot": 200},
    "band_pos": {"rebal_band": 0.02, "max_positions": 20},
}


def _fp(res) -> dict:
    """成交笔数 / 拒单数 / NAV 整条指纹 / 每个资金方案的末值 ✓。"""
    nav = np.asarray(res.nav.to_numpy(), dtype="float64")
    return {
        "trades": len(res.trades),
        "rejects": len(res.rejects),
        "nav_sha1": __import__("hashlib").sha1(nav.tobytes()).hexdigest()[:16],
        "nav_last": [round(float(x), 10) for x in res.nav.iloc[-1].to_numpy()],
    }


@pytest.fixture(scope="module")
def _panel_sig():
    return build(120, 260, 3)


def _run(_panel_sig, name):
    panel, sig = _panel_sig
    kw = dict(hold_days=10, fill="t1_open", cost=0.004, capital=1e9,
              strict_limit=True, alloc_modes=("cash_even", "event_even"))
    kw.update(MATRIX_KW[name])
    return run_backtest(sig, panel, **kw)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_matrix_fingerprints(_panel_sig, name):
    got = _fp(_run(_panel_sig, name))
    want = GOLDEN[name]
    assert got["nav_sha1"] == want["nav_sha1"], (
        "配置 `%s` 的 NAV 指纹变了 ✗（%s ≠ %s）⇒ 撮合/账目语义被改动 ✓\n"
        "  ⚠ 若是有意改口径：请同时改 GOLDEN 与 md/change_log.md ✓"
        % (name, got["nav_sha1"], want["nav_sha1"]))
    assert got["trades"] == want["trades"], (
        "配置 `%s` 成交笔数变了 ✗（%d ≠ %d）" % (name, got["trades"], want["trades"]))
    assert got["rejects"] == want["rejects"], (
        "配置 `%s` 拒单数变了 ✗（%d ≠ %d）—— 拒单口径（资金/涨跌停/持仓上限）被改动 ✓"
        % (name, got["rejects"], want["rejects"]))
    for i, v in enumerate(want["nav_last"]):
        assert abs(got["nav_last"][i] - v) < 1e-9, (
            "配置 `%s` 第 %d 个资金方案的末值变了 ✗（%.10f ≠ %.10f）"
            % (name, i, got["nav_last"][i], v))


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_matrix_is_deterministic(_panel_sig, name):
    """⭐ **同一配置跑两遍必须逐位一致** ✓ —— 否则上面那套冻结测试就是定时炸弹 ✗。

    （实测 6 个配置全部一致 ✓；引擎里若混进 `random` / 未固定种子的抽样 ✓ 这里会先红 ✓。）
    """
    a = _fp(_run(_panel_sig, name))
    b = _fp(_run(_panel_sig, name))
    assert a == b, "配置 `%s` 两次运行不一致 ✗ ⇒ 引擎有隐藏随机性 ✓" % name


def test_matrix_covers_both_alloc_modes(_panel_sig):
    """两个资金方案都必须有列 ✓（只出 1 列 = 某个方案被静默丢掉 ✗）。"""
    res = _run(_panel_sig, "base")
    cols = [str(c) for c in res.nav.columns]
    assert len(cols) == 2, "应有两个资金方案的净值列 ✓，实际 %s ✗" % cols
    assert any("cash" in c for c in cols) and any("event" in c for c in cols)
