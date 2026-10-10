# -*- coding: utf-8 -*-
"""用**米筐 bundle**（同源 ✓）重建 `cn_data` 的 `open/high/low` 三个字段（默认 dry-run ✓）。

## 为什么（2026-10-10 用户实测发现 ✗）
`cn_data` 里 `open/high/low` **被写成了 `close`**（实测：全池 95.8% 的行四值完全相同、
175/196 只股票 **100% 退化**）⇒ 任何用日内高低点的公式都失真 ——
实证 `黄金坑启动` 的 `冰谷火焰: (c/l-1)*100>=1`：退化数据下该条件成立率 **2.3%** ✗，
健康数据下 **58%** ✓ ⇒ 触发数 6 ↔ 3687（同一份数据，只差"L 是哪份"）。

⚠ 附带后果（本次一并修）：`cn_data` 的 `pre*`（前复权物化字段）**是从 cn_data2 抄来的** ✗
（实测 `prelow` 与 cn_data2 的 `prelow` 逐位一致 100%、与自家 `low/factor_last` 只 4% 相符）
⇒ **混源** ✗ + 掩盖了本次退化。本工具只修原生 O/H/L；跑完请再跑
`python backend/tools/build_preclose.py --qlib-dir <本数据集> --overwrite` 用**自家**价重算 `pre*` ✓。

## 口径（与 `$close` 严格同源，绝不引新数据源 ✓）
`$close = bundle_raw_close × $factor` 已在 v1.21.1~1.21.3 的因子修复中定稿 ✓
（实测闸门：`bundle_raw_close × $factor` 与现有 `close` 的中位相对差 **3.4e-08** = float32 级 ✓）
⇒ 同式重建：`$open/$high/$low = bundle_raw_{open,high,low} × $factor` ✓。

## 四道闸（任一不过 ⇒ 该股**整只跳过**并计入报告，不写盘 ✓）
1. **同源闸**：`bundle_raw_close × $factor` 与现有 `close` 的中位相对差 ≤ 1e-5、p99 ≤ 1e-3；
2. **轴闸**：`close/factor` 的 bin 轴一致，且新值只替换 `open/high/low`（**close/factor/volume/amount/vwap 一律不动** ✓）；
3. **覆盖闸**：bundle 覆盖到现有 `close` 非空行数的 ≥99%（缺的日子**保留原值**并计数 ✓）；
4. **形态闸**：`low ≤ min(open,close) ≤ max(open,close) ≤ high`（容差 1e-4 相对）占比 ≥99.9%，
   且新 `low` 与 `close` 逐位相同比例 ≤50%（否则说明这票本来就该是"一字板"或数据可疑 ⇒ 汇报 ✓）。

写盘前把旧 bin 备份到 `ai_test/backup_cn_data_ohlc_<时间戳>/` ✓；成功后落数据戳 ✓。

用法：
    python backend/tools/rebuild_cn_data_ohlc_from_bundle.py                 # dry-run（默认 ✓）
    python backend/tools/rebuild_cn_data_ohlc_from_bundle.py --codes sh600519,sz000002
    python backend/tools/rebuild_cn_data_ohlc_from_bundle.py --apply         # 真的写盘
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dump_finance import load_calendar, write_bin                        # noqa: E402
from dump_tushare_finance import read_bin                                # noqa: E402

SUF = {"sh": "XSHG", "sz": "XSHE", "bj": "BJSE"}
TARGETS = ("open", "high", "low")
GATE_CLOSE_MED = 1e-5
GATE_CLOSE_P99 = 1e-3
GATE_COVER = 0.99
GATE_SHAPE = 0.999


def bundle_daily(fh: h5py.File, code: str):
    """bundle `stocks.h5` 里该股的日频 OHLC → DataFrame（index=日期 ✓）。"""
    key = "%s.%s" % (code[2:], SUF.get(code[:2], ""))
    if key not in fh:
        return None
    arr = fh[key][:]
    dtv = np.asarray(arr["datetime"]).astype("int64")
    fmt = "%Y%m%d" if dtv.max() < 10 ** 9 else ("%Y%m%d%H%M%S" if dtv.max() > 10 ** 12 else "%Y%m%d%H%M")
    df = pd.DataFrame({k: np.asarray(arr[k], dtype=np.float64) for k in
                       ("open", "high", "low", "close")},
                      index=pd.to_datetime(dtv.astype(str), format=fmt))
    return df[~df.index.duplicated(keep="last")].sort_index()


def main():
    ap = argparse.ArgumentParser(description="用米筐 bundle 重建 cn_data 的 open/high/low（默认 dry-run ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data"))
    ap.add_argument("--bundle", default=os.environ.get("BUNDLE_ROOT", r"E:\rq\bundle"))
    ap.add_argument("--codes", default="", help="只处理这些（逗号分隔 qlib 代码 ✓）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认只体检 ✓）")
    args = ap.parse_args()

    qlib_dir = Path(args.qlib_dir)
    cal, _cal_int = load_calendar(qlib_dir)
    feats = qlib_dir / "features"
    if args.codes:
        codes = [c.strip().lower() for c in args.codes.split(",") if c.strip()]
    else:
        codes = sorted(d.name for d in feats.iterdir()
                       if d.is_dir() and d.name[:2] in ("sh", "sz", "bj") and d.name[2:].isdigit())
    if args.limit:
        codes = codes[:args.limit]
    print("数据集 %s | 日历 %d 天 | 待处理 %d 只 | apply=%s"
          % (qlib_dir, len(cal), len(codes), args.apply), flush=True)

    backup = ROOT / "ai_test" / ("backup_cn_data_ohlc_%s" % time.strftime("%Y%m%d-%H%M%S"))
    t0 = time.time()
    n_ok = n_skip = n_nob = n_neg = 0
    reasons: dict = {}
    worst = []
    with h5py.File(str(Path(args.bundle) / "stocks.h5"), "r") as fh:
        for i, code in enumerate(codes, 1):
            d = feats / code
            hit_c = read_bin(d / "close.day.bin")
            hit_f = read_bin(d / "factor.day.bin")
            if hit_c is None or hit_f is None:
                n_skip += 1
                reasons["缺 close/factor"] = reasons.get("缺 close/factor", 0) + 1
                continue
            first_c, close = hit_c
            first_f, factor = hit_f
            if first_c != first_f or close.size != factor.size:
                n_skip += 1
                reasons["close/factor 轴不一致"] = reasons.get("close/factor 轴不一致", 0) + 1
                continue
            bd = bundle_daily(fh, code)
            if bd is None or bd.empty:
                n_nob += 1
                reasons["bundle 里没有该股"] = reasons.get("bundle 里没有该股", 0) + 1
                continue
            dates = pd.to_datetime([cal[first_c + k] for k in range(close.size)])
            bb = bd.reindex(dates)
            ok_date = bb["close"].notna().to_numpy() & np.isfinite(close)
            if ok_date.sum() < 10:
                n_skip += 1
                reasons["可比日期太少"] = reasons.get("可比日期太少", 0) + 1
                continue
            # ⚠ bundle 的日频**只从 2005-01-01 起**（实测：2005 前 100% 缺、2005 后 0% 缺 ✓）
            #   ⇒ 覆盖闸只衡量**bundle 自身日期范围之内**的覆盖 ✓；范围之外（早年）保留原值 ✓
            #     （那段无法从米筐源重建，且**绝不**拿另一套数据补 ⇒ 不混源 ✓，如实汇报 ✓）。
            b_start = bd.index[0]
            inside = np.asarray(dates >= b_start)
            neg_rows = int((inside & np.isfinite(close)).sum())
            # ---- 闸 1：同源（bundle_raw_close × factor == close）----
            pred_c = bb["close"].to_numpy() * factor
            rel = np.abs(pred_c[ok_date] - close[ok_date]) / np.maximum(1e-12, np.abs(close[ok_date]))
            med, p99 = float(np.median(rel)), float(np.percentile(rel, 99))
            # ---- 闸 3：覆盖（只在 bundle 日期范围内衡量 ✓）----
            cover = float(ok_date.sum() / max(1, neg_rows))
            new = {}
            for t in TARGETS:
                arr = np.where(ok_date, bb[t].to_numpy() * factor, np.nan)
                arr = np.where(np.isfinite(arr), arr, np.nan)
                new[t] = arr
            # ---- 闸 4：形态（用现有 close 做参考 ✓）+ low==close 比例 ----
            m = ok_date & np.isfinite(new["low"]) & np.isfinite(new["high"])
            tol = 1e-4
            lo = np.minimum(new["open"], close)
            hi = np.maximum(new["open"], close)
            shape_ok = ((new["low"] <= lo * (1 + tol) + 1e-9)
                        & (hi <= new["high"] * (1 + tol) + 1e-9))[m]
            same_ratio = float((np.abs(new["low"][m] - close[m])
                                <= tol * np.maximum(1e-9, np.abs(close[m]))).mean()) if m.any() else 1.0
            fails = []
            if med > GATE_CLOSE_MED or p99 > GATE_CLOSE_P99:
                fails.append("同源闸(close 中位 %.2e/p99 %.2e)" % (med, p99))
            if cover < GATE_COVER:
                fails.append("覆盖闸(%.3f)" % cover)
            if shape_ok.size == 0 or shape_ok.mean() < GATE_SHAPE:
                fails.append("形态闸(%.4f)" % (float(shape_ok.mean()) if shape_ok.size else 0.0))
            if same_ratio > 0.5:
                fails.append("low==close 仍占 %.1f%%" % (same_ratio * 100))
            if fails:
                n_skip += 1
                for f in fails:
                    key = f.split("(")[0]
                    reasons[key] = reasons.get(key, 0) + 1
                if len(worst) < 8:
                    worst.append((code, "; ".join(fails), med, p99, cover))
                continue
            n_ok += 1
            n_neg += int((~inside & np.isfinite(close)).sum())      # 早于 bundle 起点、保留原值的行 ✓
            if args.apply:
                d_bak = backup / code
                d_bak.mkdir(parents=True, exist_ok=True)
                for t in TARGETS:
                    src = d / ("%s.day.bin" % t)
                    if src.exists():
                        (d_bak / src.name).write_bytes(src.read_bytes())
                    # 轴一律沿用 close 的（只换值 ✓）；缺料的日子保留原值 ✓
                    old = read_bin(src)
                    vals = new[t]
                    if old is not None and old[0] == first_c and old[1].size == vals.size:
                        keep = ~np.isfinite(vals)
                        vals = np.where(keep, old[1], vals)
                    write_bin(src, first_c, vals)
            if i % 500 == 0 or i == len(codes):
                el = time.time() - t0
                print("   进度 %d/%d  可修 %d / 跳过 %d  %.0fs（%.1fs/百只）"
                      % (i, len(codes), n_ok, n_skip, el, el / i * 100), flush=True)

    print("\n完成：可修 %d 只 / 跳过 %d 只 / bundle 无此股 %d 只，用时 %.0fs"
          % (n_ok, n_skip, n_nob, time.time() - t0), flush=True)
    if n_neg:
        print("⚠ 另有 %d 行（2005 年之前）**保留原值**：bundle 日频只从 2005-01-01 起 ✓"
              "（米筐源没有更早的日频 ⇒ 不拿另一套数据补，避免混源 ✗）" % n_neg, flush=True)
    for k, v in reasons.items():
        print("  跳过原因 %-24s %d" % (k, v), flush=True)
    for code, why, med, p99, cover in worst:
        print("  例 %-9s %s（close 中位 %.2e/p99 %.2e，覆盖 %.3f）" % (code, why, med, p99, cover), flush=True)
    if not args.apply:
        print("（DRY-RUN：未写盘；加 --apply 才写 ✓，旧 bin 会备份到 %s ✓）" % backup, flush=True)
        return
    # ★ 落数据戳（原地重写 bin 不改数据集目录 mtime ⇒ 不落戳会继续用旧面板缓存 ✗）
    try:
        from app.engine.feature_cache import bump_data_version
        print("数据戳已落 ✓ %s" % bump_data_version(str(qlib_dir), "rebuild_cn_data_ohlc_from_bundle"))
    except Exception as e:                                                  # noqa: BLE001
        print("⚠⚠ 数据戳没落上（%r）⇒ 必须手工清 backend/workdir/feature_cache/ ✗" % e)
    print("备份目录：%s" % backup, flush=True)


if __name__ == "__main__":
    main()
