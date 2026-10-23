# -*- coding: utf-8 -*-
"""**进程内**指标（计数器 + 耗时）—— ★ v2.11.25，技术债 P2-#8「无可观测性」的第一步。

## 为什么是"进程内"而不是 Prometheus
本项目的部署形态是**单机单进程**（`uvicorn` 一个 worker ✓，见 `scripts/restart_backend.ps1` ✓）
⇒ 上 Prometheus 要额外拉一个 TSDB + 一个 exporter ✗，收益远小于成本 ✓。
**先把"看什么"定下来** ✓（计数器与耗时分位 ✓），将来真要接 Prometheus 时
把 `snapshot()` 的结果映射成文本格式就行 ✓（不用改埋点 ✓）。

## 埋什么（**刻意只埋低基数**的 ✓）
· `http.requests` —— 总请求数 ✓
· `http.status.<code>` —— 按状态码（**有限** ✓，不会爆 ✓）
· `http.duration_s` —— 总耗时 + 次数 + **最大**（就够看出"有没有慢请求"✓）
⚠ **不按 URL 路径分桶** ✗ —— 本项目的路径里有 `task_id`/日期等**高基数**片段 ✓，
按路径分桶会让指标本身变成内存泄漏 ✗（Prometheus 用户最常踩的坑之一 ✓）。
要定位"是哪个接口慢"，看日志里的 `[吞]` 与 `timings` 字段 ✓，不是看指标 ✓。

## 线程安全
`uvicorn` 有线程池（同步端点跑在线程里 ✓，见 `routers/*` 都是 `def` 不是 `async def` ✓）
⇒ 计数器必须加锁 ✓（`threading.Lock` ✓，开销可忽略 ✓）。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict

#: **慢请求阈值（秒）** ✓ —— 超过就告警一条日志 ✓（★ v2.11.50）。
#:
#: 为什么加它：`/api/metrics` 只解决了"**事后**去查"✗，而最需要的是
#: "**某次请求慢到该看一眼**"时**当场**留一条痕迹 ✓。
#: 没有它的时候：用户报"卡了" ⇒ 只能翻全部日志猜是哪个接口 ✓（本项目真发生过 ✓）。
#:
#: · 默认 **3.0s** ✓（任务类接口都是**异步提交、立刻返回** ✓，
#:   真正同步跑几秒的只有面板/产物类 ⇒ 3s 已能抓住"异常变慢" ✓）；
#: · `SLOW_REQUEST_S=0` 或负数 ⇒ **关掉** ✓（压测时不想被刷屏 ✓）；
#: · 用环境变量而不是写死 ✓ —— CI / 压测 / 用户机器可以有不同界值 ✓。
SLOW_REQUEST_S: float = float(os.environ.get("SLOW_REQUEST_S") or 3.0)


def should_warn_slow(seconds: float) -> bool:
    """这次请求要不要告警 ✓（`SLOW_REQUEST_S <= 0` 表示关闭 ✓）。

    ⚠ 读的是**模块级变量**（而不是闭包捕获的常量 ✓）⇒ 测试可以
    `monkeypatch.setattr(metrics, "SLOW_REQUEST_S", 0.001)` 来验证 ✓，
    不用真等 3 秒 ✗（否则这条测试要么很慢、要么只能测到"没触发" ✓）。
    """
    return SLOW_REQUEST_S > 0 and seconds >= SLOW_REQUEST_S

_START = time.time()


class Registry:
    """计数器 + 耗时的**进程内**注册表 ✓。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._timers: Dict[str, Dict[str, float]] = {}

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(n)

    def observe(self, name: str, seconds: float) -> None:
        """记一次耗时 ⇒ `count / sum / max` ✓（不存分位 ✗：本项目 QPS 极低，
        分位没有统计意义 ✓；`max` 已经能暴露"某次特别慢"✓）。"""
        s = float(seconds)
        with self._lock:
            t = self._timers.get(name)
            if t is None:
                self._timers[name] = {"count": 1.0, "sum": s, "max": s}
            else:
                t["count"] += 1.0
                t["sum"] += s
                if s > t["max"]:
                    t["max"] = s

    def snapshot(self) -> dict:
        """**副本** ✓（调用方改了不影响内部 ✓）。"""
        with self._lock:
            counters = dict(sorted(self._counters.items()))
            timers = {k: dict(v) for k, v in sorted(self._timers.items())}
        for k, v in timers.items():
            v["avg"] = round(v["sum"] / v["count"], 6) if v["count"] else 0.0
            v["sum"] = round(v["sum"], 6)
            v["max"] = round(v["max"], 6)
        return {
            "uptime_s": round(time.time() - _START, 3),
            "counters": counters,
            "timers": timers,
        }

    def to_prometheus_text(self) -> str:
        """**Prometheus 文本格式** ✓（★ v2.11.41）—— 让"接监控"这件事变成**零改动** ✓。

        为什么加这个（模块 docstring 里说过"将来真要接 Prometheus，映射一下就行 ✓"）：
        · `json` 适合**人**看 ✓（`curl /api/metrics | jq` ✓）；
        · `text` 适合**机器**抓 ✓（Prometheus 的 `scrape_config` 直接吃 ✓，不用写 exporter ✓）。
        ⚠ 名字按 Prometheus 约定加单位后缀 ✓（`_total` 计数、`_seconds` 秒 ✓）——
        以后接真监控时，**dashboard 的查询语句就不用再改一遍** ✓。
        """
        snap = self.snapshot()
        lines = [
            "# HELP qlib_uptime_seconds 进程运行时长（秒）",
            "# TYPE qlib_uptime_seconds gauge",
            "qlib_uptime_seconds %s" % snap["uptime_s"],
        ]
        for name, n in (snap["counters"] or {}).items():
            metric = "qlib_" + name.replace(".", "_") + ("_total" if not name.endswith("_total") else "")
            lines.append("%s %d" % (metric, n))
        for name, t in (snap["timers"] or {}).items():
            # ⚠ 不能写 `.rstrip("s")` ✗ —— 那会把结尾所有 s 都剥掉（`duration_` ✗）。
            #   只精确切掉 `_s` 这个单位后缀 ✓（`duration_s` → `duration` ✓）。
            n2 = name.replace(".", "_")
            base = "qlib_" + (n2[:-2] if n2.endswith("_s") else n2)
            lines.append("# TYPE %s_seconds summary" % base)
            lines.append("%s_seconds_count %d" % (base, int(t["count"])))
            lines.append("%s_seconds_sum %s" % (base, t["sum"]))
            lines.append("%s_seconds_max %s" % (base, t["max"]))
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """清空（**只给测试用** ✓ —— 生产里清指标等于把历史抹掉 ✗）。"""
        with self._lock:
            self._counters.clear()
            self._timers.clear()


#: 全局唯一实例 ✓（埋点处直接 `from ..metrics import REGISTRY` ✓）
REGISTRY = Registry()
