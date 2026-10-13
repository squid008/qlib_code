# -*- coding: utf-8 -*-
"""补 `amount` 的**中间缝隙** ✓（2026-10-13 全面体检发现 ✓，源 = tushare `daily` ✓）。

## 发现
`cn_data2` 的 `amount` 在 **2026-05-27 ~ 2026-08-21（62 个交易日）** 对绝大多数股票是 **NaN** ✗，
而 `close`/`volume` 都有 ✓（`ai_test/hole_probe_1013.py` 实测：抽样股该段**一律整 62 天** ✓，
例如 `sh601991`：amount 84 天洞 vs close 22 天洞，**差正好 62** ✓）。
成因 ✓：上一轮"行情增量"只用了 `data/tushare_cache/daily/`（**只有 2026-08-24 起 29 天** ✗），
05-27~08-21 这段没取 ⇒ `amount` 断档 ✗（`vwap = 10×amount÷volume` 跟着断 ✗）。

## 口径（实测 ✓，**逐位** ✓）
本地 `amount` == tushare `daily.amount` ✓（比值 **1.000000** ✓，单位同为**千元** ✓）；
`volume` = 源 `vol` ÷ factor ✓、`close` = 源 × factor ✓（本工具**不动**它们 ✓）。

## 判据（"该有却没有" ✓，不是硬编码窗口 ✗）
某天"应当有 amount" 的充要条件 = **该股当天 `volume` 有值** ✓（停牌日两者都 NaN ✓）。
⇒ 本工具只补「`amount` 是 NaN **且** `volume` 有值」的格子 ✓ —— 历史里那些"停牌洞"因此**不会被填** ✓。

## 闸门 ✓
每股用**最近 20 个"本地 amount 有值"的重叠日**与源比，相对差 > `--tol`（1e-4 ✓）⇒ **拒绝写该股** ✗。

用法：
  python backend/tools/fill_amount_gap.py --qlib-dir data/cn_data2          # dry-run ✓（会取数并落缓存 ✓）
  python backend/tools/fill_amount_gap.py --qlib-dir data/cn_data2 --apply  # 写盘（带备份 ✓）
"""
import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dump_tushare_finance import token, ts_call              # noqa: E402

CACHE_AMT = ROOT / "data" / "tushare_cache" / "daily_amount"


def read_bin(p: Path):
    if not p.exists():
        return None
    a = np.fromfile(p, dtype="<f4")
    return None if a.size < 2 else (int(round(float(a[0]))), a[1:].astype(float))


