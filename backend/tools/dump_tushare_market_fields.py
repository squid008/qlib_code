# -*- coding: utf-8 -*-
"""把按天预取的 `daily_basic` 缓存**汇总成 bin** ✓（tushare 侧的 4 个市场字段）

数据链：`tools/prefetch_basic_fields.py`（按天取 ✓ 可断点续跑 ✓、失败进台账 ✓）
        → `data/tushare_cache/basic/<YYYYMMDD>.json` = `{ts_code: {total_mv, circ_mv, total_share, float_share, free_share}}`
        → 本工具 → `features/<code>/{market_cap,circulating_market_cap,capitalization,circulating_cap}.day.bin`

口径（与米筐侧**逐项对齐** ✓，便于两源对拍）：
  `market_cap` = `total_mv`（**元** ✓）、`circulating_market_cap` = `circ_mv`（元 ✓）、
  `capitalization` = `total_share`（**股** ✓）、`circulating_cap` = `float_share`（股 ✓）
（`prefetch` 里已把 tushare 的万元/万股 ×1e4 统一成 元/股 ✓）

⚠ 只写**日历内**的日期 ✓；缺的日期留 NaN ✓（绝不编数据 ✗）；已存在的 bin 默认跳过 ✓（`--force` 才重写 ✓）。

用法：python tools/dump_tushare_market_fields.py [--qlib-dir data/cn_data2] [--codes a,b] [--limit N]
      [--max-days N（只处理最近 N 天，用于快速试跑 ✓）] [--force] [--apply]（默认 dry-run ✓）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                          # noqa: E402
from dump_tushare_finance import _declared_convention, read_bin           # noqa: E402

BASIC = ROOT / "data" / "tushare_cache" / "basic"
# 目标字段 → 缓存里的键 ✓
FIELDS = (("market_cap", "total_mv"), ("circulating_market_cap", "circ_mv"),
          ("capitalization", "total_share"), ("circulating_cap", "float_share"))


def to_qlib(ts_code: str) -> str:
    num, _, suf = ts_code.strip().upper().partition(".")
    pre = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf)
    return "%s%s" % (pre, num) if pre else ""


def main():
    ap = argparse.ArgumentParser(description="daily_basic 缓存 → 4 个市场字段 bin（默认 dry-run ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data2"))
    ap.add_argument("--codes", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-days", type=int, default=0, help="只处理最近 N 天（试跑 ✓）")
    ap.add_argument("--force", action="store_true", help="已存在的 bin 也重写")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-non-tushare", action="store_true")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    conv = _declared_convention(qlib)
    if conv and conv != "tushare" and not args.allow_non_tushare:
        raise SystemExit("✗ 拒绝写入：目标 %s 的口径是 %r，不是 tushare ✗（米筐侧请用 "
                         "tools/dump_ricequant_market_fields.py ✓）" % (qlib, conv))
    cal, _ = load_calendar(qlib)
    n_cal = len(cal)
    cpos = {d: i for i, d in enumerate(cal)}
    # 只取日历内的天（缓存里 8/24~10/09 在 cn_data2 日历内 ✓）
    d8s = [d.replace("-", "") for d in cal]
    avail = [d for d in d8s if (BASIC / ("%s.json" % d)).exists()]
    if args.max_days:
        avail = avail[-args.max_days:]
    print("目标 %s（日历 %d 天，末日 %s）｜缓存可用 %d 天（%s ~ %s）"
          % (qlib, n_cal, cal[-1], len(avail), avail[0] if avail else "-", avail[-1] if avail else "-"))
    if not avail:
        raise SystemExit("✗ 缓存里一天都没有 ⇒ 先跑 tools/prefetch_basic_fields.py ✓")
    feats = sorted(d.name for d in (qlib / "features").iterdir() if d.is_dir() and d.name[2:].isdigit())
    if args.codes:
        want = {c.strip().lower() for c in args.codes.split(",") if c.strip()}
        feats = [c for c in feats if c in want]
    if args.limit:
        feats = feats[:args.limit]
    idx = {c: i for i, c in enumerate(feats)}
    print("模式 %s ｜ 目标股票 %d 只 ｜ 字段 %s\n"
          % ("APPLY ✓" if args.apply else "DRY-RUN ✓", len(feats),
             ",".join(f for f, _ in FIELDS)), flush=True)

    # ★ 预分配 [股票 × 天] float32（4 字段 × 6161 × 6484 × 4B ≈ 640 MB ✓，比 dict-of-dict 省得多 ✓）
    n_s, n_d = len(feats), len(avail)
    data = {f: np.full((n_s, n_d), np.nan, dtype=np.float32) for f, _ in FIELDS}
    dpos = {d: j for j, d in enumerate(avail)}
    miss_json = 0
    t0 = time.time()
    for j, d8 in enumerate(avail):
        p = BASIC / ("%s.json" % d8)
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                            # noqa: BLE001
            miss_json += 1
            continue
        if not obj:
            continue
        rows, cols = [], []
        for ts, rec in obj.items():
            c = to_qlib(ts)
            i = idx.get(c)
            if i is None:
                continue
            rows.append(i)
            cols.append(rec)
        if not rows:
            continue
        ri = np.asarray(rows, dtype=np.int64)
        for f, key in FIELDS:
            vals = np.asarray([(r.get(key) if r.get(key) is not None else np.nan) for r in cols],
                              dtype=np.float32)
            data[f][ri, j] = vals
        if (j + 1) % 500 == 0:
            print("   … 汇总 %d/%d 天（%.0fs）" % (j + 1, n_d, time.time() - t0), flush=True)

    # 写盘：每只股票裁剪到 [首个有效日, 末个有效日] ✓
    backup = ROOT / "ai_test" / ("backup_ts_market_%s" % time.strftime("%Y%m%d-%H%M%S"))
    n_ok = n_skip = n_bins = 0
    for c, i in idx.items():
        row_any = np.zeros(n_d, dtype=bool)
        for f, _k in FIELDS:
            row_any |= np.isfinite(data[f][i])
        if not row_any.any():
            n_skip += 1
            continue
        j0 = int(np.where(row_any)[0][0])
        j1 = int(np.where(row_any)[0][-1])
        lo = cpos[cal[len(cal) - n_d + j0]] if n_d < len(cal) else j0
        if lo + (j1 - j0 + 1) > n_cal:
            n_skip += 1
            continue
        n_ok += 1
        if args.apply:
            backup.mkdir(parents=True, exist_ok=True)
            for f, _k in FIELDS:
                p = qlib / "features" / c / ("%s.day.bin" % f)
                if p.exists() and not args.force:
                    continue
                if p.exists():
                    shutil.copy2(p, backup / ("%s_%s.day.bin" % (c, f)))
                write_bin(p, int(lo), data[f][i, j0:j1 + 1].astype(np.float64))
                n_bins += 1
    # 抽样打印
    print("\n抽样（各字段最新有效值 ✓）：")
    _with_data = [c for c in feats if np.isfinite(data["market_cap"][idx[c]]).any()][:3]
    for c in (_with_data or feats[:3]):
        i = idx[c]
        row = []
        for f, _k in FIELDS:
            col = data[f][i]
            fin = np.isfinite(col)
            row.append("%s=%s" % (f, ("%.6g" % col[np.where(fin)[0][-1]]) if fin.any() else "缺"))
        print("   %-9s %s" % (c, " ｜ ".join(row)))
    print("\n合计：可写 %d 只 / 跳过 %d 只（缓存 JSON 读失败 %d 天 ✗）" % (n_ok, n_skip, miss_json))
    if args.apply:
        print("已写 %d 个 bin ✓；备份（被覆盖的）在 %s ✓" % (n_bins, backup))
    else:
        print("（dry-run：未写盘 ✓；加 --apply 生效 ✓）")


if __name__ == "__main__":
    main()
