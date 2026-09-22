# -*- coding: utf-8 -*-
"""校验「回测净值」与「分层收益」是否自洽 ✓（回答"回测曲线算错了？"）。

两条线是**不同代码路径**算出来的：
  · `nav`           ← qlib 回测（`PortAnaRecord` ✓ topk=50 / 20 日持有 / 含成本 ✓）
  · `layer_returns` ← `_compute_analysis` 的**分层**（每日按分数分 5 组、组内等权 ✓）
⇒ 若两者**量级/符号/相关性**对得上 ⇒ 曲线**不是**算错的 ✓；
  若净值显著"凭空多赚"（例如分层组亏、净值却大涨 ✗）⇒ 就该怀疑回测路径 ✓。

⚠ 20 日持有的组合，其**当日净值变化**与"当日分组的未来 20 日收益"**不是同一时点口径** ✓
  ⇒ 因此看的是**累计量级 + 日度相关性**，而不是逐日相等 ✓。
"""
import json
import os
import sys

import numpy as np

P = (r"d:\quant\qlib_code\backend\workdir\artifacts"
     r"\20260922-000538_LightGBM_all_2021_2021_fd76879e00f0\result.json")


def main():
    r = json.load(open(P, encoding="utf-8"))
    nav = [(x["date"], float(x["value"]), float(x.get("benchmark") or np.nan))
           for x in (r.get("nav") or [])]
    groups = ((r.get("layer_returns") or {}).get("merged") or {}).get("groups") or []
    print("  nav 点=%d  %s ~ %s" % (len(nav), nav[0][0], nav[-1][0]))
    print("  layer groups 点=%d" % len(groups))
    if not nav or not groups:
        print("  数据不足")
        return

    gd, gl, gu = [], [], []
    for g in groups:
        if isinstance(g, str):                      # PowerShell 导出成字符串时兜底
            continue
        gd.append(g.get("date"))
        gl.append(float(g.get("long_average") or np.nan))
        gu.append(float(g.get("universe") or np.nan))

    print("")
    print("  %-12s %10s %10s %10s %10s" % ("日期", "nav", "策略超额", "top20%", "全市场"))
    idx = {d: i for i, (d, _, _) in enumerate(nav)}
    for i in range(0, len(gd), max(1, len(gd) // 8)):
        d = gd[i]
        j = idx.get(d)
        if j is None:
            continue
        nav_i = nav[j][1]
        print("  %-12s %10.4f %9.2f%% %9.2f%% %9.2f%%"
              % (d, nav_i, (nav_i - 1) * 100, gl[i] * 100, gu[i] * 100))
    print("  %-12s %10.4f %9.2f%% %9.2f%% %9.2f%%"
          % (nav[-1][0], nav[-1][1], (nav[-1][1] - 1) * 100, gl[-1] * 100, gu[-1] * 100))
    print("")
    print("  ★ 策略全期      = %+7.2f%%" % ((nav[-1][1] - 1) * 100))
    print("  全市场(等权全A) = %+7.2f%%" % (gu[-1] * 100))
    print("  top20%% 分层组   = %+7.2f%%" % (gl[-1] * 100))
    print("  基准(SH000300) = %+7.2f%%" % ((nav[-1][2] - 1) * 100))
    print("")
    print("  策略超额(vs全市场)      = %+7.2f%%" % ((nav[-1][1] - gu[-1]) * 100))
    print("  top20%% 组超额(vs全市场) = %+7.2f%%" % ((gl[-1] - gu[-1]) * 100))
    if gl[-1] - gu[-1] > 0:
        print("  ⇒ 策略超额 / top20%%超额 = %.2fx（top50 比 top20%% 更集中 ⇒ 应 >1 ✓；"
              % ((nav[-1][1] - gu[-1]) / (gl[-1] - gu[-1])))
        print("     按 IC≈0.079 的正态近似，top1%% 与 top20%% 的超额比约 **1.9x** ✓")
        print("     （E[z|top1%%]≈2.67 / E[z|top20%%]≈1.40 ⇒ 2.67/1.40 ≈ 1.9 ✓）")
    print("")
    print("  日度：nav 日收益 vs top20%% 组日收益 的相关系数 = "
          "%.3f" % _corr(nav, gd, gl))


def _corr(nav, gd, gl):
    idx = {d: i for i, (d, _, _) in enumerate(nav)}
    xs, ys = [], []
    prev = None
    for i, d in enumerate(gd):
        j = idx.get(d)
        if j is None or j == 0:
            prev = gl[i]
            continue
        dn = nav[j][1] / nav[j - 1][1] - 1
        dg = gl[i] - (prev if prev is not None else gl[i])
        prev = gl[i]
        xs.append(dn)
        ys.append(dg)
    if len(xs) < 10:
        return float("nan")
    return float(np.corrcoef(np.asarray(xs), np.asarray(ys))[0, 1])


if __name__ == "__main__":
    main()
    sys.exit(0)
