# -*- coding: utf-8 -*-
"""进程内指标（★ v2.11.25 —— 技术债 P2-#8「无可观测性」的第一步）。

## 这套东西的取舍（也是测试要守住的口径）
· **进程内**而不是 Prometheus ✓：本机是**单进程**部署（`restart_backend.ps1` 一个 uvicorn ✓），
  上 TSDB + exporter 的成本远大于收益 ✗；先把"看什么"定下来 ✓。
· ⚠ **不按 URL 路径分桶** ✗ —— 路径里有 `task_id`/日期等高基数片段 ✓，
  按路径分桶会让**指标本身变成内存泄漏** ✗（Prometheus 用户最常踩的坑 ✓）。
  这条用 `test_counters_have_low_cardinality` 钉住 ✓。
· 耗时只记 `count/sum/max`（+算出的 `avg`）✓，**不存分位** ✗ —— QPS 极低时
  分位没有统计意义 ✓，`max` 已能暴露"某次特别慢"✓。
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.metrics import REGISTRY, Registry

client = TestClient(app)


def test_counters_and_timers_add_up():
    """基本算术 ✓：`inc` 累加、`observe` 计次数/总和/最大值 ✓。"""
    r = Registry()
    r.inc("a")
    r.inc("a", 3)
    r.observe("t", 0.5)
    r.observe("t", 0.1)
    snap = r.snapshot()
    assert snap["counters"]["a"] == 4
    t = snap["timers"]["t"]
    assert (t["count"], round(t["sum"], 6), t["max"]) == (2, 0.6, 0.5)
    assert t["avg"] == pytest.approx(0.3)


def test_snapshot_is_a_copy():
    """⭐ `snapshot()` 返回**副本** ✓ —— 调用方改它不能影响内部 ✓
    （否则一个"看指标"的请求会**把指标改坏** ✗，那种 bug 极难查 ✓）。"""
    r = Registry()
    r.inc("x")
    snap = r.snapshot()
    snap["counters"]["x"] = 999
    snap["timers"]["injected"] = {"count": 1}
    assert r.snapshot()["counters"]["x"] == 1
    assert "injected" not in r.snapshot()["timers"]


def test_thread_safe_under_concurrency():
    """⭐ 并发累加不能丢计数 ✓ —— 同步端点跑在**线程池**里（`routers/*` 都是 `def` ✓），
    不丢才是它该有的行为 ✓。"""
    r = Registry()
    n_threads, per = 8, 500

    def worker():
        for _ in range(per):
            r.inc("c")

    ts = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert r.snapshot()["counters"]["c"] == n_threads * per, "并发丢计数 ✗ ⇒ 锁没生效 ✓"


def test_metrics_endpoint_reports_requests():
    """⭐ 打几个请求 ⇒ `http.requests` / `http.status.200` 都涨 ✓（中间件真的在记 ✓）。"""
    REGISTRY.reset()
    client.get("/api/version")
    client.get("/api/version")
    snap = client.get("/api/metrics").json()
    assert snap["counters"].get("http.requests", 0) >= 2
    assert snap["counters"].get("http.status.200", 0) >= 2
    assert "uptime_s" in snap and snap["uptime_s"] >= 0
    d = snap["timers"].get("http.duration_s")
    assert d and d["count"] >= 2 and d["max"] >= 0


def test_prometheus_text_format_is_scrape_ready():
    """⭐ `?format=text` ⇒ **Prometheus 文本格式** ✓（v2.11.41）—— 接监控零改动 ✓。

    口径：计数器带 `_total` 后缀、耗时带 `_seconds` 后缀 + `count/sum/max` ✓ ——
    这样以后接真监控时，**dashboard 的查询语句不用再改一遍** ✓。
    """
    REGISTRY.reset()
    client.get("/api/version")
    r = client.get("/api/metrics", params={"format": "text"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "qlib_uptime_seconds " in body, "缺运行时长 ✓"
    assert "qlib_http_requests_total " in body, "计数器应带 _total 后缀 ✓"
    assert "qlib_http_duration_seconds_count " in body, "耗时要有 count ✓"
    assert "qlib_http_duration_seconds_max " in body, "耗时要有 max ✓"


def test_text_format_parses_as_metric_lines():
    """⭐⭐ 每一行都得是 `名字 数值` 或 `#` 注释 ✓ —— 否则 Prometheus 抓过去会**整段丢弃** ✗。"""
    REGISTRY.reset()
    client.get("/api/version")
    body = client.get("/api/metrics", params={"format": "text"}).text
    for ln in body.strip().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split(" ")
        assert len(parts) == 2, "格式不对 ✗（应为『名字 数值』）：%r" % ln
        float(parts[1])            # 数值必须可解析 ✓（不然 scrape 直接报 malformed ✗）


def test_counters_have_low_cardinality():
    """⭐⭐ 计数器名里**不许出现 task_id / 日期 / 数字 id** ✓ ——
    按路径分桶会让指标**无限增长** ✗（每个任务一个新键 ✓，进程跑久了内存一直涨 ✓）。
    这里只允许 `http.status.<3位码>` 这一种带数字的形态 ✓。"""
    REGISTRY.reset()
    for _ in range(3):
        client.get("/api/version")
        client.get("/api/does-not-exist-xyz")
    keys = list(client.get("/api/metrics").json()["counters"])
    for k in keys:
        if k.startswith("http.status."):
            code = k.rsplit(".", 1)[-1]
            assert code.isdigit() and len(code) == 3, "状态码键形态异常：%s" % k
        else:
            assert not any(ch.isdigit() for ch in k), "计数器名里出现了数字 ⇒ 可能是高基数键：%s" % k
