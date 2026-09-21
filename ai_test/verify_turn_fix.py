# -*- coding: utf-8 -*-
"""验证「`turn` 是百分数却按小数用 ⇒ 筹码塌缩」这个 BUG ✓（**纯计算，不写数据** ✓）。

⚠ 关键：必须**显式**传 `turn_unit` ✗ —— 否则 `chip_run` 的 auto 判据（旧 `median>1.5` ✗ /
   新 `max>1` ✓）会把两条路归到一起，**看不出差别** ✗（第一次验证就踩了这个坑 ✓）。
判据：换手率大 100 倍 ⇒ 每天几乎全换 ⇒ `COST(5)…COST(95)` **几乎相等** ✗（展开比≈1 ✓）；
     正确换手率 ⇒ 四档应拉开 ✓。
⚠ 取 **最后一个有效日** ✓（不能用 `argmax` ✗ —— 那取到的是**最早**的有效日，
   刚出预热期 ⇒ 天然塌缩 ⇒ 会得出"两者一样"的假结论 ✗，第一次也踩了这个坑 ✓）。
"""
import os
import sys

import numpy as np

sys.path.insert(0, r"d:\quant\qlib_code\backend")
D = r"d:\quant\qlib_code\data\cn_data\features"


def rd(c, f):
    a = np.fromfile(os.path.join(D, c, f + ".day.bin"), dtype="<f4")
    return a[1:]


def last_finite(v):
    idx = np.flatnonzero(np.isfinite(v))
    return idx[-1] if idx.size else None


def main():
    from app.factors.chip_dist import chip_run

    codes = [c for c in sorted(os.listdir(D)) if os.path.isdir(os.path.join(D, c))]
    pick = []
    for c in codes[::5]:
        try:
            t = rd(c, "turn")
            rd(c, "market_cap")
        except Exception:
            continue
        if np.isfinite(t).sum() > 1500:
            pick.append(c)
        if len(pick) >= 5:
            break
    if not pick:
        print("找不到合适的样本")
        return

    q = (5.0, 30.0, 75.0, 95.0)
    print("  %-10s %8s │ %-30s │ %-30s" % ("code", "turn中位", "旧：当小数(不除) ✗",
                                           "新：当百分数(/100) ✓"))
    ra, rb = [], []
    for c in pick:
        try:
            close, high, low, turn = (rd(c, f) for f in
                                      ("close", "high", "low", "turn"))
            n = min(x.size for x in (close, high, low, turn))
            C, H, L, T = (x[-n:].astype(np.float64) for x in (close, high, low, turn))
            T = np.where(np.isfinite(T) & (T > 0), T, np.nan)
            if np.isfinite(T).sum() < 500:
                continue

            def run(unit):
                r = chip_run(C[None, :], H[None, :], L[None, :], T[None, :],
                             qs=q, turn_unit=unit)
                out = []
                for qq in q:
                    v = r["cost_%g" % qq][0]
                    j = last_finite(v)
                    out.append(float(v[j]) if j is not None else float("nan"))
                return out

            a, b = run("frac"), run("pct")
            er_a = a[3] / a[0] if a[0] > 0 else float("nan")
            er_b = b[3] / b[0] if b[0] > 0 else float("nan")
            ra.append(er_a)
            rb.append(er_b)
            print("  %-10s %8.3f │ 展开比 %6.3f  %-18s │ 展开比 %6.3f  %-18s"
                  % (c, float(np.nanmedian(T)), er_a,
                     " ".join("%.2f" % x for x in a), er_b,
                     " ".join("%.2f" % x for x in b)))
        except Exception as e:                                   # noqa: BLE001
            print("  %-10s 跳过：%r" % (c, e))

    if ra:
        print("")
        print("  ★ COST(95)/COST(5) 展开比中位数：旧(当小数) %.3f  →  新(当百分数) %.3f"
              % (float(np.nanmedian(ra)), float(np.nanmedian(rb))))
        print("  ★ 判读：旧 ≈1.0 ⇒ 筹码**塌缩**（只剩最近几天 ✗ = 100 倍换手率的症状 ✓）；")
        print("          新明显 >1 ⇒ 成本分布有真实跨度 ✓ ⇒ 修复生效 ✓")


if __name__ == "__main__":
    main()
    sys.exit(0)
