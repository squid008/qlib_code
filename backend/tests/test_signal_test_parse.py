# -*- coding: utf-8 -*-
"""`/api/signals/parse` 的**输入侧契约**（★ v2.11.64）。

## 为什么值得测
这条端点是"**上传文件后立刻给诊断**"✓ —— 它出问题的方式很讨厌：
**用户看到 500"服务器错误"** ✗，而不是"你这文件是加密 xlsx / 传错了"✗。
（v2.10.43 实测抓到的真事故：`parse_csv` 对二进制内容会 `raise ValueError("这不是文本文件…")`
 —— 消息写得很好 ✓，但**没人接住** ✗ ⇒ 端点 500 ✓。）

⇒ 本文件钉三件事：
① **坏输入永不 500** ✓（要么 `ok=False` + `issues` 有可读原因 ✓，要么 400 明确报"base64 坏了"✓）；
② `_raw_of` 的两种来源（文本 / base64）都通 ✓；
③ 纯文本但格式不认识时**也不许抛** ✓（前端拿 `ok=False` 去提示 ✓）。
"""
from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from app.routers.signal_test import _raw_of, parse_only
from app.signals.models import SignalTestRequest


def _req(**kw) -> SignalTestRequest:
    return SignalTestRequest(**kw)


class TestRawOf:
    def test_plain_text_passthrough(self):
        assert _raw_of(_req(content="date,code\n")) == "date,code\n"

    def test_base64_decoded_to_bytes(self):
        raw = "日期,代码\n2024-01-02,SZ000001".encode("utf-8")
        got = _raw_of(_req(content_b64=base64.b64encode(raw).decode()))
        assert got == raw

    def test_bad_base64_raises_400(self):
        with pytest.raises(HTTPException) as ei:
            _raw_of(_req(content_b64="这不是 base64 ✗"))
        assert ei.value.status_code == 400
        assert "base64" in str(ei.value.detail)


class TestParseOnlyNever500:
    def test_binary_content_gives_readable_reason(self):
        """⭐⭐ v2.10.43 的修复：二进制 ⇒ `ok=False` + **可读原因**（不是 500 ✗）。"""
        out = parse_only(_req(content="\x00\x01\x02\xff 加密屎"))
        assert out["ok"] is False
        assert out["issues"], "必须给出可读原因（否则用户只看到'服务器错误' ✗）"
        assert isinstance(out["elapsed"], float)
        assert out["headers"] == []

    def test_unknown_text_format_does_not_raise(self):
        out = parse_only(_req(content="随便什么\n都不是信号文件"))
        assert "ok" in out
        assert isinstance(out["issues"], (list, tuple))

    def test_simple_signal_csv_is_parsed(self):
        csv = "日期,代码\n2024-01-02,SZ000001\n2024-01-03,SZ000002\n"
        out = parse_only(_req(content=csv))
        # 不强制它一定认得出（格式细节随版本演进 ✓），但**必须**把字段回全 ✓
        for k in ("ok", "mode", "encoding", "sep", "headers", "stats", "issues",
                  "origin", "aligned", "elapsed"):
            assert k in out, "响应缺字段 %s（前端按这个契约取值 ✗）" % k

    def test_kind_override_is_respected(self):
        """前端可强制指定 kind ⇒ 回显必须是**它给的那个** ✓（否则前端选择被吞 ✗）。"""
        out = parse_only(_req(content="日期,代码\n2024-01-02,SZ000001\n", kind="jq_trades"))
        assert out["mode"] == "jq_trades"

    def test_tab_separated_still_works(self):
        out = parse_only(_req(content="日期\t代码\n2024-01-02\tSZ000001\n"))
        assert "ok" in out and isinstance(out["sep"], str)
