# -*- coding: utf-8 -*-
"""给指数 bin **补尾部**（8/24~日历末）—— 数据取自 tushare `index_daily` ✓（用户 2026-10-10："先修指数"后的第 1 步）

背景：`cn_data2` 的 5 个指数（`sh000300/sh000905/sh000906/sh000852/sz399300` ✓）的行情只到
**2026-08-21** ✓（与米筐源一致 ✓），而 cn_data2 的日历到 **2026-10-09** ✗ ⇒ 基准曲线在 8/21 收尾。
本工具把 8/24~10/09 的 29 天接上去 ✓（口径先在**重叠段 8/17~8/21** 上逐位对拍 ✓，对不上就不写 ✗）。

## 口径（2026-10-10 在重叠段实测确认 ✓）
`f` = 该指数**当年**的 `factor`（常数 ✓，本工具**不改它** ✓）：
| 字段 | 公式 | 实测 |
|---|---|---|
| `close/open/high/low` | `raw × f` | 与 tushare 精确到 4 位 ✓ |
| `change` | `pct_chg / 100` | 6 位小数吻合 ✓ |
| `amount` | tushare `amount`（**千元** ✓） | 吻合 ✓ |
| `volume` | `vol(手) ÷ f` | 吻合（同股票口径 ✓，±0.05% 供应商差 ✓） |
| `vwap` | `(amount_千元×1000) ÷ (vol_手×100) × f` | 吻合 ✓ |
| `preclose/prehigh/prelow/preopen/prevwap` | 见 `_pre_rule()`（按重叠段实测的**现有惯例**照抄 ✓） | — |
| `adjclose` | 与 `close` 同（指数无分红 ✓） | — |
| `factor` / `chip_*` / `mkt_*` / `is_st` / `turn` | **不动** ✓ | — |

用法：python tools/append_index_daily.py [--qlib-dir ...] [--apply]   （默认 dry-run ✓）
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                        # noqa: E402
from dump_tushare_finance import guard_convention, read_bin, token, ts_call   # noqa: E402

IDX = {"sh000300": "000300.SH", "sh000905": "000905.SH", "sh000906": "000906.SH",
       "sh000852": "000852.SH", "sz399300": "399300.SZ"}
# 需要补的行情字段（其余字段不动 ✓）
FIELDS = ("close", "open", "high", "low", "volume", "amount", "vwap",
          "adjclose", "change", "preclose", "prehigh", "prelow", "preopen", "prevwap")
COLS = "ts_code,trade_date,close,open,high,low,pre_close,change,pct_chg,vol,amount"


def fetch(tsc: str, start: str, end: str, tok: str) -> dict:
    """→ {YYYY-MM-DD: row}（tushare index_daily ✓）。"""
    r = ts_call("index_daily", {"ts_code": tsc, "start_date": start, "end_date": end}, COLS, tok)
    d = (r or {}).get("data") or {}
    rows = [dict(zip(d.get("fields", []), it)) for it in (d.get("items") or [])]
    return {"%s-%s-%s" % (str(x["trade_date"])[:4], str(x["trade_date"])[4:6], str(x["trade_date"])[6:8]): x
            for x in rows}


def build(field: str, row: dict, f: float, prev_close_raw: float | None) -> float:
    """按上表口径算一个字段的当日值 ✓。"""
    def g(k):
        v = row.get(k)
        return float(v) if v is not None else float("nan")
    if field in ("close", "open", "high", "low"):
        return g({"close": "close", "open": "open", "high": "high", "low": "low"}[field]) * f
    if field == "change":
        return g("pct_chg") / 100.0
    if field == "amount":
        return g("amount")
    if field == "volume":
        return g("vol") / f if f else float("nan")
    if field == "vwap":
        vol_sh = g("vol") * 100.0
        amt_yuan = g("amount") * 1000.0
        return (amt_yuan / vol_sh) * f if vol_sh else float("nan")
    if field == "adjclose":
        # ⚠ 重叠段实测（2026-10-10 ✓）：`adjclose` 存的**不是**复权价 ✗ ——
        #   它 = 当日**未复权**收盘（与 `preclose` 同值 ✓，8/21 = 4618.9 ✓）。照抄惯例 ✓。
        return g("close")
    if field in ("preclose", "prehigh", "prelow", "preopen", "prevwap"):
        # 现有惯例（重叠段实测 ✓）：这 5 个"pre*"字段存的其实是**当日**的**未复权**值 ——
        # `preclose`(`=`adjclose)=当日原始收盘 ✓、`prehigh/prelow/preopen`=当日原始高/低/开 ✓、
        # `prevwap`=当日**未复权** vwap（= amount_元 ÷ vol_股，**不乘 f** ✗）✓。
        # 为保持同一套惯例、不制造断层 ⇒ 照抄 ✓（不改历史语义 ✗）。
        if field == "preclose":
            return g("close")
        if field == "prevwap":
            vol_sh = g("vol") * 100.0
            amt_yuan = g("amount") * 1000.0
            return amt_yuan / vol_sh if vol_sh else float("nan")
        return g({"prehigh": "high", "prelow": "low", "preopen": "open"}[field])
    raise KeyError(field)


def main():
    ap = argparse.ArgumentParser(description="给指数补尾部行情（tushare index_daily ✓；默认 dry-run ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data2"))
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    ap.add_argument("--check-days", type=int, default=5, help="重叠段对拍天数（默认 5 ✓）")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    guard_convention(qlib, False)          # ★ 只许写 tushare 口径数据集 ✓（cn_data 是米筐源 ✗）
    cal, _ = load_calendar(qlib)
    n_cal = len(cal)
    tok = token()
    print("目标 %s（日历 %d 天，末 %s）%s\n" % (qlib, n_cal, cal[-1], "APPLY ✓" if args.apply else "DRY-RUN ✓"))

    backup = ROOT / "ai_test" / ("backup_index_append_%s" % time.strftime("%Y%m%d-%H%M%S"))
    for code, tsc in IDX.items():
        h = read_bin(qlib / "features" / code / "close.day.bin")
        if h is None:
            print("  %-9s 无 close bin ⇒ 跳过" % code)
            continue
        first, cur = h
        last_pos = int(np.where(np.isfinite(cur))[0][-1])
        last_day = cal[first + last_pos]
        have_rows = first + cur.size - 1                    # 现有 bin 覆盖到日历第几位
        need = [cal[j] for j in range(have_rows + 1, n_cal)]
        # ⚠ 取数起点要**往前留出对拍段** ✗ —— 只从 last_day 取会把重叠段漏掉（上版就踩了 ✓）
        start_pos = max(0, have_rows - args.check_days - 5)
        rows = fetch(tsc, cal[start_pos].replace("-", ""), "20261009", tok)
        print("  %-9s 现有到 %s（first=%d, n=%d）⇒ 待补 %d 天（%s ~ %s）"
              % (code, last_day, first, cur.size, len(need), need[0] if need else "-", need[-1] if need else "-"))
        if not need:
            print("     已经到日历末 ✓ 无需补")
            continue
        f = float(np.median(np.fromfile(str(qlib / "features" / code / "factor.day.bin"), dtype="<f4")[1:]))
        # ---- 重叠段对拍闸门（对不上就不写 ✗）----
        # ⚠ 每个字段必须读**它自己**的 bin ✓（上一版统一读 close ⇒ 报"change bin=4.6998"这种假错 ✗）
        # 且**全部待写字段**都要对拍 ✓（`pre*`/`adjclose` 是我按现有惯例照抄的 ⇒ 必须验证 ✗）
        GATES = (("close", 0.5, False), ("open", 0.5, False), ("high", 0.5, False), ("low", 0.5, False),
                 ("change", 1e-4, False), ("adjclose", 0.5, False), ("preclose", 0.5, False),
                 ("prehigh", 0.5, False), ("prelow", 0.5, False), ("preopen", 0.5, False),
                 ("amount", 0.005, True), ("volume", 0.005, True),
                 ("vwap", 0.01, True), ("prevwap", 0.01, True))
        bins = {}
        for fld, _t, _r in GATES:
            hh = read_bin(qlib / "features" / code / ("%s.day.bin" % fld))
            bins[fld] = ({cal[hh[0] + i]: float(v) for i, v in enumerate(hh[1]) if 0 <= hh[0] + i < n_cal}
                         if hh else {})
        bad = 0
        for day in [cal[j] for j in range(max(0, have_rows - args.check_days + 1), have_rows + 1)]:
            row = rows.get(day)
            if not row:
                print("      重叠段 %s tushare 无数据 ✗" % day)
                bad += 1
                continue
            for fld, tol, rel in GATES:
                got = bins[fld].get(day)
                want = build(fld, row, f, None)
                if got is None:
                    print("      ✗ 重叠段 %s %s 现有 bin 无值（无法确认口径 ✗）" % (day, fld))
                    bad += 1
                elif rel:
                    if abs(got - want) / max(abs(want), 1e-12) > tol:
                        print("      ✗ 重叠段 %s %s bin=%.6g 重算=%.6g（相对 tol=%g）" % (day, fld, got, want, tol))
                        bad += 1
                elif abs(got - want) > tol:
                    print("      ✗ 重叠段 %s %s bin=%.6f 重算=%.6f（tol=%g）" % (day, fld, got, want, tol))
                    bad += 1
        if bad:
            print("     ⇒ 有 %d 处对不上 ⇒ **不写**（避免接出断层 ✗）" % bad)
            continue
        print("      重叠段对拍通过 ✓（factor=%.6g 常数 ✓）" % f)
        # ---- 生成待追加的值（按字段 ✓）----
        add = {fld: [] for fld in FIELDS}
        for day in need:
            row = rows.get(day)
            if not row:
                print("      ⚠ %s tushare 无数据 ⇒ 该字段留 NaN" % day)
                for fld in FIELDS:
                    add[fld].append(float("nan"))
                continue
            for fld in FIELDS:
                add[fld].append(build(fld, row, f, None))
        print("      待补示例 %s：close %.4f→%.4f | change %+.4f%%" %
              (need[-1], add["close"][0], add["close"][-1], add["change"][-1] * 100))
        if args.apply:
            backup.mkdir(parents=True, exist_ok=True)
            for fld in FIELDS:
                p = qlib / "features" / code / ("%s.day.bin" % fld)
                if not p.exists():
                    continue
                hh = read_bin(p)
                if hh is None:
                    continue
                f0, vals = hh
                if f0 != first:
                    print("      ⚠ %s 的 first=%d 与 close 的 %d 不一致 ⇒ 跳过该字段 ✗" % (fld, f0, first))
                    continue
                shutil.copy2(p, backup / ("%s_%s.day.bin" % (code, fld)))
                new = np.concatenate([vals, np.asarray(add[fld], dtype=np.float64)])
                assert f0 >= 0 and f0 + new.size <= n_cal, "越界 ✗"
                write_bin(p, int(f0), new)                   # 只**追加**、不改已有值 ✓
        # 追加后自检（dry-run 用内存算 ✓）
        n_after = cur.size + len(need)
        flag = "✓" if first + n_after == n_cal else "✗（first+n=%d ≠ 日历 %d）" % (first + n_after, n_cal)
        print("      追加后 n=%d，first+n=%d %s" % (n_after, first + n_after, flag))
    if args.apply:
        print("\n原文件已备份到 %s ✓" % backup)
    else:
        print("\n（dry-run：未写盘 ✓；加 --apply 生效 ✓）")


if __name__ == "__main__":
    main()
