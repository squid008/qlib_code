# -*- coding: utf-8 -*-
"""补 `mf_*` 的**非连续尾/洞** ✓（2026-10-14 全量体检发现 ✓；源 = tushare `moneyflow` ✓）。

## 为什么还要一个（已有 `dump_tushare_mf.py` ✗）
`dump_tushare_mf.py` 是**整段按天写**（`2026-08-20 ~ 10-09` ✓），隐含假设"所有股票的 `mf_*`
都停在 **2026-08-19**" ✗ —— 实测有 **6 只**（`sh600984`/`sz002084`/`sz002445`/`sz002906`/
`sz300176`/`sz300862`）停在 **08-10 ~ 08-18** ✗ ⇒ 它们**整段都没被写**（`mf_pct_main` 尾部
落后 32~38 个交易日 ✗）。**这正是对照记录坑 43/47 那类病：只补"尾巴"会漏"洞"** ✓。

## 判据（"该有却没有" ✓，不硬编码窗口 ✗）
对每只股票：在 `[该股 close 首个有值日, close 末日]` 内，某天若
① 该字段当前是 **NaN** ✓、② 当天 `moneyflow` 源**有这只股票** ✓ ⇒ **补** ✓（**永不覆盖**有值格 ✓）。

## 闸门（对拍 ✓，不通过就**不写该股** ✗）
取该股**最近 30 个"当前有值且源也有"的重叠日**逐字段比，相对误差 > `--tol`（1e-3 ✓）⇒ 跳过 ✓。

用法（backend 目录下 ✓）：
    python tools/fill_mf_gap.py --qlib-dir data/cn_data2            # dry-run ✓
    python tools/fill_mf_gap.py --qlib-dir data/cn_data2 --apply    # 写盘（带备份 ✓）
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dump_finance import write_bin                                    # noqa: E402
from dump_tushare_finance import read_bin, token, ts_call             # noqa: E402
from dump_tushare_mf import FIELDS as MF_FIELDS, derive, fetch_mf     # noqa: E402
from dump_tushare_tags import cal_of, codes_of                        # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser(description="补 mf_* 的非连续尾/洞（tushare moneyflow ✓）")
    ap.add_argument("--qlib-dir", required=True)
    ap.add_argument("--codes", default="", help="默认：自动找出所有 mf 有洞的股票 ✓")
    ap.add_argument("--sleep", type=float, default=0.35)
    ap.add_argument("--tol", type=float, default=1e-3)
    ap.add_argument("--since", default="", help="只补这一天（含）之后的洞 ✓ —— "
                                                "**推荐**：历史洞（2016~2025）是**米筐/东财源本身缺行** ✗，"
                                                "用 tushare 值去填会**掺另一套口径** ✗；近期洞才是我们该补的 ✓")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    qlib = Path(a.qlib_dir)
    feat = qlib / "features"
    cal = cal_of(qlib)
    pos = {d: i for i, d in enumerate(cal)}
    codes = ([c.strip() for c in a.codes.split(",") if c.strip()] if a.codes else codes_of(qlib))
    mf_fields = sorted(derive({"buy_sm_vol": 1, "sell_sm_vol": 1,
                               "buy_sm_amount": 1, "sell_sm_amount": 1}).keys())
    # ★ `--since` 用**二分**定位 ✓（直接 `pos.get(since)` 会踩"非交易日"⇒ 回 0 ⇒ 等于没筛 ✗？
    #   2026-10-14 实测：`2026-08-01` 是周六 ✗ ⇒ `.get()` 给 0 ⇒ 筛了个寂寞 ✗✗）
    i_since = 0
    if a.since:
        i_since = int(np.searchsorted(np.asarray(cal), a.since))
        print("--since %s ⇒ 日历下标 %d（%s ✓）" % (a.since, i_since,
                                                cal[min(i_since, len(cal) - 1)]))
    print("数据集 %s ｜ 候选 %d 只 ｜ mf 字段 %d 个 ｜ 模式 %s"
          % (qlib, len(codes), len(mf_fields), "**APPLY**" if a.apply else "dry-run"))

    # ---- ① 找洞（用 mf_pct_main 的末日当"该股 mf 序列的末日" ✓，再逐字段核 ✓）
    plan: dict = {}
    need_days: set = set()
    verify_days: set = set()                 # ★ 闸门要用的"**重叠日**" ✓（该股最近 30 个有值日 ✓）
    for c in codes:
        hc = read_bin(feat / c / "close.day.bin")
        if hc is None:
            continue
        finc = np.nonzero(np.isfinite(hc[1]))[0]
        if not finc.size:
            continue
        lo, hi = hc[0] + int(finc[0]), hc[0] + int(finc[-1])
        # ★ 只算"**当天真的成交过**"的日子 ✓（`volume > 0` ✓）——
        #   停牌日源里**本来就没有** `moneyflow` 行 ✓ ⇒ 把它们当洞只会白拉几千天 API ✗✗
        #   （2026-10-14 实测：不筛时"涉及 3317 天" ✗，筛后大减 ✓）。
        hvo = read_bin(feat / c / "volume.day.bin")
        traded = None
        if hvo is not None:
            k0, k1 = max(0, lo - hvo[0]), min(hvo[1].size, hi + 1 - hvo[0])
            if k1 > k0:
                traded = (hvo[1][k0:k1] > 0) & np.isfinite(hvo[1][k0:k1])
                traded_off = hvo[0] + k0
        holes = {}
        for fl in mf_fields:
            h = read_bin(feat / c / ("%s.day.bin" % fl))
            if h is None:
                continue
            # ★ numpy 切片 ✓（2026-10-14：原来用逐日 Python 循环 ✗ ⇒ 6161×45×2400 次要几分钟 ✗）
            #   ⚠ 只从**该字段自己首个有值日**往后找 ✓ —— 否则会把"该字段本来就没覆盖的早年"
            #   当成洞 ✗（`mf_*` 从 2010 前后才有 ✗）⇒ 会去拉几千天 API ✗✗。
            f0 = np.nonzero(np.isfinite(h[1]))[0]
            if not f0.size:
                continue
            lo_f = h[0] + int(f0[0])
            j0, j1 = max(0, max(lo, lo_f) - h[0]), min(h[1].size, hi + 1 - h[0])
            if j1 <= j0:
                continue
            seg = h[1][j0:j1]
            miss_mask = ~np.isfinite(seg)
            if traded is not None:                       # ★ 再与"成交过"取交集 ✓
                a0 = (h[0] + j0) - traded_off
                sub = traded[a0:a0 + seg.size] if a0 >= 0 else np.zeros(seg.size, dtype=bool)
                if sub.size < seg.size:
                    sub = np.concatenate([sub, np.zeros(seg.size - sub.size, dtype=bool)])
                miss_mask = miss_mask & sub
            miss = np.nonzero(miss_mask)[0]
            idx = [h[0] + j0 + int(k) for k in miss]
            # ★★ 数组**末尾之后**也算洞 ✓（2026-10-14 关键修正 ✗→✓）：
            #   "尾巴缺"时 bin 数组**本身就截断** ✗（不是数组里的 NaN ✗）⇒ 只扫数组内部
            #   会**完全看不到**这类缺口 ✗ —— 实测那 6 只"停牌后复牌"的股票就是这么被漏掉的 ✗
            #   （`sh600984`：`mf` 停 2026-08-10 ✗，而 `close/volume` 09-30 起又有值 ✓）。
            t0 = max(h[0] + h[1].size, lo_f)
            if t0 <= hi:
                span = hi - t0 + 1
                if traded is not None:
                    off0 = t0 - traded_off                   # ★ 别叫 `a` ✗（会盖掉 args ✓）
                    sub = (traded[off0:off0 + span] if off0 >= 0 else np.zeros(span, dtype=bool))
                    if sub.size < span:
                        sub = np.concatenate([sub, np.zeros(span - sub.size, dtype=bool)])
                else:
                    sub = np.ones(span, dtype=bool)
                idx += [t0 + int(k) for k in np.nonzero(sub)[0]]
            if not idx:
                continue
            if i_since:
                idx = [g for g in idx if g >= i_since]
                if not idx:
                    continue
            holes[fl] = idx
            need_days.update(cal[g] for g in idx if g < len(cal))
            for k in f0[::-1][:30]:                      # ★ 记下闸门要用的重叠日 ✓
                g = h[0] + int(k)
                if g < len(cal):
                    verify_days.add(cal[g])
        if holes:
            plan[c] = holes
    print("⇒ 有洞的股票 **%d 只** ｜ 涉及交易日 **%d 天**（%s ~ %s）"
          % (len(plan), len(need_days), min(need_days) if need_days else "-",
             max(need_days) if need_days else "-"))
    if not plan:
        print("没有洞 ✓ 结束")
        return 0

    # ---- ② 取数（按天 ✓，带缓存 ✓）+ 对拍闸门 ✓
    tok = token()
    src_by_day = {}
    todo = sorted(set(need_days) | verify_days)          # ★ 闸门日一起取 ✓（否则"0 天重叠" ✗）
    for i, d in enumerate(todo, 1):
        src_by_day[d] = {k: derive(v) for k, v in fetch_mf(d, tok, a.sleep).items()}
        if i % 20 == 0:
            print("   …取数 %d/%d ✓（含闸门日 %d 天 ✓）" % (i, len(todo), len(verify_days)), flush=True)
    print("取数完成 ✓ %d 天" % len(src_by_day))

    # ★★ 键必须是 **qlib 代码** ✓（`fetch_mf` 用 `to_qlib_code` 建键，如 `sh600984` ✗ 不是 `600984.SH` ✗）——
    #   2026-10-14 实测踩过 ✗：按 ts_code 查 ⇒ 一处都对不上 ⇒ 闸门 `cnt=0` ⇒ 白跑一轮 ✗。

    n_ok = n_gate = 0
    final: dict = {}
    bad = []
    for c, holes in plan.items():
        err, cnt = 0.0, 0
        for fl in list(holes):
            h = read_bin(feat / c / ("%s.day.bin" % fl))
            if h is None:
                continue
            fin = np.nonzero(np.isfinite(h[1]))[0]
            for j in fin[::-1][:30]:
                d = cal[h[0] + int(j)]
                v = (src_by_day.get(d) or {}).get(c, {}).get(fl)
                if v is None or not np.isfinite(v):
                    continue
                cnt += 1
                err = max(err, abs(float(h[1][j]) - float(v)) / max(1e-9, abs(float(h[1][j]))))
        if cnt < 5 or err > a.tol:
            n_gate += 1
            bad.append((c, cnt, round(err, 8)))
            continue
        n_ok += 1
        final[c] = holes
    print("⇒ 待补 %d 只 ｜ **闸门不过** %d 只" % (n_ok, n_gate))
    if bad:
        print("   闸门不过样例 ✓：%s" % bad[:5])
    n_cell = sum(len(v) for hs in final.values() for v in hs.values())
    print("   合计将补 %d 格（**只写当前为 NaN 的格子** ✓ 永不覆盖 ✓）" % n_cell)
    if not a.apply:
        print("dry-run ✓（`--apply` 才写 ✓；备份 `_backup_mf_gap_20261014/` ✓）")
        return 0

    backup = qlib / "_backup_mf_gap_20261014"
    backup.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for c, holes in final.items():
        touched = False
        for fl, idxs in holes.items():
            h = read_bin(feat / c / ("%s.day.bin" % fl))
            if h is None:
                continue
            arr = h[1].copy()
            need = (max(idxs) - h[0] + 1)                # ★ "尾巴"要**先把数组接长** ✓（否则写不进去 ✗）
            if arr.size < need:
                arr = np.concatenate([arr, np.full(need - arr.size, np.nan)])
            for gi in idxs:
                v = (src_by_day.get(cal[gi]) or {}).get(c, {}).get(fl)
                j = gi - h[0]
                if v is not None and np.isfinite(v) and 0 <= j < arr.size and not np.isfinite(arr[j]):
                    arr[j] = float(v)
                    wrote += 1
                    touched = True
            if touched:
                p = feat / c / ("%s.day.bin" % fl)
                (backup / c).mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, backup / c / ("%s.day.bin" % fl))
                write_bin(p, h[0], arr)
    print("完成 ✓ %d 只 / 写 %d 格 ⇒ 备份 %s ✓" % (len(final), wrote, backup))
    try:
        b = ROOT / "backend"
        if str(b) not in sys.path:
            sys.path.insert(0, str(b))
        from app.engine.feature_cache import bump_data_version
        print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "fill_mf_gap"))
    except Exception as e:                                                    # noqa: BLE001
        print("⚠⚠ 数据戳**没落上**（%r）⇒ 手工清 backend/workdir/feature_cache/ ✗" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
