# -*- coding: utf-8 -*-
"""`signals/engine.py` 的"输出成形"纯函数（★ v2.9.97，2026-10-20 ✓ —— 1357 行文件里的 4 小块 ✓）。

## 这几段决定"用户看到什么"

引擎主体（`run_backtest` 614 行 / `run_batch_backtest` 367 行 ✗）本轮不碰 ✓，只钉它们**之后**的成形层 ✓：

1. **`_split_mode`** ✓：`"event_even@0.05"` ⇒ 列名 `event_even_band5` ✓ ——
   死区档位**共用同一个方案**、靠列名区分 ✓（用户 2026-09-15：「给个"死区 5%"的对比曲线」✓）。
   拼错 ⇒ 前端图例里出现两个"事件驱动再平衡"却分不清哪条是哪档 ✗。
2. **`_interleave_rejects`** ✓：**轮转**而不是"拼完截断" ✗ —— 用户 2026-09-16 报过
   「导出里没有分批次分片的结果」✓：拼接后截断 ⇒ **靠后的方案一条都看不到** ✗
   （实测 12 年 + 严格口径下 `batch_even` 自己有 3 万多条 ✓ 会把名额占满 ✗）。
3. **`attach_benchmark`** ✓：基准必须**归一到与策略同一净值起点 1.0** ✓，
   否则"策略 1.07 / 基准 1.02"这种对比**看着像基准输了** ✗（其实是起点不同 ✓）。
4. **`nav_rows`** ✓：`NaN → None` ✓ —— 前端据此**断线**而不是"猜一个数"✗（猜数会把缺口连成假趋势 ✓）；
   数值 `round(5)` ✓、日期统一 `YYYY-MM-DD` ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals.engine import _interleave_rejects, _split_mode, attach_benchmark, nav_rows


# ------------------------------------------------------------------ _split_mode
def test_split_mode_plain():
    assert _split_mode("event_even") == ("event_even", None, "event_even")


@pytest.mark.parametrize("spec,band,col", [
    ("event_even@0.05", 0.05, "event_even_band5"),
    ("event_even@0.1", 0.1, "event_even_band10"),
    ("cash_even@0.025", 0.025, "cash_even_band2p5"),
])
def test_split_mode_with_dead_band(spec, band, col):
    """⭐ 死区档位必须体现在**列名**里 ✓（否则前端图例分不清哪条是哪档 ✗）。"""
    base, b, name = _split_mode(spec)
    assert b == pytest.approx(band)
    assert name == col
    assert base == spec.split("@")[0]
    assert "." not in name, "列名里不能留小数点 ✓（`2.5` ⇒ `2p5` ✓）"


# --------------------------------------------------------- _interleave_rejects
def _items(n, tag):
    return [{"mode": tag, "i": i} for i in range(n)]


def test_interleave_single_mode_takes_prefix():
    out = _interleave_rejects({"a": _items(10, "a")}, 3)
    assert [x["i"] for x in out] == [0, 1, 2]


def test_interleave_rotates_so_later_modes_are_not_starved():
    """⭐⭐ 用户 2026-09-16 那个 bug：`a` 有很多条时，`b` 一条都不能少 ✓。"""
    out = _interleave_rejects({"a": _items(1000, "a"), "b": _items(2, "b")}, 6)
    tags = [x["mode"] for x in out]
    # ⚠ 断言消息里**不许**嵌 ASCII 双引号 ✓ —— 今天已经因为这条 SyntaxError 三次 ✗（改「」✓）
    assert "b" in tags, "靠后的方案被饿死 ✗（这正是当年「导出里没有分批次分片」的原因 ✓）"
    assert tags.count("b") == 2, "b 只有 2 条 ⇒ 两条都该出现 ✓"
    assert tags[0] == "a" and tags[1] == "b", "应当轮转取 ✓"


def test_interleave_respects_cap_and_total():
    out = _interleave_rejects({"a": _items(50, "a"), "b": _items(50, "b")}, 7)
    assert len(out) == 7
    assert sum(1 for x in out if x["mode"] == "a") in (3, 4)


def test_interleave_empty_and_zero_cap():
    assert _interleave_rejects({}, 5) == []
    assert _interleave_rejects({"a": []}, 5) == []
    assert _interleave_rejects({"a": _items(3, "a")}, 0) == []


def test_interleave_terminates_when_all_exhausted():
    """全部取完 ⇒ 正常结束 ✓（不会死循环 ✗ —— 循环体里 `keys.remove` 是唯一出口 ✓）。"""
    out = _interleave_rejects({"a": _items(2, "a"), "b": _items(1, "b")}, 100)
    assert len(out) == 3


# ----------------------------------------------------------- attach_benchmark
def _nav(dates, vals):
    return pd.DataFrame({"cash": vals}, index=pd.DatetimeIndex(dates))


def test_attach_benchmark_noop_cases():
    nav = _nav(["2024-01-02", "2024-01-03"], [1.0, 1.01])
    assert attach_benchmark(nav, None, "SH000300") is nav          # 没基准 ✓
    assert attach_benchmark(nav, pd.DataFrame({"OTHER": [1.0, 1.0]},
                                              index=nav.index), "SH000300") is nav
    empty = nav.iloc[0:0]
    assert attach_benchmark(empty, pd.DataFrame({"SH000300": []}, index=empty.index),
                            "SH000300") is empty


def test_attach_benchmark_normalizes_to_one_and_ffills():
    """⭐ 基准必须从 **1.0** 起 ✓（否则"策略 1.07 vs 基准 0.98"看着像基准输了 ✗）。"""
    nav = _nav(["2024-01-02", "2024-01-03", "2024-01-04"], [1.0, 1.02, 1.05])
    bench = pd.DataFrame({"SH000300": [98.0, np.nan, 102.9]}, index=nav.index)
    out = attach_benchmark(nav, bench, "SH000300")
    assert out is not nav, "应当返回副本 ✓（不改入参 ✓）"
    assert float(out["benchmark"].iloc[0]) == pytest.approx(1.0)
    # 中间缺失被 ffill ⇒ 仍是 1.0 ✓（不是 NaN ✗）
    assert float(out["benchmark"].iloc[1]) == pytest.approx(1.0)
    assert float(out["benchmark"].iloc[2]) == pytest.approx(102.9 / 98.0)
    # 策略列原样保留 ✓
    assert list(out["cash"]) == [1.0, 1.02, 1.05]


def test_attach_benchmark_all_nan_returns_unchanged():
    nav = _nav(["2024-01-02"], [1.0])
    bench = pd.DataFrame({"SH000300": [np.nan]}, index=nav.index)
    assert attach_benchmark(nav, bench, "SH000300") is nav


# -------------------------------------------------------------------- nav_rows
def test_nav_rows_shape_rounding_and_nan_to_none():
    """⭐ `NaN → None` ✓ —— 前端据此**断线** ✓（填 0 或前值会把缺口连成假趋势 ✗）。"""
    nav = pd.DataFrame(
        {"cash": [1.0, 1.23456789, np.nan], "event": [1.0, 1.0, 1.01]},
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    rows = nav_rows(nav)
    assert [r["date"] for r in rows] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert rows[0] == {"date": "2024-01-02", "cash": 1.0, "event": 1.0}
    assert rows[1]["cash"] == pytest.approx(1.23457)          # 5 位 ✓
    assert rows[2]["cash"] is None, "NaN 必须是 None ✓（不是 0、也不是字符串 ✗）"
    assert rows[2]["event"] == 1.01


def test_nav_rows_empty_frame():
    assert nav_rows(pd.DataFrame()) == []
