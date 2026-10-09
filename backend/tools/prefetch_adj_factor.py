# -*- coding: utf-8 -*-
"""按**股票**预取 tushare `adj_factor` **全历史**（2026-10-09，为"tushare 口径"重建做准备）。

## 为什么需要
用户 2026-10-09 定调：**米筐数据以后拿不到了** ⇒ 复权因子的来源必须换成 tushare ✓。
`adj_factor(ts_code=...)` **一次调用返回该股全历史** ✓（实测 002487 一次返回 3883 行 = 全历史 ✓）。

## 落盘格式
`data/tushare_cache/adj/<ts_code>.json` = `{"ts_code": "002487.SZ", "updated": "YYYY-MM-DD",
"rows": {"YYYYMMDD": adj_factor}}` ✓（键用紧凑的 8 位日期 ✓ 与其它缓存风格一致 ✓）。
原子写（tmp + `os.replace` ✓）⇒ 中断也不会留半个文件 ✓；**已存在的默认跳过** ⇒ 可断点续跑 ✓。

## 用法
    python tools/prefetch_adj_factor.py                    # dry-run：列出要拉的股票与预计耗时
    python tools/prefetch_adj_factor.py --apply            # 真的拉（后台跑 ✓ 约 0.4s/只）
    python tools/prefetch_adj_factor.py --apply --limit 20 # 冒烟
    python tools/prefetch_adj_factor.py --apply --force    # 重拉已有的

跑完可用 `--check 300` 抽查"qlib 官方 factor 是否 ∝ tushare adj"（两套口径等价性核验 ✓）。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_tushare_finance import atomic_json_dump, to_ts_code, token, ts_call   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ADJ_DIR = ROOT / "data" / "tushare_cache" / "adj"
MAIN_ROOT = Path(os.environ.get("MARKET_DAILY_ROOT", r"E:\quant\trader_code\data\market\daily"))
RQ_SUFFIX = {"sh": "XSHG", "sz": "XSHE", "bj": "BJSE"}


def has_main_source(code: str) -> bool:
    suf = RQ_SUFFIX.get(code[:2])
    return bool(suf and code[2:].isdigit() and (MAIN_ROOT / ("%s.%s.h5" % (code[2:], suf))).exists())


def target_codes(qlib_dir: Path) -> list:
    """要拉的股票 = qlib provider 里**有主源 raw** 的代码（没有主源的修不了，拉了也没用 ✓）。"""
    feats = qlib_dir / "features"
    return [d.name for d in sorted(feats.iterdir())
            if d.is_dir() and d.name[:2] in RQ_SUFFIX and d.name[2:].isdigit() and has_main_source(d.name)]


def main():
    ap = argparse.ArgumentParser(description="按股票预取 tushare adj_factor 全历史 ✓")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--rate", type=float, default=0.31, help="每次调用后 sleep（秒 ✓）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="已有缓存也重拉 ✓")
    ap.add_argument("--apply", action="store_true", help="真的拉取并写缓存（默认 dry-run ✓）")
    ap.add_argument("--check", type=int, default=0,
                    help="改跑核验模式：抽查 N 只，验证 qlib 官方 factor 是否 ∝ tushare adj ✓")
    ap.add_argument("--qlib-ref", default=str(ROOT / "data" / "cn_data3"),
                    help="核验用的 qlib 官方 provider（默认 cn_data3 ✓）")
    args = ap.parse_args()

    CODES = target_codes(Path(args.qlib_dir))
    if args.limit:
        CODES = CODES[:args.limit]
    print("待处理 %d 只 | 缓存目录 %s | 主源 %s" % (len(CODES), ADJ_DIR, MAIN_ROOT), flush=True)

    if args.check:
        return check_proportionality(CODES, int(args.check), Path(args.qlib_ref))

    todo = [c for c in CODES if args.force or not (ADJ_DIR / ("%s.json" % to_ts_code(c))).exists()]
    print("已有缓存 %d 只 ⇒ 还需拉 %d 只（约 %.0f 分钟，按 %.2fs/只 ✓）"
          % (len(CODES) - len(todo), len(todo), len(todo) * (args.rate + 0.15) / 60.0, args.rate), flush=True)
    if not args.apply:
        print("dry-run（加 --apply 才真的拉 ✓）")
        return

    ADJ_DIR.mkdir(parents=True, exist_ok=True)
    tok = token()
    t0 = time.time()
    n_ok = n_empty = n_err = 0
    for i, code in enumerate(todo, 1):
        ts_code = to_ts_code(code)
        try:
            r = ts_call("adj_factor", {"ts_code": ts_code}, "ts_code,trade_date,adj_factor", tok)
            items = (r.get("data") or {}).get("items") or []
            rows = {}
            for it in items:
                if len(it) >= 3 and it[1] is not None and it[2] is not None:
                    rows[str(it[1])] = float(it[2])
            if not rows:
                n_empty += 1
                print("  ⚠ %-9s 返回空（tushare 无覆盖）⇒ 记空对象 ✓" % code, flush=True)
            atomic_json_dump(ADJ_DIR / ("%s.json" % ts_code),
                             {"ts_code": ts_code, "updated": time.strftime("%Y-%m-%d"), "rows": rows})
            n_ok += 1
        except Exception as e:                                            # noqa: BLE001
            n_err += 1
            print("  ✗ %-9s %s（跳过，下次续跑 ✓）" % (code, e), flush=True)
        time.sleep(args.rate)
        if i % 50 == 0 or i == len(todo):
            el = time.time() - t0
            print("  进度 %d/%d  成功 %d / 空 %d / 失败 %d  %.0fs（%.2fs/只）预计剩余 %.0f 分钟"
                  % (i, len(todo), n_ok, n_empty, n_err, el, el / i, el / i * (len(todo) - i) / 60.0),
                  flush=True)
    print("完成：成功 %d / 空 %d / 失败 %d，用时 %.0fs ✓" % (n_ok, n_empty, n_err, time.time() - t0))


def check_proportionality(codes: list, n: int, qlib_ref: Path) -> None:
    """核验 `qlib 官方 factor` 与 `tushare adj` 是否只差一个**常数**（⇒ 两套口径等价 ✓）。"""
    have = [c for c in codes if (ADJ_DIR / ("%s.json" % to_ts_code(c))).exists()]
    random.seed(20261009)
    pick = random.sample(have, min(n, len(have))) if have else []
    if not pick:
        print("核验需要先跑 --apply 拉一批 adj ✓")
        return
    print("抽查 %d 只：算 factor_qlib(d) / adj_tushare(d) 的相对极差（≈1e-6 就说明只差常数 ✓）" % len(pick))
    rows_out = []
    for code in pick:
        p = qlib_ref / "features" / code / "factor.day.bin"
        if not p.exists():
            continue
        a = np.fromfile(str(p), dtype="<f4")
        first, vals = int(a[0]), a[1:].astype(np.float64)
        cal = [d for d in np.genfromtxt(qlib_ref / "calendars" / "day.txt", dtype=str)]
        adj = json.loads((ADJ_DIR / ("%s.json" % to_ts_code(code))).read_text(encoding="utf-8"))["rows"]
        rr = []
        for i, v in enumerate(vals):
            d8 = str(cal[first + i]).replace("-", "")
            t = adj.get(d8)
            if t and v > 0:
                rr.append(v / t)
        if len(rr) < 100:
            continue
        r = np.asarray(rr)
        rows_out.append((code, len(rr), float(np.median(r)), float((r.max() - r.min()) / np.median(r))))
    rows_out.sort(key=lambda x: -x[3])
    print("%-10s %6s %14s %12s" % ("代码", "共同天数", "中位比值", "相对极差"))
    for code, nn, med, spread in rows_out[:20]:
        print("%-10s %6d %14.8f %12.2e" % (code, nn, med, spread))
    if rows_out:
        sp = np.asarray([x[3] for x in rows_out])
        print("⇒ 相对极差：中位 %.2e / 最大 %.2e / >1e-3 的只数 %d/%d"
              % (float(np.median(sp)), float(sp.max()), int((sp > 1e-3).sum()), len(sp)))


if __name__ == "__main__":
    main()
