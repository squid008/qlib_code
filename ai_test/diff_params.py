# -*- coding: utf-8 -*-
"""对比回测参数（**只比标量 + 大文本取指纹** ✓，避免被巨型表达式撑爆 ✗）。

目的（2026-09-21）：诊断"净值涨得夸张"（当前年化 ≈99% vs 基线 17%）✗。
关键：**基线那次（09-17）用的也是旧筹码** ✗ ⇒ 筹码解释不了 ⇒ 必须比参数 ✓。
"""
import hashlib
import json
import os
import sys

ART = r"d:\quant\qlib_code\backend\workdir\artifacts"
TASKS = {
    "NOW(9469bdc1f9b7/续测)": "20260921-161809_LightGBM_all_2021_2026_6d1367884bad",
    "today 15:44": "20260921-154459_LightGBM_all_2021_2026_b6345a63cfca",
    "today 14:07": "20260921-140741_LightGBM_all_2021_2026_5e1f338dc34e",
    "BASE 09-17(年化17.35%)": "20260917-130228_LightGBM_all_2021_2021_00e9caa98000",
    "09-15(年化10.49%)": "20260915-005141_LightGBM_all_2021_2023_c2caacbbf141",
}


def load(dirname):
    p = os.path.join(ART, dirname, "params.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def fp(v):
    s = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return "len=%d md5=%s" % (len(s), hashlib.md5(s.encode("utf-8")).hexdigest()[:10])


def main():
    data = {k: load(v) for k, v in TASKS.items()}
    names = list(data)
    keys = []
    for n in names:
        if data[n]:
            for k in data[n]:
                if k not in keys:
                    keys.append(k)
    print("  %-26s %s" % ("字段", "  ".join("%-24s" % n for n in names)))
    for k in keys:
        vals = [data[n].get(k, "—") if data[n] else "·" for n in names]
        if all(isinstance(v, (int, float, str, bool, type(None))) for v in vals):
            if len({str(v) for v in vals}) == 1:
                continue                       # 全一致就跳过（只看差异 ✓）
            print("  ★ %-24s %s" % (k, "  ".join("%-24s" % str(v)[:24] for v in vals)))
        else:
            fps = [fp(v) if v != "—" else "·" for v in vals]
            if len(set(fps)) == 1:
                print("  = %-24s %s" % (k, fps[0]))
            else:
                print("  ★ %-24s %s" % (k, "  ".join(fps)))
    print("")
    print("  ★ = 各方不一致的字段（重点看这些 ✗）")


if __name__ == "__main__":
    main()
    sys.exit(0)
