# -*- coding: utf-8 -*-
"""诊断"净值涨得夸张"—— 重点看**跳点是否落在段边界** ✓。

背景（2026-09-21）：`chip_*` 在 **17:55** 被重物化（换手率口径修正 ✓），而运行的续测任务
`9469bdc1f9b7` 接着旧目录写 ⇒ `segment_1/2` 是**旧筹码** ✗、`segment_3+` 是**新筹码** ✗
⇒ 同一张净值图混了两套特征口径 ✗。若净值在 **segment_2→3 边界**上出现台阶 ⇒ 坐实 ✓。
"""
import json
import os
import sys

import numpy as np

P = (r"d:\quant\qlib_code\backend\workdir\artifacts"
     r"\20260921-161809_LightGBM_all_2021_2026_6d1367884bad\partial_result.json")


def main():
    j = json.load(open(P, encoding="utf-8"))
    print("  segments_done=%s / total=%s" % (j.get("segments_done"), j.get("segments_total")))
    nav = j.get("nav") or []
    if not nav:
        print("  nav 为空")
        return
    d = [x["date"] for x in nav]
    v = np.asarray([float(x["value"]) for x in nav], dtype=float)
    b = np.asarray([float(x.get("benchmark") or np.nan) for x in nav], dtype=float)
    print("  nav: %s ~ %s  (%d 点)" % (d[0], d[-1], len(v)))
    print("  末值 %.4f | 最大 %.4f (%s) | 基准末值 %.4f"
          % (v[-1], v.max(), d[int(np.argmax(v))], b[-1]))
    yrs = (np.datetime64(d[-1]) - np.datetime64(d[0])).astype("timedelta64[D]").astype(int) / 365.25
    print("  区间 %.2f 年 ⇒ 年化约 %.1f%% | 基准年化约 %.1f%% | 年化超额约 %.1f%%"
          % (yrs, (v[-1] ** (1 / yrs) - 1) * 100,
             ((b[-1] ** (1 / yrs)) - 1) * 100,
             ((v[-1] / b[-1]) ** (1 / yrs) - 1) * 100))
    r = np.diff(v) / v[:-1]
    print("")
    print("  单日收益 Top8（看有没有离谱单日 ✗）:")
    for i in np.argsort(-np.abs(r))[:8]:
        print("    %s  %+7.2f%%   nav %.4f → %.4f" % (d[i + 1], r[i] * 100, v[i], v[i + 1]))
    print("")
    print("  净值在**段边界**附近（segment_2 结束 ≈ 2021-08-31 ⇒ 每段 6 个月）:")
    for seg_start in ("2021-01-04", "2021-03-01", "2021-09-01"):
        idx = [i for i, x in enumerate(d) if x <= seg_start]
        if idx:
            i = idx[-1]
            print("    %s   nav=%.4f" % (d[i], v[i]))
    print("")
    print("  判读：若出现**单日跳变**（远大于正常日波动 ✗）或**只在某段之后开始陡涨** ⇒")
    print("        基本是口径/数据混用造成的**假收益** ✗（不是策略真本事 ✓）。")


if __name__ == "__main__":
    main()
    sys.exit(0)
