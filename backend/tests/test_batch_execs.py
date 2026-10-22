# -*- coding: utf-8 -*-
"""`build_batch_execs`：`batch_even` 的信号折叠（★ v2.11.12 抽出后能单测）。

## 为什么这四语义值得钉
折叠错一步，回测**不会报错**，只会"少买几只/晚买一天"✓ —— 而净值曲线**看着依然合理** ✗。

| 语义 | 错了会怎样 |
|---|---|
| ⭐ **卖出信号一律忽略** | 与"移出名单 ⇒ 清仓"**重复卖** ⇒ 甚至卖成负仓 ✗ |
| ⭐ **`defer` 只是统计、不丢弃** | 周末/停牌的信号被 `continue` 掉 ⇒ **整批凭空消失** ✓，用户只看到"某些调仓日没动" ✗ |
| ⭐⭐ **执行日钳到最后一根** `min(n_row-1, p+lag)` | 最后一批**永远不成交** ⇒ 回测尾部缺一段 ✗ |
| ⭐ **同 (批次, 日) 权重累加** | 同一只票出现两次 ⇒ 权重被覆盖而非相加 ✗ |
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals.batch_execs import build_batch_execs, NO_SIGNAL_ERROR

CAL = pd.DatetimeIndex(pd.bdate_range("2024-01-02", periods=5))   # 5 个交易日
COL_OF = {"AAA": 0, "BBB": 1}
N = len(CAL)


def _sig(rows):
    """rows = [(date, code, side, bucket?, weight?)]"""
    d = {"date": [r[0] for r in rows], "code": [r[1] for r in rows], "side": [r[2] for r in rows]}
    if len(rows[0]) > 3:
        d["bucket"] = [r[3] for r in rows]
    if len(rows[0]) > 4:
        d["weight"] = [r[4] for r in rows]
    return pd.DataFrame(d)


def _run(rows, lag=1):
    return build_batch_execs(_sig(rows), col_of=COL_OF, cal=CAL, n_row=N, lag=lag)


def test_sell_signals_are_ignored():
    """⭐ **卖出（`side<=0`）一律忽略** ✓ —— 卖出由"移出名单"自动产生 ✓，
    照单执行会**重复卖** ✓（甚至卖成负仓 ✗）。"""
    buckets, execs, i_start, defer, beyond, n_map, _n_ref, err = _run([
        ("2024-01-02", "AAA", 1, "b1"),
        ("2024-01-02", "BBB", -1, "b1"),      # 卖出 ⇒ 忽略 ✓
    ])
    assert err is None
    assert buckets == ["b1"]
    assert n_map == 1, "只有 1 条买入被映射 ✓（卖出不计 ✗）"
    assert execs == {1: [("b1", {0: 1.0}, 0)]}


def test_non_trading_day_signals_are_deferred_not_dropped():
    """⭐⭐ **非交易日（周末）的信号仍然记账** ✓，只把 `defer` +1 ✓ ——
    若 `continue` 掉 ⇒ **整批凭空消失** ✓，用户只看到"某些调仓日没动" ✗。"""
    buckets, execs, i_start, defer, beyond, n_map, _n_ref, err = _run([
        ("2024-01-06", "AAA", 1, "b1"),       # 周六 ⇒ 不在 CAL 里 ✓
    ])
    assert err is None, "非交易日**不等于**没有信号 ✓（不许走 no-signal 分支 ✗）"
    assert defer == 1, "要计 1 条 defer ✓（便于排查 ✓）"
    assert n_map == 1, "但它**必须仍然被映射** ✓（不是丢弃 ✗）"
    # ⚠ 实测：`searchsorted(周六)` 落到**下一个交易日**（`CAL[4]` = 周一 2024-01-08 ✓），
    #   再 `+lag=1` ⇒ 5 ⇒ 被**钳到最后一根** `N-1 = 4` ✓。
    #   （我第一版按"周一 = idx 2"算 ⇒ 期望 2 ✗ —— 忘了 `bdate_range` 跳过周末 ✓）
    assert list(execs) == [N - 1]


def test_signals_outside_calendar_are_dropped():
    """⭐ 早于日历起点 / 晚于终点的信号 ⇒ **确实丢弃** ✓ 并计入 `beyond` ✓
    （它们没有可对应的行 ✓，与 `defer` 不同 ✗）。"""
    _b, execs, _i, defer, beyond, n_map, _n, err = _run([
        ("2023-12-01", "AAA", 1, "b1"),       # 早于起点 ✓
        ("2024-02-01", "BBB", 1, "b1"),       # 晚于终点 ✓
    ])
    assert err == NO_SIGNAL_ERROR
    assert execs == {} and n_map == 0
    assert beyond == 2 and defer == 0


def test_exec_day_is_clamped_to_last_row():
    """⭐⭐ **执行日钳到最后一根** ✓ —— 最后一天的信号 `+lag` 越界时落在**最后一个交易日** ✓，
    而不是被丢掉 ✓（丢掉的话"最后一批永远不成交" ✗，回测尾部缺一段 ✓）。"""
    last = CAL[N - 1]
    _b, execs, _i, _d, _by, _nm, _nr, err = _run([(last.strftime("%Y-%m-%d"), "AAA", 1, "b1")], lag=2)
    assert err is None
    assert list(execs) == [N - 1], "必须钳到最后一根 ✓"


def test_weights_accumulate_per_bucket_and_day():
    """⭐ 同一 `(批次, 日)` 里同一只票出现两次 ⇒ **权重相加** ✓（不是覆盖 ✗）；
    `weight` 缺失 ⇒ 按 1.0 ✓。"""
    # ⚠ 夹具要求**每行长度一致** ✗→✓（我第一版最后一行少给一个字段 ⇒ `IndexError` ✓）：
    #   这里显式给 1.0 ✓；"`weight` 列缺失 ⇒ 默认 1.0" 由下面的独立断言覆盖 ✓。
    _b, execs, _i, _d, _by, _nm, _nr, err = _run([
        ("2024-01-02", "AAA", 1, "b1", 0.3),
        ("2024-01-02", "AAA", 1, "b1", 0.4),
        ("2024-01-02", "BBB", 1, "b1", 1.0),
    ])
    assert err is None
    _buckets, lst = next(iter(execs.items()))
    _bname, names, _p = lst[0]
    assert names[0] == pytest.approx(0.7), "0.3 + 0.4 ✓"
    assert names[1] == pytest.approx(1.0)


def test_zero_weight_still_occupies_a_slot():
    """⭐ 权重 0（或负）⇒ 被抬到 **1e-9** ✓ ⇒ **仍占一个位置** ✓ ——
    "名单里有它但权重≈0" 与 "不在名单里" 语义不同 ✓（后者会被清仓 ✓）。"""
    _b, execs, _i, _d, _by, _nm, _nr, err = _run([("2024-01-02", "AAA", 1, "b1", 0.0)])
    assert err is None
    names = next(iter(execs.values()))[0][1]
    assert names[0] > 0 and names[0] < 1e-6, "必须 >0 ✓（否则就退化成「不在名单」✗）"


def test_multiple_buckets_and_refresh_count():
    """`buckets` 升序 ✓、`n_refresh` = **`(批次,日)` 去重后的条数** ✓、`i_start` = 最早执行日 ✓。"""
    buckets, execs, i_start, _d, _by, _nm, n_ref, err = _run([
        ("2024-01-02", "AAA", 1, "b2"),
        ("2024-01-02", "BBB", 1, "b1"),
        ("2024-01-03", "AAA", 1, "b1"),
    ])
    assert err is None
    assert buckets == ["b1", "b2"], "必须升序 ✓（均分依赖顺序稳定 ✓）"
    assert n_ref == 3, "3 个 (批次,日) ✓"
    assert i_start == 1, "最早执行日 = 第 0 天 + lag ✓"
    assert set(execs) == {1, 2}


def test_unknown_codes_are_skipped():
    """代码不在行情里 ⇒ 跳过 ✓（`col_of` 缺它 ✓）—— 否则逐日循环下标越界 ✗。"""
    _b, execs, _i, _d, _by, n_map, _nr, err = _run([("2024-01-02", "ZZZ", 1, "b1")])
    assert err == NO_SIGNAL_ERROR and execs == {} and n_map == 0
