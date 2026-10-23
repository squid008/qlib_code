# -*- coding: utf-8 -*-
"""**可选的访问控制**（★ v2.11.67）—— token 鉴权 + 限流，**默认全关** ✓。

## 为什么默认关（而不是默认开）
本项目是**本机单用户**工具 ✓，且后端默认只绑 `127.0.0.1`（v2.11.50 ✓）——
对回环请求收 token **不增加任何安全性** ✗，只会让本地开发和 1900 条测试都得多带一个头 ✗。
⇒ 设计成**开关**：只在"**把它开给局域网/别人用**"时才需要 ✓（`BACKEND_HOST=0.0.0.0` 时配 token ✓）。

## 两个开关（都读环境变量，**每次请求重新读模块变量** ✓ ⇒ 测试可 monkeypatch）
| 变量 | 默认 | 作用 |
|---|---|---|
| `BACKEND_API_TOKEN` | 空 = **关闭** | 非空则要求 `Authorization: Bearer <token>`（裸 token 也认 ✓）|
| `BACKEND_RATE_LIMIT_RPM` | 0 = **关闭** | 每客户端**每分钟**请求上限，超了回 429 + `Retry-After` |

## ⚠ 为什么"每次请求重新读"而不是启动时固化
和 `metrics.SLOW_REQUEST_S` 同一个理由 ✓：**测试要能在不重启进程的情况下改它** ✓。
进程启动时读一次 ⇒ 想验证"超限返回 429"就得真发 60 个请求 ✗（慢且脆）。

## 限流口径
**滑动窗口 60 秒**（不是固定窗口 ✓ —— 固定窗口在边界处会放过 2 倍流量 ✗），
key = 客户端 IP ✓（本项目没有用户体系 ⇒ 只能到 IP ✓，够用于"防误开给公网"）。
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

#: API token（空串 = 关闭 ✓）。用 `.strip()` 防止 `export TOKEN=""` 这类空值被当成"开启"✗。
API_TOKEN: str = os.environ.get("BACKEND_API_TOKEN", "").strip()

#: 限流上限（次/分钟，0 = 关闭 ✓）。
RATE_LIMIT_RPM: int = int(os.environ.get("BACKEND_RATE_LIMIT_RPM") or 0)

#: **豁免路径** ✓ —— 探活与指标必须能匿名读，否则"服务活着但监控看不到"✗
#: （运维上最常见的自伤：加了鉴权把 healthcheck 也保护了 ✓）。
PUBLIC_PATHS: Tuple[str, ...] = ("/api/version", "/api/metrics")

_WINDOW_S = 60.0


def token_enabled() -> bool:
    return bool(API_TOKEN)


def rate_limit_enabled() -> bool:
    return RATE_LIMIT_RPM > 0


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS


def token_ok(header_value: Optional[str]) -> bool:
    """校验 `Authorization` 头 ✓（未开启 ⇒ 恒真 ✓）。

    ⚠ 用 `hmac.compare_digest` **常量时间比较** ✗→✓ —— 普通 `==` 会在第一个不同字节处返回 ✓，
    理论上可被逐字节计时试探 ✓（本项目风险极低，但这是个"写对了就永远不用管"的习惯 ✓）。
    """
    if not API_TOKEN:
        return True
    v = (header_value or "").strip()
    if v[:7].lower() == "bearer ":
        v = v[7:].strip()
    return hmac.compare_digest(v, API_TOKEN)


class RateLimiter:
    """**滑动窗口**限流 ✓（线程安全；`check` 返回 `(是否放行, 该等几秒)`）。

    ⚠ 为什么不用固定窗口（`计数/60秒` 清零）：窗口边界处**可以连发 2 倍上限** ✗
    （59.9s 发满 + 60.1s 再发满 ✓）。滑动窗口没这个缝 ✓。
    """

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()
        self._max_keys = 2048          # 防"海量 IP"把自身内存吃光 ✗（超了就整体清空 ✓）

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, key: str, *, now: Optional[float] = None) -> Tuple[bool, int]:
        limit = RATE_LIMIT_RPM
        if limit <= 0:
            return True, 0
        t = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > self._max_keys:
                self._hits.clear()
            q = self._hits.setdefault(key, deque())
            while q and t - q[0] >= _WINDOW_S:
                q.popleft()
            if len(q) >= limit:
                wait = int(_WINDOW_S - (t - q[0])) + 1
                return False, max(1, wait)
            q.append(t)
            return True, 0


#: 进程级单例 ✓（中间件每请求都用它 ✓）
LIMITER = RateLimiter()


def client_key(host: Optional[str]) -> str:
    """限流用的客户端标识 ✓（无用户体系 ⇒ 只能到 IP ✓；拿不到就当 `?` 合并计数 ✓）。"""
    return (host or "?").strip() or "?"
