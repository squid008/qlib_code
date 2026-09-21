# -*- coding: utf-8 -*-
"""重物化之后，验证筹码分布是否**不再塌缩** ✓（直接读物化 bin，不猜 ✓）。

判据：`spread = chip_cost_95 / chip_cost_5`（同一交易日）。
  · ≈1.00~1.05 ⇒ **塌缩** ✗（成本分布只剩最近几天 —— 换手率大 100 倍的症状 ✓）；
  · 明显 >1（如 1.3~3）⇒ 成本分布有真实跨度 ✓。
并**分组**对比：A 组 = 有 `$turn` 字段的（走 `turn`/100 ✓）；B 组 = 依赖反推的（逐股手/股校准 ✓）。
"""
import os
import sys

import numpy as np

D = r"d:\quant\qlib_code\data\cn_data\features"


def rd(a, f):
    p = os.path.join(D, a, f + ".day.bin")
    if not os.path.exists(p):
        raise FileNotFoundError(f)
    x = np.fromfile(p, dtype="<f4")
    return int(x[0]), x[1:].astype(np.float64)


def main():
    codes = sorted(n for n in os.listdir(D) if os.path.isdir(os.path.join(D, n)))
    step = max(1, len(codes) // 400)
    grp = {"A_有turn": [], "B_反推": []}
    n_ok = n_bad = 0
    for c in codes[::step]:
        try:
            j5, c5 = rd(c, "chip_cost_5")
            j95, c95 = rd(c, "chip_cost_95")
            n = min(c5.size, c95.size)
            v5, v95 = c5[-n:], c95[-n:]
            ok = np.isfinite(v5) & np.isfinite(v95) & (v5 > 0)
            idx = np.flatnonzero(ok)
            if idx.size < 30:
                continue
            i = idx[-1]                       # 最后一个有效日 ✓
            sp = v95[i] / v5[i]
            if not np.isfinite(sp):
                continue
            try:
                _ = rd(c, "turn")
                has_turn = True
            except Exception:
                has_turn = False
            grp["A_有turn" if has_turn else "B_反推"].append(sp)
            n_ok += 1
        except Exception:
            n_bad += 1

    print("  样本 %d 只（缺文件/无数据 %d 只）" % (n_ok, n_bad))
    for k, v in grp.items():
        if not v:
            print("  %-10s 无样本" % k)
            continue
        a = np.asarray(v)
        print("  %-10s n=%4d │ p10 %.3f  p25 %.3f  **p50 %.3f**  p75 %.3f  p90 %.3f │ ≈1.0 占比 %.1f%%"
              % (k, a.size, *np.percentile(a, [10, 25, 50, 75, 90]),
                 float((a < 1.05).mean() * 100)))
    print("")
    print("  ★ 判读：p50 明显 >1.1 ⇒ 修复生效 ✓（成本分布有真实跨度 ✓）；")
    print("          p50 ≈1.0 ⇒ 仍然塌缩 ✗（换手率还是偏大 ✗）。")


if __name__ == "__main__":
    main()
    sys.exit(0)
