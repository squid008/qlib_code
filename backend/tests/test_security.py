# -*- coding: utf-8 -*-
"""**可选访问控制**（★ v2.11.67 —— `app/security.py`）。

## 这条为什么以前一直扣分（安全 4/10 分那项）
"**无鉴权 / 无限流**" ✓ —— 本机单用户时**合理**✓，但一旦 `BACKEND_HOST=0.0.0.0`
开给局域网/别人用，就是"谁都能跑你的回测、还能把 CPU 占满" ✗。

## 这里的口径（测试要守住的）
· **默认全关** ✓ —— 回环单用户场景**零行为变化** ✓（1900 条测试不用带 token ✓）；
· 开启后：**公开路径豁免** ✓（`/api/version` 探活、`/api/metrics` 监控 ——
  把它们也保护起来是最常见的自伤 ✗：服务活着但监控/探活看不到 ✓）；
· **401/429 也要进指标** ✓（不然"被拦了多少"根本看不见 ✗）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.security as S
from app.main import app
from app.metrics import REGISTRY

client = TestClient(app)

#: 一个**静态**、不读盘、不 init qlib 的受保护端点 ✓（`options` 只回常量表 ✓）。
#: ⚠ 路径别猜 ✗ —— 路由前缀是 `APIRouter(prefix="/api/signal-**test**")` ✓，
#:   我第一版写成 `/api/signals/options` ⇒ **404** ⇒ 4 条测试"红"得莫名其妙 ✓（其实是路径错 ✗）。
PROTECTED = "/api/signal-test/options"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每条都从"全关"开始 ✓ —— 否则前一条留下的 429/401 会污染后一条 ✗。"""
    monkeypatch.setattr(S, "API_TOKEN", "")
    monkeypatch.setattr(S, "RATE_LIMIT_RPM", 0)
    S.LIMITER.reset()
    yield
    S.LIMITER.reset()


class TestDefaultsAreOff:
    def test_no_token_no_limit_by_default(self):
        """⭐ 默认全关：受保护端点**匿名可访问** ✓（本机单用户的正常形态 ✓）。"""
        assert client.get(PROTECTED).status_code == 200

    def test_token_ok_true_when_disabled(self):
        assert S.token_ok(None) is True
        assert S.token_ok("随便什么") is True

    def test_limiter_allows_everything_when_disabled(self):
        for _ in range(50):
            assert S.LIMITER.check("1.2.3.4")[0] is True


class TestToken:
    def test_missing_token_is_401(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        r = client.get(PROTECTED)
        assert r.status_code == 401
        assert r.headers.get("www-authenticate") == "Bearer"
        assert "Bearer" in r.json()["detail"], "错误信息要告诉人怎么带 ✓"

    def test_wrong_token_is_401(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        r = client.get(PROTECTED, headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    def test_bearer_token_ok(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        r = client.get(PROTECTED, headers={"Authorization": "Bearer secret-1"})
        assert r.status_code == 200

    def test_bare_token_also_ok(self, monkeypatch):
        """裸 token（不带 `Bearer `）也认 ✓ —— 方便 `curl -H "Authorization: xxx"` ✓。"""
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        r = client.get(PROTECTED, headers={"Authorization": "secret-1"})
        assert r.status_code == 200

    def test_bearer_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        r = client.get(PROTECTED, headers={"Authorization": "BEARER secret-1"})
        assert r.status_code == 200

    @pytest.mark.parametrize("path", ["/api/version", "/api/metrics"])
    def test_public_paths_stay_open(self, monkeypatch, path):
        """⭐⭐ 探活/监控**必须豁免** ✓ —— 加了鉴权把它们也锁上，就是"服务活着但没人知道"✗。"""
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        assert client.get(path).status_code == 200

    def test_unknown_token_does_not_leak_expected_value(self, monkeypatch):
        """错误信息里**不许出现正确 token** ✓（日志/前端错误提示都会带出去 ✗）。"""
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        r = client.get(PROTECTED, headers={"Authorization": "Bearer nope"})
        assert "secret-1" not in r.text


class TestRateLimit:
    def test_exceed_returns_429_with_retry_after(self, monkeypatch):
        monkeypatch.setattr(S, "RATE_LIMIT_RPM", 2)
        codes = [client.get(PROTECTED).status_code for _ in range(3)]
        assert codes[:2] == [200, 200], "上限内应全部放行 ✓：%s" % codes
        assert codes[2] == 429, "第 3 次应被限流 ✓：%s" % codes
        r = client.get(PROTECTED)
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) >= 1, "必须告诉客户端等多久 ✓（否则只能瞎重试 ✗）"

    def test_sliding_window_expires(self):
        """⭐ 滑动窗口：60 秒后的老记录**必须过期** ✓（否则限流会永久累积 ⇒ 变成"封禁"✗）。"""
        lim = S.RateLimiter()
        S.RATE_LIMIT_RPM = 1          # 直接改类级读的模块变量 ✓（下面 finally 还原 ✓）
        try:
            assert lim.check("k", now=0.0)[0] is True
            assert lim.check("k", now=1.0)[0] is False
            assert lim.check("k", now=61.0)[0] is True, "过了窗口应当恢复 ✓"
        finally:
            S.RATE_LIMIT_RPM = 0

    def test_keys_are_isolated(self):
        lim = S.RateLimiter()
        S.RATE_LIMIT_RPM = 1
        try:
            assert lim.check("a", now=0.0)[0] is True
            assert lim.check("b", now=0.0)[0] is True, "不同客户端互不影响 ✓"
            assert lim.check("a", now=0.0)[0] is False
        finally:
            S.RATE_LIMIT_RPM = 0

    def test_401_still_counted_in_metrics(self, monkeypatch):
        """⭐⭐ 中间件顺序：**metrics 要在 guard 外层** ✓ —— 否则被拦的请求不进指标 ✗，
        "今天被扫了多少次"就永远看不到 ✓。
        """
        monkeypatch.setattr(S, "API_TOKEN", "secret-1")
        REGISTRY.reset()
        client.get(PROTECTED)
        snap = REGISTRY.snapshot()
        keys = str(snap)
        assert "401" in keys, "401 没进指标 ⇒ guard 挂在了 metrics 外层 ✗：%s" % keys[:200]