def day_amounts(day: str, tok: str, rate: float, stats: dict) -> dict:
    """某天的 {ts_code: amount} ✓：先读缓存（**两处** ✓：本工具自己的 + 旧的 `daily/` ✓），
    没有就取 API 并落缓存 ✓（零重复调用 ✓）。"""
    p = CACHE_AMT / ("%s.json" % day.replace("-", ""))
    if p.exists():
        with open(p, encoding="utf-8") as f:
            stats["cache"] += 1
            return json.load(f)
    legacy = ROOT / "data" / "tushare_cache" / "daily" / ("%s.json" % day.replace("-", ""))
    if legacy.exists():
        with open(legacy, encoding="utf-8") as f:
            d = json.load(f)
        stats["cache"] += 1
        rows = d.get("daily") if isinstance(d, dict) and "daily" in d else None
        if isinstance(rows, list):
            return {r["ts_code"]: r["amount"] for r in rows
                    if r.get("ts_code") and r.get("amount") is not None}
        if isinstance(d, dict):
            return d
    r = ts_call("daily", {"trade_date": day.replace("-", "")},
                "ts_code,trade_date,close,vol,amount", tok)
    time.sleep(rate)
    stats["api"] += 1
    out = {}
    for it in (r.get("data") or {}).get("items") or []:
        if it[0] and it[4] is not None:
            out[it[0]] = float(it[4])
    CACHE_AMT.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlib-dir", required=True, help="★ 必填（防写错数据集 ✗）")
    ap.add_argument("--codes", default="")
    ap.add_argument("--rate", type=float, default=0.25)
    ap.add_argument("--verify-days", type=int, default=20)
    ap.add_argument("--tol", type=float, default=1e-4)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    qlib = Path(a.qlib_dir)
    feats = qlib / "features"
    cal = [ln.strip() for ln in open(qlib / "calendars" / "day.txt", encoding="utf-8") if ln.strip()]
    codes = ([c.strip() for c in a.codes.split(",") if c.strip()] if a.codes
             else sorted(d.name for d in feats.iterdir()
                         if d.is_dir() and d.name[2:].isdigit() and d.name[:2] in ("sh", "sz", "bj")))
    print("数据集 %s ｜ 候选 %d 只 ｜ 模式 %s" % (qlib, len(codes),
                                            "**APPLY**" if a.apply else "dry-run"))
    need_days = Counter()
    per_stock = {}
    verify_days = set()
    for c in codes:
        d = feats / c
        ha = read_bin(d / "amount.day.bin")
        hv = read_bin(d / "volume.day.bin")
        if ha is None or hv is None:
            continue
        hdr = ha[0]
        mis = []
        fin = [i for i in range(ha[1].size) if np.isfinite(ha[1][i])]
        for i in fin[-a.verify_days:]:                     # ★ 闸门用的重叠日 ✓
            gi = hdr + int(i)
            if gi < len(cal):
                verify_days.add(cal[gi])
        for i in range(ha[1].size):
            gi = hdr + i
            j = gi - hv[0]
            # 判据 ✓：amount 缺 **且** 当天**真的成交过**（volume > 0 ✓；=0/NaN 都是停牌 ⇒ 本来就没有 amount ✓）
            if (not np.isfinite(ha[1][i]) and 0 <= j < hv[1].size
                    and np.isfinite(hv[1][j]) and hv[1][j] > 0 and gi < len(cal)):
                mis.append(gi)
                need_days[cal[gi]] += 1
        if mis:
            per_stock[c] = (ha, hv, mis)
    print("⇒ 有缝隙的股票 %d 只 ｜ 涉及的交易日 %d 天（%s ~ %s）"
          % (len(per_stock), len(need_days),
             min(need_days) if need_days else "-", max(need_days) if need_days else "-"))
    if not need_days:
        print("没有缝隙 ✓ 结束")
        return
    # ★ 只补"**很多股票都缺**"的那些天 ✓（= 真实缝隙窗口 ✓）；个别股票自己缺一大片 ✗
    #   那是"整只没物化好"✗，本工具治不了 ⇒ 报出来交人工 ✓（别去拉几千天 API ✗）
    thr = max(50, int(0.05 * len(per_stock)))
    common = sorted([d for d, k in need_days.items() if k >= thr])
    rare = sorted([d for d, k in need_days.items() if k < thr])
    print("   其中 **共同窗口**（≥%d 只都缺 ✓）%d 天：%s ~ %s ｜ 罕见日期 %d 天（只个别股票 ✗ 交人工 ✓）"
          % (thr, len(common), common[0] if common else "-", common[-1] if common else "-", len(rare)))
    if not common:
        print("没有共同窗口 ✓ 结束（可能全是个别股票的问题 ✗）")
        return
    stats = {"api": 0, "cache": 0}
    tok = token()
    todo = sorted(set(common) | verify_days)              # ★ 闸门日一起取 ✓（否则闸门"0 天重叠" ✗）
    print("   需取数：共同窗口 %d 天 + 闸门日 %d 天 ⇒ 合计 %d 天 ✓"
          % (len(common), len(verify_days), len(todo)))
    by_day = {}
    for i, d in enumerate(todo, 1):
        by_day[d] = day_amounts(d, tok, a.rate, stats)
        if i % 20 == 0:
            print("   …取数 %d/%d（api %d / cache %d ✓）" % (i, len(todo), stats["api"], stats["cache"]),
                  flush=True)
    print("取数完成 ✓ api %d 次 / 缓存命中 %d 次" % (stats["api"], stats["cache"]))
    # 只保留共同窗口内的缺口 ✓
    for c in list(per_stock):
        ha, hv, mis = per_stock[c]
        keep = [gi for gi in mis if cal[gi] in set(common)]
        if not keep:
            del per_stock[c]
            continue
        if len(keep) < len(mis):
            print("   ⚠ %s：自身还缺 %d 天在共同窗口之外 ✗（未补，交人工 ✓）" % (c, len(mis) - len(keep)))
        per_stock[c] = (ha, hv, keep)

    n_ok = n_gate = 0
    plan, bad = {}, []
    # ts_code ↔ qlib 代码映射（**只建一次** ✓，别在循环里重复扫 5500 行 ✗）
    ts_of = {}
    for rec in by_day.values():
        for k in rec:
            num, _, suf = k.partition(".")
            pre = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf.upper())
            if pre:
                ts_of[pre + num] = k
    print("  映射表 ✓ %d 只" % len(ts_of))
    for c, (ha, hv, mis) in per_stock.items():
        ts = ts_of.get(c)
        if ts is None:
            n_gate += 1
            bad.append((c, "源无该股", 0.0))
            continue
        # 闸门：最近 N 个"本地有值"的重叠日，比本地/源 ✓
        fin = np.nonzero(np.isfinite(ha[1]))[0][::-1][:a.verify_days]
        err, cnt = 0.0, 0
        for i in fin:
            d = cal[ha[0] + int(i)]
            src = (by_day.get(d) or {}).get(ts)
            if src:
                cnt += 1
                err = max(err, abs(float(ha[1][i]) / float(src) - 1.0))
        if cnt < 5 or err > a.tol:
            n_gate += 1
            bad.append((c, "%d 天重叠" % cnt, round(err, 8)))
            continue
        n_ok += 1
        plan[c] = (ha, hv, mis, ts)
    print("⇒ 待补 %d 只 ｜ **闸门不过/无源** %d 只" % (n_ok, n_gate))
    if bad:
        print("   闸门不过样例：%s" % bad[:5])
    if not a.apply:
        print("dry-run ✓（`--apply` 才写 ✓；备份 _backup_amount_20261013/ ✓；取数缓存 %s ✓）" % CACHE_AMT)
        return
    backup = qlib / "_backup_amount_20261013"
    backup.mkdir(parents=True, exist_ok=True)
    n_cell = 0
    for c, (ha, _hv, mis, ts) in plan.items():
        p = feats / c / "amount.day.bin"
        arr = ha[1].copy()
        for gi in mis:
            src = (by_day.get(cal[gi]) or {}).get(ts)
            if src:
                j = gi - ha[0]
                if 0 <= j < arr.size:
                    arr[j] = src
                    n_cell += 1
        (backup / c).mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, backup / c / "amount.day.bin")
        tmp = str(p) + ".tmp"
        np.hstack([np.asarray([ha[0]], dtype="<f4"), arr.astype("<f4")]).astype("<f4").tofile(tmp)
        os.replace(tmp, p)
    print("完成 ✓ %d 只 / 填 %d 格 ⇒ 备份 %s ✓" % (len(plan), n_cell, backup))
    try:
        b = ROOT / "backend"
        if str(b) not in sys.path:
            sys.path.insert(0, str(b))
        from app.engine.feature_cache import bump_data_version
        print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "fill_amount_gap"))
    except Exception as e:                                                    # noqa: BLE001
        print("⚠⚠ 数据戳**没落上**（%r）⇒ 手工清 backend/workdir/feature_cache/ ✗" % e)


if __name__ == "__main__":
    main()
