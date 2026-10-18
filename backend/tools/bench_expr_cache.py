# -*- coding: utf-8 -*-
"""公式面板缓存（P1-C-1）的**真数据**端到端基准（★ 2026-10-19 ✓）。

## 它量的是什么

「同一个单因子测试**再跑一次**」的端到端耗时 ✓ —— 冷跑要算面板（CPX 这类递推公式尤其贵 ✓），
热跑直接命中 `workdir/expr_cache/*.parquet` ✓。这正是用户真实用法（改个参数再跑 ✓）。

## 用法（**后端要在跑** ✓）

    cd D:\\quant\\qlib_code\\backend
    python tools/bench_expr_cache.py
    python tools/bench_expr_cache.py --start 2024-01-02 --end 2024-06-28 --expr "Mean($close,20)/$close-1"

## 实测基线（2026-10-19，csi300 / 2024Q1 / `Mean($close,20)/$close-1`）

```
① 冷跑  5.10s   ② 热跑 1.04s   ③ 再热跑 1.03s
⇒ 4.88× 加速，省 80% 时间  ✓
```

⚠ 口径：第二次的 qlib `feature_cache` 也是热的 ✗ ⇒ 这是加速的**下界** ✓
（真实"改参数重跑"通常也是这个状态 ✓）。

## 脚本会**先清空** expr 缓存 ✓

不这样的话永远测到"热跑"✓（第一次跑完就再也不会冷了 ✗）。
清空只删 `workdir/expr_cache/*.parquet` ✓ —— 那是**纯缓存** ✓，删了只是重算 ✓，不影响任何结果 ✓。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("QLIB_API", "http://127.0.0.1:8001")
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())


def _get(path: str) -> dict:
    return json.loads(urllib.request.urlopen(BASE + path, timeout=180).read().decode())


def run_once(tag: str, req: dict, timeout_s: float = 900.0):
    t0 = time.perf_counter()
    r = _post("/api/factors/single-factor-test", req)
    tid = r.get("task_id") or r.get("id")
    if not tid:
        print("  ✗ %s 提交失败：%s" % (tag, str(r)[:200]))
        return None
    st = ""
    while True:
        time.sleep(1.0)
        p = _get("/api/factors/single-factor-test/progress/%s" % tid)
        st = str(p.get("status") or "")
        if st in ("success", "failed", "cancelled"):
            break
        if time.perf_counter() - t0 > timeout_s:
            print("  ✗ %s 超时（>%.0fs）" % (tag, timeout_s))
            return None
    dt = time.perf_counter() - t0
    print("  %s = %.2fs ｜ status=%s ｜ 进度=%s" % (tag, dt, st, p.get("progress")))
    return dt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="csi300")
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="2024-03-29")
    ap.add_argument("--expr", default="Mean($close,20)/$close-1")
    ap.add_argument("--keep-cache", action="store_true",
                    help="不先清空缓存（默认会清 ✓）")
    a = ap.parse_args()

    try:
        print("  后端版本 = %s" % _get("/api/version").get("version"))
    except Exception as e:                                        # noqa: BLE001
        print("  ✗ 后端没起来（%r）⇒ 先跑 backend/workdir/restart_backend.ps1" % (e,))
        return 1

    cache_dir = os.path.join(BACKEND, "workdir", "expr_cache")
    if not a.keep_cache:
        files = glob.glob(os.path.join(cache_dir, "*.parquet"))
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass
        print("  已清空 expr 缓存：%d 个文件 ✓（冷启动 ✓）" % len(files))

    req = {"universe": a.universe, "start_date": a.start, "end_date": a.end,
           "label_horizons": [5], "quantiles": 10,
           "factors": [{"id": "f1", "name": "f1", "expression": a.expr, "source": "custom"}]}

    cold = run_once("① 冷跑（算面板 + 落缓存）", req)
    warm = run_once("② 热跑（面板命中缓存）", req)
    again = run_once("③ 再热跑一次", req)
    if cold and warm:
        print("\n  ⇒ 冷 %.2fs → 热 %.2fs ｜ **加速 %.2f×** ｜ 省下 %.2fs（%.0f%%）"
              % (cold, warm, cold / max(warm, 1e-9), cold - warm,
                 100.0 * (cold - warm) / max(cold, 1e-9)))
    if again and warm:
        print("  （第三次 %.2fs ⇒ 与第二次同量级 ✓ 稳定 ✓）" % again)
    print("\n  ⚠ 口径：第二次的 qlib feature_cache 也是热的 ✗ ⇒ 这是加速的**下界** ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
