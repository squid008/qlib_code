# -*- coding: utf-8 -*-
"""修复"行情 bin 与日历错位"（用户 2026-10-09，v1.20.92）。

## 背景（只读核对结论，见 ai_test/dryrun_three_checks.py）
`dump_tushare_daily.py` 的判据是 `first + 值个数 == base_len`（`base_len=日历长-本次要补的天数`）；
实测 6141 只里 **没有一只能接上**：

| 组 | 只数 | 写入时间 | 含义 |
|---|---|---|---|
| `+28` | 5205 | 今天 10-09 12:xx | 值整体**多算 28 天**（头偏大）✗ |
| `-28` | 343 | 08-22 08:xx | 值已对齐 ✓，只缺 8/24~10/09 这 28 个交易日 |
| 其他偏短 | 593 | 08-22 08:xx | 缺口更早（-242/-347/-1045/-4965…），缓存覆盖不到 |

③ 各字段 `(first, n)` 完全一致 ✓；④ `adjclose/close` 逐股近似常数（float32 舍入级 ✓）⇒ 可整套一起修 ✓。

## 本工具做什么（**只把 bin 对齐到"日历的倒数第二天 10/08"** ✓）
- `+28` 组：把整套值**前移 28** ⇒ `first' = first - 28`；若 `first < 28`（全历史股）则丢掉开头
  `28 - first` 个值、`first' = 0` ✓ ⇒ 之后 `first' + n' == base_len` ✓
- 偏短组：用缓存**补齐** `calendar[first+n : base_len]`（值按 `dump_tushare_daily` 同一套公式：
  `close=raw×f(d)`、`volume=vol÷f(d)`、`f(d)=f(锚)×adj(d)/adj(锚)`、`change=pct_chg/100` ✓，
  锚定日 = 该 bin 的末日期 ✓，`f(锚)` 取该股 factor bin 末值 ✓、`adj(锚)` 从缓存或 tushare 取 ✓）；
  ⚠ 缺口超过 `--cap`（默认 60 天 ⇒ 缓存补不了）的**跳过并列入人工清单** ✓
- ⚠ 10/09 **不在这里写** ✗ —— 对齐后跑一次官方 `dump_tushare_daily.py --apply` 即可正常"追加" ✓

用法：python tools/repair_daily_bins.py [--apply] [--cap 60] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                       # noqa: E402
from dump_tushare_daily import (                                        # noqa: E402
    CACHE, EXTRA_FIELDS, PRICE_FIELDS, load_cache_days, read_day, to_qlib_code,
)
from dump_tushare_finance import read_bin                               # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIELDS = PRICE_FIELDS + EXTRA_FIELDS


def main():
    ap = argparse.ArgumentParser(description="对齐行情 bin 与日历（先把 10/09 的那一天留出来 ✓）")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    ap.add_argument("--cap", type=int, default=60, help="只修缺口 ≤ 该天数的（能由缓存补齐 ✓）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（冒烟 ✓）")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    cal, _ = load_calendar(qlib)
    base_len = len(cal) - 1                     # 日历**已含 10/09** ⇒ 对齐目标是 10/08 ✓
    feats = qlib / "features"
    codes = sorted(d.name for d in feats.iterdir()
                   if d.is_dir() and d.name[:2] in ("sh", "sz", "bj"))
    if args.limit:
        codes = codes[:args.limit]
    cache_days = load_cache_days()
    print("日历 %d 天（末日 %s）| 目标：每只 bin 末值落在 %s（再跑官方工具追加 %s ✓）"
          % (len(cal), cal[-1], cal[base_len - 1], cal[-1]))
    print("缓存可用交易日 %d 个：%s ~ %s" % (len(cache_days), cache_days[0], cache_days[-1]),
          flush=True)

    # 缓存（按需加载 ✓）
    day_cache: Dict[str, tuple] = {}
    adj_cache: Dict[str, Dict[str, float]] = {}

    def adj_of(d: str) -> Dict[str, float]:
        """某日的 adj_factor（缓存优先，缺则现拉一次 ✓）。"""
        if d in adj_cache:
            return adj_cache[d]
        p = CACHE / (d.replace("-", "") + ".json")
        if p.exists():
            _, adj = read_day(d)
        else:
            from dump_tushare_finance import token, ts_call
            r = ts_call("adj_factor", {"trade_date": d.replace("-", "")}, "ts_code,adj_factor", token())
            adj = {to_qlib_code(x[0]): float(x[1])
                   for x in (r.get("data", {}).get("items") or [])}
            print("   （锚定日 %s 不在缓存 ⇒ 已向 tushare 现拉 %d 只的 adj_factor ✓）" % (d, len(adj)))
        adj_cache[d] = adj
        return adj

    n_over = n_under = n_ok = n_skip = n_write = 0
    skip_list = []
    verify_bad = []
    verify_ok = 0
    for c in codes:
        bins = {}
        for f in FIELDS:
            p = feats / c / ("%s.day.bin" % f)
            h = read_bin(p) if p.exists() else None
            if h is not None:
                bins[f] = (p, h[0], h[1])
        if not bins:
            n_skip += 1
            continue
        p0, first, v0 = next(iter(bins.values()))
        n = v0.size
        d = first + n - base_len
        if d == 0:
            n_ok += 1
            continue

        if d > 0:                                    # ---- 超长：前移 d 个槽 ✓ ----
            drop = min(d, first)
            new_first = first - drop
            # ★ 安全闸：末值必须等于"缓存里 10/08 的复权价"（= 证明值只是整体后移 ✓，不是内容坏了 ✗）
            # ⚠ 口径必须与工具写盘一致：bin 的 close = raw × **我们的 f(d)** ✗ ≠ 缓存的 adjclose
            #   （raw × adj_factor ✓）—— 两者比值 = adj(锚)/f(锚) 常数（sh600000 ≈ 25.57 ✓），
            #   拿它们直接比会全场误判 ✗（v1.20.92 第一版就踩了 ✓）。正确期望值 = 缓存原始收盘 ×
            #   该股 **factor bin 末值** ✓（= f(10/08) ✓，与 002487 手工验算一致 ✓）。
            d08 = cal[base_len - 1]
            if d08 not in day_cache:
                day_cache[d08] = read_day(d08)
            _r = day_cache[d08][0].get(c)
            raw_c = float(_r["close"]) if (_r and _r.get("close") is not None) else float("nan")
            fac = float(bins["factor"][2][-1]) if "factor" in bins else float("nan")
            exp = raw_c * fac
            got = float(v0[-1])
            if np.isfinite(got) and np.isfinite(exp) and abs(exp) > 1e-6:
                if abs(got - exp) / abs(exp) < 5e-3:
                    verify_ok += 1
                else:
                    verify_bad.append((c, round(got, 4), round(exp, 4)))
                    n_skip += 1                      # 校验不过 ⇒ **不写**（交人工 ✓）
                    continue
            if not args.apply:
                n_over += 1
                if n_over <= 3:
                    print("  [超长] %-9s first %d→%d 丢前 %d 个值 | 末值 %.4f vs 缓存复权 %.4f ✓"
                          % (c, first, new_first, drop, got, exp))
            else:
                for f, (p, s, v) in bins.items():
                    write_bin(p, new_first, v[drop:])
                n_write += 1
                n_over += 1
            continue

        # ---- 偏短：补齐 calendar[first+n : base_len] ✓ ----
        need = -d
        if need > args.cap:
            n_skip += 1
            if len(skip_list) < 12:
                skip_list.append((c, need, cal[max(0, first + n - 1)]))
            continue
        miss = [cal[i] for i in range(first + n, base_len)]
        if miss and miss[0] < cache_days[0]:
            # ⚠ 缺失区间**从缓存覆盖之前**就开始了 ⇒ 我们无法知道这些天它有没有真实数据 ✗
            #   ⇒ 一律不补（补 NaN 等于悄悄抹掉真实行情 ✗✗）⇒ 交人工 ✓
            n_skip += 1
            if len(skip_list) < 12:
                skip_list.append((c, need, cal[max(0, first + n - 1)]))
            continue
        f_last = float(bins["factor"][2][-1]) if "factor" in bins else float("nan")
        anchor = cal[first + n - 1]
        adj_c = adj_of(anchor).get(c)
        if not np.isfinite(f_last) or not adj_c:
            n_skip += 1
            continue
        scale = f_last / float(adj_c)                # f(锚)/adj(锚) ⇒ f(d)=scale×adj(d) ✓
        new_vals = {f: np.full(len(miss), np.nan) for f in FIELDS if f in bins}
        for i, dd in enumerate(miss):
            if dd not in day_cache:
                if not (CACHE / (dd.replace("-", "") + ".json")).exists():
                    continue                         # 缓存没这一天 ⇒ 留 NaN ✓（不猜 ✗）
                day_cache[dd] = read_day(dd)
            daily, adj = day_cache[dd]
            r, ad = daily.get(c), adj.get(c)
            if r is None or ad is None:
                continue                             # 停牌/无行 ⇒ NaN ✓
            fd = scale * ad
            for f in PRICE_FIELDS:
                if f in new_vals and r.get(f) is not None:
                    new_vals[f][i] = float(r[f]) * fd
            if r.get("vol") and "volume" in new_vals:
                new_vals["volume"][i] = float(r["vol"]) / fd
                new_vals["vwap"][i] = (float(r["amount"]) * 1000.0
                                       / (float(r["vol"]) * 100.0)) * fd
            if r.get("amount") is not None and "amount" in new_vals:
                new_vals["amount"][i] = float(r["amount"])
            if r.get("pct_chg") is not None and "change" in new_vals:
                new_vals["change"][i] = float(r["pct_chg"]) / 100.0
            if "factor" in new_vals:
                new_vals["factor"][i] = fd
            if r.get("close") is not None and "adjclose" in new_vals:
                new_vals["adjclose"][i] = float(r["close"]) * ad
        if not args.apply:
            n_under += 1
            if n_under <= 3:
                print("  [偏短] %-9s n %d→%d 补 %d 天（%s ~ %s）| 锚 %s f(锚)=%.6f"
                      % (c, n, n + len(miss), len(miss), miss[0], miss[-1], anchor, f_last))
        else:
            for f, (p, s, v) in bins.items():
                if f in new_vals:
                    write_bin(p, s, np.concatenate([v, new_vals[f].astype(np.float64)]))
            n_write += 1
            n_under += 1

    print("\n汇总：超长 %d / 偏短 %d / 本来就对齐 %d / 人工（缺口过大或无字段）%d"
          % (n_over, n_under, n_ok, n_skip))
    if skip_list:
        print("需人工（缺口超出缓存，样例 ≤12）：")
        for c, need, last in skip_list:
            print("   %-9s 缺 %d 天，末日期约 %s" % (c, need, last))
    print("写入：%s" % ("已完成 %d 只 × 各字段 ✓" % n_write if args.apply
                       else "dry-run（加 --apply 才写盘 ✓）"))
    if verify_ok or verify_bad:
        print("末值校验：通过 %d、不匹配 %d %s" % (verify_ok, len(verify_bad), verify_bad[:3]))


def _fmt(x):
    try:
        return "%.4f" % float(x),
    except Exception:                                                    # noqa: BLE001
        return (str(x),)


def _cached_adj_close(code: str, day: str, day_cache, adj_of):
    """该股在 `day` 的复权价（缓存原始收盘 × adj_factor ✓），用于末值校验 ✓。"""
    if day not in day_cache:
        day_cache[day] = read_day(day)
    daily, adj = day_cache[day]
    r = daily.get(code)
    a = adj.get(code)
    if r is None or a is None or r.get("close") is None:
        return float("nan")
    return float(r["close"]) * float(a)


if __name__ == "__main__":
    main()
