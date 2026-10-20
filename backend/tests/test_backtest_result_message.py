# -*- coding: utf-8 -*-
"""`BacktestResult.message`（★ v2.10.4，2026-10-20 ✓ —— **mypy 抓出来的真 bug** ✓）。

## 这个 bug 的形状很有意思

`metrics._extract_result` 里有两条 **错误分支**：

```python
except Exception as e:
    result.message = f"指标提取异常: {e}"      # ← 这里
else:
    result.message = "未找到回测报告(report_normal)"
```

而 `BacktestResult`（pydantic v2）**原先没有 `message` 字段** ✗ ⇒ 赋值直接抛

```
ValueError: "BacktestResult" object has no field "message"
```

⇒ **错误分支自身崩** ✓：用户本想看到「指标提取异常：…」✓，实际拿到的是
「has no field message」✗ —— 一句和真正原因毫不相干的报错 ✓。

## 为什么之前没被发现

那两条分支只在"指标提取失败 / 找不到 `report_normal`"时才走到 ✓，
而正常回测路径**一次都不会碰** ✓ ⇒ 常规跑测时永远绿 ✗。
**是 mypy 在 `--ignore-missing-imports` 下把 `result.message` 标成 `attr-defined` 才暴露的** ✓。

⇒ 本文件把"**结果对象必须能承载状态说明**"钉住 ✓，以后谁删了这个字段就会红 ✓。
"""
from __future__ import annotations

import pytest

from app.engine.metrics import _aggregate_from_nav
from app.models.backtest import BacktestResult


def test_message_field_is_assignable():
    """⭐ 就是那条崩溃路径 ✓（改回没有该字段的模型，这一行会抛 `ValueError` ✗）。"""
    r = BacktestResult()
    r.message = "指标提取异常: boom"
    assert r.message == "指标提取异常: boom"


def test_message_is_serialized_for_the_api():
    """前端/接口要能读到它 ✓（只在内存里有、`model_dump` 里没有 ⇒ 等于没写 ✗）。"""
    r = BacktestResult()
    r.message = "未找到回测报告(report_normal)"
    dumped = r.model_dump()
    assert dumped.get("message") == "未找到回测报告(report_normal)"
    assert "message" in BacktestResult.model_fields


def test_default_is_none_so_normal_paths_are_unaffected():
    """正常路径不该多出一个非空 message ✓（否则每个回测响应都带一句莫名其妙的提示 ✗）。"""
    r = _aggregate_from_nav([], [])
    assert r.message is None


@pytest.mark.parametrize("text", ["", "x" * 200])
def test_message_accepts_empty_and_long_text(text):
    """边界：空串与长文本都允许 ✓（不让"提示太长"变成新的崩溃点 ✗）。"""
    r = BacktestResult(message=text)
    assert r.message == text
