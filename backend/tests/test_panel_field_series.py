# -*- coding: utf-8 -*-
"""字段读成面板 + 并集布局（★ v2.10.73，2026-10-21 ✓ —— 技术债 #2 的第六刀）。

## 测的是"从 .bin 到面板"这一步，也是**最容易错位**的一步

两个函数的输入都是「起始日历下标 `start_idx` + 数据数组」✓，输出是
`MultiIndex(instrument, datetime)` 的面板 ✓。这中间只有**减法**：
`seg = vals[lo - start_idx : hi - start_idx + 1]` ✓ ——
**偏移算错 1 天 ⇒ 整条序列错位** ✓，而面板**看起来完全正常** ✗。

## ⭐⭐ `_union_layout` 的关键语义：**多字段覆盖要取并集**

不同字段的 `.day.bin` 覆盖范围**可能不同** ✓（长期停牌股的 `close` 止于某日，
而 `limit_up` / `is_st` 这类标签覆盖更长 ✓）。qlib 的 `D.features` 对多列是**取 union 行集** ✓
⇒ 只拿单字段算索引 ⇒ **标签会被截断** ✓，而截断是**静默**的 ✗
（表现为"某些天没有信号"，而不是报错 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as P

DAYS = 10
CAL = pd.bdate_range("2024-01-02", periods=DAYS)


@pytest.fixture()
def feat(tmp_path, monkeypatch):
    """临时 `_feature_dir` + 固定日历 ✓（真实实现要读 qlib 的 `calendars/day.txt` ✗）。"""
    monkeypatch.setattr(P, "_feature_dir", lambda: str(tmp_path))
    monkeypatch.setattr(P, "_calendar", lambda: CAL)
    monkeypatch.setattr(P, "_MERGED_ENABLED", False)
    P.clear_bin_cache()
    P._BIN_META_CACHE.clear()
    P._DIR_META_CACHE.clear()
    yield tmp_path
    P.clear_bin_cache()
    P._BIN_META_CACHE.clear()
    P._DIR_META_CACHE.clear()


def write_bin(root, inst: str, field: str, start: int, values):
    d = root / inst
    d.mkdir(exist_ok=True)
    arr = np.concatenate([[float(start)], np.asarray(values, dtype=np.float32)])
    (d / f"{field}.day.bin").write_bytes(arr.astype("<f4").tobytes())


# ------------------------------------------------------- load_field_series
def test_load_single_inst_maps_dates_from_start_idx(feat):
    """⭐ 起始下标 `start=2` + 4 个值 ⇒ 落在 `cal[2..5]` 上 ✓（偏移算错就整体错位 ✗）。"""
    write_bin(feat, "SH600000", "close", 2, [10.0, 11.0, 12.0, 13.0])
    s = P.load_field_series(["SH600000"], "close", CAL[0], CAL[-1])
    assert len(s) == 4
    assert list(s.index.get_level_values(1)) == list(CAL[2:6])
    assert list(s.to_numpy()) == [10.0, 11.0, 12.0, 13.0]


def test_load_clips_to_requested_window(feat):
    """⭐⭐ 请求窗口只覆盖中间两天 ⇒ **必须按偏移裁出那两天** ✓
    （裁错 1 天 ⇒ 用户选的区间和实际算的数据不一致 ✓，且看不出 ✗）。"""
    write_bin(feat, "SH600000", "close", 2, [10.0, 11.0, 12.0, 13.0])   # cal[2..5]
    s = P.load_field_series(["SH600000"], "close", CAL[3], CAL[4])       # 只要 cal[3], cal[4]
    assert list(s.index.get_level_values(1)) == list(CAL[3:5])
    assert list(s.to_numpy()) == [11.0, 12.0], "对应 vals[1] 与 vals[2] ✓"


def test_load_returns_empty_when_no_overlap(feat):
    """数据完全在请求区间之外 ⇒ **空 Series** ✓（不是抛、也不是全 NaN 行 ✗）。"""
    write_bin(feat, "SH600000", "close", 8, [1.0, 2.0])                  # cal[8..9]
    s = P.load_field_series(["SH600000"], "close", CAL[0], CAL[3])
    assert isinstance(s, pd.Series) and s.empty
    assert s.dtype == np.float64


def test_load_skips_instruments_without_file(feat):
    """⭐ 没落盘的股票 ⇒ **直接跳过** ✓，不许为它造 NaN 行 ✗
    （造 NaN 行会让"覆盖天数"统计和 `_union_layout` 不一致 ✓）。"""
    write_bin(feat, "SH600000", "close", 0, [1.0, 2.0])
    s = P.load_field_series(["SH600000", "SH999999"], "close", CAL[0], CAL[-1])
    assert set(s.index.get_level_values(0)) == {"SH600000"}
    assert len(s) == 2


def test_load_multi_inst_is_sorted_by_instrument(feat):
    """多股 ⇒ **按 instrument 排序** ✓（与传入顺序无关 ✓ —— 下游按 `_full` 对齐要靠它 ✓）。"""
    write_bin(feat, "SZ000002", "close", 0, [3.0])
    write_bin(feat, "SH600000", "close", 0, [1.0])
    s = P.load_field_series(["SZ000002", "SH600000"], "close", CAL[0], CAL[-1])
    assert list(s.index.get_level_values(0)) == ["SH600000", "SZ000002"]


# ------------------------------------------------------- _union_layout
def test_union_layout_single_field(feat):
    """单字段 ⇒ layout 的 `(row0,row1,cal_lo)` 必须自洽 ✓：行长 = 覆盖天数 ✓，且累加等于总行数 ✓。"""
    write_bin(feat, "SH600000", "close", 2, [1.0, 2.0, 3.0])             # cal[2..4]
    idx, layout, req_lo, req_hi = P._union_layout(["SH600000"], CAL[0], CAL[-1], ("close",))
    assert len(idx) == 3
    assert len(layout) == 1
    inst, row0, row1, cal_lo = layout[0]
    assert (inst, row0, row1, cal_lo) == ("SH600000", 0, 3, 2)
    assert (req_lo, req_hi) == (0, DAYS - 1)


def test_union_layout_takes_union_of_field_coverage(feat):
    """⭐⭐⭐ **多字段覆盖取并集** ✓ —— 这是本节最该钉的一条 ✓。

    `close` 只覆盖 `cal[2..5]`（长期停牌 ✓），`limit_up` 覆盖 `cal[0..9]` ✓
    ⇒ 该股在并集上占 **10 行** ✓。
    ⚠ 若按"单字段"或"交集"算 ⇒ 只有 4 行 ✗ ⇒ **标签被静默截断** ✓
    （表现为"某些天没有信号"✓，而不是报错 ✗）。
    """
    write_bin(feat, "SH600000", "close", 2, [1.0, 2.0, 3.0, 4.0])         # cal[2..5]
    write_bin(feat, "SH600000", "limit_up", 0, [0.0] * 10)                # cal[0..9]
    idx, layout, _lo, _hi = P._union_layout(
        ["SH600000"], CAL[0], CAL[-1], ("close", "limit_up"))
    inst, row0, row1, cal_lo = layout[0]
    assert row1 - row0 == 10, "并集必须覆盖 10 天 ✓（单字段/交集只有 4 天 ✗）"
    assert cal_lo == 0, "并集左端取**更早**的那个 ✓"
    assert len(idx) == 10


def test_union_layout_skips_inst_without_any_field(feat):
    """一个字段都没有的股票 ⇒ **不出现在 layout** ✓，且不占行 ✓（`pos` 不前进 ✓）。"""
    write_bin(feat, "SH600000", "close", 0, [1.0])
    idx, layout, _lo, _hi = P._union_layout(
        ["SH600000", "SH999999"], CAL[0], CAL[-1], ("close",))
    assert [x[0] for x in layout] == ["SH600000"]
    assert len(idx) == 1


def test_union_layout_clips_to_request_window(feat):
    """请求窗口内的并集 ✓：`close` 覆盖 `cal[2..9]`、请求 `cal[4..6]` ⇒ 3 行 ✓。"""
    write_bin(feat, "SH600000", "close", 2, [1.0] * 8)                   # cal[2..9]
    idx, layout, _lo, _hi = P._union_layout(["SH600000"], CAL[4], CAL[6], ("close",))
    assert len(idx) == 3
    assert layout[0][3] == 4, "cal_lo 必须是裁剪后的起点 ✓"


def test_union_layout_multi_inst_rows_add_up(feat):
    """多股 ⇒ 每段 `(row0,row1)` 首尾相接、总长等于 index 长度 ✓
    （`_load_field_on` 靠这个把值**按位铺**上去 ✓，错一段就整块错 ✓）。"""
    write_bin(feat, "SH600000", "close", 0, [1.0, 2.0])
    write_bin(feat, "SZ000002", "close", 1, [3.0, 4.0, 5.0])
    idx, layout, _lo, _hi = P._union_layout(
        ["SH600000", "SZ000002"], CAL[0], CAL[-1], ("close",))
    assert len(idx) == 5
    assert [x[1:3] for x in layout] == [(0, 2), (2, 5)], "段必须首尾相接 ✓"
