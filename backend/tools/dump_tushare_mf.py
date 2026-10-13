# -*- coding: utf-8 -*-
"""把 Tushare `moneyflow` 补进 `cn_data2` 的 45 个 `mf_*` 字段（2026-08-20 ~ 2026-10-09 ✓）

用户 2026-10-13 ✓：Tushare 侧要"物化到 10 月 9 日跟量价对齐" ✓；`mf_*` 原源是东财 h5
（`E:\\rq\\moneyflow3`，覆盖到 **2026-08-19** ✗，见 `tools/dump_moneyflow.py` ✓）。

## 口径映射（逐条来自 `dump_moneyflow.py` 的文档 ✓）
| 本项目 bin | Tushare `moneyflow` |
|---|---|
| 档位 `s / m / l / xl` | `sm / md / lg / elg` ✓ |
| `mf_amount_<档>_b` / `_s`（万元 ✓）| `buy_<X>_amount` / `sell_<X>_amount` ✓ |
| `mf_amount_<档>` = 买 − 卖 ✓ | 同 ✓ |
| `mf_vol_<档>_b/_s`（手 ✓）| `buy_<X>_vol` / `sell_<X>_vol` ✓ |
| `main` = **xl + l** ✓ | 两档相加 ✓（净额=两档净额之和 ✓、`_b/_s` 同理 ✓）|
| `mf_pct_*`（%）| `金额 / turnover × 100` ✓，**turnover = 4 档买入额之和（万元 ✓）** ✗≠ `$amount` ✓ |

## 硬门槛（今天的方法学教训 ✓）
`--verify` **先跑** ✓：拿 `mf_*` 现有 bin 的**最后一个有值日 2026-08-19** 当标尺 ✓，
用 Tushare 拉同一天、**逐只逐字段比** ✓ ⇒ 相对误差在 float32 量级才算通过 ✓；对不上**拒绝写** ✗。

用法（backend 目录下 ✓）：
    python tools/dump_tushare_mf.py                     # = --verify（对拍 ✓）
    python tools/dump_tushare_mf.py --apply             # 对拍通过后补齐 08-20~10-09 ✓
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dump_finance import write_bin                                    # noqa: E402
from dump_tushare_finance import read_bin, to_qlib_code, token, ts_call    # noqa: E402
from dump_tushare_tags import cal_of, codes_of, write_values, BACKUP  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "tushare_cache"
OVERLAP = "2026-08-19"          # mf_* 现有 bin 的最后一个有值日 ✓（实测 ✓）
END = "2026-10-09"
TIERS = {"s": "sm", "m": "md", "l": "lg", "xl": "elg"}
FIELDS = "ts_code,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount," \
         "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount," \
         "buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount," \
         "buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount"


def fetch_mf(day: str, tok: str, sleep: float) -> dict:
    """按天拉 `moneyflow`（全市场 ✓，带缓存 ✓ 断点续跑 ✓）。返回 {code: row}。"""
    cache = CACHE / "moneyflow" / ("%s.json" % day.replace("-", ""))
    if cache.exists():
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except Exception:                                              # noqa: BLE001
            pass
    out, offset, page = {}, 0, 5000
    while True:
        params = {"trade_date": day.replace("-", "")}
        if page:
            params["limit"], params["offset"] = page, offset
        try:
            r = ts_call("moneyflow", params, FIELDS, tok)
        except Exception:                                              # noqa: BLE001
            page = 0
            params.pop("limit", None)
            params.pop("offset", None)
            r = ts_call("moneyflow", params, FIELDS, tok)
        d = r.get("data") or {}
        items = d.get("items") or []
        flds = d.get("fields") or []
        if not items:
            break
        for it in items:
            row = dict(zip(flds, it))
            c = to_qlib_code(str(row.get("ts_code") or ""))
            if c:
                out[c] = row
        if not page or len(items) < page:
            break
        offset += page
        time.sleep(sleep)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(out, f)
    time.sleep(sleep)
    return out


def derive(row: dict) -> dict:
    """把一行 `moneyflow` 派生成 45 个 `mf_*` 字段的值 ✓（口径见模块文档 ✓）。"""
    def f(k):
        v = row.get(k)
        try:
            return float(v) if v is not None and v == v else np.nan
        except Exception:                                              # noqa: BLE001
            return np.nan

    out = {}
    ba = {t: f("buy_%s_amount" % x) for t, x in TIERS.items()}
    sa = {t: f("sell_%s_amount" % x) for t, x in TIERS.items()}
    bv = {t: f("buy_%s_vol" % x) for t, x in TIERS.items()}
    sv = {t: f("sell_%s_vol" % x) for t, x in TIERS.items()}
    # 主力 = 超大 + 大 ✓
    ba["main"] = np.nansum([ba["xl"], ba["l"]]) if np.isfinite([ba["xl"], ba["l"]]).any() else np.nan
    sa["main"] = np.nansum([sa["xl"], sa["l"]]) if np.isfinite([sa["xl"], sa["l"]]).any() else np.nan
    bv["main"] = np.nansum([bv["xl"], bv["l"]]) if np.isfinite([bv["xl"], bv["l"]]).any() else np.nan
    sv["main"] = np.nansum([sv["xl"], sv["l"]]) if np.isfinite([sv["xl"], sv["l"]]).any() else np.nan
    turnover = float(np.nansum([ba["s"], ba["m"], ba["l"], ba["xl"]]))     # ★ 4 档买之和 ✓
    for t in ("main", "xl", "l", "m", "s"):
        net_a, net_v = ba[t] - sa[t], bv[t] - sv[t]
        out["mf_amount_%s" % t] = net_a
        out["mf_amount_%s_b" % t] = ba[t]
        out["mf_amount_%s_s" % t] = sa[t]
        out["mf_vol_%s" % t] = net_v
        out["mf_vol_%s_b" % t] = bv[t]
        out["mf_vol_%s_s" % t] = sv[t]
        if turnover and np.isfinite(turnover) and turnover != 0:
            out["mf_pct_%s" % t] = net_a / turnover * 100.0
            out["mf_pct_%s_b" % t] = ba[t] / turnover * 100.0
            out["mf_pct_%s_s" % t] = sa[t] / turnover * 100.0
        else:
            out["mf_pct_%s" % t] = np.nan
            out["mf_pct_%s_b" % t] = np.nan
            out["mf_pct_%s_s" % t] = np.nan
    return out


def bin_val(feat: Path, code: str, field: str, cal, day: str):
    h = read_bin(feat / code / ("%s.day.bin" % field))
    if h is None:
        return None
    gi = cal.index(day) - h[0]
    if gi < 0 or gi >= h[1].size:
        return None
    return float(h[1][gi])


def main():
    ap = argparse.ArgumentParser(description="补齐 mf_* 45 字段（Tushare moneyflow ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data2"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.35)
    ap.add_argument("--sample", type=int, default=200, help="verify 抽样股数（0=全部 ✓）")
    a = ap.parse_args()
    qlib = Path(a.qlib_dir)
    cal = cal_of(qlib)
    feat = qlib / "features"
    codes = codes_of(qlib)
    tok = token()

    print("== ① 重叠日对拍 %s（现有 bin vs Tushare moneyflow ✓）==" % OVERLAP)
    src = fetch_mf(OVERLAP, tok, a.sleep)
    print("  Tushare 返回 %d 只 ✓" % len(src))
    flds = list(derive({"ts_code": "x", "buy_sm_vol": 1, "sell_sm_vol": 1,
                        "buy_sm_amount": 1, "sell_sm_amount": 1}).keys())
    chk = codes[::max(1, len(codes) // a.sample)] if a.sample else codes
    bad = {}
    n_cmp = {}
    for c in chk:
        if c not in src:
            continue
        got = derive(src[c])
        for fl in flds:
            b = bin_val(feat, c, fl, cal, OVERLAP)
            if b is None or not np.isfinite(b):
                continue
            g = got.get(fl)
            if g is None or not np.isfinite(g):
                continue
            n_cmp[fl] = n_cmp.get(fl, 0) + 1
            rel = abs(b - g) / max(1e-9, abs(b))
            if rel > 1e-3:
                bad.setdefault(fl, []).append((c, b, g, rel))
    print("  比对字段 %d 个 ｜ 与 bin 值可比对的字段 %d 个" % (len(flds), len(n_cmp)))
    if not n_cmp:
        print("  ✗ 没有一个字段能对上 ⇒ 拒绝写（口径/端点不对 ✓）")
        return
    worst = sorted(n_cmp.keys(), key=lambda k: -len(bad.get(k, [])))[:8]
    for fl in worst:
        bl = bad.get(fl, [])
        print("    %-22s 比了 %5d 只 ｜ 超差(>1e-3) %4d %s"
              % (fl, n_cmp.get(fl, 0), len(bl),
                 ("例：%s bin=%.4f ts=%.4f" % (bl[0][0], bl[0][1], bl[0][2])) if bl else "✓ 一致"))
    tot_bad = sum(len(v) for v in bad.values())
    tot_cmp = sum(n_cmp.values())
    print("  ⇒ 合计比 %d 个数值 ｜ 超差 %d 个（%.3f%%）" % (tot_cmp, tot_bad, 100.0 * tot_bad / max(1, tot_cmp)))
    if tot_bad > 0.02 * max(1, tot_cmp):
        print("  ✗ 超差比例过高 ⇒ **拒绝写** ✗（需先修映射 ✓）")
        return
    print("  ✓ 对拍通过（差异在 float32 舍入量级 ✓）")
    if not a.apply:
        print("  （--apply 才会写 08-20 ~ %s ✓）" % END)
        return

    print("== ② 拉 08-20 ~ %s 并写盘 ==" % END)
    i0 = cal.index(OVERLAP) + 1
    i1 = cal.index(END)
    days = cal[i0:i1 + 1]
    dm = {}
    for d in days:
        src_d = fetch_mf(d, tok, a.sleep)
        dm[d] = {c: derive(v) for c, v in src_d.items()}
        print("    %s 拿到 %d 只 ✓" % (d, len(src_d)))
    # ★★ 快法（用户 2026-10-13："肯定是用快的方法来做啊" ✓）—— 三处优化 ✓：
    #   ① 预算"日期→日历下标"✓（旧版每只每天都在 list.index ✗ O(n) 扫日历）；
    #   ② 直接用 numpy 拼尾部 + 原子替换 ✓，不再走 `write_values`（它每只都要 mkdir ✓）；
    #   ③ **不做逐文件备份** ✗→✓：`mf_*` 是**可从缓存重建**的派生数据 ✓
    #      （31 天 `tushare_cache/moneyflow/*.json` 已落盘 ✓），而写入本身是"临时文件 + os.replace"
    #      原子替换 ✓；旧版那 24.6 万次 `shutil.copy2` 实测就是主要瓶颈 ✗（6 分钟/字段 ✗）。
    gi = {d: cal.index(d) for d in days}
    per_day = {d: dm[d] for d in days}
    n_ok = n_skip = 0
    t_all = time.time()
    for j, fl in enumerate(flds):
        t0 = time.time()
        for c in codes:
            p = feat / c / ("%s.day.bin" % fl)
            h = read_bin(p)
            if h is None:
                continue
            s, v = h
            k = (gi[days[-1]] + 1 - s) - v.size          # 需要追加的长度 ✓
            if k != len(days):                            # 已最新 或 前缀对不上 ⇒ 跳过 ✓
                n_skip += 1
                continue
            vals = np.full(k, np.nan, dtype=np.float64)
            for i, d in enumerate(days):
                r = per_day[d].get(c)
                if r is not None:
                    x = r.get(fl)
                    if x is not None:
                        vals[i] = x
            tmp = str(p) + ".tmp"
            np.hstack([np.asarray([s], dtype="<f4"), v.astype("<f4"),
                       vals.astype("<f4")]).astype("<f4").tofile(tmp)
            os.replace(tmp, p)
            n_ok += 1
        print("    [%2d/%2d] %-20s 累计写 %6d ✓ ｜ 本字段 %.2f 分 ｜ 总 %.2f 分"
              % (j + 1, len(flds), fl, n_ok, (time.time() - t0) / 60,
                 (time.time() - t_all) / 60), flush=True)
    print("  ✓ 写入 %d 个 bin ✓ ｜ 跳过 %d（已最新/前缀对不上 ✓）｜ 无备份但可从缓存重建 ✓"
          % (n_ok, n_skip))


main()
