# -*- coding: utf-8 -*-
"""**绝对**性能护栏（★ v2.11.23）—— 补上"只有相对比值门"的缺口。

## ⚠ v2.11.60：整文件打了 `perf` 标记 ⇒ CI **单独串行跑** ✓
外部评审实测：全量跑时 `test_batch_even_speed_is_guarded` 量到 **7.52s**，
而上限是 `3 × 2.464 + 0.2 = 7.59s` ✓ —— **只差 0.07s**，属于"随时会红" ✓。
原因不是阈值不合理 ✓，而是**它和 1900 条用例交错跑** ⇒ 计时被别人的负载污染 ✗
（`_best_of` 取最小值已经能避开**短时抖动** ✓，但避不开**持续负载** ✗）。
⇒ 正解是**隔离**（`pytest -m perf` 单独一步 ✓），而**不是**把阈值放宽到 5× ✗ ——
放宽等于削弱护栏 ✓（拖慢 2 倍就不红了 ✗）。
⚠ 我改这段时**误加了一个三引号把本文档字符串提前闭合** ✗ ⇒ 后面的正文变成代码
  ⇒ `SyntaxError: invalid character '：'` ✓（全角冒号在代码里 ✓）。
  修的时候又犯一次：**在正文里写那三个引号本身也会闭合它** ✗
  ⇒ `SyntaxError: invalid character '✗'`（这一行 ✓）。
  教训：**在 docstring 正文里既不能带结束引号，也不能"引用"它** ✗ —— 要说得绕一点 ✓。

## 为什么需要它
原来只有 `tests/test_perf_relative.py`：它比的是"两次实现谁快" ✓（**相对**）。
但"**某次改动把一条路径拖慢 2 倍**"它**看不见** ✗ ——
因为比值没变、只是绝对值变大了 ✓。这类退化不会报错、不会有任何测试红 ✗，
只会让用户某天觉得"怎么这么慢"✓（本项目已经因为这种"静默变慢"排查过好几轮 ✓）。

## 三条基准（全部**合成数据** ✓ ⇒ 无真数据依赖、能跨机器跑 ✓）
| # | 路径 | 规模 | 本地基线 |
|---|---|---|---|
| 1 | `run_backtest` | 120 只 × 260 日 | **0.045s** |
| 2 | `run_period_roll` | 同上 + 77 批次 | **0.128s** |
| 3 | `run_batch_backtest` | 同上 + 77 批次 | **2.464s** ⚠ |
| 4 | 算子求值（`Mean/Std/Corr` 组合） | 500 只 × 500 日 | **0.466s** |

⚠ **3 比 1 慢 55 倍** ✓ —— 这是**已知的相对开销**（`batch_even` 要按批次逐日重建 ✓），
不是缺陷 ✓；这里只保证它**别继续变慢** ✓。

## 阈值怎么定的（**故意宽松** ✓）
`上限 = 3 × 基线 + 0.2s` ✓：
· `3×` 抓"真回归"（拖慢 2 倍必红 ✓）；
· `+0.2s` 给**小数值**绝对余量 ✓（0.045s 的 3 倍只有 0.135s，CI 抖一下就红 ✗）；
· 取 **best-of-3** ✓（避开偶发抖动 ✓）。

⚠ 若某天它红了：**先看是不是真回归** ✓；若确认不是，说明机器比基线慢 3 倍以上 ✓ ——
那就把实测耗时附在 PR 里调阈值 ✓（**不要**直接删测试 ✗）。
"""
import os
import sys
import time
from collections import OrderedDict

import numpy as np
import pandas as pd
import pytest

BE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BE)
sys.path.insert(0, os.path.join(BE, "tools"))

from bench_engine import build  # noqa: E402

# ★ v2.11.60：**整文件打 `perf`** ✓ —— 这些用例量的是**绝对耗时** ✓，
#   与 1900 条其它用例交错跑时会被负载污染 ✗（实测 `batch_even` 量到 7.52s，
#   上限 7.59s ⇒ 只差 0.07s ✓）。CI 里改成 `pytest -m perf` **单独一步串行** ✓。
pytestmark = pytest.mark.perf

from app.factors import panel_expr as P  # noqa: E402
from app.signals.engine import run_backtest, run_batch_backtest  # noqa: E402
from app.signals.period_roll import run_period_roll  # noqa: E402

#: 本地基线（秒）—— 2026-10-22 实测 ✓
BASE = {
    "run_backtest": 0.045,
    "run_period_roll": 0.128,
    "run_batch_backtest": 2.464,
    "eval_500x500": 0.466,
}

