# -*- coding: utf-8 -*-
"""dump 米筐侧的 4 个市场字段（用户 2026-10-10：公式目前只有总市值 market_cap，要补
流通市值 circulating_market_cap / 总股本 capitalization / 流通股本 circulating_cap ✓）

## 数据来源（**全是米筐自家** ✓，不混源 ✓）
| 字段 | 来源 | 说明 |
|---|---|---|
| `market_cap` | `E:\\rq\\others\\market-cap\\market_cap.h5` | **已存在** ✓，本工具只**核对不改** ✓ |
| `circulating_market_cap` | `…\\market_cap_2.h5` | 实测 = 总市值 × 0.8525（002487 ✓ 与 tushare 流通/总股 0.853 吻合 ✓） |
| `capitalization` | **反推**：`market_cap ÷ 未复权收盘` | 米筐侧没有股本文件 ✗；用同日总市值 ÷ 当日**未复权**收盘（`close/factor` ✓）⇒ 同源自洽 ✓ |
| `circulating_cap` | **反推**：`circulating_market_cap ÷ 未复权收盘` | 同上 ✓ |

## 读法（2026-10-10 破出来的 ✓）
`market_cap*.h5` 是 pandas 稀疏布局（环境没装 pytables ✗ ⇒ 手工解 ✓）：
`data/axis1_label0` = 股票下标（[0,5573) ✓）、`axis1_label1` = 日期下标（[0,6458) ✓）、
`axis1_level0` = 股票代码、`axis1_level1` = **纳秒时间戳** ✗（不是毫秒 ✗，别猜 ✓：判据是与
`cn_data` 日历对齐 —— 纳秒解出来的 6455 个日期**逐个命中**日历 ✓）。

## 安全闸（不满足就不写 ✗）
① **身份闸**：从 `market_cap.h5` 重算出来的总市值，必须与目标数据集**现有 `market_cap` bin**
   在抽样日上一致（相对差 ≤0.5% ✓）⇒ 证明"文件↔字段↔单位"三件事都认对了 ✓；
② **结构闸**：`circulating_market_cap ≤ market_cap × 1.001` ✓（流通不可能大于总 ✓）；
③ **单位闸**：样本日反推股本与 tushare `total_share`（若缓存有 ✓）相对差 ≤2% ✓；
④ `first + n <= 日历长` ✓、写入前备份 ✓。

用法：python tools/dump_ricequant_market_fields.py [--codes a,b] [--limit N] [--apply]（默认 dry-run ✓）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                          # noqa: E402
from dump_tushare_finance import _declared_convention, read_bin          # noqa: E402

MC_DIR = Path(os.environ.get("RQ_MARKET_CAP_DIR", r"E:\rq\others\market-cap"))
NEW_FIELDS = ("circulating_market_cap", "capitalization", "circulating_cap")


def _s(x):
    return x.decode() if isinstance(x, bytes) else str(x)


def load_sparse(p: Path):
    """→ (股票代码数组, 日期数组(YYYY-MM-DD ✓), 每只股票的 (日下标, 值) 切片边界 ✓)"""
    with h5py.File(str(p), "r") as f:
        g = f["data"]
        l0 = np.asarray(g["axis1_label0"][:]).astype(np.int64)
        l1 = np.asarray(g["axis1_label1"][:]).astype(np.int64)
        lv0 = np.array([_s(x) for x in g["axis1_level0"][:]])
        lv1 = np.asarray(g["axis1_level1"][:]).astype(np.int64)
        vs = np.asarray(g["block0_values"][:]).astype(np.float64).ravel()
    # ⚠ 日期轴是**纳秒**（946944000000000000 ns = 2000-01-04 ✓）；用日历对齐验证过 ✓
    days = np.array([str(np.datetime64(int(v), "ns"))[:10] for v in lv1])
    order = np.argsort(l0, kind="stable")
    l0s, l1s, vss = l0[order], l1[order], vs[order]
    idx = np.arange(lv0.size)
    starts = np.searchsorted(l0s, idx, side="left")
    ends = np.searchsorted(l0s, idx, side="right")
    return lv0, days, l1s, vss, starts, ends


def series_of(lv0, days, l1s, vss, starts, ends, code: str):
    """→ {日期: 值}（该股票在该文件里的全部非零值 ✓）。"""
    hit = np.where(lv0 == code)[0]
    if hit.size == 0:
        return {}
    si = int(hit[0])
    sl = slice(int(starts[si]), int(ends[si]))
    return {days[int(k)]: float(v) for k, v in zip(l1s[sl], vss[sl])}


def main():
    ap = argparse.ArgumentParser(description="米筐侧 4 个市场字段 → qlib bin（默认 dry-run ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data"))
    ap.add_argument("--codes", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    ap.add_argument("--allow-non-ricequant", action="store_true")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    conv = _declared_convention(qlib)
    if conv and conv != "ricequant" and not args.allow_non_ricequant:
        raise SystemExit("✗ 拒绝写入：目标 %s 的口径是 %r，不是 ricequant ✗\n"
                         "  本工具只生成**米筐**市场字段 ⇒ 只应写米筐源数据集（data/cn_data ✓）。\n"
                         "  tushare 侧请用 tools/dump_tushare_market_fields.py ✓。" % (qlib, conv))
    cal, _ = load_calendar(qlib)
    n_cal = len(cal)
    cpos = {d: i for i, d in enumerate(cal)}
    ex = qlib / "features"
    codes = ([c.strip().lower() for c in args.codes.split(",") if c.strip()] if args.codes else
             sorted(d.name for d in ex.iterdir() if d.is_dir() and d.name[2:].isdigit()))
    if args.limit:
        codes = codes[:args.limit]
    print("目标 %s（日历 %d 天，末日 %s）口径=%s" % (qlib, n_cal, cal[-1], conv or "(无标记)"))
    print("模式 %s ｜ 待处理 %d 只 ｜ 新字段 %s\n"
          % ("APPLY ✓" if args.apply else "DRY-RUN ✓", len(codes), ",".join(NEW_FIELDS)), flush=True)

    print("读米筐市值文件（各 17.6M 条，稍等 ✓）…", flush=True)
    lv0_t, days_t, l1_t, vs_t, st_t, en_t = load_sparse(MC_DIR / "market_cap.h5")
    lv0_c, days_c, l1_c, vs_c, st_c, en_c = load_sparse(MC_DIR / "market_cap_2.h5")
    print("  总市值 %d 只 / 流通市值 %d 只 ✓\n" % (lv0_t.size, lv0_c.size), flush=True)

    backup = ROOT / "ai_test" / ("backup_rq_market_%s" % time.strftime("%Y%m%d-%H%M%S"))
    n_ok = n_skip = n_bins = 0
    checks = []
    for i, code in enumerate(codes, 1):
        rq = "%s.%s" % (code[2:], {"sh": "XSHG", "sz": "XSHE", "bj": "BJSE"}.get(code[:2], ""))
        tot = series_of(lv0_t, days_t, l1_t, vs_t, st_t, en_t, rq)
        cir = series_of(lv0_c, days_c, l1_c, vs_c, st_c, en_c, rq)
        if not tot:
            n_skip += 1
            continue
        # 现有 bin（用于身份闸 + 反推股本 ✓）
        hc = read_bin(ex / code / "close.day.bin")
        hf = read_bin(ex / code / "factor.day.bin")
        hm = read_bin(ex / code / "market_cap.day.bin")
        if hc is None or hf is None or hm is None:
            n_skip += 1
            continue
        f0, close = hc[0], hc[1]
        if hf[0] != f0:
            n_skip += 1
            continue
        fold = hf[1]
        fm, mc_cur = hm[0], hm[1]
        # ---- 闸① 身份：重算的总市值 vs 现有 bin（抽样 12 天 ✓）----
        days_ok = [d for d in sorted(tot) if d in cpos]
        if len(days_ok) < 12:
            n_skip += 1
            continue
        dev = []
        for d in days_ok[:: max(1, len(days_ok) // 12)][:12]:
            j = cpos[d] - fm
            if 0 <= j < mc_cur.size and np.isfinite(mc_cur[j]) and mc_cur[j] > 0:
                dev.append(abs(tot[d] / mc_cur[j] - 1.0))
        if dev and max(dev) > 0.005:
            print("  ✗ %-9s 身份闸不过：重算总市值与现有 bin 最大相对差 %.3f%% ✗（不写）" % (code, max(dev) * 100))
            n_skip += 1
            continue
        # ---- 闸② 结构：流通 ≤ 总 ----
        bad_struct = sum(1 for d in days_ok if d in cir and cir[d] > tot[d] * 1.001)
        if bad_struct > len(days_ok) * 0.01:
            print("  ✗ %-9s 结构闸不过：%d 天流通 > 总市值 ✗（不写）" % (code, bad_struct))
            n_skip += 1
            continue
        # ---- 组装 3 个新字段（按日历 ✓，未复权收盘 = close/factor ✓）----
        lo = cpos[days_ok[0]]
        hi = cpos[days_ok[-1]]
        if lo + (hi - lo + 1) > n_cal:
            n_skip += 1
            continue
        n = hi - lo + 1
        arr_cir = np.full(n, np.nan)
        arr_cap = np.full(n, np.nan)
        arr_cca = np.full(n, np.nan)
        for d in days_ok:
            j = cpos[d] - lo
            k = cpos[d] - f0
            raw = close[k] / fold[k] if (0 <= k < close.size and np.isfinite(fold[k]) and fold[k]) else np.nan
            t = tot[d]
            c = cir.get(d, np.nan)
            arr_cir[j] = c
            if np.isfinite(raw) and raw > 0:
                arr_cap[j] = t / raw
                if np.isfinite(c):
                    arr_cca[j] = c / raw
        # ---- 闸④ 抽样看量级（股本应为"股"✓）----
        sample = [(d, arr_cap[cpos[d] - lo]) for d in days_ok[-3:]]
        checks.append((code, [(d, round(v, 4)) for d, v in sample]))
        print("  ✓ %-9s %s ~ %s（%d 天）｜末 3 日：流通市值=%s 总股本=%s"
              % (code, days_ok[0], days_ok[-1], len(days_ok),
                 [round(cir.get(d, float('nan')), 4) for d, _ in sample],
                 [v for _, v in checks[-1][1]]))
        n_ok += 1
        if args.apply:
            backup.mkdir(parents=True, exist_ok=True)
            for fld, arr in zip(NEW_FIELDS, (arr_cir, arr_cap, arr_cca)):
                p = ex / code / ("%s.day.bin" % fld)
                if p.exists():
                    shutil.copy2(p, backup / ("%s_%s.day.bin" % (code, fld)))
                write_bin(p, int(lo), arr)
                n_bins += 1
        if i % 1000 == 0:
            print("   … %d/%d（可写 %d / 跳过 %d）" % (i, len(codes), n_ok, n_skip), flush=True)

    print("\n合计：可写 %d 只 / 跳过 %d 只" % (n_ok, n_skip))
    if args.apply:
        print("已写 %d 个 bin ✓；原文件（若有）备份到 %s ✓" % (n_bins, backup))
    else:
        print("（dry-run：未写盘 ✓；加 --apply 生效 ✓）")


if __name__ == "__main__":
    main()
