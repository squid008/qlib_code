# -*- coding: utf-8 -*-
"""用 tushare `daily_basic.turnover_rate` **全量重建 `turn`** ✓（2026-10-14 用户要求 ✓）。

## 为什么必须重建（实测 ✓）
`cn_data2` 的 `turn` 历史是**米筐口径**（成交额 ÷ 总市值 × 100 ✓，`build_turn_field.py` ✓），
尾段才是 tushare 的 `daily_basic.turnover_rate`（成交量 ÷ **流通股本** ✓）⇒ 一条序列两种口径 ✗；
更糟的是**早年有假的 0 值** ✗：实测 `sh600000` 2005 年有 **108 天** `turn=0` 而当天**确有成交** ✓
（`ai_test/probe_1014.py` ✓）⇒ 0 换手会让筹码类因子（`COST`/`WINNER`）**低估筹码移动** ✗。
用户 2026-10-14 定稿 ✓：**「根治要用 `daily_basic.turnover_rate` 全量重建」✓，米筐测也同步解决，
只是时间到他那边（`cn_data` 日历末 2026-08-21 ✓）**。

## 取数与缓存 ✓（**per-stock 一次拿全史** ✓，比按天取数便宜几千倍 ✓）
- 先读 `data/tushare_cache/turn_stock/<code>.json` ✓（早前 `dump_tushare_tags.py` 落的，
  实测**是整条历史** ✓，如 `sz002926` 覆盖 2018-02-05~2026-10-09 ✓）⇒ 命中就**零 API** ✓；
- 否则 `daily_basic?ts_code=` 拉全史 ✓ 并回写**同一个缓存** ✓（下次复用 ✓）。

## 闸门 ✓（不满足 ⇒ **不写该股** ✗，报出来 ✓）
覆盖率：源有值的天数 ÷ 该股「`volume>0` 的天数」< `--min-cov`（默认 0.5 ✓）⇒ 跳过 ✓。
另**打印新旧对比** ✓（在 `--report-start/--report-end` 窗口上算 `旧/新` 中位 ✓）——
米筐口径与 tushare 口径本就不同 ✓（≈ 流通市值/总市值 ✓），看到系统性偏差是**预期** ✓。

用法：
  python backend/tools/rebuild_turn.py --qlib-dir data/cn_data2              # dry-run ✓
  python backend/tools/rebuild_turn.py --qlib-dir data/cn_data2 --apply      # 写盘（带备份 ✓）
  python backend/tools/rebuild_turn.py --qlib-dir data/cn_data  --apply      # 米筐数据集（自动到它的日历末 ✓）
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dump_tushare_finance import token, ts_call              # noqa: E402

CACHE = ROOT / "data" / "tushare_cache" / "turn_stock"


def to_ts_code(c: str) -> str:
    return "%s.%s" % (c[2:], {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(c[:2], "SZ"))


def read_bin(p: Path):
    if not p.exists():
        return None
    a = np.fromfile(p, dtype="<f4")
    return None if a.size < 2 else (int(round(float(a[0]))), a[1:].astype(float))


def source_of(code: str, tok: str, rate: float, stats: dict) -> dict:
    p = CACHE / ("%s.json" % code)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if d:
            stats["cache"] += 1
            return d
    r = ts_call("daily_basic", {"ts_code": to_ts_code(code)},
                "ts_code,trade_date,turnover_rate", tok)
    time.sleep(rate)
    stats["api"] += 1
    out = {}
    for it in (r.get("data") or {}).get("items") or []:
        if it[1] and it[2] is not None:
            d8 = str(it[1])
            out["%s-%s-%s" % (d8[:4], d8[4:6], d8[6:])] = float(it[2])
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlib-dir", required=True)
    ap.add_argument("--codes", default="")
    ap.add_argument("--rate", type=float, default=0.25)
    ap.add_argument("--min-cov", type=float, default=0.5)
    ap.add_argument("--report-start", default="2025-01-01")
    ap.add_argument("--report-end", default="2026-09-30")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    qlib = Path(a.qlib_dir)
    feats = qlib / "features"
    cal = [ln.strip() for ln in open(qlib / "calendars" / "day.txt", encoding="utf-8") if ln.strip()]
    if a.codes:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    else:
        # 只做**股票** ✓（指数走 `build_index_turn.py` ✓）
        codes = sorted(d.name for d in feats.iterdir()
                       if d.is_dir() and d.name[2:].isdigit() and d.name[:2] in ("sh", "sz", "bj")
                       and not (d.name[:5] in ("sh000", "sz399")))
    print("数据集 %s ｜ 日历 %s ~ %s（%d 天 ✓）｜ 候选 %d 只 ｜ 模式 %s"
          % (qlib, cal[0], cal[-1], len(cal), len(codes), "**APPLY**" if a.apply else "dry-run"))
    pos = {d: i for i, d in enumerate(cal)}                  # ★ 日历下标表 ✓（别在循环里 cal.index ✗）
    tok = token()
    stats = {"api": 0, "cache": 0}
    n_ok = n_cov = n_nodata = 0
    plan, bad = {}, []
    ratios = []
    for c in codes:
        d = feats / c
        hv = read_bin(d / "volume.day.bin")
        src = source_of(c, tok, a.rate, stats)
        if not src:
            n_nodata += 1
            continue
        # 覆盖率闸门 ✓（源 vs 该股"真的成交过"的天数 ✓）
        traded = 0
        hit = 0
        if hv is not None:
            for i in range(hv[1].size):
                gi = hv[0] + i
                if gi >= len(cal):
                    break
                if np.isfinite(hv[1][i]) and hv[1][i] > 0:
                    traded += 1
                    if src.get(cal[gi]) is not None:
                        hit += 1
        if traded and hit / traded < a.min_cov:
            n_cov += 1
            bad.append((c, "覆盖率 %.2f" % (hit / traded)))
            continue
        # 组装整条序列（按本数据集日历 ✓；超出源范围/源没值的天 ⇒ NaN ✓）
        idx = [pos[k] for k in src if k in pos]
        if not idx:
            n_cov += 1
            bad.append((c, "源与本数据集日历无交集"))
            continue
        first = min(idx)
        arr = np.full(len(cal) - first, np.nan)
        for k, v in src.items():
            j = pos.get(k)
            if j is not None and 0 <= j - first < arr.size:
                arr[j - first] = v
        # 新旧对比 ✓（报告窗口内 ✓）
        old = read_bin(d / "turn.day.bin")
        if old is not None:
            for i in range(old[1].size):
                gi = old[0] + i
                if gi >= len(cal):
                    break
                dd = cal[gi]
                if (np.isfinite(old[1][i]) and a.report_start <= dd <= a.report_end
                        and gi >= first and np.isfinite(arr[gi - first]) and arr[gi - first] > 0
                        and old[1][i] > 0):
                    ratios.append(float(old[1][i]) / float(arr[gi - first]))
        n_ok += 1
        plan[c] = (first, arr)
    print("⇒ 待重建 %d 只 ｜ **覆盖率闸门不过** %d 只 ｜ 源无数据 %d 只 ｜ 取数 api %d / cache %d"
          % (n_ok, n_cov, n_nodata, stats["api"], stats["cache"]))
    if bad:
        print("   闸门不过样例 ✓：%s" % bad[:6])
    if ratios:
        r = np.asarray(ratios)
        print("   新旧对比（%s~%s 窗口 ✓，n=%d ✓）：旧/新 中位 **%.4f** ｜ p10 %.4f p90 %.4f"
              % (a.report_start, a.report_end, r.size, float(np.median(r)),
                 float(np.percentile(r, 10)), float(np.percentile(r, 90))))
        print("   （米筐口径 ≈ 成交额÷总市值 vs tushare ≈ 成交量÷流通股本 ⇒ 系统性偏差是**预期** ✓）")
    if n_ok:
        tot = int(sum(int(np.isfinite(v).sum()) for _f, v in plan.values()))
        print("   合计将写 %d 个有值格子 ✓（例：%s ⇒ 首日 %s、%d 个有值 ✓）"
              % (tot, list(plan)[0], cal[plan[list(plan)[0]][0]],
                 int(np.isfinite(plan[list(plan)[0]][1]).sum())))
    if not a.apply:
        print("dry-run ✓（`--apply` 才写 ✓；旧 bin 备份到 `_backup_turn_20261014/` ✓）")
        return
    backup = qlib / "_backup_turn_20261014"
    backup.mkdir(parents=True, exist_ok=True)
    n_cell = 0
    for c, (first, arr) in plan.items():
        d = feats / c
        p = d / "turn.day.bin"
        if p.exists():
            (backup / c).mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup / c / "turn.day.bin")
        tmp = str(p) + ".tmp"
        np.hstack([np.asarray([first], dtype="<f4"), arr.astype("<f4")]).astype("<f4").tofile(tmp)
        os.replace(tmp, p)
        n_cell += int(np.isfinite(arr).sum())
    print("完成 ✓ %d 只 / %d 个有值格 ⇒ 备份 %s ✓" % (len(plan), n_cell, backup))
    try:
        b = ROOT / "backend"
        if str(b) not in sys.path:
            sys.path.insert(0, str(b))
        from app.engine.feature_cache import bump_data_version
        print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "rebuild_turn"))
    except Exception as e:                                                    # noqa: BLE001
        print("⚠⚠ 数据戳**没落上**（%r）⇒ 手工清 backend/workdir/feature_cache/ ✗" % e)


if __name__ == "__main__":
    main()
