# -*- coding: utf-8 -*-
"""补齐 `cn_data2` 的标签/派生字段到 **2026-10-09**（与量价对齐 ✓）—— 用户 2026-10-13 定 ✓

背景（`ai_test/plan_fill_1009.md` ✓）：Tushare 侧增量只扩了**行情类**字段 ✓
⇒ `turn` / `limit_up/down` / `is_st` / `chip_*` / `mf_*` 落后 ✗（实测 `ai_test/worklist_missing_1009.py` ✓）。
本工具负责其中**能用 Tushare 按天拉**的两组；`chip_*` 由 `materialize_chip.py` 本地重跑（**必须等 turn 补完** ✓）；
`mf_*`（45 字段）另用 `--stage mf`（需先做重叠日对拍 ✓）。

## 口径（全部按"**按天拉全市场**"✓，省调用 ✓）
| 组 | 字段 | Tushare 端点 | 口径 |
|---|---|---|---|
| `turn` | `turn` | `daily_basic.turnover_rate` | **百分数** ✓（`chip_store` 内 `/100` ✓ ⇒ 与之相容 ✓）|
| `limit` | `limit_up` / `limit_down` | `stk_limit`（up_limit/down_limit ✓）| **价格**（不是 0/1 ✗）✓ |
| `ist` | `is_st` | `stock_basic`/`namechange` 的 ST 名称 ✓ | **只补 2026-10-09 这一天** ✓（用户决定：不换口径 ✓）|

## 安全写法（沿用 `dump_tushare_daily.py` ✓）
- 默认 **dry-run** ✓（**不调 API** ✓：只算"哪些股/哪些天/几次调用" ✓）；`--apply` 才真拉真写 ✓；
- 只写**已有该 bin 的股票** ✓；`--stage turn --create-missing` 才给缺 bin 的**新建**（⚠ 排除北交所 `bj*` ✓，用户决定 ✓）；
- 追加/覆盖**尾部** ✓（前缀长度对不上就跳过并报告 ✓，绝不重排历史 ✓）；写前**备份** ✓；**临时文件原子替换** ✓；
- 拉数**节流** ✓（`--sleep` ✓）+ `ts_call(retry=3)` ✓；按天结果**落缓存** ✓（断点续跑 ✓）。

用法（在 backend 目录下 ✓）：
    python tools/dump_tushare_tags.py --stage turn                # dry-run（推荐先跑 ✓）
    python tools/dump_tushare_tags.py --stage turn --apply --create-missing
    python tools/dump_tushare_tags.py --stage limit --apply
    python tools/dump_tushare_tags.py --stage ist --apply
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
from dump_finance import write_bin                                   # noqa: E402
from dump_tushare_finance import read_bin, to_qlib_code, token, ts_call   # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "tushare_cache"
END = "2026-10-09"
BACKUP = "_backup_dump_tags_20261013"
BJ = ("bj",)


def cal_of(qlib: Path):
    with open(qlib / "calendars" / "day.txt", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def codes_of(qlib: Path, only_shsz: bool = False):
    feat = qlib / "features"
    out = [d.name for d in feat.iterdir() if d.is_dir() and not d.name.startswith("_")]
    if only_shsz:
        out = [c for c in out if not c.lower().startswith(BJ)]
    return sorted(out)


def fetch_day(api: str, day: str, fields: str, tok: str, sleep: float, key: str) -> dict:
    """按天拉全市场（带缓存 ✓、分页 ✓、节流 ✓）。返回 {qlib_code: {field: value}}。"""
    cache = CACHE / key / ("%s.json" % day.replace("-", ""))
    if cache.exists():
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except Exception:                                          # noqa: BLE001
            pass
    out, offset, page = {}, 0, 5000
    while True:
        params = {"trade_date": day.replace("-", "")}
        if page:
            params["limit"], params["offset"] = page, offset
        try:
            r = ts_call(api, params, fields, tok)
        except Exception as e:                                     # noqa: BLE001
            if page and "limit" in str(e).lower():
                page = 0
                params.pop("limit", None)
                params.pop("offset", None)
                r = ts_call(api, params, fields, tok)
            else:
                raise
        d = r.get("data") or {}
        items, flds = d.get("items") or [], d.get("fields") or []
        if not items:
            break
        for it in items:
            row = dict(zip(flds, it))
            c = to_qlib_code(str(row.get("ts_code") or ""))
            if c:
                out[c] = {k: row.get(k) for k in flds if k != "ts_code"}
        if not page or len(items) < page:
            break
        offset += page
        time.sleep(sleep)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(out, f)
    time.sleep(sleep)
    return out


def plan_days(cal, field: str, feat: Path, codes, end: str):
    """返回 (需补的交易日列表 ✓, 各字段现状统计)。"""
    i_end = cal.index(end)
    last = None
    for c in codes:
        h = read_bin(feat / c / ("%s.day.bin" % field))
        if h is None:
            continue
        v = h[1]
        fin = np.nonzero(np.isfinite(v))[0]
        if fin.size:
            g = h[0] + int(fin[-1])
            if last is None or g > last:
                last = g
    if last is None:
        return [], None
    return cal[last + 1:i_end + 1], cal[last]


def target_len(cal, start_idx: int, end: str) -> int:
    """该 bin 应有的长度（从它的 header 起算到 end ✓）。"""
    return cal.index(end) + 1 - start_idx


def write_values(feat: Path, code: str, field: str, cal, daymap: dict, days, apply: bool,
                 create_missing: bool):
    """把 days 的值写进 features/<code>/<field>.day.bin ✓。返回状态字符串。"""
    p = feat / code / ("%s.day.bin" % field)
    h = read_bin(p)
    vals = []
    for d in days:
        v = (daymap.get(d) or {}).get(code)
        vals.append(float(v) if v is not None else np.nan)
    if h is None:
        if not create_missing:
            return "skip-new"
        if code.lower().startswith(BJ):
            return "skip-bj"
        # 新建：以**该股 close 的跨度**为准 ✓（有 close 才能确定上市区间 ✓）
        hc = read_bin(feat / code / "close.day.bin")
        if hc is None:
            return "skip-noclose"
        s, v = hc[0], np.full(hc[1].size, np.nan)
        base = cal.index(days[0]) - s
        if base < 0:
            return "skip-range"
        seg = np.full(len(days), np.nan)
        for i, d in enumerate(days):
            gi = cal.index(d) - s
            if 0 <= gi < v.size:
                seg[i] = vals[i]
        n_new = min(len(days), v.size - base)
        if n_new <= 0:
            return "skip-range"
        v[base:base + n_new] = seg[:n_new]
        if apply:
            write_bin(p, s, v.astype(np.float64))
        return "create+%d" % n_new
    s, v = h
    # ★★ 2026-10-13 修 BUG：**别再假设 bin 的 header 索引的是当前日历** ✗→✓
    #   实测 `limit_up/limit_down` 只写进 338 只 ✗、其余 5582 只被"前缀对不上"跳过 ✓
    #   —— 因为这些 bin 的 header 来自**另一套日历**（米筐 bundle 那套 ✓），用
    #   `cal.index(END)+1-s` 去推"应有长度"必然对不上 ✗。
    #   改用**同源对齐** ✓：同一个 header 的 `close` bin 有多长，本字段就应有多长 ✓
    #   （与今天数据侧那条"`factor` 必须与 `close` 等长"同一条原则 ✓✓）。
    need = target_len(cal, s, END)
    hc = read_bin(feat / code / "close.day.bin")
    if hc is not None and int(hc[0]) == int(s):
        need = int(hc[1].size)
    k = need - v.size
    if k <= 0:
        return "up-to-date"
    if k != len(days):
        return "skip-gap(len %d vs need %d)" % (len(days), k)
    if apply:
        bdir = feat.parent / BACKUP
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, bdir / ("%s.%s.day.bin" % (code, field)))
        write_bin(p, s, np.concatenate([v, np.asarray(vals, dtype=np.float64)]))
    return "append+%d" % k


def to_ts_code(qlib_code: str) -> str:
    """qlib 代码 → tushare 代码：`sh600000` → `600000.SH` ✓（`sz`/`bj` 同理 ✓）。"""
    c = str(qlib_code)
    return "%s.%s" % (c[2:].upper(), c[:2].upper())


def fetch_stock_turn(code: str, since: str, end: str, tok: str, sleep: float) -> dict:
    """**按股票**拉 `daily_basic.turnover_rate`（比按天快 ~5 倍 ✓；用户 2026-10-13 质疑后改 ✓）。

    每只一次调用即可拿到**该股整段历史**（~3000 行 ✓ 1~3 秒 ✓）⇒ 3123 只 ≈ **1~1.5 小时** ✓
    （按天拉要 3339 次 ✗ 约 7 小时 ✗）。结果落缓存 `data/tushare_cache/turn_stock/<code>.json` ✓ 可续跑 ✓。
    """
    cache = CACHE / "turn_stock" / ("%s.json" % code)
    if cache.exists():
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except Exception:                                          # noqa: BLE001
            pass
    # ★★ 2026-10-13 修 BUG（用户："肯定用快的方法" + 复验发现）：
    #   实测 3123 只连续调用会**撞限频** ✗ ⇒ 旧代码拿到空 dict 就 `continue`（**不报错、不重试** ✗）
    #   ⇒ "**9 秒跑完 3123 只**" 的假象 ✗（其实一只都没建 ✓）。
    #   现在：**3 次重试 + 退避** ✓；**空结果不入缓存** ✓（下次还能再来 ✓）；
    #   仍拿不到 ⇒ **显式打警告**并返回空 ✓（由调用方计数上报 ✓），**绝不静默** ✗。
    out = {}
    for attempt in range(3):
        r = ts_call("daily_basic", {"ts_code": to_ts_code(code),
                                    "start_date": since.replace("-", ""),
                                    "end_date": end.replace("-", "")},
                    "trade_date,turnover_rate", tok)
        d = r.get("data") or {}
        flds = d.get("fields") or []
        for it in d.get("items") or []:
            row = dict(zip(flds, it))
            td, tv = row.get("trade_date"), row.get("turnover_rate")
            if td and tv is not None:
                k = "%s-%s-%s" % (str(td)[:4], str(td)[4:6], str(td)[6:8])
                out[k] = float(tv)
        if out:
            break
        time.sleep(sleep * (attempt + 2))
    if not out:
        print("    ⚠ %s：重试 3 次仍拿不到 turnover_rate（限频/权限？✓）" % code, flush=True)
        return {}
    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(out, f)
    time.sleep(sleep)
    return out


def main():
    ap = argparse.ArgumentParser(description="补齐 cn_data2 标签/派生字段到 2026-10-09")
    ap.add_argument("--stage", required=True, choices=("turn", "limit", "ist"))
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data2"))
    ap.add_argument("--end", default=END)
    ap.add_argument("--since", default="", help="turn 新建 bin 时最早从哪天拉（默认 2013-01-01 ✓）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--create-missing", action="store_true", help="turn：给缺 bin 的股票也建（排除北交所 ✓）")
    ap.add_argument("--sleep", type=float, default=0.35)
    ap.add_argument("--limit-codes", type=int, default=0, help="冒烟：只处理前 N 只")
    a = ap.parse_args()

    qlib = Path(a.qlib_dir)
    cal = cal_of(qlib)
    feat = qlib / "features"
    codes = codes_of(qlib)
    print("数据集 %s ｜ 日历末日 %s ｜ 目标 %s ｜ 股票目录 %d 只 ｜ 模式 %s"
          % (qlib.name, cal[-1], a.end, len(codes), "**APPLY**" if a.apply else "dry-run"))

    if a.stage == "turn":
        days, last = plan_days(cal, "turn", feat, codes, a.end)
        print("turn 现状：最新 %s ⇒ 需补 %d 天：%s ~ %s"
              % (last, len(days), days[0] if days else "-", days[-1] if days else "-"))
        have = [c for c in codes if (feat / c / "turn.day.bin").exists()]
        miss = [c for c in codes if not (feat / c / "turn.day.bin").exists()]
        miss_shsz = [c for c in miss if not c.lower().startswith(BJ)]
        print("  有 bin %d 只 ｜ 缺 bin %d 只（其中 sh/sz %d 只 ✓、北交所 %d 只 ✗ 按用户决定不建）"
              % (len(have), len(miss), len(miss_shsz), len(miss) - len(miss_shsz)))
        if not a.apply:
            print("  dry-run：将调 `daily_basic` **%d 次**（按天 ✓ 每次全市场 ✓）" % len(days))
            if a.create_missing:
                since = a.since or "2013-01-01"
                print("  create-missing：**按股票拉** %d 只 × 每只 1 次 ⇒ 约 **%d 次**调用 ✓"
                      "（每只拿整段历史 %s ~ %s ✓，实测 1~3 秒/只 ⇒ **约 1~1.5 小时** ✓）"
                      % (len(miss_shsz), len(miss_shsz), since, a.end))
                print("  ⚠ 新建只覆盖【该股 close 有值的日历年】✓；北交所已排除 ✓")
            print("  ⇒ 加 --apply 才真拉（会先写缓存 %s/basic_turn/*.json ✓，可断点续跑 ✓）" % CACHE)
            return
        tok = token()
        n_ok = n_skip = 0
        # ① 已有 bin 的股票：补 days 那段 ✓
        daymaps = {}
        for d in days:
            daymaps[d] = {c: (v or {}).get("turnover_rate") for c, v in
                          fetch_day("daily_basic", d, "ts_code,turnover_rate", tok, a.sleep,
                                    "basic_turn").items()}
        for c in (have[:a.limit_codes] if a.limit_codes else have):
            st = write_values(feat, c, "turn", cal, daymaps, days, True, False)
            n_ok += 1 if st.startswith("append") else 0
            n_skip += 0 if st.startswith("append") else 1
        print("① 已有 bin：写入 %d ✓ / 跳过 %d" % (n_ok, n_skip))
        # ② 新建（可选 ✓）★ 2026-10-13 改：**按股票拉**（用户质疑"turn 至于那么慢吗"✓ ⇒ 确实不该 ✓）
        #   按天拉要 3339 次（~7 小时 ✗）；按股票拉每只 1 次、拿整段历史 ⇒ 3123 次、约 **1~1.5 小时** ✓。
        if a.create_missing:
            since = a.since or "2013-01-01"
            print("② 新建：**按股票拉** %d 只 × 1 次（%s ~ %s ✓，比按天快 ~5 倍 ✓）"
                  % (len(miss_shsz), since, a.end), flush=True)
            n_new = n_skip2 = 0
            for j, c in enumerate(miss_shsz):
                rows = fetch_stock_turn(c, since, a.end, tok, a.sleep)
                hc = read_bin(feat / c / "close.day.bin")
                if hc is None or not rows:
                    n_skip2 += 1
                    continue
                s, v = hc[0], np.full(hc[1].size, np.nan)
                for d, val in rows.items():
                    if d in cal:
                        gi = cal.index(d) - s
                        if 0 <= gi < v.size:
                            v[gi] = val
                write_bin(feat / c / "turn.day.bin", s, v)
                n_new += 1
                if (j + 1) % 200 == 0:
                    print("    %d/%d ｜ 已建 %d ✓ ｜ 跳过 %d" % (j + 1, len(miss_shsz), n_new, n_skip2),
                          flush=True)
            print("② 新建完成：建了 %d 只 ✓ / 跳过 %d（无 close 或拿不到数据 ✓）" % (n_new, n_skip2))
        return

    if a.stage in ("limit", "ist"):
        flds = ("limit_up", "limit_down") if a.stage == "limit" else ("is_st",)
        for f in flds:
            days, last = plan_days(cal, f, feat, codes, a.end)
            print("%-11s 现状：最新 %s ⇒ 需补 %d 天：%s"
                  % (f, last, len(days), days[0] if days else "-"))
            if not a.apply:
                print("  dry-run：将调端点 **%d 次**（按天 ✓）"
                      % len(days))
                continue
            tok = token()
            if a.stage == "limit":
                dm = {}
                for d in days:
                    dm[d] = {c: v for c, v in fetch_day(
                        "stk_limit", d, "ts_code,up_limit,down_limit", tok, a.sleep,
                        "stk_limit").items()}
                # limit_up / limit_down 分开写 ✓
                for f2, kk in (("limit_up", "up_limit"), ("limit_down", "down_limit")):
                    mp_all = {d: {c: (v or {}).get(kk) for c, v in dm[d].items()} for d in days}
                    n_ok = n_skip = 0
                    for c in codes:
                        if not (feat / c / ("%s.day.bin" % f2)).exists():
                            continue
                        st = write_values(feat, c, f2, cal, mp_all, days, True, False)
                        n_ok += 1 if st.startswith("append") else 0
                        n_skip += 0 if st.startswith("append") else 1
                    print("  %s 写入 %d ✓ / 跳过 %d" % (f2, n_ok, n_skip))
            else:
                # is_st：**只补这一天** ✓（用户决定：不换口径 ✓；只用当天 ST 名单 ✓）
                # ⚠ 本轮**不写** ✓ —— ST 名单来源还需定（`namechange` 的生效日 vs 米筐 `st_stock_days`
                #   的"ST 覆盖交易日"可能差 1 天 ✗），先把空档天数报出来 ✓ 再定 ✓。
                print("  ⚠ is_st 本次不写 ✗（待定 ST 名单来源 ✓）；空档 = %s ~ %s（%d 天 ✓）"
                      % (days[0] if days else "-", days[-1] if days else "-", len(days)))
        return


if __name__ == "__main__":       # ★ 加守卫 ✓（否则被 import 时会直接跑 argparse ✗）
    main()
