# -*- coding: utf-8 -*-
"""对比「分段净值曲线」与「分层回测」两组的**末尾数值**（用户 2026-09-21 提问）。"""
import json
import os
import sys

BASE = r"d:\quant\qlib_code\backend\workdir\artifacts"


def _find(tid):
    for d in os.listdir(BASE):
        if tid in d:
            return os.path.join(BASE, d)
    return None


def main(tid):
    d = _find(tid)
    j = json.load(open(os.path.join(d, "partial_result.json"), encoding="utf-8"))

    nav = j["nav"]
    print("=== 分段净值曲线 nav（段1）===")
    print("  点数 = %d   %s ~ %s" % (len(nav), nav[0]["date"], nav[-1]["date"]))
    for r in (nav[0], nav[len(nav) // 2], nav[-1]):
        print("    %s  value=%.6f  benchmark=%.6f  超额=%+.2f%%"
              % (r["date"], r["value"], r["benchmark"],
                 (r["value"] / r["benchmark"] - 1) * 100))
    print("  末点：组合 %.4f%%  基准 %.4f%%  超额 %+.4f%%"
          % ((nav[-1]["value"] - 1) * 100, (nav[-1]["benchmark"] - 1) * 100,
             (nav[-1]["value"] - nav[-1]["benchmark"]) * 100))

    lr = j["layer_returns"]
    for key in ("segments", "merged"):
        blk = lr[key]
        if isinstance(blk, list):
            if not blk:
                print("\n=== 分层回测 layer_returns.%s === (空)" % key)
                continue
            blk = blk[0]
        g = blk["groups"]
        print("\n=== 分层回测 layer_returns.%s（%s）===" % (key, blk.get("segment")))
        print("  点数 = %d   基准 = %s   %s ~ %s"
              % (len(g), blk.get("benchmark"), g[0]["date"], g[-1]["date"]))
        cols = [k for k in g[-1].keys() if k != "date"]
        print("  末点累计：")
        for c in cols:
            print("    %-14s %.6f   (%+.2f%%)" % (c, g[-1][c], (g[-1][c] - 1) * 100))
        print("  首点：")
        for c in cols:
            print("    %-14s %.6f" % (c, g[0][c]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "5e1f338dc34e")
