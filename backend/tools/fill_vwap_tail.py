# -*- coding: utf-8 -*-
"""把 `vwap` / `prevwap` 补到**该股 `close` 的末日** ✓（2026-10-13 全面体检发现 ✓，**零 API** ✓）。

## 发现（`ai_test/health_both_1013.py` ✓，2026-10-13）
`cn_data2` 里 **5178 只沪深股**的 `vwap`/`prevwap` 停在 **2026-05-26** ✗，而 `close` 已到
**2026-10-09** ✗ ⇒ 用 VWAP 的因子在 05-26 之后**整段没有值** ✗（`prevwap` 更隐蔽 ✓ ——
它属于 `pre*` 家族，会被当成"前复权价"用 ✓）。
⚠ 为什么以前没发现 ✗：`ai_test/worklist_missing_1009.py` 那张表按"**全股票取 max**" ✗ ⇒
只要有一两只到末日就显示"落后 0" ✓，把**个股的落后藏起来** ✗。正确定义 =
「**该字段末日 < 该股自己的 `close` 末日**」✓。

## 口径（实测两条恒等式 ✓，**逐位成立** ✓，不是猜 ✓）
| 字段 | 恒等式 | 实测 |
|---|---|---|
| `vwap` | **`10 × amount ÷ volume`** ✓ | 5 只 × 各 5 天比值 **1.000000** ✓ |
| `prevwap` | **`vwap ÷ factor_last`** ✓（`factor_last` = 该股 `factor` bin **末值** ✓） | 同上 ✓ |

## 闸门（不满足就**不写该股** ✓，交人工 ✓）
重叠校验 ✓：在该股**已有的 vwap 段**上取最近 `--verify-days`（默认 60 ✓）天，
两条恒等式的**最大相对误差 > `--tol`（默认 1e-4 ✓）⇒ 拒绝写** ✗ 并计数 ✓。

用法：
  python backend/tools/fill_vwap_tail.py --qlib-dir data/cn_data2            # dry-run ✓
  python backend/tools/fill_vwap_tail.py --qlib-dir data/cn_data2 --apply    # 写盘（带备份 ✓）
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def read_bin(p: Path):
    if not p.exists():
        return None
    a = np.fromfile(p, dtype="<f4")
    return None if a.size < 2 else (int(round(float(a[0]))), a[1:].astype(float))


def last_finite(v):
    f = np.nonzero(np.isfinite(v))[0]
    return int(f[-1]) if f.size else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlib-dir", required=True, help="★ 必填（防写错数据集 ✗）")
    ap.add_argument("--codes", default="")
    ap.add_argument("--verify-days", type=int, default=60)
    ap.add_argument("--tol", type=float, default=1e-4)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    qlib = Path(a.qlib_dir)
    feats = qlib / "features"
    cal = [ln.strip() for ln in open(qlib / "calendars" / "day.txt", encoding="utf-8") if ln.strip()]
    codes = ([c.strip() for c in a.codes.split(",") if c.strip()] if a.codes
             else sorted(d.name for d in feats.iterdir()
                         if d.is_dir() and d.name[2:].isdigit() and d.name[:2] in ("sh", "sz", "bj")))
    print("数据集 %s ｜ 日历末 %s ｜ 候选 %d 只 ｜ 模式 %s"
          % (qlib, cal[-1], len(codes), "**APPLY**" if a.apply else "dry-run"))

    n_ok = n_gate = n_nosrc = n_uptodate = 0
    plan, bad = {}, []
    for c in codes:
        d = feats / c
        hv = read_bin(d / "vwap.day.bin")
        hp = read_bin(d / "prevwap.day.bin")
        ha = read_bin(d / "amount.day.bin")
        hm = read_bin(d / "volume.day.bin")
        hf = read_bin(d / "factor.day.bin")
        hc = read_bin(d / "close.day.bin")
        if hv is None or ha is None or hm is None or hf is None or hc is None:
            n_nosrc += 1
            continue
        lv, lc = last_finite(hv[1]), last_finite(hc[1])
        if lc is None:
            n_nosrc += 1
            continue
        # ★★ 2026-10-14 修 ✗→✓：`last_finite` 返回的是**数组内相对下标** ✗，而 `hv[0]` 是**绝对** header ✓
        #   ⇒ 拿"相对的 close 末日"去减"绝对的 vwap header"会算错范围 ✗
        #   （全 NaN 的 vwap + close header=0 的股票 ⇒ `hi_i` 变负 ⇒ 一个洞都找不到 ✗，
        #    退市股因此整条建不出来 ✗）。⇒ 统一换成**绝对下标** ✓。
        lv = (hv[0] + lv) if lv is not None else None
        lc = hc[0] + lc
        # ★ 2026-10-14：`vwap` **整条全 NaN**（退市股 ✓，如 `sh600145`）⇒ 不再是"补洞"而是"**建整条**" ✓
        #   （用户："退市股的 amount 和 vwap 得到退市那天才对吧？" ✓）
        if lv is None:
            lv = hv[0] - 1                              # 下限设成 header 之前 ⇒ 全部可算日都是"洞" ✓
        # ★ 判据（2026-10-13 修 ✗→✓）：不是"只看尾巴" ✗，而是"**该有值却缺**" ✓ ——
        #   `vwap` 是 NaN、但 `10×amount÷volume` 算得出来 ⇒ 该补 ✓（含**中间洞** ✓：amount 补完后
        #   2026-05-27~08-21 那 62 天正是这种情况 ✗；只看尾巴会报"待补 0" 而放过 ✗）。
        gaps = []
        hi_i = min(hv[1].size - 1, lc - hv[0])           # ★ 从数组头扫到该股 close 末日 ✓（不越界 ✓）
        for i in range(0, hi_i + 1):
            gi = hv[0] + i
            ja, jm = gi - ha[0], gi - hm[0]
            am = ha[1][ja] if 0 <= ja < ha[1].size else np.nan
            vo = hm[1][jm] if 0 <= jm < hm[1].size else np.nan
            if (not np.isfinite(hv[1][i])) and np.isfinite(am) and np.isfinite(vo) and vo > 0:
                gaps.append(i)
        if not gaps:
            n_uptodate += 1
            continue
        fin = np.nonzero(np.isfinite(hf[1]))[0]
        flast = float(hf[1][int(fin[-1])]) if fin.size else float("nan")
        # ---- 闸门：在已有段上验两条恒等式 ✓
        lv_rel = lv - hv[0]                              # 绝对 ⇒ 相对 ✓（闸门按数组下标取 ✓）
        idx = [i for i in range(max(0, lv_rel - a.verify_days + 1), lv_rel + 1) if np.isfinite(hv[1][i])]
        e1 = e2 = 0.0
        for i in idx:
            gi = hv[0] + i
            ja, jm = gi - ha[0], gi - hm[0]
            am = ha[1][ja] if 0 <= ja < ha[1].size else np.nan
            vo = hm[1][jm] if 0 <= jm < hm[1].size else np.nan
            vw = float(hv[1][i])
            if np.isfinite(am) and np.isfinite(vo) and vo > 0:
                e1 = max(e1, abs(vw / (10.0 * am / vo) - 1.0))
            if hp is not None and np.isfinite(flast) and flast > 0:
                jp = gi - hp[0]
                if 0 <= jp < hp[1].size and np.isfinite(hp[1][jp]):
                    e2 = max(e2, abs(float(hp[1][jp]) / (vw / flast) - 1.0))
        if e1 > a.tol or e2 > a.tol:
            n_gate += 1
            bad.append((c, round(e1, 8), round(e2, 8)))
            continue
        # ---- 逐洞算值 ✓（`10×amount÷volume` ✓；prevwap = vwap ÷ factor_last ✓）----
        vv = np.full(len(gaps), np.nan)
        pp = np.full(len(gaps), np.nan)
        miss = 0
        for k, i in enumerate(gaps):
            gi = hv[0] + i
            ja, jm = gi - ha[0], gi - hm[0]
            am = ha[1][ja] if 0 <= ja < ha[1].size else np.nan
            vo = hm[1][jm] if 0 <= jm < hm[1].size else np.nan
            if np.isfinite(am) and np.isfinite(vo) and vo > 0:
                vv[k] = 10.0 * am / vo
                if np.isfinite(flast) and flast > 0:
                    pp[k] = vv[k] / flast
            else:
                miss += 1
        n_ok += 1
        plan[c] = {"hdr": hv[0], "v": hv[1], "p": hp[1] if hp else None, "phdr": hp[0] if hp else None,
                   "idx": gaps, "vv": vv, "pp": pp, "miss": miss, "nd": len(gaps),
                   "tail": int(sum(1 for i in gaps if i > lv_rel))}
    print("⇒ 待补 %d 只 ｜ 无洞 %d 只 ｜ 缺依赖 bin %d 只 ｜ **闸门不过** %d 只"
          % (n_ok, n_uptodate, n_nosrc, n_gate))
    if bad:
        print("   闸门不过样例（e1=vwap 恒等式误差 / e2=prevwap ✓）：%s" % bad[:5])
    if n_ok:
        tot = sum(p["nd"] for p in plan.values())
        tail = sum(p["tail"] for p in plan.values())
        print("   合计要补 %d 格（其中**尾巴** %d 格 / **中间洞** %d 格 ✓）"
              % (tot, tail, tot - tail))
        for c, p in sorted(plan.items())[:3]:
            print("   例 %s：补 %d 格（尾 %d / 洞 %d ✓）｜ 有洞的首日 %s ｜ 首值 %.4f ｜ prevwap 有bin %s"
                  % (c, p["nd"], p["tail"], p["nd"] - p["tail"],
                     cal[p["hdr"] + p["idx"][0]], p["vv"][0] if np.isfinite(p["vv"][0]) else float("nan"),
                     p["p"] is not None))
    if not a.apply:
        print("dry-run ✓（`--apply` 才写 ✓；备份到 _backup_vwap_20261013/ ✓；**零 API** ✓）")
        return
    backup = qlib / "_backup_vwap_20261013"
    backup.mkdir(parents=True, exist_ok=True)
    n_cell = 0
    for c, p in plan.items():
        d = feats / c
        for fld, new, h in (("vwap", p["vv"], (p["hdr"], p["v"])),
                            ("prevwap", p["pp"], (p["phdr"], p["p"]))):
            if h[1] is None:
                continue                                # 该股本来没有 prevwap bin ⇒ 不新建 ✗（保持一致 ✓）
            pth = d / ("%s.day.bin" % fld)
            src = (p["hdr"], p["v"]) if fld == "vwap" else (p["phdr"], p["p"])
            hdr, v = src
            arr = v.copy()
            # ★ 若最早要写的日子在现有 header **之前** ⇒ 往前补 NaN 并把 header 前移 ✓（全 NaN bin 会这样 ✓）
            first_target = p["hdr"] + p["idx"][0]
            if first_target < hdr:
                pad = hdr - first_target
                arr = np.concatenate([np.full(pad, np.nan), arr])
                hdr = first_target
            need = (p["hdr"] + p["idx"][-1] + 1) - hdr
            if arr.size < need:
                arr = np.concatenate([arr, np.full(need - arr.size, np.nan)])
            for k, val in enumerate(new):
                j = (p["hdr"] + p["idx"][k]) - hdr       # ★ 按**洞的真实下标**写 ✓（不再假设"尾巴" ✗）
                if 0 <= j < arr.size and np.isfinite(val):
                    arr[j] = val
                    n_cell += 1
            (backup / c).mkdir(parents=True, exist_ok=True)
            shutil.copy2(pth, backup / c / ("%s.day.bin" % fld))
            tmp = str(pth) + ".tmp"
            np.hstack([np.asarray([hdr], dtype="<f4"), arr.astype("<f4")]).astype("<f4").tofile(tmp)
            os.replace(tmp, pth)
    print("完成 ✓ %d 只 / 写 %d 格 ⇒ 备份 %s ✓" % (len(plan), n_cell, backup))
    try:
        b = ROOT / "backend"
        if str(b) not in sys.path:
            sys.path.insert(0, str(b))
        from app.engine.feature_cache import bump_data_version
        print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "fill_vwap_tail"))
    except Exception as e:                                                    # noqa: BLE001
        print("⚠⚠ 数据戳**没落上**（%r）⇒ 手工清 backend/workdir/feature_cache/ ✗" % e)


if __name__ == "__main__":
    main()
