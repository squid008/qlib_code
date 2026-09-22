# -*- coding: utf-8 -*-
"""实验 A（2026-09-22）：**只改 `selected_features`** ✓ —— 其余一字不动 ✓。

假设：`mean_ic = 0.531` ✗（正常 0.02~0.05 ✓）⇒ 523 特征集里有 **future leak** ✗。
A 组做法：把 `selected_features` 换成**基线 09-17（17.35% ✓，无 leak ✓）所用的那 7 个 A158** ✓
  ⇒ 若 IC 塌回 0.02~0.05 ✓ ⇒ leak 在"被换掉的那些特征"里 ✓；
  ⇒ 若 IC 仍 ≈0.5 ✗ ⇒ leak 不在特征上 ✓，而在别处（`meta_gate` 的巨型公式 ✓ / label 口径 ✓）。
区间缩到 **3 段**（2021-01-01~2021-03-31 ✓ ≈2 小时 ✓）：看 `mean_ic` 与 5 层单调性**不需要更长** ✓。

⚠ 基准是把 `2021-01-04` 那段（`ic` 从 0.36 起 ✓）跑出来即可 ✓。
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8001"
HERE = os.path.dirname(os.path.abspath(__file__))
ART = r"d:\quant\qlib_code\backend\workdir\artifacts"
BASE_DIR = "20260917-130228_LightGBM_all_2021_2021_00e9caa98000"   # 基线：年化 17.35%、7 特征 ✓
CLEAN_TID_FILE = os.path.join(HERE, "clean_run_task_id.txt")
OUT_TID = os.path.join(HERE, "ab_a_task_id.txt")


def get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(url, body, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def find_dir(tid):
    if tid:
        for n in os.listdir(ART):
            if n.endswith("_" + tid):
                return n
    return None


def main():
    # ---- 基准 body：拿刚才那次"干净重跑"的 params（它已经 resume_task_id=None ✓）----
    tid = open(CLEAN_TID_FILE, encoding="utf-8").read().strip() if os.path.exists(CLEAN_TID_FILE) else ""
    src = find_dir(tid)
    if not src:
        print("✗ 找不到干净重跑的目录（clean_run_task_id.txt = %r）" % tid)
        return 2
    p = json.load(open(os.path.join(ART, src, "params.json"), encoding="utf-8"))
    base = json.load(open(os.path.join(ART, BASE_DIR, "params.json"), encoding="utf-8"))
    feats = base.get("selected_features")

    allowed = set(get(API + "/openapi.json")["components"]["schemas"]
                  ["BacktestRequest"].get("properties", {}))
    body = {k: v for k, v in p.items() if k in allowed}

    print("  基准目录      = %s" % src)
    print("  A 组唯一改动  = selected_features → %s" % json.dumps(feats, ensure_ascii=False))
    print("  区间          = %s ~ 2021-03-31（3 段 ✓）" % body.get("start_date"))
    print("  其余保持：universe=%s model=%s topk=%s hold=%s meta_gate=%s custom_formulas=%d 条"
          % (body.get("universe"), body.get("model"), body.get("topk"),
             body.get("n_days_hold"), body.get("meta_gate"),
             len(body.get("custom_formulas") or [])))

    body["selected_features"] = feats
    body["end_date"] = "2021-03-31"
    body["resume_task_id"] = None
    body["load_model_task_id"] = None
    print("")
    try:
        r = post(API + "/api/backtest", body)
    except urllib.error.HTTPError as e:
        print("  ✗ 提交失败 %s：%s" % (e.code, e.read().decode("utf-8", "ignore")[:600]))
        return 1
    print("  ✓ 已提交 A：%s" % r)
    with open(OUT_TID, "w", encoding="utf-8") as f:
        f.write(str(r.get("task_id")))
    print("  task_id 已写入 ai_test/ab_a_task_id.txt ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
