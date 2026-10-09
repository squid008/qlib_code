# -*- coding: utf-8 -*-
"""三源重建行情 bin（用户 2026-10-09，v1.20.94）：把「与日历错位 / 早段被丢」的股票整套修回来。

## 为什么要三源重建（背景）
`dump_tushare_daily.py` 的缓存只覆盖最近 29 个交易日（8/24~10/9），而 2026-10-09 的体检发现
**约 1385 只**股票的 bin 与日历对不上：1365 只「缺口更早」（缓存覆盖不到 ⇒ `first+n < 日历长`），
20 只字段缺失/无 market_cap。它们的**早段历史**要么被写坏、要么根本没有值 ✗，缓存补不了 ✗。

## 三源与分工（2026-10-09 全部逐位验证过 ✓）
| 源 | 位置 | 作用 | 验证结论 |
|---|---|---|---|
| **①主源 raw** | `E:\\quant\\trader_code\\data\\market\\daily\\<CODE>.{XSHE,XSHG,BJSE}.h5` 的 `raw` 组 | **未复权真值** | 与 bundle 分钟聚合**逐位**一致（抽样 ✓）；含北交所 352 只 ✓ |
| **②因子源** | `data/cn_data2/features/<code>/factor.day.bin` | 复权因子 `f(d)` | `f / adj_tushare` = **常数**（002487/600519/300073 全历史 ✓，极差 ~1e-7）⇒ 干净 ✓ |
| **③交叉核对** | `data/cn_data2`、`E:\\rq\\bundle\\h5\\equities`、`data/tushare_cache/daily` | 独立验算 | 按**日期**比对、相对容差默认 0.5% ✓ |

## ⚠ 这次挖出来的两个「静默数据错误」（本工具修的就是它们）
1. **现 `cn_data` 的 `factor` bin 不可信**：它与 tushare `adj_factor` **不成比例** ——
   002487 的 `factor/adj` = 0.021008（2010）→ **0.031707**（2012-03）→ 0.021009（2026），
   而 tushare 的 `adj` 直到 2012-11 才从 1.0 跳到 1.509 ✗ ⇒ 用 `raw = close / factor` 反解
   未复权价时，早段能差 **13%~30%** ✗。所以本工具**不用现 factor** ✗，改用 cn_data2 的（∝ adj ✓）。
2. **现 `cn_data` 的早段 close/volume/amount 与主源 raw 不成比例**（不是复权路径差异 ✗）：
   002487 前 6 天 `close/raw` = 0.018603 / 0.018299 / 0.019374 / 0.020670 / 0.020640 / 0.020483 ——
   复权因子只会在除权日跳变、绝不可能逐日摆动 ✗ ⇒ 早段是**坏数据**，必须用主源重算 ✓。

## 口径（★ 全部经主源逐位反解验证 ✓，与 `dump_tushare_daily.py` 的表逐项一致）
| 字段 | 公式 |
|---|---|
| close/open/high/low | `raw × f(d)` |
| volume | `(raw_volume_股 ÷ 100) ÷ f(d)`（主源量单位是**股** ⇒ 先转手 ✓；tushare 的 vol 本就是手 ✓） |
| amount | `raw_amount_元 ÷ 1000`（= **千元** ✓；主源最近 91 天为 NaN ⇒ 用 tushare 缓存补 ✓） |
| vwap | `(raw_amount_元 ÷ raw_volume_股) × f(d)` |
| factor | `f(d)` |
| adjclose | `raw_close × adj(d)`（与 `dump_tushare_daily` 同口径 ✓） |
| change | 缓存 `pct_chg ÷ 100`；没缓存的日子用 `adjclose(d) / adjclose(前一交易日) − 1` ✓ |

`f(d)` 取法（**默认零 API 调用** ✓）：
- `d ≤ cn_data2 末日（2026-09-04）` ⇒ 直接取 `cn_data2/factor.day.bin`（按 **cn_data2 自己的日历**
  定位 ✓，绝不用本项目索引混用 ✗）；
- 更晚 ⇒ `f(d) = f(锚) × adj(d) ÷ adj(锚)`，锚 = 该股在 cn_data2 段最后一格有 adj 的日期 ✓
  （`adj` 取自 `data/tushare_cache/daily/*.json` ✓ 已缓存 8/24~10/9 ✓）。

## 用法
    python tools/rebuild_daily_from_sources.py                  # dry-run：目标清单 + 三源差异清单
    python tools/rebuild_daily_from_sources.py --limit 20 -v    # 冒烟（逐只看明细）
    python tools/rebuild_daily_from_sources.py --codes sz002487,sh600519
    python tools/rebuild_daily_from_sources.py --apply          # 写盘（默认先备份到 ai_test/ ✓）
    python tools/rebuild_daily_from_sources.py --bundle-sample 0   # 关掉 bundle 抽查（更快 ✓）
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import h5py
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar, write_bin                        # noqa: E402
from dump_tushare_daily import EXTRA_FIELDS, PRICE_FIELDS, read_day      # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIELDS = PRICE_FIELDS + EXTRA_FIELDS                                     # 10 个行情字段 ✓
MAIN_ROOT = Path(os.environ.get("MARKET_DAILY_ROOT", r"E:\quant\trader_code\data\market\daily"))
BUNDLE_ROOT = Path(os.environ.get("BUNDLE_EQUITIES_ROOT", r"E:\rq\bundle\h5\equities"))
CN2 = ROOT / "data" / "cn_data2"
TS_CACHE = ROOT / "data" / "tushare_cache" / "daily"
RQ_SUFFIX = {"sh": "XSHG", "sz": "XSHE", "bj": "BJSE"}                   # ⚠ 北交所是 BJSE，不是 XBSE ✗


# ---------------------------------------------------------------- ① 主源 raw
def main_source_path(code: str) -> Optional[Path]:
    """qlib 代码 → 主源 h5（sz002487 → 002487.XSHE.h5 ✓；bj430047 → 430047.BJSE.h5 ✓）。"""
    suf = RQ_SUFFIX.get(code[:2])
    if not suf or not code[2:].isdigit():
        return None
    p = MAIN_ROOT / ("%s.%s.h5" % (code[2:], suf))
    return p if p.exists() else None


def read_main_raw(code: str) -> Optional[Dict[str, dict]]:
    """读主源 `raw` 组 ⇒ {日期: {open,high,low,close,volume(股),amount(元)}} ✓。"""
    p = main_source_path(code)
    if p is None:
        return None
    with h5py.File(str(p), "r") as f:
        if "raw" not in f:
            return None
        g = f["raw"]
        dt = g["date"][:]
        cols = {k: (g[k][:] if k in g else np.full(dt.size, np.nan))
                for k in ("open", "high", "low", "close", "volume", "amount")}
    out: Dict[str, dict] = {}
    for i, d in enumerate(dt):
        s = str(int(d))
        out["%s-%s-%s" % (s[:4], s[4:6], s[6:8])] = {k: float(cols[k][i]) for k in cols}
    return out or None


# ---------------------------------------------------------------- ② 因子源
class FactorSource:
    """`f(d)`：cn_data2 的 factor（∝ tushare adj ✓）+ 尾部用已缓存的 adj 延伸 ✓。"""

    def __init__(self, cn2: Path, ts_cache: Path):
        self.cal2 = pd.read_csv(cn2 / "calendars" / "day.txt", header=None)[0].astype(str).tolist()
        self.last2 = self.cal2[-1]                                       # 2026-09-04 ✓
        self.feats = cn2 / "features"
        self.cache = ts_cache
        # ★ 只把**缓存里真有的那几天**（8/24~10/9 ✓）一次读进来，其余日期一律 None ✓
        #   踩过的坑（2026-10-09）：`adj_of` 原来对**每个历史交易日**都 `Path.exists()` 一次
        #   ⇒ 每只 ~8000 次系统调用 ✗✗（Defender 下 ~3 秒/只）+ 把 None 全塞进字典
        #   ⇒ 内存 1GB+、CPU 只有 15%（时间全耗在等 I/O）✗。现在改成**纯字典查** ✓。
        self._have: Dict[str, Dict[str, float]] = {}
        for p in sorted(self.cache.glob("*.json")):
            if len(p.stem) == 8 and p.stem.isdigit():
                day = "%s-%s-%s" % (p.stem[:4], p.stem[4:6], p.stem[6:8])
                try:
                    self._have[day] = read_day(day)[1]
                except Exception:                                    # noqa: BLE001
                    continue
        self._asof: Dict[str, Optional[Tuple[str, float]]] = {}

    def adj_of(self, day: str) -> Optional[Dict[str, float]]:
        """某交易日的 adj_factor ⇒ **只在预读窗口内**返回（否则 None ✓，绝不逐日碰文件系统 ✗）。"""
        return self._have.get(day)

    def asof(self, code: str) -> Optional[Tuple[str, float]]:
        """该股在 cn_data2 段**最后一格**有 adj 的 (日期, adj) ⇒ 延伸的锚 ✓。"""
        if code not in self._asof:
            res = None
            for j in range(len(self.cal2) - 1, max(-1, len(self.cal2) - 60), -1):
                day = self.cal2[j]
                adj = self.adj_of(day)
                if adj and adj.get(code):
                    res = (day, float(adj[code]))
                    break
            self._asof[code] = res
        return self._asof[code]

    def series(self, code: str) -> Optional[Dict[str, float]]:
        """`f(d)` ⇒ {日期: f}（cn_data2 段按 **cn_data2 日历**定位 ✓ + 尾部按 adj 延伸 ✓）。

        ⚠ 不缓存结果（每次现算 ✓）：读一只 factor bin 只要 0.1ms ✓，而缓存 1385 只的
          4000 项字典会吃掉 GB 级内存 ✗（2026-10-09 实测 1GB 就是这么来的 ✓）。
        """
        p = self.feats / code / "factor.day.bin"
        out: Dict[str, float] = {}
        if p.exists():
            a = np.fromfile(str(p), dtype="<f4")
            if a.size >= 2:
                first, vals = int(a[0]), a[1:]
                for i, v in enumerate(vals):
                    j = first + i
                    if 0 <= j < len(self.cal2) and np.isfinite(v) and v > 0:
                        out[self.cal2[j]] = float(v)
        if out:
            anch = self.asof(code)
            if anch is not None:
                a_day, a_adj = anch
                f_a = out.get(a_day)
                if f_a and a_adj > 0:
                    for day in self._tail_days():
                        if day in out:
                            continue
                        adj = self.adj_of(day)
                        if adj and adj.get(code):
                            out[day] = f_a * float(adj[code]) / a_adj
        return out or None

    def _tail_days(self) -> List[str]:
        """> cn_data2 末日、且缓存里有的交易日（升序 ✓；直接用预读窗口 ✓ 不再 glob ✗）。"""
        return sorted(d for d in self._have if d > self.last2)


# ---------------------------------------------------------------- 组装
def build_series(cal: List[str], cal_pos: Dict[str, int], code: str, raw: Dict[str, dict],
                 fmap: Dict[str, float], adj_of, cache_day) -> Dict[str, object]:
    """按日历逐日组装 10 个字段（缺数据 ⇒ NaN ✓，绝不猜 ✗）。返回 {first, vals, days} ✓。"""
    days = sorted(d for d in raw if d in cal_pos)
    if not days:
        return {}
    first = cal_pos[days[0]]
    n = len(cal) - first
    out = {f: np.full(n, np.nan) for f in FIELDS}
    prev_ac = np.nan
    for d in days:
        i = cal_pos[d] - first
        r = raw[d]
        fv = fmap.get(d)
        if fv is None or not np.isfinite(fv) or fv <= 0:
            continue                                                     # 无因子 ⇒ 该日留 NaN ✗
        c, v, amt = r["close"], r["volume"], r["amount"]
        for k in ("open", "high", "low", "close"):
            if np.isfinite(r[k]):
                out[k][i] = r[k] * fv
        if np.isfinite(v) and v > 0:
            out["volume"][i] = (v / 100.0) / fv                          # 股 → 手 → ÷f ✓
            if np.isfinite(amt):
                out["vwap"][i] = (amt / v) * fv                          # 未复权 vwap × f ✓
        daily = cache_day(d)[0] if cache_day else {}
        crow = (daily or {}).get(code)
        if np.isfinite(amt):
            out["amount"][i] = amt / 1000.0                              # 元 → 千元 ✓
        elif crow and crow.get("amount") is not None:
            out["amount"][i] = float(crow["amount"])                     # 缓存本就是千元 ✓
        out["factor"][i] = fv
        adj = (adj_of(d) or {}).get(code) if adj_of else None
        if adj and np.isfinite(c):
            out["adjclose"][i] = c * float(adj)                          # raw × adj ✓
        if crow and crow.get("pct_chg") is not None:
            out["change"][i] = float(crow["pct_chg"]) / 100.0
        else:
            ac = out["adjclose"][i]
            if np.isfinite(ac) and np.isfinite(prev_ac) and prev_ac != 0:
                out["change"][i] = ac / prev_ac - 1.0                    # 复权口径日收益 ✓
        if np.isfinite(out["adjclose"][i]):
            prev_ac = out["adjclose"][i]
    return {"first": first, "vals": out, "days": days}


def read_cur_bin(qlib: Path, code: str, field: str) -> Optional[Tuple[int, np.ndarray]]:
    p = qlib / "features" / code / ("%s.day.bin" % field)
    if not p.exists():
        return None
    a = np.fromfile(str(p), dtype="<f4")
    if a.size < 2:
        return None
    return int(a[0]), a[1:].astype(np.float64)


def overlap_dev(cur: Tuple[int, np.ndarray], new: dict, field: str) -> Tuple[float, float, int]:
    """重叠段（两边都有值）的相对偏差 ⇒ (中位, 最大, 天数) ✓。

    ⚠ 这是**索引对齐**比较（同一下标 ⇒ 对"已对齐/超长"的票 = 同一天 ✓；对"缺口/错位"的票
      **下标对应的日期不同** ✗ ⇒ 只有"超长"组的这一列才有判断意义 ✓，别的组只能当
      "两边不一样"的指示灯 ✗）。**跨源比较请认准 `vs_cn_data2_*`（按日期对齐 ✓）**。
    """
    cf, cv = cur
    nf, nv = new["first"], new["vals"][field]
    lo = max(cf, nf)
    hi = min(cf + cv.size, nf + nv.size)
    if hi <= lo:
        return float("nan"), float("nan"), 0
    a = cv[lo - cf:hi - cf]
    b = nv[lo - nf:hi - nf]
    m = np.isfinite(a) & np.isfinite(b) & (np.abs(a) > 1e-9)
    if not m.any():
        return float("nan"), float("nan"), 0
    rel = np.abs(a[m] - b[m]) / np.abs(a[m])
    return float(np.median(rel)), float(rel.max()), int(m.sum())


def bundle_check(code: str, new: dict, cal_pos: Dict[str, int], sample: int) -> Tuple[int, int, float]:
    """③交叉核对：bundle 分钟数据按日聚合（o/h/l/c/volume ✓）与重建值比 ⇒ (天数, 不一致, 最大相对差) ✓。

    ⚠ 只读被抽到的那些天的行（`data[line:end]` ✓）—— 整份 26MB 全读进内存对 1385 只太慢 ✗。
    """
    if sample <= 0:
        return 0, 0, float("nan")
    suf = RQ_SUFFIX.get(code[:2])
    p = BUNDLE_ROOT / ("%s.%s.h5" % (code[2:], suf)) if suf else None
    if p is None or not p.exists():
        return 0, 0, float("nan")
    days = new["days"]
    if len(days) > sample:
        step = max(1, len(days) // sample)
        pick = days[::step][:sample]
    else:
        pick = days
    n_ok = n_bad = 0
    worst = 0.0
    with h5py.File(str(p), "r") as f:
        idx = f["index"][:]
        dts = idx["date"].astype(np.int64)
        lines = idx["line_no"].astype(np.int64)
        dmap = {int(d): k for k, d in enumerate(dts)}
        for d in pick:
            k = dmap.get(int(d.replace("-", "")))
            if k is None:
                continue
            end = int(lines[k + 1]) if k + 1 < len(lines) else int(idx.size and lines[-1])
            seg = f["data"][int(lines[k]):end]
            if not len(seg):
                continue
            raw = {"open": float(seg["open"][0]), "high": float(seg["high"].max()),
                   "low": float(seg["low"].min()), "close": float(seg["close"][-1]),
                   "volume": float(seg["volume"].sum())}
            i = cal_pos[d] - new["first"]
            fv = new["vals"]["factor"][i]
            if not np.isfinite(fv) or fv <= 0:
                continue
            ok = True
            for kk in ("open", "high", "low", "close"):
                got = new["vals"][kk][i]
                if not np.isfinite(got):
                    ok = False
                    break
                if abs(got - raw[kk] * fv) > max(1e-6, abs(raw[kk] * fv) * 1e-5):
                    ok = False
                    worst = max(worst, abs(got - raw[kk] * fv) / max(abs(raw[kk] * fv), 1e-9))
            gv = new["vals"]["volume"][i]
            if np.isfinite(gv) and np.isfinite(raw["volume"]):
                exp = (raw["volume"] / 100.0) / fv
                if abs(gv - exp) > max(1e-6, abs(exp) * 1e-5):
                    ok = False
                    worst = max(worst, abs(gv - exp) / max(abs(exp), 1e-9))
            n_ok += int(ok)
            n_bad += int(not ok)
    return n_ok + n_bad, n_bad, worst


# ---------------------------------------------------------------- 目标清单
def classify(code: str, qlib: Path, cal_pos: Dict[str, int],
             raw: Optional[dict]) -> Tuple[str, str]:
    """判定是否要重建 ⇒ (类别, 原因)；类别 == '' 表示已健康 ✓。

    ⚠ `cal_pos` 必须**传进来**（不要在这里 `set(cal)` ✗）：踩过的坑（2026-10-09）——
      写成 `sorted(d for d in raw if d in set(cal))` 时，`set(cal)` 会在**生成器的每一轮**
      重建一次（6484 元素 × 每只 6000+ 行 ⇒ **1.3 秒/只** ✗✗，全量扫描 ~83 分钟 ✗）。
      改用外面建好一次的字典 ⇒ 3000 倍提速 ✓（cProfile 实测 ✓）。
    """
    n_cal = len(cal_pos)
    have = [f for f in FIELDS if (qlib / "features" / code / ("%s.day.bin" % f)).exists()]
    if not have:
        return "字段缺失", "无任何行情 bin"
    if raw is None:
        return "无主源", "主源 h5 缺失（重建不了 ✗）"
    h = read_cur_bin(qlib, code, "close")
    if h is None:
        return "字段缺失", "close bin 不可读"
    first, vals = h
    n = vals.size
    raw_days = sorted(d for d in raw if d in cal_pos)
    if not raw_days:
        return "无主源", "主源在日历内无行"
    raw_first = cal_pos[raw_days[0]]
    if first + n != n_cal:
        kind = "未对齐-超长" if first + n > n_cal else "未对齐-缺口"
        return kind, "first=%d n=%d ⇒ first+n=%d（日历 %d）" % (first, n, first + n, n_cal)
    if raw_first < first:
        return "早段被丢", "主源首日 %s(下标%d) 早于 bin 首日 下标%d" % (
            raw_days[0], raw_first, first)
    return "", "已对齐 ✓"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="三源重建行情 bin（默认 dry-run ✓）")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or str(ROOT / "data" / "cn_data"))
    ap.add_argument("--codes", default="", help="只处理这些（qlib 代码，逗号分隔 ✓）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="连健康的股票也一起处理（默认只修目标清单 ✓）")
    ap.add_argument("--apply", action="store_true", help="真的写盘（默认 dry-run ✓）")
    ap.add_argument("--no-backup", action="store_true", help="--apply 时不备份（危险 ✗）")
    ap.add_argument("--tol", type=float, default=0.005, help="判定「差异超标」的相对容差（默认 0.5%% ✓）")
    ap.add_argument("--bundle-sample", type=int, default=6, help="每只用 bundle 分钟数据抽查几天（0=关 ✓）")
    ap.add_argument("--report", default="", help="差异清单 CSV 落盘路径（默认 ai_test/ 自动命名 ✓）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    cal, _ = load_calendar(qlib)
    cal_pos = {d: i for i, d in enumerate(cal)}
    feats = qlib / "features"
    codes = sorted(d.name for d in feats.iterdir()
                   if d.is_dir() and d.name[:2] in ("sh", "sz", "bj") and d.name[2:].isdigit())
    if args.codes:
        want = {c.strip().lower() for c in args.codes.split(",") if c.strip()}
        codes = [c for c in codes if c in want]
    print("日历 %d 天（%s ~ %s）| 候选 %d 只 | 主源 %s" % (len(cal), cal[0], cal[-1], len(codes), MAIN_ROOT),
          flush=True)
    if not MAIN_ROOT.exists():
        raise SystemExit("主源目录不存在：%s（可用 MARKET_DAILY_ROOT 覆盖 ✓）" % MAIN_ROOT)

    fs = FactorSource(CN2, TS_CACHE)
    print("因子源 cn_data2：日历 %d 天（~%s）| 尾部延伸用 tushare 缓存 %d 天"
          % (len(fs.cal2), fs.last2, len(fs._tail_days())), flush=True)

    t0 = time.time()
    targets: List[Tuple[str, str, str, dict, Dict[str, float]]] = []
    stat: collections.Counter = collections.Counter()
    for i, code in enumerate(codes, 1):
        raw = read_main_raw(code)
        kind, why = classify(code, qlib, cal_pos, raw)
        stat[kind or "健康"] += 1
        if kind or args.all:
            if raw is None:
                continue
            fmap = fs.series(code)
            if not fmap:
                stat["无因子源"] += 1
                if args.verbose:
                    print("  ✗ %-9s 无因子源（cn_data2 factor 缺失）⇒ 跳过 ✗" % code, flush=True)
                continue
            targets.append((code, kind or "健康", why, raw, fmap))
        if i % 200 == 0:
            # ⚠ 心跳里带上"当前代码"（踩过 ✓）：万一某个主源 h5 把 `h5py.File` 拖住/卡住，
            #   日志能直接告诉你卡在哪只 ✗（原来每 500 只一行、还在卡住时静默几十分钟 ✗）
            print("  扫描 %d/%d（当前 %s）⇒ 目标 %d 只  %.0fs"
                  % (i, len(codes), code, len(targets), time.time() - t0), flush=True)
    print("分类：%s" % dict(stat), flush=True)
    print("⇒ 待处理 %d 只" % len(targets), flush=True)
    if args.limit:
        targets = targets[:args.limit]
        print("（--limit %d ⇒ 只处理前 %d 只 ✓）" % (args.limit, len(targets)), flush=True)

    rows: List[dict] = []
    n_add = 0
    n_bad = 0
    n_written = 0
    bundle_cache_days: Dict[str, Tuple[dict, dict]] = {}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = ROOT / "ai_test" / ("backup_rebuild_%s" % stamp)

    def cache_day(d: str):
        if d not in bundle_cache_days:
            p = TS_CACHE / (d.replace("-", "") + ".json")
            bundle_cache_days[d] = read_day(d) if p.exists() else ({}, {})
        return bundle_cache_days[d]

    for k, (code, kind, why, raw, fmap) in enumerate(targets, 1):
        new = build_series(cal, cal_pos, code, raw, fmap, fs.adj_of, cache_day)
        if not new:
            print("  ✗ %-9s 重建为空 ⇒ 跳过 ✗" % code, flush=True)
            continue
        nv = new["vals"]
        new_valid = int(np.isfinite(nv["close"]).sum())
        cur = {f: read_cur_bin(qlib, code, f) for f in FIELDS}
        cur_n = int(np.isfinite(cur["close"][1]).sum()) if cur.get("close") else 0
        add_days = max(0, new_valid - cur_n)
        n_add += add_days
        devs = {f: overlap_dev(cur[f], new, f) for f in FIELDS if cur.get(f) is not None}
        worst = max((devs[f][1] for f in devs if np.isfinite(devs[f][1])), default=float("nan"))
        bad = bool(np.isfinite(worst) and worst > args.tol)
        n_bad += int(bad)
        # 交叉核对：cn_data2 的 close / factor（同日 ✓）
        c2_max = float("nan")
        p2 = CN2 / "features" / code / "close.day.bin"
        if p2.exists():
            a2 = np.fromfile(str(p2), dtype="<f4")
            rel = []
            for j in range(1, a2.size):
                d = fs.cal2[int(a2[0]) + j - 1] if int(a2[0]) + j - 1 < len(fs.cal2) else None
                if d is None or d not in cal_pos:
                    continue
                i = cal_pos[d] - new["first"]
                b = nv["close"][i] if 0 <= i < nv["close"].size else np.nan
                v = float(a2[j])
                if np.isfinite(b) and abs(v) > 1e-9:
                    rel.append(abs(b - v) / abs(v))
            if rel:
                c2_max = float(np.max(rel))
        b_n, b_bad, b_worst = bundle_check(code, new, cal_pos, args.bundle_sample)
        rows.append({
            "code": code, "kind": kind, "why": why,
            "cur_n": cur_n, "new_n": new_valid, "add_days": add_days,
            "overlap_n": devs.get("close", (0, 0, 0))[2],
            # ⚠ 下面的 dev_index_* 是**索引对齐**比较 ⇒ 只有「已对齐/超长」组有意义 ✓；
            #   跨源判断看 vs_cn_data2_max_close（按日期对齐 ✓）
            "dev_index_med_close": devs.get("close", (float("nan"),))[0],
            "dev_index_max_close": devs.get("close", (float("nan"), float("nan")))[1],
            "dev_index_max_volume": devs.get("volume", (float("nan"), float("nan")))[1],
            "dev_index_max_amount": devs.get("amount", (float("nan"), float("nan")))[1],
            "dev_index_max_factor": devs.get("factor", (float("nan"), float("nan")))[1],
            "dev_index_max_adjclose": devs.get("adjclose", (float("nan"), float("nan")))[1],
            "vs_cn_data2_max_close": c2_max,
            "bundle_n": b_n, "bundle_bad": b_bad, "bundle_worst": b_worst,
            "diff_over_tol": int(bad),
        })
        if k % 25 == 0:
            print("  处理 %d/%d（当前 %s）%.0fs" % (k, len(targets), code, time.time() - t0), flush=True)
        if args.verbose or bad or k <= 5:
            print("  %-9s %-11s 现 %d 天 → 重建 %d 天（可补回 %d）| 索引对齐重叠 %d 天 close 中位 %.2e/最大 %.2e "
                  "| 按日期对齐 vs cn_data2 %.2e %s"
                  % (code, kind, cur_n, new_valid, add_days, rows[-1]["overlap_n"],
                     rows[-1]["dev_index_med_close"], rows[-1]["dev_index_max_close"],
                     c2_max, "⚠超容差" if bad else ""), flush=True)

        if not args.apply:
            continue
        # ---------------- 写盘（整段重写 ✓；先备份 ✓）----------------
        for f in FIELDS:
            p = feats / code / ("%s.day.bin" % f)
            arr = nv[f]
            fin = np.isfinite(arr)
            if p.exists() and not args.no_backup:
                (backup_root / code).mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(p), str(backup_root / code / p.name))
            if not fin.any():
                continue
            lo = int(np.where(fin)[0][0])
            hi = int(np.where(fin)[0][-1])
            first = new["first"] + lo
            write_bin(p, first, arr[lo:hi + 1])
            # ★ 硬规矩（见 tests/test_bin_index_invariants.py）：绝不写负 header / 越界 ✗
            if first < 0 or first + (hi - lo + 1) > len(cal):
                raise SystemExit("✗ %s/%s 写盘越界：first=%d n=%d 日历=%d" % (code, f, first, hi - lo + 1, len(cal)))
            n_written += 1

    # ---------------- 报告 ----------------
    rep = Path(args.report) if args.report else (
        ROOT / "ai_test" / ("rebuild_dryrun_%s.csv" % stamp))
    rep.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with open(rep, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    add_hist = collections.Counter()
    for r in rows:
        add_hist[min(r["add_days"] // 500 * 500, 5000)] += 1
    print("\n===== 汇总（%s）=====" % ("已写盘 ✓" if args.apply else "dry-run ✓"))
    print("处理 %d 只 | 三源/现值差异超容差（%.1f%%）%d 只 | 可补回行情天数合计 %d"
          % (len(rows), args.tol * 100, n_bad, n_add))
    print("可补回天数分布（下界 500 天分桶 ⇒ 只数）：%s"
          % dict(sorted(add_hist.items())))
    if rows:
        c2 = sorted((r for r in rows if np.isfinite(r["vs_cn_data2_max_close"])),
                    key=lambda r: -r["vs_cn_data2_max_close"])[:10]
        print("★ 与 cn_data2 按**日期**对齐差异最大的 10 只（跨源差异清单 ✓）：")
        for r in c2:
            print("   %-9s %-11s vs cn_data2 最大 %.3e | 索引对齐重叠 %s 天 中位 %.3e | 可补回 %s"
                  % (r["code"], r["kind"], r["vs_cn_data2_max_close"], r["overlap_n"],
                     r["dev_index_med_close"], r["add_days"]))
        ok = [r for r in rows if np.isfinite(r["vs_cn_data2_max_close"])
              and r["vs_cn_data2_max_close"] <= 1e-4]
        print("其中「与 cn_data2 逐位一致（≤1e-4）」的 %d/%d 只 ⇒ 这些就是**口径已确证**的 ✓"
              % (len(ok), len(rows)))
        worst_rows = sorted((r for r in rows if np.isfinite(r["dev_index_max_close"])),
                            key=lambda r: -r["dev_index_max_close"])[:5]
        print("索引对齐差异最大的 5 只（⚠ 对「未对齐」的票此列只表示「两边不一样」，不代表谁错 ✗）：")
        for r in worst_rows:
            print("   %-9s %-11s 重叠 %5d 天 最大 %.3e（中位 %.3e）"
                  % (r["code"], r["kind"], r["overlap_n"], r["dev_index_max_close"],
                     r["dev_index_med_close"]))
        b = [r for r in rows if r["bundle_n"]]
        if b:
            print("bundle 分钟聚合抽查：%d 只共 %d 天，不一致 %d 天（最大相对差 %.2e ✓）"
                  % (len(b), sum(r["bundle_n"] for r in b), sum(r["bundle_bad"] for r in b),
                     max(r["bundle_worst"] for r in b)))
    print("清单 CSV：%s" % rep)
    if args.apply:
        print("写盘 %d 个 bin | 备份：%s" % (n_written, backup_root))
        print("⚠ 行情更新后记得跑：python tools/build_preclose.py --overwrite ✓")
    else:
        print("dry-run：加 --apply 才写盘 ✓")
    print("用时 %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
