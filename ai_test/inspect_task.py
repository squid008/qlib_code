# -*- coding: utf-8 -*-
"""看正在跑的回测任务里，"分段收益曲线" 与 "分层回测" 两组结果的结构与数值。

用户 2026-09-21：「第一段收益曲线跑赢基准，怎么分层回测反而是跑不过基准了？有 BUG 么？」
用法：`python inspect_task.py <任务ID>`
"""
import json
import os
import sys

BASE = r"d:\quant\qlib_code\backend\workdir\artifacts"


def _find(tid):
    for d in os.listdir(BASE):
        if tid in d or tid in d.replace("-", ""):
            return os.path.join(BASE, d)
    return None


def _keys(o, prefix="", depth=0):
    """打印结构（只到 depth 层，避免刷屏 ✓）。"""
    if depth > 2:
        return
    if isinstance(o, dict):
        for k, v in list(o.items())[:40]:
            t = type(v).__name__
            extra = ""
            if isinstance(v, list):
                extra = " len=%d" % len(v)
                if v and isinstance(v[0], dict):
                    extra += " item0_keys=%s" % list(v[0].keys())[:12]
            elif isinstance(v, dict):
                extra = " keys=%s" % list(v.keys())[:12]
            elif isinstance(v, (int, float, str, bool)) or v is None:
                s = str(v)
                extra = " = " + (s[:80] if len(s) <= 80 else s[:80] + "…")
            print("  " * depth + "%s: %s%s" % (k, t, extra))
            if isinstance(v, (dict, list)) and depth < 2:
                _keys(v if isinstance(v, dict) else (v[0] if v else {}),
                      prefix, depth + 1)


def main(tid):
    d = _find(tid)
    print("任务目录:", d)
    if not d:
        return
    p = os.path.join(d, "partial_result.json")
    if not os.path.exists(p):
        print("无 partial_result.json")
        return
    j = json.load(open(p, encoding="utf-8"))
    print("")
    print("=== partial_result.json 结构 ===")
    _keys(j)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "5e1f338dc34e")
