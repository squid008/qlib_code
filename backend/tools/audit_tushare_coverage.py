# -*- coding: utf-8 -*-
"""审计既有 tushare 数据**有没有"拉失败静默跳过"** ✗（用户 2026-10-10："之前的数据要检查一下"）

五项检查（全部只读 ✓）：
  ① `tushare_cache/total_mv.json`：按天统计股票数 ⇒ 明显偏少的那天 = 拉失败过 ✓
  ② `tushare_cache/adj/*.json`：与 `cn_data2` 的代码表对差 ⇒ 缺哪些、哪些 rows 为空 ✓
  ③ `tushare_cache/fin/*.json`：四表（income/balancesheet/cashflow/fina_indicator）空的清单 ✓
  ④ `tushare_cache/daily/*.json`：每天覆盖多少只 ✓
  ⑤ ★ **bin 层反查**：缓存里明明有数据（如 income 非空 ✓），但 `cn_data2` 的 `fin_*` bin 却全 NaN ✗
     ⇒ 这就是"当初拉到了但没写进去/写失败"的**静默跳过** ✓（最直接命中用户担心的问题 ✓）

用法：python tools/audit_tushare_coverage.py [--qlib-dir data/cn_data2] [--top 20]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "tushare_cache"
TABLES = ("income", "balancesheet", "cashflow", "fina_indicator")


def jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                              # noqa: BLE001
        return {"__error__": "%s: %s" % (type(e).__name__, e)}


def main():
    ap = argparse.ArgumentParser(description="审计 tushare 缓存/落盘的覆盖率（只读 ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data2"))
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    qlib = Path(args.qlib_dir)

    print("=" * 100)
    print("① total_mv.json 按天覆盖")
    p = CACHE / "total_mv.json"
    if not p.exists():
        print("   文件不存在 ✗")
    else:
        d = jload(p)
        if "__error__" in d:
            print("   **读失败** ✗ %s" % d["__error__"])
        else:
            by_day = Counter(k.split("|")[0] for k in d)
            days = sorted(by_day)
            med = int(np.median([by_day[x] for x in days])) if days else 0
            print("   %d 条 / %d 天（%s ~ %s）；每天中位数 %d 只" % (len(d), len(days), days[0], days[-1], med))
            low = [(x, by_day[x]) for x in days if by_day[x] < med * 0.8]
            print("   明显偏少（< 中位数 80%%）：%d 天%s" % (len(low), " ⇒ " + str(low[:args.top]) if low else " ✓"))

    print("=" * 100)
    print("② adj/*.json 覆盖")
    adjs = sorted((CACHE / "adj").glob("*.json")) if (CACHE / "adj").is_dir() else []
    codes = sorted(d.name for d in (qlib / "features").iterdir() if d.is_dir() and d.name[2:].isdigit())
    want = {"%s.%s" % (c[2:], {"sh": "SH", "sz": "SZ", "bj": "BJ"}[c[:2]]) for c in codes}
    have = {x.stem for x in adjs}
    empty = []
    for x in adjs:
        o = jload(x)
        if isinstance(o, dict) and not (o.get("rows") or {}):
            empty.append(x.stem)
    print("   文件 %d 个；cn_data2 需要 %d 个" % (len(adjs), len(want)))
    print("   **缺失**：%d 个%s" % (len(want - have), " ⇒ " + ",".join(sorted(want - have)[:args.top]) if want - have else " ✓"))
    print("   **rows 为空**：%d 个%s" % (len(empty), " ⇒ " + ",".join(empty[:args.top]) if empty else " ✓"))

    print("=" * 100)
    print("③ fin/*.json 四表为空的情况")
    fins = sorted((CACHE / "fin").glob("*.json"))
    all_empty = []
    tab_missing = Counter()
    errs = []
    for x in fins:
        o = jload(x)
        if not isinstance(o, dict) or "__error__" in o:
            errs.append(x.stem)
            continue
        ne = [t for t in TABLES if not (o.get(t) or [])]
        if len(ne) == len(TABLES):
            all_empty.append(x.stem)
        for t in ne:
            tab_missing[t] += 1
    print("   文件 %d 个；**四表全空** %d 只%s" % (len(fins), len(all_empty),
                                             " ⇒ " + ",".join(all_empty[:args.top]) if all_empty else " ✓"))
    print("   各表为空计数：%s" % dict(tab_missing))
    if errs:
        print("   **读失败**（JSON 坏 ✗）：%d 个 ⇒ %s" % (len(errs), ",".join(errs[:args.top])))

    print("=" * 100)
    print("④ daily/*.json 按天覆盖")
    dl = sorted((CACHE / "daily").glob("*.json"))
    rows = []
    for x in dl:
        o = jload(x)
        n = len(o) if isinstance(o, dict) and "__error__" not in o else -1
        rows.append((x.stem, n))
    med = int(np.median([n for _d, n in rows if n >= 0])) if rows else 0
    print("   文件 %d 天；每天条数中位数 %d" % (len(rows), med))
    low = [(d, n) for d, n in rows if n < med * 0.8]
    print("   偏少（<80%%）或读失败：%d 天%s" % (len(low), " ⇒ " + str(low[:args.top]) if low else " ✓"))

    print("=" * 100)
    print("⑤ ★ bin 层反查：缓存里**有**数据、但 bin 里全是 NaN ✗（= 当初静默跳过 ✓）")
    fields = ("fin_roe", "fin_rev_yoy", "fin_np_yoy", "fin_eps")
    susp = {}
    n_ck = 0
    for x in fins[:4000]:
        o = jload(x)
        if not isinstance(o, dict) or not (o.get("income") or []):
            continue
        code = x.stem
        dirp = qlib / "features" / code
        if not dirp.is_dir():
            continue
        n_ck += 1
        for f in fields:
            b = dirp / ("%s.day.bin" % f)
            if b.exists():
                a = np.fromfile(str(b), dtype="<f4")
                if a.size >= 2 and not np.isfinite(a[1:]).any():
                    susp.setdefault(code, []).append(f)
    print("   抽查 %d 只（缓存 income 非空 ✓ 且在 %s 里有目录 ✓）" % (n_ck, qlib.name))
    print("   ⇒ **缓存有、bin 全 NaN** 的：%d 只%s"
          % (len(susp), " ⇒ " + str(list(susp.items())[:args.top]) if susp else " ✓（没有静默跳过 ✓）"))


if __name__ == "__main__":
    main()