KW = dict(cost=0.004, capital=1e9, fill="t1_open", strict_limit=True, lot=100)
EXPR = "Mean($close,20)/$close + Std($close,20) + Corr($close,$volume,10)"


def _limit(name: str) -> float:
    """`3 × 基线 + 0.2s` ✓（小数值给绝对余量，见模块 docstring）。"""
    return 3.0 * BASE[name] + 0.2


def _best_of(fn, n=3) -> float:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return min(ts)


def _assert_faster(name: str, fn) -> None:
    # ⚠ 慢的那条只取 2 次 ✓（`batch_even` 单次 2.5s ⇒ 3 次就 7.5s，
    #   会把整个测试套拖慢 25% ✗；2 次已足够避开抖动 ✓）
    got = _best_of(fn, n=2 if BASE[name] > 1.0 else 3)
    lim = _limit(name)
    assert got <= lim, (
        "%s 变慢了 ✗：实测 **%.3fs** ＞ 上限 %.3fs（基线 %.3fs）\n"
        "  ⇒ 若这是真回归：找这次改动里新增的循环/拷贝/重算 ✓\n"
        "  ⇒ 若确认没回归：说明本机比基线慢 3 倍以上 ✓ ⇒ 附实测耗时来调阈值（**不要删测试** ✗）"
        % (name, got, lim, BASE[name]))


@pytest.fixture(scope="module")
def panel_sig():
    """合成面板 + 带 bucket 的信号（固定种子 ⇒ 可复现 ✓）。"""
    panel, sig = build(120, 260, 3)
    sig = sig.copy()
    ranks = {d: i for i, d in enumerate(sorted(sig["date"].unique()))}
    sig["bucket"] = sig["date"].map(lambda d: "b%03d" % ranks[d])
    return panel, sig


def _evaluator(n_days=500, n_codes=500):
    """手工构造求值器 ✓（绕开 qlib 读盘 ✓，只量**算子层**）。"""
    days = pd.bdate_range("2020-01-01", periods=n_days)
    codes = ["S%04d" % i for i in range(n_codes)]
    idx = pd.MultiIndex.from_product([codes, days], names=["instrument", "datetime"])
    rng = np.random.default_rng(7)
    ev = object.__new__(P.PanelEvaluator)
    ev._full = idx
    ev._field_cache = {
        "close": pd.Series(rng.uniform(5, 50, len(idx)), index=idx, dtype=np.float64),
        "volume": pd.Series(rng.uniform(1e5, 1e7, len(idx)), index=idx, dtype=np.float64),
    }
    ev._node_cache = OrderedDict()
    ev._node_bytes = 0
    ev._active_cache = {}
    ev._active_pos = {}
    ev.read_start = "2020-01-01"
    return ev


def test_backtest_speed_is_guarded(panel_sig):
    """① 回测主循环 ✓（最热的路径：每次「跑回测」都走它 ✓）。"""
    panel, sig = panel_sig
    _assert_faster("run_backtest", lambda: run_backtest(
        sig, panel, hold_days=10, alloc_modes=("cash_even", "event_even"), **KW))


def test_period_roll_speed_is_guarded(panel_sig):
    """② 每期全仓滚动 ✓（v2.11.6~7 拆过它的内部结构 ⇒ 顺手把性能也钉住 ✓）。"""
    panel, sig = panel_sig
    _assert_faster("run_period_roll", lambda: run_period_roll(sig, panel, **KW))


def test_batch_even_speed_is_guarded(panel_sig):
    """③ `batch_even` ✓ —— ⚠ 它比 ① 慢 **55 倍**（按批次逐日重建 ✓），这是已知相对开销 ✓，
    只保证别再变慢 ✓。"""
    panel, sig = panel_sig
    _assert_faster("run_batch_backtest", lambda: run_batch_backtest(sig, panel, **KW))


def test_operator_layer_speed_is_guarded():
    """④ 算子求值（`Mean/Std/Corr` 组合 × 500 只 × 500 日）✓ ——
    ⚠ **每次新建求值器** ✗→✓：`PanelEvaluator` 有 `_node_cache` ✓，
    复用同一个实例时第二次直接命中缓存 ✓ ⇒ 量出来 0.000s ✗（等于没测 ✓）。"""
    node = P.parse_expr(EXPR)
    _assert_faster("eval_500x500", lambda: _evaluator().eval(node))
