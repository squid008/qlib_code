# -*- coding: utf-8 -*-
"""`POST /api/signal-test/run` 的**模式③（只上传《收益概述》）端到端测试**（★ v2.11.15）。

## 为什么这个端点此前零端到端覆盖、而这一条能测
`/run` 有三个模式，其中两个要**真行情**（几十秒）✗ —— `test_routers_smoke.py` 里明写
"**刻意不测** `/signal-test/run`" ✓。
但**模式③不需要行情** ✓：只把《收益概述》CSV 里的逐日净值画出来 ✓ ⇒
可以起一个**真请求**（FastAPI TestClient ✓）而不碰数据集 ✓。

## 守什么
1. ⭐ **`nav = 1 + 收益/100`** ✓ —— 源码 docstring 记着实测对拍：
   末日 `策略收益 767.62%` ⇒ 净值 **8.6762** ✓（用它反查重建的 C=8.6603，差 0.18% ✓）。
   口径写成"直接拿百分数当净值"✗ 就会得到 767 这种数 ✓（图纵轴直接爆 ✗）。
2. ⭐ `diag.mode == "only_perf"` ✓ —— 前端据此提示"只有官方净值（未提供成交明细）"✓；
3. ⭐ **坏输入 ⇒ 4xx，不许 500** ✓（500 = "我们崩了" ✗，前端只能显示"系统错误" ✗）。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

#: 聚宽《收益概述》的最小合法形状（识别要求 `时间` + `策略收益` + `基准收益` ✓）
PERF_CSV = (
    "时间,策略收益,基准收益\n"
    "2024-01-02,0.00,0.00\n"
    "2024-01-03,1.50,0.80\n"
    "2024-01-04,-0.50,0.20\n"
    "2024-01-05,767.62,12.00\n"
)


def test_only_perf_mode_returns_official_nav():
    """⭐ 只传收益概述 ⇒ 200 + `mode=jq_perf` + 官方净值非空 ✓（且**不取行情** ✓ 秒回 ✓）。"""
    r = client.post("/api/signal-test/run", json={"content": PERF_CSV, "filename": "perf.csv"})
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body.get("mode") == "jq_perf"
    rep = body.get("replay") or {}
    assert (rep.get("diag") or {}).get("mode") == "only_perf", "前端据此提示『只有官方净值』✓"
    nav = rep.get("nav") or []
    assert nav, "官方净值不能为空 ✓"
    assert isinstance(body.get("elapsed"), (int, float)) and body["elapsed"] >= 0


def test_official_nav_is_one_plus_pct_over_100():
    """⭐⭐ **`nav = 1 + 收益/100`** ✓ —— 末日 `767.62%` ⇒ **8.6762** ✓
    （若直接拿百分数当净值 ✗ ⇒ 图纵轴会到 767 ✓，而数据"看着有" ✗）。"""
    r = client.post("/api/signal-test/run", json={"content": PERF_CSV, "filename": "perf.csv"})
    assert r.status_code == 200, r.text[:300]
    nav = r.json()["replay"]["nav"]
    last = nav[-1]
    # 行结构由 `_nav_rows` 决定 ⇒ 只断言"有一个字段约等于 8.6762" ✓（不绑死列名 ✓）
    vals = [v for v in last.values() if isinstance(v, (int, float))]
    assert any(abs(v - 8.6762) < 1e-3 for v in vals), (
        "末日净值应为 1 + 767.62/100 = 8.6762 ✓，实际 %r" % (last,))


def test_empty_content_is_4xx_not_500():
    """⭐ 空内容 ⇒ **4xx**（我们明确拒绝 ✓），不许 500 ✓。"""
    r = client.post("/api/signal-test/run", json={"content": "", "filename": "empty.csv"})
    assert 400 <= r.status_code < 500, "空输入必须 4xx ✓，实际 %d" % r.status_code
