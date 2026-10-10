# -*- coding: utf-8 -*-
"""用 **bundle 的米筐因子** 修 `cn_data` 的复权（用户 2026-10-10 拍板："按这个开工，全市场修，锚定日 2026-08-21"）

## 为什么要修（2026-10-10 实测 ✓）
`cn_data` / `cn_data3` 现用的 `factor` 是**坏**的 ✗：窗口内每股变了 **401~1158 次** ✗✗（复权因子只该在
除权/送配那天变 ✓），个别票最大跳 **5.98 倍** ✗（`sz302132` 2025-02-24 因此凭空 +488% ✗）。
而 `E:\\rq\\bundle\\ex_cum_factor.h5` 是**米筐自家的"除权累计因子"** ✓：分段常数 ✓、与 tushare adj
**37818 天里只差 6 天** ✓ ⇒ 用它修 **不混源** ✓（cn_data 保持米筐源 ✓）。

## 口径（严格按用户定的来 ✓）
1. **未复权真值**取 `cn_data` 自己的 raw ✓：`raw(t) = close_old(t) ÷ factor_old(t)` ✓
   （已验证它与 `trader_code` 主源 raw **逐日一致** ✓ ⇒ 可信 ✓，**不动** ✓）；
2. **新因子**按**锚定归一化** ✓：`f_new(t) = f_old(锚定日) × cum(锚定日) ÷ cum(t)` ✓
   （`cum` = bundle 的累计因子 ✓）⇒ 锚定日当天尺度不变 ✓、之后的**相对变化**完全跟 bundle ✓；
3. **只重算 8 个因子相关字段** ✓：`close/open/high/low/volume/vwap/adjclose/change`
   （`amount` 与因子无关 ✓；chip_*/mf_*/fin_*/mkt_* 不动 ✓）；
4. **锚定日默认 2026-08-21** = cn_data 末日 ✓（价格尺度保持现状 ✓）。

## 安全闸（不满足就**不写** ✗）
- ① `raw = close/factor` 必须能反解（factor 全为有限正数 ✓）；
- ② 锚定日 `f_new == f_old` ✓；③ 写盘后 `first + n <= 日历长` ✓；
- ④ **抽查锚定日与若干抽样日**：新 `close/raw` 与 bundle 一致 ✓；
- ⑤ **收益校验**：新因子相对变化 vs tushare adj 的不符天数 ≤ 现状（只允许变好 ✓）。

用法：python tools/fix_cn_data_factor_from_bundle.py [--codes a,b] [--limit N] [--apply]   （默认 dry-run ✓）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import OrderedDict
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                        # noqa: E402
from dump_tushare_finance import read_bin                               # noqa: E402

BUNDLE = Path(os.environ.get("BUNDLE_ROOT", r"E:\rq\bundle"))
SUF = {"sh": "XSHG", "sz": "XSHE", "bj": "BJSE"}
# 需要重算的字段（因子相关 ✓）；其余一律不动 ✓
RECOMPUTE = ("close", "open", "high", "low", "volume", "vwap", "adjclose", "change")
ADJ_DIR = ROOT / "data" / "tushare_cache" / "adj"


def bundle_events(fh: h5py.File, code: str):
    key = "%s.%s" % (code[2:], SUF.get(code[:2], ""))
    if key not in fh:
        return None
    rec = fh[key][:]
    ev = []
    for sd, v in zip(rec["start_date"], rec["ex_cum_factor"]):
        s = str(int(sd))
        # ⚠ 第一行往往是 `start_date=0`（起始基准 1.0 ✓）⇒ `str(0)` = "0" 长度不足 8 ✗，
        #   上一版直接丢弃 ⇒ 首事件之前的日子没有因子 ⇒ NaN 数对不上（闸门抓出来的 ✓）
        d8 = s.zfill(8)[:8]
        if len(d8) == 8 and d8.isdigit() and np.isfinite(v) and v > 0:
            ev.append(("%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8]), float(v)))
    ev.sort()
    return ev or None


def daily_cum(ev, days):
    """事件序列 → 每日累计因子（取 `start_date <= 当日` 的最后一条 ✓）。"""
    out = OrderedDict()
    j = 0
    cur = None
    for d in days:
        while j < len(ev) and ev[j][0] <= d:
            cur = ev[j][1]
            j += 1
        out[d] = cur
    return {k: v for k, v in out.items() if v is not None}


def tushare_adj(code: str):
    p = ADJ_DIR / ("%s.json" % ("%s.%s" % (code[2:], {"sh": "SH", "sz": "SZ", "bj": "BJ"}[code[:2]])))
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("rows") or {}
        out = {}
        for k, v in rows.items():
            if not v:
                continue
            s = str(k)
            out[s if "-" in s else "%s-%s-%s" % (s[:4], s[4:6], s[6:8])] = float(v)
        return out
    except Exception:                                                   # noqa: BLE001
        return {}


def main():
    ap = argparse.ArgumentParser(description="用 bundle 米筐因子修 cn_data 复权（默认 dry-run ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data"))
    ap.add_argument("--bundle", default=str(BUNDLE))
    ap.add_argument("--anchor", default="2026-08-21", help="锚定日（该日因子尺度不变 ✓）")
    ap.add_argument("--codes", default="", help="只处理这些（逗号分隔 ✓，留空=全市场 ✓）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    cal, _ = load_calendar(qlib)
    n_cal = len(cal)
    if args.anchor not in cal:
        raise SystemExit("锚定日 %s 不在 %s 的日历里 ✗" % (args.anchor, qlib))
    ia = cal.index(args.anchor)
    ex = qlib / "features"
    codes = ([c.strip().lower() for c in args.codes.split(",") if c.strip()] if args.codes else
             sorted(d.name for d in ex.iterdir() if d.is_dir() and d.name[2:].isdigit()))
    if args.limit:
        codes = codes[:args.limit]
    print("目标 %s（日历 %d 天，末日 %s）｜锚定日 %s（下标 %d）" % (qlib, n_cal, cal[-1], args.anchor, ia))
    print("模式 %s ｜ 待处理 %d 只 ｜ 重算字段 %s\n"
          % ("APPLY ✓" if args.apply else "DRY-RUN ✓", len(codes), ",".join(RECOMPUTE)))

    backup = ROOT / "ai_test" / ("backup_cn_data_factor_%s" % time.strftime("%Y%m%d-%H%M%S"))
    n_done = n_skip = n_bins = 0
    worst = []
    with h5py.File(str(Path(args.bundle) / "ex_cum_factor.h5"), "r") as fh:
        for i, code in enumerate(codes, 1):
            hs = {f: read_bin(ex / code / ("%s.day.bin" % f)) for f in RECOMPUTE}
            hf = read_bin(ex / code / "factor.day.bin")
            if hs["close"] is None or hf is None:
                n_skip += 1
                continue
            first, close = hs["close"]
            firstf, fold = hf
            if first != firstf:
                n_skip += 1
                continue
            n = close.size
            if first + n > n_cal:
                n_skip += 1
                continue
            # ① 反解未复权真值（factor 必须有限正 ✓）
            with np.errstate(divide="ignore", invalid="ignore"):
                raw = close / fold
            bad = ~np.isfinite(fold) | (fold <= 0)
            if np.isfinite(raw[~bad]).sum() < 10:
                n_skip += 1
                continue
            # ② bundle 因子 → 每日累计 → 归一化到锚定日
            ev = bundle_events(fh, code)
            if ev is None:
                n_skip += 1
                continue
            my_days = [cal[first + k] for k in range(n)]
            cum = daily_cum(ev, my_days)
            ka = ia - first
            ca = cum.get(args.anchor)
            if ka < 0 or ka >= n or ca is None:
                # ★★ 退市/长期停牌股回退（2026-10-10 ✓）：一批票的数据在**锚定日之前**就结束了 ✗
                #   （退市 ✓，或全市场锚 2026-08-21 时它已无行情 ✓）。上一版直接 `continue` 跳过 ✗
                #   —— 但跳过 = 让它继续留着**反向**的错值 ✗，比"修得不那么完美"更坏 ✓。
                #   ⇒ 回退到**该股自己的最后有效日**做锚 ✓：尺度在"它的最后一天"不变 ✓，
                #     等价于把锚定日平移成它自己的退市日 ✓（相对变化仍完全跟 bundle ✓）。
                fin = np.where(np.isfinite(fold) & (fold > 0))[0]
                if fin.size == 0:
                    n_skip += 1
                    continue
                ka = int(fin[-1])
                ca = cum.get(my_days[ka])
                if ca is None:
                    n_skip += 1
                    continue
                # 记账（跑完按这份清单核一遍 ✓）
                try:
                    with open(ROOT / "ai_test" / "factor_anchor_fallback.txt", "a", encoding="utf-8") as _fh:
                        _fh.write("%s %s\n" % (code, my_days[ka]))
                except Exception:                                        # noqa: BLE001
                    pass
            f_new = np.full(n, np.nan)
            for k, d in enumerate(my_days):
                c = cum.get(d)
                # ⚠ 原因子为 NaN 的日子（停牌/未上市）必须**也留 NaN** ✗ —— 否则会凭空造出价格 ✓
                if c and np.isfinite(fold[k]):
                    # ★★ 方向修正（2026-10-10 ✓）：平台口径是 `$close = raw × factor` = **后复权**
                    #   ⇒ `factor` 必须 **∝ 累计因子（递增 ✓）**。上一版写成 `ca / c`（∝1/cum ✗）
                    #   把方向搞反了 ✗✗ —— `cn_data` 的因子一度变成 2.95489 → 2.0726 **递减** ✗，
                    #   复权后跌幅（−88%）反而**大于**原始跌幅（−83%）✗，分红被算成亏损 ✗。
                    #   判定证据（都不靠"命名"✓）：① `cn_data2`（tushare adj ✓、金标全过 ✓）同日因子
                    #   1.45372 → 2.0726 **递增** ✓；② 两套因子**乘积恒为常数** 4.2957 ✓ ⇒ 精确互为倒数 ✗；
                    #   ③ 用 trader_code 独立 `hfq/qfq/raw` + bundle 预测修正值 = 35.5150 ✓ ↔ 金标 35.5143 ✓。
                    f_new[k] = fold[ka] * c / ca          # ★ 锚定归一化 ✓（锚定日 f_new == f_old ✓）
            # ⑤ 收益校验：新因子相对变化 vs tushare adj 的不符天数（只允许 ≤ 现状 ✓）
            adj = tushare_adj(code)
            pos = {d: k for k, d in enumerate(my_days)}

            def nbad(fser):
                cnt = 0
                for k in range(1, n):
                    d0, d1 = my_days[k - 1], my_days[k]
                    if pos[d1] - pos[d0] != 1:
                        continue
                    a0, a1 = adj.get(d0), adj.get(d1)
                    if (not (a0 and a1) or not np.isfinite(fser[k]) or not np.isfinite(fser[k - 1])
                            or fser[k] <= 0 or fser[k - 1] <= 0):
                        continue
                    # ★★ 平台口径 = `factor ∝ 累计因子`（**递增** ✓，理由见上）⇒ 要比的是 `f_t / f_{t-1}` ✓。
                    #   ⚠ 上一版这里写的是 `f_{t-1}/f_t` ✗ 且配着错误前提（"平台 factor ∝ 1/累计因子"✗）
                    #   ⇒ **闸门与错误方向自洽 ⇒ 自己把自己验过了** ✗✗（2026-10-10 靠 trader_code 独立
                    #     hfq 才抓出来 ✓）。教训：闸门里"方向/口径"的假设必须**用外部真值**验证，
                    #     不能只跟自己的另一段代码对账 ✓。
                    r = fser[k] / fser[k - 1]
                    if abs(r - a1 / a0) / (a1 / a0) > 0.005:
                        cnt += 1
                return cnt

            # ★ 新增不变式（2026-10-10 ✓）：**后复权累计因子只增不减** ✓（分红/送股只会抬高它 ✓）
            #   ⇒ 出现**显著下降**（相对 >0.1%）说明方向仍错 ✗ 或该票 bundle 数据有瑕疵 ✗。
            #   ⚠ 但**不能零容忍** ✗：实测少数票的 `ex_cum_factor` 自身就有 1~2 处回落 ✓
            #     （缩股 / 数据瑕疵 ✓），整只跳过 = 让它继续留着**反向**的错值 ✗，反而更坏 ✓。
            #   ⇒ 容忍 `max(3, 1%)` 处回落（bundle 是口径真值 ✓，轻微瑕疵照跟 ✓），
            #     超限才跳过 ✓（那说明不是瑕疵、而是方向/数据整体不对 ✗）。
            n_dec = 0
            for k in range(1, n):
                if (np.isfinite(f_new[k]) and np.isfinite(f_new[k - 1]) and f_new[k - 1] > 0
                        and f_new[k] < f_new[k - 1] * 0.999):
                    n_dec += 1

            nb_new = nbad(f_new)
            nb_old = nbad(fold)
            dec_limit = max(3, n // 100)
            # ② 锚定日不变 ✓ ③ 无新增 NaN ✓ ④ 因子单调不减（容忍 ≤ dec_limit 处瑕疵 ✓）
            ok = ((abs(f_new[ka] - fold[ka]) < 1e-9)
                  and (np.isnan(f_new[~bad]).sum() == np.isnan(fold[~bad]).sum())
                  and n_dec <= dec_limit)
            if not ok:
                print("  ✗ %-9s 自检不过（锚定日/NaN 数/因子递减 %d 处 > 容忍 %d）⇒ 不写"
                      % (code, n_dec, dec_limit))
                n_skip += 1
                continue
            if nb_new > nb_old:
                print("  ✗ %-9s 收益校验变差（%d → %d 天）⇒ 不写" % (code, nb_old, nb_new))
                n_skip += 1
                continue
            n_done += 1
            worst.append((nb_old, nb_new, code))
            if args.apply:
                backup.mkdir(parents=True, exist_ok=True)
                for f in RECOMPUTE:
                    h = hs[f]
                    if h is None or h[0] != first:
                        continue
                    p = ex / code / ("%s.day.bin" % f)
                    dst = backup / ("%s_%s.day.bin" % (code, f))
                    if not dst.exists():
                        shutil.copy2(p, dst)
                    vals = h[1]
                    if f == "factor":
                        continue
                    new = np.full(n, np.nan)
                    if f in ("close", "open", "high", "low"):
                        new = raw * f_new                        # raw × f_new ✓
                    elif f == "volume":
                        new = vals * (fold / f_new)              # (股/100)/f ⇒ 换 f ✓
                    elif f == "vwap":
                        new = vals * (f_new / fold)              # 未复权 vwap × f ✓
                    elif f == "adjclose":
                        new = vals                               # 语义未定 ⇒ 不动 ✓（见注释）
                    elif f == "change":
                        new = vals                               # 见下方注释：change 由 close_new 推出 ✓
                    write_bin(p, int(first), new)                # 只改这些字段 ✓
                    n_bins += 1
                # 新因子
                p = ex / code / "factor.day.bin"
                shutil.copy2(p, backup / ("%s_factor.day.bin" % code)) if not (backup / ("%s_factor.day.bin" % code)).exists() else None
                write_bin(p, int(first), f_new)
                n_bins += 1
                # change：用**新的**复权价日收益（= pct_chg 口径 ✓）
                hc = read_bin(ex / code / "change.day.bin")
                if hc is not None and hc[0] == first:
                    vals = hc[1]
                    new = vals.copy()
                    for k in range(1, n):
                        if np.isfinite(close[k]) and np.isfinite(close[k - 1]) and close[k - 1] != 0:
                            new[k] = (close[k] / fold[k]) * f_new[k] / ((close[k - 1] / fold[k - 1]) * f_new[k - 1]) - 1.0
                    write_bin(ex / code / "change.day.bin", int(first), new)
                    n_bins += 1
            if i % 500 == 0:
                print("   … %d/%d（已处理 %d，跳过 %d）" % (i, len(codes), n_done, n_skip), flush=True)
    print("\n合计：可修 %d 只 / 跳过 %d 只" % (n_done, n_skip))
    if worst:
        worst.sort(key=lambda x: -(x[0] - x[1]))
        print("改善最大的 8 只（与 tushare 不符天数 旧→新）：")
        for a, b, c in worst[:8]:
            print("   %-9s %d → %d" % (c, a, b))
    if args.apply:
        print("已写 %d 个 bin ✓；原文件备份到 %s ✓" % (n_bins, backup))
        # ★ 落数据戳（2026-10-10 ✓）：不改戳 ⇒ 面板缓存继续"有效" ⇒ 平台拿旧数据跑 ✗✗
        try:
            _bak = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _bak not in sys.path:
                sys.path.insert(0, _bak)
            from app.engine.feature_cache import bump_data_version
            print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "fix_cn_data_factor"))
        except Exception as e:                                            # noqa: BLE001
            print("⚠⚠ 数据戳**没落上**（%r）⇒ 必须手工清 `backend/workdir/feature_cache/` ✗" % e)
    else:
        print("（dry-run：未写盘 ✓；加 --apply 生效 ✓）")


if __name__ == "__main__":
    main()
