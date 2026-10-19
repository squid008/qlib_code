# -*- coding: utf-8 -*-
"""`_inst_codes` / `_bad_date_arg`（★ v2.9.87，2026-10-19 ✓ —— 两个"小而关键"的工具 ✓）。

## `_inst_codes`：性能改造必须**逐元素等价**

v1.18.33 把逐行 `.astype(str).str.upper()` 换成 `pd.factorize` + 整数回填 ✓
（全 A 760 万行从 ~4~5s 降到可接受 ✓）。注释承诺"**逐元素语义与原来完全一致（含 NaN → 'NAN'）**" ✓
⇒ 这里直接用**老写法**对拍 ✓（等价性有测试，这类优化才敢做第二遍 ✓）。

## `_bad_date_arg`：用户输错日期的**第一道提示**

注释写着 v1.18.24 的修复理由 ✓：非法日期若放行，会在后续 pandas 解析处报成
「特征计算失败: day is out of range for month: 2026-06-31」✗ —— 用户完全看不懂 ✓。
⇒ 现在**提交前**就给出人话 ✓，并且**两种错要分清** ✓：
「格式无效：…（需为 YYYY-MM-DD）」vs「无效：…（该日期不存在，如 6 月没有 31 日）」✓ ——
混成一句，用户会去改格式而问题其实是日期不存在 ✗。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import _bad_date_arg, _inst_codes


# ------------------------------------------------------------------ _inst_codes
def _frame(codes):
    idx = pd.MultiIndex.from_arrays(
        [codes, pd.bdate_range("2024-01-02", periods=len(codes))],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame({"X": np.arange(len(codes))}, index=idx)


def test_inst_codes_uppercases():
    df = _frame(["sz300001", "SH688001", "bj430047"])
    s = _inst_codes(df)
    assert list(s) == ["SZ300001", "SH688001", "BJ430047"]
    assert list(s.index) == list(df.index)          # ⚠ 必须用**同一个** df 比 ✓（我第一版另造了一个 ✗）


def test_inst_codes_matches_naive_implementation():
    """⭐ 逐元素等价 ✓（含重名、大小写混排、数字结尾 ✓）。"""
    codes = ["sz300001", "SZ300001", "sh600000", "a", "A", "bj430047", "sh600000"]
    got = _inst_codes(_frame(codes))
    naive = pd.Series([str(c).upper() for c in codes], index=got.index, dtype=object)
    pd.testing.assert_series_equal(got, naive)


def test_inst_codes_nan_becomes_string_nan():
    """⭐ 注释承诺的语义：NaN ⇒ `'NAN'` ✓（不是空串、也不是 NaN ✗）。"""
    s = _inst_codes(_frame(["sz300001", None, np.nan]))   # noqa: E501
    assert list(s) == ["SZ300001", "NAN", "NAN"]


def test_inst_codes_single_level_returns_empty_strings():
    """非 MultiIndex（单标的）⇒ 每行空串 ✓，长度保持 ✓。"""
    df = pd.DataFrame({"X": [1, 2, 3]}, index=pd.bdate_range("2024-01-02", periods=3))
    s = _inst_codes(df)
    assert list(s) == ["", "", ""]
    assert len(s) == 3


def test_inst_codes_empty_frame():
    df = pd.DataFrame({"X": []}, index=pd.MultiIndex.from_arrays(
        [[], []], names=["instrument", "datetime"]))
    s = _inst_codes(df)
    assert len(s) == 0


# ---------------------------------------------------------------- _bad_date_arg
def test_valid_range_returns_empty_string():
    assert _bad_date_arg("2024-01-02", "2024-12-31") == ""
    assert _bad_date_arg("2024-02-29", "2024-03-01") == ""      # 闰年 ✓


def test_bad_format_is_reported_as_format_error():
    """格式错 ⇒ 说"需为 YYYY-MM-DD" ✓（不要让人去查"日期是否存在" ✗）。"""
    for bad in ("2024-1-1", "20240101", "2024/01/01", "", None, "  "):
        msg = _bad_date_arg(bad, "2024-12-31")
        assert "开始日期格式无效" in msg, bad
        assert "YYYY-MM-DD" in msg


def test_nonexistent_date_is_reported_as_not_existing():
    """⭐ v1.18.24：格式对但**日期不存在** ⇒ 必须说"该日期不存在" ✓。"""
    for bad in ("2026-06-31", "2023-02-29", "2024-04-31", "2024-13-01"):
        msg = _bad_date_arg(bad, "2024-12-31")
        assert "开始日期无效" in msg, bad
        assert "该日期不存在" in msg, bad
        assert "6 月没有 31 日" in msg


def test_end_date_checked_after_start():
    """开始合法、结束非法 ⇒ 报**结束日期** ✓（不是笼统"日期无效"✗）。"""
    msg = _bad_date_arg("2024-01-02", "2026-06-31")
    assert "结束日期无效" in msg
    # 两个都错时先报开始 ✓（顺序稳定 ✓）
    assert "开始日期" in _bad_date_arg("bad", "2026-06-31")
