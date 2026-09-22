# -*- coding: utf-8 -*-
"""同期对照：现在的 2021 净值 vs 09-17 那次完整跑（00e9caa98000，年化 17.35%）的 2021 净值 ✓。

这是"苹果对苹果" ✓（同一年、同一模型/池子 ✓），用来回答"现在这个 99% 年化正常吗" ✗。
"""
import json
import os
import sys

ART = r"d:\quant\qlib_code\backend\workdir\artifacts"
B_BASE = "20260917-130228_LightGBM_all_2021_2021_00e9caa98000"    # 09-17 完整跑（年化 17.35% ✓）


def _now_dir():
    """默认对照**干净重跑**（`start_clean_run.py` 写的 task_id ✓），也支持命令行传 task_id ✓。"""
    if len(sys.argv) > 1:
        tid = sys.argv[1].strip()
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clean_run_task_id.txt")
        tid = open(p, encoding="utf-8").read().strip() if os.path.exists(p) else ""
    if tid:
        for n in os.listdir(ART):
            if n.endswith("_" + tid):
                return n
    # 兜底：拿最新的那个（含已被污染的旧续测 ✗，仅用于对照 ✓）
    dirs = [n for n in os.listdir(ART) if os.path.isdir(os.path.join(ART, n))]
    return sorted(dirs, key=lambda n: os.path.getmtime(os.path.join(ART, n)))[-1]


A_NOW = _now_dir()


def nav_of(dirname):
    d = os.path.join(ART, dirname)
    for fn in ("partial_result.json", "result.json"):
        p = os.path.join(d, fn)
        if os.path.exists(p):
            try:
                j = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            nv = j.get("nav")
            if nv:
                return [(x["date"], float(x["value"])) for x in nv]
    return []


def at(nav, day):
    best = None
    for d, v in nav:
        if d <= day:
            best = (d, v)
        else:
            break
    return best


def main():
    a, b = nav_of(A_NOW), nav_of(B_BASE)
    print("  现在(进行中)  nav 点=%d  %s ~ %s" % (len(a), a[0][0] if a else "-", a[-1][0] if a else "-"))
    print("  基线(09-17)   nav 点=%d  %s ~ %s" % (len(b), b[0][0] if b else "-", b[-1][0] if b else "-"))
    if not a or not b:
        print("  数据不足，无法对照")
        return
    print("")
    print("  %-12s %-22s %-22s" % ("日期", "现在(进行中)", "基线 09-17(完整)"))
    for day in ("2021-01-04", "2021-02-01", "2021-03-01", "2021-04-01", "2021-05-03",
                "2021-06-01", "2021-07-01", "2021-08-02", "2021-09-01"):
        pa, pb = at(a, day), at(b, day)
        print("  %-12s %-22s %-22s" % (day,
                                       ("%s  %.4f" % pa) if pa else "-",
                                       ("%s  %.4f" % pb) if pb else "-"))
    print("")
    pa1, pb1 = at(a, "2021-09-01"), at(b, "2021-09-01")
    if pa1 and pb1:
        print("  ★ 同期对照：现在 %.4f  vs  基线 %.4f  ⇒ 倍数 %.2fx"
              % (pa1[1], pb1[1], pa1[1] / pb1[1]))


if __name__ == "__main__":
    main()
    sys.exit(0)
