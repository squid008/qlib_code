# -*- coding: utf-8 -*-
"""生成 `md/api_index.md`（★ P2-B，2026-10-18 ✓）。

用法（后端启动后跑 ✓，它读运行中实例的 `/openapi.json`）：

    cd D:\\quant\\qlib_code\\backend
    python tools/gen_api_index.py

为什么读**运行中**的 openapi 而不是 `from app.main import app` ✗：
  · 导入式会**初始化 qlib / 读配置**（几秒 ✓）且可能与运行实例的挂载不一致 ✗；
  · 读 HTTP 拿到的就是**线上真实路由表** ✓（正是索引想记录的东西 ✓）。

⚠ 索引文件由本工具生成 ✓ —— **别手改** ✗（下次一跑就覆盖 ✓）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

URL = os.environ.get("QLIB_OPENAPI_URL", "http://127.0.0.1:8001/openapi.json")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "md", "api_index.md")


def main() -> int:
    try:
        spec = json.loads(urllib.request.urlopen(URL, timeout=30).read().decode("utf-8"))
    except Exception as e:                                       # noqa: BLE001
        print("✗ 取不到 openapi（%s）：%r" % (URL, e))
        print("  先启动后端：powershell -File backend/workdir/restart_backend.ps1")
        return 1
    paths = spec.get("paths") or {}
    rows = []
    for p in sorted(paths):
        for method, op in sorted(paths[p].items()):
            rows.append((p, method.upper(), (op.get("summary") or "").strip(),
                         ",".join(op.get("tags") or [])))
    body = ["# API 索引（自动生成 ✓ **别手改** ✗）\n",
            "> 由 `backend/tools/gen_api_index.py` 从 `openapi.json` 生成（★ P2-B，2026-10-18 ✓）。\n",
            "> 路径 **%d** 条 ｜ 操作 **%d** 个。\n" % (len(paths), len(rows)),
            "\n| 方法 | 路径 | 说明 | tag |\n|---|---|---|---|\n"]
    for p, m, s, t in rows:
        body.append("| `%s` | `%s` | %s | %s |\n" % (m, p, s.replace("|", "/"), t))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(body))
    print("✓ 已写 %s（%d 路径 / %d 操作）" % (OUT, len(paths), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
