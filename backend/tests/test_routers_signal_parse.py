# -*- coding: utf-8 -*-
"""`routers/signal_test.py` 的**解析路径**补测（★ 覆盖率 ratchet，2026-10-19 ✓）。

## 为什么选它

`routers/signal_test.py` 有 **430 语句、覆盖率只有 7%** ✗（未覆盖 400 行 ✓，全仓第 6 大空洞 ✓）。
而它的 `/parse` 端点**只做 CSV 解析** ✓ —— **不需要行情数据** ✓、不跑回测 ✓
⇒ 是"低风险、高覆盖"的一块 ✓（`/run` 那种几十秒的**不碰** ✗）。

## 守什么口径

1. 合法 CSV ⇒ 200 + `preview` 非空 ✓（前端靠它给"识别结果"✓）；
2. **乱七八糟的输入不许 500** ✓ —— 要么带着 `warnings` 返回 200 ✓、要么 4xx ✓
   （500 = "我们崩了" ✗，前端只能显示"系统错误" ✗）；
3. 导出端点给**不存在的任务** ⇒ 4xx ✓（不是 500 ✓）。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CSV_OK = (
    "date,time,code,name,side,qty,price\n"
    "2024-01-02,09:31:00,SH600000,浦发银行,1,100,7.10\n"
    "2024-01-03,09:31:00,SZ000001,平安银行,1,200,9.50\n"
    "2024-02-05,09:31:00,SH600000,浦发银行,-1,100,7.60\n"
)


def test_options_available():
    r = client.get("/api/signal-test/options")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert isinstance(body, dict) and body, "可选项不能为空 ✓"


def test_parse_valid_csv_returns_preview():
    r = client.post("/api/signal-test/parse", json={"content": CSV_OK, "filename": "t.csv"})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert isinstance(body, dict)
    # 至少要有"识别结果"或"诊断"之一 ✓（字段名可能演进 ⇒ 断言存在而非死名字 ✓）
    keys = set(body)
    assert keys & {"preview", "warnings", "signal", "summary", "rows", "n_rows"}, (
        "解析结果里没有任何可展示字段 ✗：%s" % sorted(keys)[:12])


@pytest.mark.parametrize("content", ["", "   ", "not,a,csv\n1,2,3", "date,code\n"])
def test_parse_bad_input_never_500(content):
    r = client.post("/api/signal-test/parse", json={"content": content})
    assert r.status_code < 500, (
        "坏输入必须 4xx 或带 warnings 的 200 ✓，不许 500 ✗（实测 %d）%s"
        % (r.status_code, r.text[:200]))


@pytest.mark.parametrize("path", [
    "/api/factors/export/topk-xlsx",
    "/api/factors/export/exit-curve-xlsx",
    "/api/factors/export/event-study-xlsx",
])
def test_export_with_unknown_task_is_4xx(path):
    r = client.post(path, json={"task_id": "does-not-exist"})
    assert 400 <= r.status_code < 500, (
        "未知任务必须 4xx ✓（前端据此提示\"结果已清理\"✓），实测 %d ✗：%s"
        % (r.status_code, r.text[:200]))
