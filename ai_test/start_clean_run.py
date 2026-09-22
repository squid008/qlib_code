# -*- coding: utf-8 -*-
"""干净重跑（2026-09-22）—— 只改 3 个字段，其余参数与旧跑**完全一致** ✓（苹果对苹果 ✓）。

背景：旧跑 `9469bdc1f9b7` 是**续测** ✗（`resume_task_id=6d1367884bad` ✓）⇒ 前 2 段用**旧筹码** ✗、
后 6 段用**新筹码** ✗ ⇒ 净值图混口径 ✗（年化虚高到 ≈99% ✗，同期基线 10~17% ✓）。

本脚本的改动（**只这 3 项** ✗）：
  ① `resume_task_id`  : `6d1367884bad` → **None**   ⇒ **开新产物目录** ✓（绝不复用被污染的旧目录 ✓）；
  ② `end_date`        : `2026-08-01`   → **`2021-09-30`** ⇒ 只跑 2021-01~2021-09（≈9 段 ✓）——
     ⚠ 全区间 67 段 × ~40min ≈ **45 小时** ✗，先跑诊断窗口 ✓（覆盖观察到的异常月 2021-08 ✓）；
  ③ `load_model_task_id`: 本就是 None ⇒ 保持 None ✓（不引入外部模型 ✓）。
其余（`meta_gate` / `selected_features` / 成本 / 本金 / 池子 ✓）**一律不动** ✓
⇒ 这样跑出来的差异**只能**归因于"口径干净"与"旧跑被污染" ✓，不是"我顺手改了参数" ✗。

⚠ 只提交任务、不等待 ✓（后台跑 ✓，之后用 `/api/backtest/<id>` 看进度 ✓）。
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8001"
OLD_PARAMS = (r"d:\quant\qlib_code\backend\workdir\artifacts"
              r"\20260921-161809_LightGBM_all_2021_2026_6d1367884bad\params.json")


def get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(url, body, timeout=120):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    spec = get(API + "/openapi.json")
    schema = spec["components"]["schemas"].get("BacktestRequest")
    if not schema:
        print("✗ 取不到 BacktestRequest schema")
        return 2
    allowed = set(schema.get("properties", {}))
    p = json.load(open(OLD_PARAMS, encoding="utf-8"))
    dropped = sorted(k for k in p if k not in allowed)

    body = {k: v for k, v in p.items() if k in allowed}
    body["resume_task_id"] = None
    body["end_date"] = "2021-09-30"
    body["load_model_task_id"] = None

    print("  BacktestRequest 接受 %d 个字段；旧 params 里被丢弃 %d 个（多为运行时回填 ✓）：" % (len(allowed), len(dropped)))
    for k in dropped:
        print("      - %s" % k)
    print("")
    print("  ★ 本次关键三项：resume_task_id=None、end_date=%s、load_model_task_id=None"
          % body["end_date"])
    print("    区间 %s ~ %s | 池子 %s | 模型 %s | topk=%s hold=%s | meta_gate=%s"
          % (body.get("start_date"), body.get("end_date"), body.get("universe"),
             body.get("model"), body.get("topk"), body.get("n_days_hold"),
             body.get("meta_gate")))
    print("")
    try:
        r = post(API + "/api/backtest", body)
    except urllib.error.HTTPError as e:
        print("  ✗ 提交失败 %s：%s" % (e.code, e.read().decode("utf-8", "ignore")[:800]))
        return 1
    print("  ✓ 已提交：%s" % r)
    tid = r.get("task_id")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "clean_run_task_id.txt"), "w", encoding="utf-8") as f:
        f.write(str(tid))
    print("  task_id 已写入 ai_test/clean_run_task_id.txt ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
