# -*- coding: utf-8 -*-
"""`engine/inst_mask.py` 的逐日成分掩码（★ v2.9.91，2026-10-19 ✓ —— 覆盖 9.1% 的模块 ✓）。

## 这块直接决定"有没有未来函数"

背景（docstring 原话 ✓）：`D.list_instruments(scope, start_time=...)` **不传 end_time** 的语义是
「start 之后**曾属于**该池的全部股票」= **全期并集** ✗ —— 实测 csi300 为 **459 只**，而真实成分
每天只有 300 只 ⇒ 2021 年样本里混入 **106 只彼时尚未纳入**的股票 ⇒ **幸存者偏差、系统性高估** ✗。

本模块直接解析 instruments **区间文件**，按「日 × 股」布尔矩阵给每行一个 mask ✓。
⇒ 最核心的一条断言：**区间外的行必须是 `False`**（不是"不管"✗，也不是 `True`✗）。

另钉四个容易错的边界 ✓：
1. **区间是闭区间** ✓（首尾当天都算在池内 ✓ —— `searchsorted` 的 left/right 用反就少一天 ✗）；
2. **同一代码可有多段**（进出池多次 ✓）各段都算 ✓；
3. **文件里没有的代码 ⇒ 全 False** ✓（宁可剔掉 ✓ —— 反过来就是"未知的全都放进来"✗）；
4. **无从判断时返回 `None`** ✓（池名没有对应文件 / index 缺层名 / 空表 ✓）——
   调用方据此**保持旧行为**（不筛）✓，而不是"全 False 把样本抹干净"✗。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine import inst_mask as IM

SPANS = """\
# comment line
SH600000\t2024-01-01\t2024-01-03
SZ000001\t2024-01-03\t2024-01-05
SH600000\t2024-01-06\t2024-01-07
BADLINE
SZ399999\tnot-a-date\t2024-01-09
"""


@pytest.fixture()
def spans_file(tmp_path, monkeypatch):
    p = tmp_path / "csi300.txt"
    p.write_text(SPANS, encoding="utf-8")
    monkeypatch.setattr(IM, "_inst_spans_path", lambda universe: str(p))
    return p


def _index(codes=("SH600000", "SZ000001", "SH601398"), days=6):
    d = pd.bdate_range("2024-01-01", periods=days)
    tup = [(c, dd) for c in codes for dd in d]
    return pd.MultiIndex.from_tuples(tup, names=["instrument", "datetime"])


def _mask_by_code(code, m, idx):
    sub = idx.get_level_values(0) == code
    return list(m[sub])


def test_member_inside_span_true_outside_false(spans_file):
    """⭐ 区间外必须 **False** ✓（这正是"彼时尚未纳入"的股票要被剔掉的机制 ✓）。"""
    idx = _index(days=6)          # 2024-01-01 ~ 01-08（bdate）✓
    m = IM._daily_member_mask("csi300", idx)
    assert m is not None and m.dtype == bool
    got = _mask_by_code("SH600000", m, idx)
    days = list(pd.bdate_range("2024-01-01", periods=6))
    # 第一段 01-01 ~ 01-03 闭区间 ✓；第二段 01-06 ~ 01-07 ✓；其余 False ✓
    expect = [
        days[0] >= pd.Timestamp("2024-01-01") and days[0] <= pd.Timestamp("2024-01-03"),
        days[1] <= pd.Timestamp("2024-01-03"),
        days[2] <= pd.Timestamp("2024-01-03"),
        False,                                            # 01-04 不在任何一段 ✓
        days[4] >= pd.Timestamp("2024-01-06") and days[4] <= pd.Timestamp("2024-01-07"),
        False,                                            # 01-08 超出第二段 ✓
    ]
    assert got == expect


def test_span_boundaries_are_inclusive(spans_file):
    """⭐ 首尾当天都算在池内 ✓（`searchsorted` 的 left/right 用反会少一天 ✗）。"""
    idx = pd.MultiIndex.from_tuples(
        [("SH600000", pd.Timestamp("2024-01-01")),
         ("SH600000", pd.Timestamp("2024-01-03")),
         ("SH600000", pd.Timestamp("2024-01-04"))],
        names=["instrument", "datetime"])
    m = IM._daily_member_mask("csi300", idx)
    assert list(m) == [True, True, False]


def test_multiple_spans_both_count(spans_file):
    idx = _index(codes=("SH600000",), days=8)
    m = IM._daily_member_mask("csi300", idx)
    days = list(pd.bdate_range("2024-01-01", periods=8))
    trues = [d.strftime("%m-%d") for d, ok in zip(days, m) if ok]
    # ⚠ 第二段是 01-06 ~ 01-07（**周六日**）⇒ 没有任何**交易日**落在里面 ✓ ⇒ 一个 False 都不该翻成 True ✓
    #   （我第一版期望多一个 "01-08" ✗ —— 实测当场纠正 ✓。这也说明掩码是按**交易日**算的 ✓。）
    assert trues == ["01-01", "01-02", "01-03"]
    assert all(d.weekday() < 5 for d in days)          # 面板日期本身都是交易日 ✓
    # ⚠ 不能用 `pd.bdate_range("2024-01-06", ...)` 去验周末 ✗ —— 它**只产工作日** ✓（实测报错 ✓）
    assert pd.Timestamp("2024-01-06").weekday() == 5   # 周六 ✓
    assert pd.Timestamp("2024-01-07").weekday() == 6   # 周日 ✓


def test_code_not_in_file_is_all_false(spans_file):
    """⭐ 文件里没有的代码 ⇒ **全 False** ✓（宁可剔掉；反过来"未知全放进来"就是前视 ✗）。"""
    idx = _index(codes=("SH601398",), days=6)
    m = IM._daily_member_mask("csi300", idx)
    assert m is not None and not bool(m.any())


def test_codes_are_normalized_to_upper(spans_file):
    idx = pd.MultiIndex.from_tuples(
        [("sh600000", pd.Timestamp("2024-01-02"))], names=["instrument", "datetime"])
    m = IM._daily_member_mask("csi300", idx)
    assert list(m) == [True]      # 小写也能对上 ✓


def test_bad_lines_are_skipped(tmp_path, monkeypatch):
    """区间文件里的坏行（字段不足 / 日期非法）⇒ 跳过，不炸 ✓。"""
    p = tmp_path / "x.txt"
    p.write_text("BADLINE\nSZ399999\tnot-a-date\t2024-01-09\n", encoding="utf-8")
    monkeypatch.setattr(IM, "_inst_spans_path", lambda u: str(p))
    idx = _index(codes=("SZ399999",), days=3)
    m = IM._daily_member_mask("csi300", idx)
    assert m is not None and not bool(m.any())


def test_returns_none_when_undecidable(tmp_path, monkeypatch):
    """无从判断 ⇒ `None` ✓（调用方保持旧行为 ✓，不是"全 False 把样本抹干净"✗）。"""
    monkeypatch.setattr(IM, "_inst_spans_path", lambda u: None)
    assert IM._daily_member_mask("自定义池", _index()) is None

    p = tmp_path / "x.txt"
    p.write_text("", encoding="utf-8")
    monkeypatch.setattr(IM, "_inst_spans_path", lambda u: str(p))
    # index 缺 instrument/datetime 层名 ⇒ None ✓
    flat = pd.MultiIndex.from_tuples([("A", pd.Timestamp("2024-01-02"))], names=["c", "d"])
    assert IM._daily_member_mask("csi300", flat) is None
    # 空表 ⇒ None ✓
    empty = pd.MultiIndex.from_arrays([[], []], names=["instrument", "datetime"])
    assert IM._daily_member_mask("csi300", empty) is None


def test_inst_spans_path_warns_and_returns_none(tmp_path, monkeypatch):
    """`_inst_spans_path`：uri 为空 / 文件缺失 ⇒ **None** ✓（并只警告一次 ✓）。"""
    from app.engine import utils as U

    calls = []
    monkeypatch.setattr(IM, "_warn_once", lambda k, m: calls.append(k))

    monkeypatch.setattr(U, "_default_qlib_uri", lambda: "")
    assert IM._inst_spans_path("csi300") is None
    assert calls and calls[0] == "uri-empty"

    calls.clear()
    monkeypatch.setattr(U, "_default_qlib_uri", lambda: str(tmp_path))
    assert IM._inst_spans_path("csi300") is None          # 文件不存在 ✓
    assert calls and calls[0].startswith("miss-")
