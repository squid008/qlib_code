# -*- coding: utf-8 -*-
"""真数据验证 v1.20.46 的「分层 1 日前视」修复 ✓（2026-09-22）。

做法（最干净 ✓）：拿**现有产物**的 `segment_*/test_pl.pkl`（含 `score` + `label` ✓），
**只替换 `ret` 列** ✓ ⇒ 走同一个 `_compute_layers` ✓ ⇒ 对比修正前/后 ✓：
  · 旧口径 `ret = $close/Ref($close,1)-1`        （t 日**当日**收益 ✗ = BUG）
  · 新口径 `ret = Ref($close,-2)/Ref($close,-1)-1`（t+1→t+2 ✓ 与 label/成交口径对齐 ✓）
⇒ 若结果从"5 层完美单调 + 多空 ±60%"变成"接近 0 / 乱序" ✓ ⇒ 修复生效 ✓✓
  （即：原来的"分层信号"本来就是同日错位造出来的假象 ✓）。
"""
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd

ART = r"d:\quant\qlib_code\backend\workdir\artifacts"
DIRN = "20260922-000538_LightGBM_all_2021_2021_fd76879e00f0"
OLD_RET = "$close / Ref($close, 1) - 1"
NEW_RET = "Ref($close, -2) / Ref($close, -1) - 1"


def main():
    sys.path.insert(0, r"d:\quant\qlib_code\backend")
    from app.services.qlib_runtime import ensure_qlib_init
    ensure_qlib_init()
    from qlib.data import D
    from app.engine.analysis import _compute_layers

    base = os.path.join(ART, DIRN)
    print("  目录：%s" % DIRN)
    print("")
    print("  %-6s %-28s %-28s" % ("段", "旧口径（同日 ✗ BUG）", "新口径（t+1→t+2 ✓ 修正）"))
    agg = {"old": [], "new": []}
    for si in range(1, 10):
        p = os.path.join(base, "segment_%d" % si, "test_pl.pkl")
        if not os.path.exists(p):
            continue
        pl = pd.read_pickle(p)
        if not isinstance(pl, pd.DataFrame) or "score" not in pl.columns:
            print("  段%d：test_pl.pkl 结构不符，跳过" % si)
            continue
        codes = sorted(set(pl.index.get_level_values("instrument").astype(str)))
        dates = pl.index.get_level_values("datetime")
        d0, d1 = dates.min(), dates.max()
        ft = D.features(codes, [OLD_RET, NEW_RET],
                        start_time=(pd.Timestamp(d0) - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                        end_time=(pd.Timestamp(d1) + pd.Timedelta(days=15)).strftime("%Y-%m-%d"))
        if ft is None or ft.empty:
            print("  段%d：取价失败，跳过" % si)
            continue
        ft = ft.copy()
        ft.columns = ["ret_old", "ret_new"]
        ft.index.names = ["instrument", "datetime"]

        res = {}
        for tag, col in (("old", "ret_old"), ("new", "ret_new")):
            df = pl[["score", "label"]].join(ft[[col]], how="inner").rename(columns={col: "ret"})
            pts = _compute_layers(df, N=5, benchmark_ret=None, rebalance_period=1)
            if not pts:
                continue
            res[tag] = pts[-1]
            agg[tag].append(pts[-1])
        if "old" in res and "new" in res:
            o, n = res["old"], res["new"]
            print("  %-6d G1 %+7.2f%% G5 %+7.2f%% 多空 %+7.2f%%  |  G1 %+7.2f%% G5 %+7.2f%% 多空 %+7.2f%%"
                  % (si, o["Group1"] * 100, o["Group5"] * 100, o["long_short"] * 100,
                     n["Group1"] * 100, n["Group5"] * 100, n["long_short"] * 100))
        else:
            print("  段%d：分层计算失败" % si)

    def summ(tag):
        if not agg[tag]:
            return None
        keys = ["Group1", "Group2", "Group3", "Group4", "Group5", "long_short", "long_average"]
        return {k: float(np.mean([a[k] for a in agg[tag]])) * 100 for k in keys}

    print("")
    for tag, nm in (("old", "旧口径（同日 ✗）"), ("new", "新口径（t+1→t+2 ✓）")):
        s = summ(tag)
        if not s:
            continue
        print("  %-20s 各段末值均值：G1 %+6.2f  G2 %+6.2f  G3 %+6.2f  G4 %+6.2f  G5 %+6.2f │ 多空 %+6.2f │ 超额 %+6.2f"
              % (nm, s["Group1"], s["Group2"], s["Group3"], s["Group4"], s["Group5"],
                 s["long_short"], s["long_average"]))
    print("")
    print("  ★ 判读：旧口径若呈现『单调 + 多空量级很大』✗、新口径显著收敛 ✓ ⇒ 修复确实打掉了那个假象 ✓")


if __name__ == "__main__":
    main()
    sys.exit(0)
