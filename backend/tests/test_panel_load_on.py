# -*- coding: utf-8 -*-
"""按位铺字段 ＋ 读盘的两条路径（★ v2.10.92，技术债 P1-#2 的第十二刀）。

## `_load_field_on` 是什么
`_union_layout` 算好"每股占用哪几行、对应日历起点 `cal_lo`"之后 ✓，
这个函数把某字段的值**按日历下标差直接写进数组** ✓（等价于
`load_field_series(...).reindex(idx)`，但省掉三次 pandas 重排 ✓）。

**最容易错的就一句**：`a = r0 + (lo - cal_lo)` —— `lo` 是**日历下标**、
`cal_lo` 是该股 union 段的**日历起点** ⇒ 两者相减才是"段内第几行" ✓。
写成 `r0 + lo` 会把整段值**整体偏移** ✗（而且前几行还是"看起来正常"的数 ✓）。

## `_read_field_bin` 的两条路径
· **合并层**（`_MERGED_ENABLED` ✓）：`features_merged/<field>.bin` 一次 memmap + 切片 ✓
  —— 全池读盘次数 **3213 → 7**（实测 65× ✓）；
· **原路径**：每股每字段一个 `.day.bin` ✓。
源码明写"**三条路径产出的 `(start, vals)` 逐位相同**"✓ —— 这正是要钉的契约 ✓。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.factors import panel_expr as P

DAYS = 8


@pytest.fixture()
def feat(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_feature_dir", lambda: str(tmp_path))
    monkeypatch.setattr(P, "_MERGED_ENABLED", False)
    P.clear_bin_cache()
    yield tmp_path
    P.clear_bin_cache()


def write_bin(root, inst: str, field: str, start: int, values):
    d = root / inst
    d.mkdir(exist_ok=True)
    arr = np.asarray([float(start)] + list(values), dtype="<f4")
    arr.tofile(d / f"{field}.day.bin")


# ---------------------------------------------------------------- _load_field_on
def test_load_on_places_values_by_calendar_offset(feat):
    """⭐ 起始 `cal_lo=2`、字段从 `start=2` 开始 ⇒ 值必须落在**段内第 0 行** ✓
    （写成 `r0 + lo` 会把值整体后移两行 ✗，而后面几行照样"看起来正常" ✓）。"""
    write_bin(feat, "A", "close", 2, [10.0, 11.0, 12.0])
    idx = ["x"] * 3
    layout = [("A", 0, 3, 2)]                      # 该股占第 0~2 行, 日历起点 = 2
    out = P._load_field_on(idx, layout, 0, DAYS - 1, "close")
    assert list(out) == [10.0, 11.0, 12.0]


def test_load_on_leaves_nan_before_field_start(feat):
    """字段从 `cal=4` 才开始 ⇒ 段内前 2 行必须是 **NaN** ✓（不能从前一行"借"值 ✗）。"""
    write_bin(feat, "A", "close", 4, [20.0, 21.0])
    idx = ["x"] * 4
    layout = [("A", 0, 4, 2)]                      # 段覆盖 cal[2..5], 但字段只在 cal[4..5]
    out = P._load_field_on(idx, layout, 0, DAYS - 1, "close")
    assert np.isnan(out[0]) and np.isnan(out[1]), "字段没覆盖的日子必须是 NaN ✓"
    assert list(out[2:]) == [20.0, 21.0]


def test_load_on_clips_to_request_window(feat):
    """⭐ 请求窗口 `[5,6]` ⇒ 只写这两天 ✓（其余留 NaN ✓）——
    裁剪错 ⇒ 用户选的区间与实际算的数据不一致 ✓。"""
    write_bin(feat, "A", "close", 4, [20.0, 21.0, 22.0, 23.0])   # cal[4..7]
    idx = ["x"] * 4
    layout = [("A", 0, 4, 4)]                      # 段 = cal[4..7]
    out = P._load_field_on(idx, layout, 5, 6, "close")
    assert np.isnan(out[0])
    assert list(out[1:3]) == [21.0, 22.0], "cal[5], cal[6] 两天的值 ✓"
    assert np.isnan(out[3])


def test_load_on_missing_field_is_all_nan(feat):
    """没落盘的字段 ⇒ **全 NaN、不抛** ✓（某只股缺某字段是正常情况 ✓）。"""
    idx = ["x"] * 3
    out = P._load_field_on(idx, [("A", 0, 3, 0)], 0, DAYS - 1, "no_such_field")
    assert np.isnan(out).all()


def test_load_on_field_entirely_outside_window(feat):
    """字段完全落在请求窗口之外 ⇒ 该股跳过 ⇒ 全 NaN ✓（不许越界写 ✗）。"""
    write_bin(feat, "A", "close", 6, [99.0])
    idx = ["x"] * 2
    out = P._load_field_on(idx, [("A", 0, 2, 0)], 0, 1, "close")
    assert np.isnan(out).all()


def test_load_on_two_instruments_keep_their_own_rows(feat):
    """⭐⭐ 多股：各段只写自己那几行 ✓（`r0` 用错 ⇒ 第二只会覆盖第一只 ✗）。"""
    write_bin(feat, "A", "close", 0, [1.0, 2.0])
    write_bin(feat, "B", "close", 0, [7.0, 8.0, 9.0])
    idx = ["x"] * 5
    layout = [("A", 0, 2, 0), ("B", 2, 5, 0)]
    out = P._load_field_on(idx, layout, 0, DAYS - 1, "close")
    assert list(out) == [1.0, 2.0, 7.0, 8.0, 9.0]


# ---------------------------------------------------------------- _read_field_bin：合并层
def test_read_field_bin_merged_path_is_used_when_enabled(feat, monkeypatch):
    """⭐⭐ 开了合并层且该 (股,字段) 被收录 ⇒ **走合并层** ✓（不再 stat 单个 .bin ✓）。"""
    called = {"merged": 0}

    def _meta(inst, field):
        called["merged"] += 1
        return (123, 2, 3)                          # (mtime, start, n_rows)

    monkeypatch.setattr(P, "_MERGED_ENABLED", True)
    monkeypatch.setattr(P, "_merged_meta", _meta)
    monkeypatch.setattr(P, "_merged_values", lambda inst, field: (2, np.array([5.0, 6.0, 7.0])))

    start, vals = P._read_field_bin("A", "close")
    assert called["merged"] == 1, "必须问过合并层 ✓"
    assert (start, list(vals)) == (2, [5.0, 6.0, 7.0])


def test_read_field_bin_merged_cache_keyed_by_mtime(feat, monkeypatch):
    """⭐ 合并层也走同一套缓存，且**以合并层的 mtime 为失效键** ✓
    （用单文件 mtime ⇒ 合并层更新后仍读旧值 ✗）。"""
    calls = {"values": 0}

    def _values(inst, field):
        calls["values"] += 1
        return (0, np.array([1.0, 2.0]))

    monkeypatch.setattr(P, "_MERGED_ENABLED", True)
    monkeypatch.setattr(P, "_merged_meta", lambda inst, field: (777, 0, 2))
    monkeypatch.setattr(P, "_merged_values", _values)

    P._read_field_bin("A", "close")
    P._read_field_bin("A", "close")             # 第二次应命中缓存
    assert calls["values"] == 1, "mtime 未变 ⇒ 第二次不许再切一次 memmap ✓"


def test_read_field_bin_merged_returns_none_is_respected(feat, monkeypatch):
    """合并层说"没有这段"（`_merged_values` 返 None）⇒ **直接 None** ✓
    （回退去读单个 .bin 会把"未收录"和"文件缺失"混为一谈 ✗）。"""
    monkeypatch.setattr(P, "_MERGED_ENABLED", True)
    monkeypatch.setattr(P, "_merged_meta", lambda inst, field: (1, 0, 1))
    monkeypatch.setattr(P, "_merged_values", lambda inst, field: None)
    assert P._read_field_bin("A", "close") is None


def test_read_field_bin_too_short_is_missing(feat):
    """⭐ 只有头部（`< 2` 个 float32）⇒ 当**缺失** ✓（不是"有数据但 0 行" ✗ ——
    后者会让下游得到一条全是 NaN、但"存在"的序列 ✓，比缺失更难排查 ✗）。"""
    write_bin(feat, "A", "close", 5, [])
    assert P._read_field_bin("A", "close") is None
