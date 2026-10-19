# -*- coding: utf-8 -*-
"""`routers/export_xlsx.py` 的三个入口级纯函数（★ v2.9.96，2026-10-20 ✓ —— 覆盖 30.7% 的模块 ✓）。

## 为什么挑这三个

它们是**导出 XLSX 这条链上唯一"纯"** 的三段 ✓，而各自的坏法都很具体 ✗：

1. **`_decode_shot`** —— 注释原话：这是**唯一"用户侧原样进文件"的输入** ✓
   ⇒ 只收 **PNG 魔数** + **4MB 上限** ✓。不校验的后果：一个坏 base64 让**导出 500** ✗，
   或把 **GB 级垃圾塞进文件** ✗（用户下载到一个巨大且打不开的 xlsx ✓）；
   而且失败时要给**可读原因** ✓（"⇒ 本次未附图"），不能静默丢 ✗ —— 否则用户以为截图导出成功了 ✓。
2. **`_safe_name`** —— Windows 不允许 `\\/:*?"<>|` ✓。不换掉就可能**写不出文件** ✗
   或把表达式里的 `/` 变成**路径分隔符** ✗。
3. **`_find_event_study`** —— docstring 记着一个真实缺陷 ✓：单因子"秒开"路径下，
   因子名/表达式挂在**行**上，而结果里可能没有 ✗ ⇒ 旧实现"导出成空"✗
   ⇒ 现在必须**连行一起返回** ✓（否则第 1 页的标题/参数是空的 ✓）。
"""
from __future__ import annotations

import base64

import pytest

from app.routers.export_xlsx import (
    MAX_SHOT_BYTES,
    _decode_shot,
    _find_event_study,
    _safe_name,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _b64(raw: bytes, prefix: str = "") -> str:
    return prefix + base64.b64encode(raw).decode()


# ------------------------------------------------------------------ _decode_shot
def test_decode_shot_returns_none_for_empty():
    assert _decode_shot(None) == (None, None)
    assert _decode_shot("") == (None, None)
    assert _decode_shot("   ") == (None, None)
    assert _decode_shot(123) == (None, None)          # 非 str ✓


def test_decode_shot_accepts_png_with_and_without_data_prefix():
    raw, err = _decode_shot(_b64(PNG))
    assert raw == PNG and err is None
    raw2, err2 = _decode_shot(_b64(PNG, prefix="data:image/png;base64,"))
    assert raw2 == PNG and err2 is None


def test_decode_shot_rejects_non_png_with_readable_reason():
    """⭐ 非 PNG ⇒ 丢掉，并**明确告知**（静默丢 ⇒ 用户以为图导进去了 ✗）。"""
    raw, err = _decode_shot(_b64(b"GIF89a" + b"\x00" * 20))
    assert raw is None
    assert err and "不是 PNG" in err
    assert "本次未附图" in err


def test_decode_shot_rejects_oversized_png():
    """⭐ 4MB 上限 ✓ —— 不限的话用户能塞一个 GB 级文件进来 ✗。"""
    big = PNG + b"\x00" * (MAX_SHOT_BYTES + 1)
    raw, err = _decode_shot(_b64(big))
    assert raw is None
    assert err and "过大" in err and "MB" in err


def test_decode_shot_bad_base64_reports_type_not_traceback():
    """坏 base64 ⇒ 返回**类型名**级别的可读原因 ✓（不能把 traceback 甩给用户 ✗）。"""
    raw, err = _decode_shot("A")            # 长度非法 ⇒ binascii.Error ✓
    assert raw is None
    assert err and "解码失败" in err
    assert "本次未附图" in err


# ------------------------------------------------------------------- _safe_name
@pytest.mark.parametrize("ch", list('\\/:*?"<>|'))
def test_safe_name_replaces_windows_forbidden_chars(ch):
    out = _safe_name("a%sb" % ch)
    assert out == "a_b", "Windows 非法字符必须换掉 ✓"


def test_safe_name_strips_control_chars_and_falls_back():
    assert _safe_name("a\r\nb") == "a__b"
    assert _safe_name("") == "factor"
    assert _safe_name(None) == "factor"
    assert _safe_name("   ", fallback="x") == "x"


def test_safe_name_caps_length():
    """表达式可能很长 ✓ —— 文件名截到 60 字符 ✓（否则某些系统写不出 ✓）。"""
    long = "f" * 200
    assert len(_safe_name(long)) == 60
    assert _safe_name(long).startswith("fff")


# ------------------------------------------------------------- _find_event_study
def test_find_event_study_direct_result():
    """独立「事件研究」任务 ⇒ `result` 本身就是结果 ✓（没有"行" ⇒ 第二个返回 None ✓）。"""
    res = {"curve": [1, 2, 3]}
    got, row = _find_event_study({"result": res}, "whatever")
    assert got is res and row is None


def test_find_event_study_by_factor_id_returns_row():
    """⭐ 必须**连行一起返回** ✓ —— 因子名/表达式挂在行上，结果里可能没有 ✗（否则导出成空 ✓）。"""
    es1 = {"curve": [1]}
    es2 = {"curve": [2]}
    state = {"result": {"items": [
        {"id": "e1", "name": "因子一", "expression": "$close", "event_study": es1},
        {"id": "e2", "name": "因子二", "expression": "$open", "event_study": es2},
    ]}}
    got, row = _find_event_study(state, "e2")
    assert got is es2
    assert row is not None and row["name"] == "因子二"
    # 按 name / expression 也能命中 ✓
    assert _find_event_study(state, "因子一")[0] is es1
    assert _find_event_study(state, "$open")[0] is es2


def test_find_event_study_empty_factor_id_takes_first():
    es1 = {"curve": [1]}
    state = {"result": {"items": [{"id": "e1", "event_study": es1}]}}
    got, row = _find_event_study(state, None)
    assert got is es1 and row is not None


def test_find_event_study_returns_none_pair_when_absent():
    """找不到 ⇒ `(None, None)` ✓（调用方给可读提示 ✓，不抛 ✗）。"""
    assert _find_event_study({}, "x") == (None, None)
    assert _find_event_study({"result": {"items": []}}, "x") == (None, None)
    # 行里有 event_study 但 id 不匹配 ⇒ 也不选它 ✓
    state = {"result": {"items": [{"id": "a", "event_study": {"curve": [1]}}]}}
    assert _find_event_study(state, "zzz") == (None, None)
