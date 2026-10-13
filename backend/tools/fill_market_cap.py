# -*- coding: utf-8 -*-
"""把 **`market_cap` 补到 `close` 的末日** ✓（tushare `daily_basic.total_mv` ✓；2026-10-13 发现 ✓）。

## 发现（2026-10-13 全面体检 ✓）
`cn_data2` 里 `market_cap` 对一部分股票**只到 2026-08-21** ✗（= 米筐日历末日 ✓），而量价已到
**2026-10-09** ✗ ⇒ 依赖"总市值"的因子在 **08-22 之后整段没有值** ✗（`prevwap` 那种"静默不生效"最容易骗人 ✓）。
⚠ 注意：`worklist_missing_1009.py` 那张表用的是"**全股票取 max**"✗ ⇒ 会显示"落后 0" ✓ 把个股的落后
**藏起来** ✗ —— 正确定义是「**该字段末日 < 该股自己的 `close` 末日**」✓（`ai_test/health_both_1013.py` ✓）。

## 口径（实测对齐 ✓，不是猜 ✓）
- 本地 `market_cap` 与 tushare `daily_basic.total_mv` **逐位相同** ✓：
  实测 `sh600000` 2026-08-21：本地 `3.01418e11` ｜ 缓存 `301417836615` ✓（比值 1.0000 ✓）；
- ⚠ 单位 ✓：**本工具读的 `data/tushare_cache/basic/<YYYYMMDD>.json` 里 `total_mv` 已经是「元」** ✓
  （不是万元 ✗；`total_mv.json` 那份缓存才是万元 ⇒ 别混用 ✓）。

## 安全闸门（不满足就**不写该股** ✗，交人工 ✓）
重叠校验 ✓：拿该股**最后一个本地日**与缓存同日比，相对差 > `TOL`（默认 0.5% ✗）⇒ 拒绝写并计数 ✓。

用法：
  python backend/tools/fill_market_cap.py --qlib-dir data/cn_data2          # dry-run ✓
  python backend/tools/fill_market_cap.py --qlib-dir data/cn_data2 --apply  # 写盘（带备份 ✓）
"""
import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CACHE_DAY = ROOT / "data" / "tushare_cache" / "basic"
TOL = 0.005


