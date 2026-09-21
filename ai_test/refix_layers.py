# -*- coding: utf-8 -*-
"""把**已完成段**的 `layers`（分层回测）用**修好的** `_compute_layers` 重算一遍。

为什么要它（v1.20.42 迁移）：
  断点续跑 `POST /api/backtest/{tid}/resume` 对**已完成段**是**直接读回**
  `segment_N/seg_result.json` 里的 `layers` ✗（见 `qlib_engine.py:959-968` ✓）
  ⇒ 即使 v1.20.42 修好了 `long_average` 的口径混用 ✗，**已跑段仍是旧（错误）值** ✗✗。

本脚本**不重训任何模型** ✓ —— 直接吃 `segment_N/test_pl.pkl`（该段 test 窗口的
`score/label/ret` ✓，只有 1 个月 ⇒ 极快 ✓），只重跑 `_compute_layers` ✓，
把新的 `layers` 写回 `seg_result.json` ✓。

⚠ `partial_result.json` / `merged` **不用管** ✓ —— 续跑时 `_run_rolling` 会用
「读回的段 layers + 新跑段的 layers」重新 `_build_merged_analysis` 并覆写 ✓
（`qlib_engine.py:1159-1164` ✓）。

用法：
  python refix_layers.py <task_id>            # **预览**（只算不写 ✓）
  python refix_layers.py <task_id> --apply    # 写回 ✓（建议先备份 artifacts 目录 ✓）
"""
import json
import os
import pickle
import sys

sys.path.insert(0, r"d:\quant\qlib_code\backend")

from app.services.qlib_runtime import ensure_qlib_init  # noqa: E402

# ⚠⚠ 必须初始化 qlib ✗ —— `_compute_benchmark_returns` 走 `D.calendar`/qlib ✗，
#   未初始化时会**静默失败**（异常被吞 ✓）⇒ `benchmark` 列变 None ⇒ **图上基准线消失** ✗✗
#   （第一版漏了这一步，实测把段1 的 benchmark 弄丢 ✓ 已修 ✓）。
ensure_qlib_init()

from app.engine.analysis import _compute_benchmark_returns, _compute_layers  # noqa: E402


BASE = r"d:\quant\qlib_code\backend\workdir\artifacts"


def _find(tid):
    for d in os.listdir(BASE):
        if tid in d:
            return os.path.join(BASE, d)
    return None


def main(tid, apply=False):
    art = _find(tid)
    if not art:
        print("找不到任务目录:", tid)
        return
    params = json.load(open(os.path.join(art, "params.json"), encoding="utf-8"))
    benchmark = str(params.get("benchmark") or "SH000300")
    rbp = int(params.get("layer_rebalance") or 1)
    print("任务目录:", os.path.basename(art))
    print("  benchmark=%s  layer_rebalance=%d  （apply=%s）" % (benchmark, rbp, apply))
    print("")
    segs = sorted(d for d in os.listdir(art) if d.startswith("segment_"))
    if not segs:
        print("  没有 segment_* 目录")
        return
    n_done = n_skip = 0
    for sd in segs:
        d = os.path.join(art, sd)
        sp = os.path.join(d, "seg_result.json")
        tp = os.path.join(d, "test_pl.pkl")
        if not (os.path.exists(sp) and os.path.exists(tp)):
            print("  %-12s 跳过（缺 seg_result.json 或 test_pl.pkl）" % sd)
            n_skip += 1
            continue
        seg = json.load(open(sp, encoding="utf-8"))
        tpl = pickle.load(open(tp, "rb"))
        if tpl is None or not len(tpl):
            print("  %-12s 跳过（test_pl 为空）" % sd)
            n_skip += 1
            continue
        dt = tpl.index.get_level_values("datetime")
        start, end = dt.min(), dt.max()
        try:
            bench_ret = _compute_benchmark_returns(benchmark, start, end)
        except Exception:
            bench_ret = None
        new_groups = _compute_layers(tpl, benchmark_ret=bench_ret, rebalance_period=rbp)
        if not new_groups:
            print("  %-12s 跳过（重算失败）" % sd)
            n_skip += 1
            continue
        new_layers = {"segment": sd.replace("segment_", "段"),
                      "groups": new_groups, "benchmark": benchmark}
        old = (seg.get("layers") or {}).get("groups") or []
        # ⚠⚠ 基准**与本 BUG 无关** ✓（它一直是"指数累计收益"口径 ✓ 是对的 ✓）
        #   ⇒ 重算拿不到时（qlib 未初始化 / 该区间无指数数据 ✗）**一律沿用旧值** ✓，
        #     绝不能让 benchmark 变成 None ✗（否则图上基准线直接消失 ✗）。
        if old:
            for _i, _p in enumerate(new_groups):
                if _p.get("benchmark") is None and _i < len(old):
                    _p["benchmark"] = old[_i].get("benchmark")
        _nb = sum(1 for _p in new_groups if _p.get("benchmark") is not None)
        print("  %-12s 基准点 %d/%d" % (sd, _nb, len(new_groups)))
        old_la = old[0].get("long_average") if old else None
        new_la = new_groups[0].get("long_average")
        old_lt = old[-1].get("long_average") if old else None
        new_lt = new_groups[-1].get("long_average")
        print("  %-12s 点数 %d→%d   首点 long_average %s → %s   末点 %s → %s"
              % (sd, len(old), len(new_groups),
                 old_la, new_la, old_lt, new_lt))
        if apply:
            seg["layers"] = new_layers
            tmp = sp + ".tmp%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(seg, f, ensure_ascii=False, default=str)
            os.replace(tmp, sp)
        n_done += 1
    print("")
    print("  处理 %d 段，跳过 %d 段%s" % (n_done, n_skip, "（已写回 ✓）" if apply else "（预览模式，未写 ✗）"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "5e1f338dc34e",
         "--apply" in sys.argv)
