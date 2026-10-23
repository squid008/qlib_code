#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**指标抓取端 + 告警判定**（★ v2.11.61）—— 给 `/api/metrics?format=text` 补上"有人看"。

## 为什么需要它（这是"可观测性"缺的那一环）
后端早就有 `/api/metrics`（JSON + Prometheus 文本 ✓）和慢请求告警日志 ✓，
但**没有抓取端** ✗ —— 指标写在那里，**没人定时看** ✓，出事还是翻日志 ✓。
（外部评审原话：「有 `/api/metrics` 是 0→1 的进步，但**没有抓取、告警、看板**」✓）

## 三种用法
```bash
# ① 人看：拉一次，打印一张小看板
python scripts/scrape_metrics.py

# ② 机器看（cron / 定时任务）：超阈值就以非 0 退出 ⇒ 能被任何调度器当"告警"用
python scripts/scrape_metrics.py --max-error-rate 5 --max-avg-ms 2000

# ③ 接真监控：打印 Prometheus 的 scrape_config + 一条告警规则（贴过去即可）
python scripts/scrape_metrics.py --prometheus-config
```

## ⚠ 为什么不做成"内置定时器/看板"
本项目是**单进程、本机单用户** ✓ —— 内置一个后台抓取线程只会让它多一个"没人看的状态"✗。
真正的看板要么是 Prometheus+Grafana（`--prometheus-config` 已经把配置生成好 ✓），
要么就是你 crontab 里一行 `scrape_metrics.py --max-error-rate 5` ✓。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8001"


def fetch(url: str, timeout: float) -> str:
    """拉 Prometheus 文本（`?format=text` ✓）。"""
    full = url.rstrip("/") + "/api/metrics?format=text"
    with urllib.request.urlopen(full, timeout=timeout) as r:   # noqa: S310 —— 只打本机
        return r.read().decode("utf-8", "replace")


def parse(text: str) -> dict:
    """`名字 数值` → dict（跳过 `#` 注释 ✓；解析不了的整行跳过并计数 ✓）。"""
    out: dict = {}
    bad = 0
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        if len(parts) != 2:
            bad += 1
            continue
        try:
            out[parts[0]] = float(parts[1])
        except ValueError:
            bad += 1
    if bad:
        out["_unparsed_lines"] = bad
    return out


def summarize(m: dict) -> dict:
    """从原始指标里**算出人真正关心的那几个数** ✓（而不是把 20 行原始值丢给人 ✓）。"""
    req = m.get("qlib_http_requests_total", 0.0)
    # 状态码计数器 → 5xx 占比（`qlib_http_status_5xx_total` 是每个具体码一个 ✓）
    err = sum(v for k, v in m.items() if re.match(r"qlib_http_status_5\d\d_total$", k))
    return {
        "uptime_s": m.get("qlib_uptime_seconds", 0.0),
        "requests": req,
        "errors_5xx": err,
        "error_rate_pct": (100.0 * err / req) if req else 0.0,
        "dur_avg_ms": (1000.0 * m.get("qlib_http_duration_seconds_sum", 0.0) /
                       m["qlib_http_duration_seconds_count"])
        if m.get("qlib_http_duration_seconds_count") else 0.0,
        "dur_max_ms": 1000.0 * m.get("qlib_http_duration_seconds_max", 0.0),
    }


PROM_CONFIG = """\
# ① 让 Prometheus 来抓（贴到 prometheus.yml 的 scrape_configs 下 ✓）
scrape_configs:
  - job_name: qlib_backend
    scrape_interval: 15s
    static_configs:
      - targets: ['127.0.0.1:8001']
    metrics_path: /api/metrics
    params:
      format: ['text']

# ② 两条最该有的告警（贴到 rules 里 ✓）
groups:
  - name: qlib
    rules:
      - alert: QlibSlowRequests
        # 5 分钟内平均耗时 > 2s（本项目同步接口大多在百毫秒级 ✓）
        expr: rate(qlib_http_duration_seconds_sum[5m]) / rate(qlib_http_duration_seconds_count[5m]) > 2
        for: 5m
        annotations:
          summary: "后端平均响应 > 2s"
      - alert: QlibServerErrors
        expr: rate(qlib_http_status_500_total[5m]) > 0
        for: 2m
        annotations:
          summary: "出现 5xx"
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL, help="后端地址（默认本机 8001）")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    ap.add_argument("--prometheus-config", action="store_true", help="打印抓取/告警配置")
    ap.add_argument("--max-error-rate", type=float, default=None,
                    help="5xx 占比上限（%%）；超了退出码 1 ⇒ 可当告警用")
    ap.add_argument("--max-avg-ms", type=float, default=None, help="平均耗时上限（ms）；超了退出码 1")
    a = ap.parse_args()

    if a.prometheus_config:
        print(PROM_CONFIG)
        return 0

    try:
        text = fetch(a.url, a.timeout)
    except (urllib.error.URLError, OSError) as e:
        print("✗ 拉不到 %s/api/metrics ⇒ %r" % (a.url, e),
              "（后端没起？`scripts\\restart_backend.ps1` ✓）")
        return 2

    m = parse(text)
    s = summarize(m)
    if a.json:
        print(json.dumps({"raw": m, "summary": s}, ensure_ascii=False, indent=2))
    else:
        print("\n  后端指标（%s）" % a.url)
        print("  " + "-" * 52)
        print("  运行时长        %8.1f 分钟" % (s["uptime_s"] / 60.0))
        print("  请求总数        %8d" % int(s["requests"]))
        print("  5xx             %8d  (%.2f%%)" % (int(s["errors_5xx"]), s["error_rate_pct"]))
        print("  平均耗时        %8.1f ms" % s["dur_avg_ms"])
        print("  最慢一次        %8.1f ms   ← 慢请求告警阈值在 /api/metrics 的日志里 ✓"
              % s["dur_max_ms"])
        bad = [k for k in m if k.startswith("qlib_http_status_4")]
        if bad:
            print("  4xx 分类        %s" % ", ".join("%s=%d" % (k.replace("qlib_http_status_", ""),
                                                             int(m[k])) for k in sorted(bad)))
        if m.get("_unparsed_lines"):
            print("  ⚠ 有 %d 行解析不了（格式变了？）" % int(m["_unparsed_lines"]))
        print("  " + "-" * 52)

    rc = 0
    if a.max_error_rate is not None and s["error_rate_pct"] > a.max_error_rate:
        print("  ✗ 5xx 占比 %.2f%% > 上限 %.1f%%" % (s["error_rate_pct"], a.max_error_rate))
        rc = 1
    if a.max_avg_ms is not None and s["dur_avg_ms"] > a.max_avg_ms:
        print("  ✗ 平均耗时 %.1fms > 上限 %.1fms" % (s["dur_avg_ms"], a.max_avg_ms))
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