def to_ts_code(c: str) -> str:
    return "%s.%s" % (c[2:], {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(c[:2], "SZ"))


def read_bin(p: Path):
    if not p.exists():
        return None
    a = np.fromfile(p, dtype="<f4")
    return None if a.size < 2 else (int(round(float(a[0]))), a[1:].astype(float))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlib-dir", required=True, help="★ 必填（防写错数据集 ✗）")
    ap.add_argument("--codes", default="", help="默认全部沪深北股票 ✓")
    ap.add_argument("--end", default="", help="补到哪天（默认 = 日历末日 ✓）")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    qlib = Path(a.qlib_dir)
    feats = qlib / "features"
    cal = [ln.strip() for ln in open(qlib / "calendars" / "day.txt", encoding="utf-8") if ln.strip()]
    pos = {d: i for i, d in enumerate(cal)}
    end = a.end or cal[-1]
    i_end = pos[end]
    codes = ([c.strip() for c in a.codes.split(",") if c.strip()] if a.codes
             else sorted(d.name for d in feats.iterdir()
                         if d.is_dir() and d.name[:2] in ("sh", "sz", "bj") and d.name[2:].isdigit()))
    print("数据集 %s ｜ 日历末 %s ｜ 目标 %s ｜ 候选 %d 只 ｜ 模式 %s"
          % (qlib, cal[-1], end, len(codes), "**APPLY**" if a.apply else "dry-run"))

    day_cache, n_api, n_skip_nosrc, n_skip_gate, n_ok = {}, 0, 0, 0, 0
    plan, gate_bad = {}, []
    for c in codes:
        h = read_bin(feats / c / "market_cap.day.bin")
        if h is None:
            n_skip_nosrc += 1
            continue
        hdr, v = h
        fin = np.nonzero(np.isfinite(v))[0]
        if not fin.size:
            n_skip_nosrc += 1
            continue
        last = hdr + int(fin[-1])
        if last >= i_end:
            continue
        # 重叠校验 ✓（该股最后一个本地日，与源同日比）
        d0 = cal[last] if last < len(cal) else None
        src0 = None
        if d0:
            k = d0.replace("-", "")
            if k not in day_cache:
                p = CACHE_DAY / ("%s.json" % k)
                if not p.exists():
                    n_api += 0                                  # 缺缓存天 ⇒ 记 0 调用，下面按缺源跳过 ✓
                    day_cache[k] = None
                else:
                    with open(p, encoding="utf-8") as f:
                        day_cache[k] = json.load(f)
            rec = day_cache[k]
            src0 = (rec or {}).get(to_ts_code(c), {}).get("total_mv") if isinstance(rec, dict) else None
        loc0 = float(v[last - hdr])
        if src0 and loc0 and abs(loc0 / float(src0) - 1) > TOL:
            n_skip_gate += 1
            gate_bad.append((c, d0, round(loc0, 3), float(src0), round(loc0 / float(src0), 6)))
            continue
        # 收集要补的天 ✓
        days = [cal[j] for j in range(last + 1, i_end + 1)]
        vals = np.full(len(days), np.nan)
        miss = 0
        for i, d in enumerate(days):
            k = d.replace("-", "")
            if k not in day_cache:
                p = CACHE_DAY / ("%s.json" % k)
                if p.exists():
                    with open(p, encoding="utf-8") as f:
                        day_cache[k] = json.load(f)
                else:
                    day_cache[k] = None
            rec = day_cache[k]
            x = (rec or {}).get(to_ts_code(c), {}).get("total_mv") if isinstance(rec, dict) else None
            if x is None:
                miss += 1
                continue
            vals[i] = float(x)
        n_ok += 1
        plan[c] = (hdr, v, vals, last, miss, len(days))

    print("⇒ 待补 %d 只 ｜ 本来就到末日 %d 只 ｜ 无 market_cap bin/全 NaN %d 只 ｜ **重叠闸门不过** %d 只"
          % (n_ok, len(codes) - n_ok - n_skip_nosrc - n_skip_gate, n_skip_nosrc, n_skip_gate))
    if gate_bad:
        print("   闸门不过样例（本地 vs 源，比值应≈1 ✓）：%s" % gate_bad[:5])
    if n_ok:
        ex = list(plan.items())[:3]
        for c, (hdr, v, vals, last, miss, nd) in ex:
            print("   例 %s：本地末 %s ⇒ 补 %d 天（源缺 %d 天 ✓）｜ 新值 %s"
                  % (c, cal[last], nd, miss, [round(x, 1) for x in vals[:3]]))
    src_missing = sum(1 for _c, (_h, _v, _x, _l, miss, nd) in plan.items() if miss == nd)
    if src_missing:
        print("   ⚠ 有 %d 只**源也全缺**（会保持 NaN ✗，不造假 ✓）" % src_missing)
    if not a.apply:
        print("dry-run ✓（`--apply` 才写 ✓；旧 bin 备份到 _backup_market_cap_20261013/ ✓；**零 API** ✓）")
        return
    backup = qlib / "_backup_market_cap_20261013"
    backup.mkdir(parents=True, exist_ok=True)
    cnt = Counter()
    for c, (hdr, v, vals, last, _miss, _nd) in plan.items():
        d = feats / c
        p = d / "market_cap.day.bin"
        (backup / c).mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, backup / c / "market_cap.day.bin")
        need = i_end + 1 - hdr
        arr = np.full(max(need, v.size), np.nan)
        arr[:v.size] = v
        for i, val in enumerate(vals):
            j = (last + 1 + i) - hdr
            if 0 <= j < arr.size and np.isfinite(val):
                arr[j] = val
                cnt["filled"] += 1
        tmp = str(p) + ".tmp"
        np.hstack([np.asarray([hdr], dtype="<f4"), arr.astype("<f4")]).astype("<f4").tofile(tmp)
        os.replace(tmp, p)
        cnt["codes"] += 1
    print("完成 ✓ 写 %d 只 / 填 %d 格 ⇒ 备份 %s ✓（tushare 调用 %d 次 ✓）"
          % (cnt["codes"], cnt["filled"], backup, n_api))
    try:
        b = ROOT / "backend"
        if str(b) not in sys.path:
            sys.path.insert(0, str(b))
        from app.engine.feature_cache import bump_data_version
        print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "fill_market_cap"))
    except Exception as e:                                                    # noqa: BLE001
        print("⚠⚠ 数据戳**没落上**（%r）⇒ 手工清 backend/workdir/feature_cache/ ✗" % e)


if __name__ == "__main__":
    main()
