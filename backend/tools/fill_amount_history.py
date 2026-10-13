# -*- coding: utf-8 -*-
"""按**股票**补 `amount` 的**整条历史缺口** ✓（含退市股 ✓；源 = tushare `daily?ts_code=` 一次拿全史 ✓）。

## 与 `fill_amount_gap.py` 的分工 ✓
- `fill_amount_gap.py`：补「**很多股票都缺**」的共同窗口（按天取数 ✓，62 天 ✓）；
- 本工具：补「**只有个别股票缺**」的历史缺口 ✓ —— 实测 **487 只 / 333,491 格** ✓，
  其中 **335 只的 `amount` 整条是 NaN** ✗（`sh600001` 等退市股 ✓：`close`/`volume` 都到**退市日** ✓，
  但 `amount`/`vwap` 一个值都没有 ✗ —— 用户 2026-10-14："退市股的 amount 和 vwap 得到退市那天才对吧？" ✓）。
- 按股票取数比按天取数**便宜得多** ✓（487 只 = 487 次调用 ✓ vs 需要 ~6000 个交易日 ✗）。

## 闸门 ✓（不满足就跳过该股 ✓，报出来 ✓）
① 覆盖率：源覆盖的交易日数 ÷ 该股「`volume>0` 的天数」< `--min-cov`（默认 0.5 ✓）⇒ 跳过 ✗；
② 重叠一致性：在「本地 `amount` 有值、源也有」的天上取最近 20 天，比值偏离 1 超 `--tol`（1e-4 ✓）⇒ 跳过 ✗。

用法：
  python backend/tools/fill_amount_history.py --qlib-dir data/cn_data2              # dry-run ✓
  python backend/tools/fill_amount_history.py --qlib-dir data/cn_data2 --apply      # 写盘（带备份 ✓）
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

CACHE = ROOT / "data" / "tushare_cache" / "daily_by_code"


def to_ts_code(c: str) -> str:
    return "%s.%s" % (c[2:], {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(c[:2], "SZ"))


def read_bin(p: Path):
    if not p.exists():
        return None
    a = np.fromfile(p, dtype="<f4")
    return None if a.size < 2 else (int(round(float(a[0]))), a[1:].astype(float))


def fetch(code: str, tok: str, rate: float, stats: dict) -> dict:
    """`{YYYY-MM-DD: amount}` ✓（一次拿全史 ✓ + 落缓存 ✓）。"""
    p = CACHE / ("%s.json" % code)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            stats["cache"] += 1
            return json.load(f)
    r = ts_call("daily", {"ts_code": to_ts_code(code)},
                "ts_code,trade_date,close,vol,amount", tok)
    time.sleep(rate)
    stats["api"] += 1
    out = {}
    for it in (r.get("data") or {}).get("items") or []:
        if it[1] and it[4] is not None:
            d = str(it[1])
            out["%s-%s-%s" % (d[:4], d[4:6], d[6:])] = float(it[4])
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlib-dir", required=True)
    ap.add_argument("--codes", default="", help="默认=所有存在'amount 缺且 volume>0'的股票 ✓")
    ap.add_argument("--rate", type=float, default=0.25)
    ap.add_argument("--min-cov", type=float, default=0.5)
    ap.add_argument("--tol", type=float, default=1e-4)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    qlib = Path(a.qlib_dir)
    feats = qlib / "features"
    cal = [ln.strip() for ln in open(qlib / "calendars" / "day.txt", encoding="utf-8") if ln.strip()]
    if a.codes:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    else:
        codes = sorted(d.name for d in feats.iterdir()
                       if d.is_dir() and d.name[2:].isdigit() and d.name[:2] in ("sh", "sz", "bj"))
    print("数据集 %s ｜ 候选 %d 只 ｜ 模式 %s" % (qlib, len(codes),
                                            "**APPLY**" if a.apply else "dry-run"))
    tok = token()
    stats = {"api": 0, "cache": 0}
    n_ok = n_gate_cov = n_gate_val = n_nogap = 0
    plan, bad = {}, []
    for c in codes:
        d = feats / c
        ha, hv = read_bin(d / "amount.day.bin"), read_bin(d / "volume.day.bin")
        if ha is None or hv is None:
            continue
        gaps = []
        for i in range(ha[1].size):
            gi = ha[0] + i
            if gi >= len(cal):
                break
            j = gi - hv[0]
            if (not np.isfinite(ha[1][i]) and 0 <= j < hv[1].size
                    and np.isfinite(hv[1][j]) and hv[1][j] > 0):
                gaps.append(gi)
        if not gaps:
            n_nogap += 1
            continue
        src = fetch(c, tok, a.rate, stats)
        # ① 覆盖率闸门 ✓
        need_days = [cal[g] for g in gaps]
        hit = sum(1 for dd in need_days if src.get(dd) is not None)
        if not need_days or hit / len(need_days) < a.min_cov:
            n_gate_cov += 1
            bad.append((c, "覆盖率 %.2f" % (hit / max(1, len(need_days))), len(gaps)))
            continue
        # ② 重叠一致性闸门 ✓
        fin = np.nonzero(np.isfinite(ha[1]))[0][::-1][:20]
        err, cnt = 0.0, 0
        for i in fin:
            s = src.get(cal[ha[0] + int(i)])
            if s:
                cnt += 1
                err = max(err, abs(float(ha[1][i]) / float(s) - 1.0))
        if cnt >= 5 and err > a.tol:
            n_gate_val += 1
            bad.append((c, "重叠偏差 %.2e" % err, len(gaps)))
            continue
        n_ok += 1
        plan[c] = (ha, gaps, src)
    print("⇒ 待补 %d 只 ｜ 无缺口 %d 只 ｜ **覆盖率闸门不过** %d 只 ｜ **一致性闸门不过** %d 只 ｜ "
          "取数 api %d / cache %d" % (n_ok, n_nogap, n_gate_cov, n_gate_val, stats["api"], stats["cache"]))
    if bad:
        print("   闸门不过样例 ✓：%s" % bad[:6])
    if n_ok:
        tot = sum(len(g) for _h, g, _s in plan.values())
        print("   合计要补 %d 格（例：%s ⇒ %d 格）"
              % (tot, list(plan)[0], len(plan[list(plan)[0]][1])))
    if not a.apply:
        print("dry-run ✓（`--apply` 才写 ✓；备份 `_backup_amount_hist_20261014/` ✓）")
        return
    backup = qlib / "_backup_amount_hist_20261014"
    backup.mkdir(parents=True, exist_ok=True)
    n_cell = 0
    for c, (ha, gaps, src) in plan.items():
        p = feats / c / "amount.day.bin"
        arr = ha[1].copy()
        for gi in gaps:
            s = src.get(cal[gi])
            j = gi - ha[0]
            if s is not None and 0 <= j < arr.size:
                arr[j] = s
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
        print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "fill_amount_history"))
    except Exception as e:                                                    # noqa: BLE001
        print("⚠⚠ 数据戳**没落上**（%r）⇒ 手工清 backend/workdir/feature_cache/ ✗" % e)


if __name__ == "__main__":
    main()
