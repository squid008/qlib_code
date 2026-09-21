# -*- coding: utf-8 -*-
"""核对物化数据（`pre*` 前复权 5 个 + `chip_*` 筹码 7 个）—— 换机器 / 数据更新后跑一次。

检查项（都对不上就是"静默失效"，所以值得每次物化后跑）：

**前复权 `pre*`**（源字段 `close/open/high/low/vwap`）
  1. 覆盖率：与源字段同名的股票数应一致（源没有的跳过亦可）；
  2. **同轴**：首值（起始日历下标）与长度 == 源字段；
  3. **NaN 位置逐位相同**；
  4. **值逐位等于 `源 / factor_last`**（`factor_last` = `factor.day.bin` 末值）。

**筹码 `chip_*`**（7 个字段）
  5. 覆盖率 ≥ 有 `close` 的股票数；
  6. **同轴**：与 `close.day.bin` 首值+长度一致（多股票一起加载才不会报
     `identically-labeled` —— v1.19.91/92 的坑）；
  7. 抽样数值合理：`cost5 ≤ cost95`、`WINNER ∈ [0,1]`、非全 NaN；
  8. ★ v1.20.45 **物化口径戳**（`features/_chip_meta.json` 里的 `chip_semantics`）是否
     与当前代码一致 ✓ —— **换机器 / `git pull` 新代码后最容易漏的一步** ✗；
  9. ★ v1.20.45 **筹码展开比** `COST(95)/COST(5)`（跨度体检 ✓）——
     ⚠ 第 7 项的单调检查**抓不到**"筹码塌缩"✗（塌缩后照样单调 ✓），
     2026-09-21「换手率放大 100 倍」就是这种**静默**错误 ✗，只有看跨度才发现 ✓。
     期望：有 `$turn` 组 p50 ≈1.58、反推组 ≈2.41；`p50 < 1.15` 判为塌缩 ✗。

用法（cwd 任意）：python backend/tools/verify_materialized.py [抽样数，默认 200]
退出码：0 = 全过；1 = 有问题。
"""
import os
import random
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = backend/（本脚本在 backend/tools/ 下）
sys.path.insert(0, _BACKEND)

PRE_PAIRS = (("close", "preclose"), ("open", "preopen"), ("high", "prehigh"),
             ("low", "prelow"), ("vwap", "prevwap"))
CHIP_FIELDS = ("chip_cost_5", "chip_cost_30", "chip_cost_75", "chip_cost_95",
               "chip_win_close", "chip_win_high", "chip_win_low")


def read_bin(path):
    import numpy as np

    if not os.path.exists(path):
        return None, None
    raw = np.fromfile(path, dtype="<f4")
    return (int(raw[0]), raw[1:]) if raw.size else (None, None)


def main() -> int:
    import numpy as np

    from app.config import QLIB_PROVIDER_URI

    fdir = str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"
    if not os.path.isdir(fdir):
        print("✗ 数据目录不存在: %s" % fdir)
        return 1
    insts = sorted(n for n in os.listdir(fdir) if os.path.isdir(os.path.join(fdir, n)))
    has_close = [n for n in insts if os.path.exists(os.path.join(fdir, n, "close.day.bin"))]
    n_samp = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    random.seed(20260919)
    samp = random.sample(has_close, min(n_samp, len(has_close)))

    bad = 0

    # ---- 覆盖率 ----
    pre_cov = sum(1 for n in has_close if all(os.path.exists(os.path.join(fdir, n, d + ".day.bin"))
                                              for _s, d in PRE_PAIRS))
    chip_cov = sum(1 for n in has_close if all(os.path.exists(os.path.join(fdir, n, f + ".day.bin"))
                                               for f in CHIP_FIELDS))
    print("覆盖率：有 close 的股票 %d 只 | pre* 齐全 %d | chip_* 齐全 %d" % (len(has_close), pre_cov, chip_cov))
    if pre_cov < len(has_close):
        print("  ⚠ pre* 不齐（缺 %d 只）⇒ 跑 tools/build_preclose.py" % (len(has_close) - pre_cov))
        bad += 1
    if chip_cov < len(has_close):
        print("  ⚠ chip_* 不齐（缺 %d 只）⇒ 跑 tools/materialize_chip.py" % (len(has_close) - chip_cov))
        bad += 1

    # ---- ★ v1.20.45 物化口径戳（换机器 / pull 新代码后最容易漏的一步 ✗）----
    #   旧口径物化出来的 bin **完全看不出异常** ✗（数值合理、四档单调 ✓）⇒ 必须靠戳 ✓
    try:
        from app.factors.chip_store import chip_meta_state
        st = chip_meta_state()
        print("物化口径戳：%s" % st.get("message"))
        if not st.get("ok"):
            bad += 1
    except Exception as e:                                # noqa: BLE001
        print("  ⚠ 物化口径戳检查失败：%r" % (e,))
        bad += 1

    # ---- 抽样逐位核对 ----
    n_axis = n_nan = n_val = n_order = 0
    spreads = []                                          # ★ v1.20.45：COST95/COST5 跨度 ✓
    for inst in samp:
        d = os.path.join(fdir, inst)
        _fi, fv = read_bin(os.path.join(d, "factor.day.bin"))
        fl = float(fv[-1]) if (fv is not None and fv.size and float(fv[-1]) > 0) else None
        for src, dst in PRE_PAIRS:
            si, sv = read_bin(os.path.join(d, src + ".day.bin"))
            di, dv = read_bin(os.path.join(d, dst + ".day.bin"))
            if sv is None or dv is None:
                continue
            if si != di or sv.size != dv.size:
                n_axis += 1
                continue
            if not np.array_equal(np.isnan(sv), np.isnan(dv)):
                n_nan += 1
            if fl is not None:
                m = np.isfinite(sv)
                if m.any() and not np.allclose(dv[m], sv[m] / fl, rtol=0, atol=1e-5):
                    n_val += 1
        # 筹码：同轴 + 单调 + WINNER 值域
        ci, cv = read_bin(os.path.join(d, "close.day.bin"))
        c5i, c5 = read_bin(os.path.join(d, "chip_cost_5.day.bin"))
        c95i, c95 = read_bin(os.path.join(d, "chip_cost_95.day.bin"))
        wi, w = read_bin(os.path.join(d, "chip_win_close.day.bin"))
        if cv is not None:
            for i, v in ((c5i, c5), (c95i, c95), (wi, w)):
                if v is not None and (i != ci or v.size != cv.size):
                    n_axis += 1
        m = np.isfinite(c5) & np.isfinite(c95)
        if m.any() and np.any(c5[m] > c95[m] + 1e-6):
            n_order += 1
        if w is not None:
            mw = np.isfinite(w)
            if mw.any() and (np.any(w[mw] < -1e-6) or np.any(w[mw] > 1 + 1e-6)):
                n_order += 1
        # ★ v1.20.45：最后一个有效日的 `COST(95)/COST(5)`（筹码分布的**跨度** ✓）
        m2 = np.isfinite(c5) & np.isfinite(c95) & (c5 > 0)
        if m2.any():
            j = int(np.flatnonzero(m2)[-1])
            spreads.append(float(c95[j] / c5[j]))

    print("抽样 %d 只：轴不符 %d | NaN 位置不符 %d | 值不符 %d | 筹码数值异常 %d"
          % (len(samp), n_axis, n_nan, n_val, n_order))
    bad += (n_axis + n_nan + n_val + n_order)

    # ---- ★★ v1.20.45「展开比」体检：抓**筹码塌缩**（口径错了的**静默**症状 ✗）----
    #   为什么必须加：`cost5 ≤ cost95` 这类单调检查**抓不到塌缩** ✗（塌缩后依然单调 ✓）——
    #   2026-09-21「换手率放大 100 倍」就是这种 ✗：数值全"合理"、只有**跨度**塌成 ≈1.0 ✗。
    #   期望值（v1.20.44 重物化后 360 只实测 ✓）：有 `$turn` 组 p50 ≈1.58、反推组 ≈2.41 ✓。
    if spreads:
        a = np.asarray(spreads)
        p25, p50, p90 = (float(x) for x in np.percentile(a, [25, 50, 90]))
        print("筹码展开比 COST95/COST5：n=%d | p25 %.3f | **p50 %.3f** | p90 %.3f | <1.05 占比 %.1f%%"
              % (a.size, p25, p50, p90, float((a < 1.05).mean() * 100)))
        if p50 < 1.15:
            print("  ⚠ 展开比中位数 %.3f **偏低** ⇒ 筹码很可能**塌缩** ✗"
                  "（换手率口径偏大 / bin 是旧口径 ✗）" % p50)
            print("     ⇒ 期望：有 turn 组 ≈1.58、反推组 ≈2.41（2026-09-21 实测 ✓）"
                  "；修法：`materialize_chip.py 400 --overwrite` ✓")
            bad += 1
    else:
        print("  ⚠ 无法计算筹码展开比（有效样本不足）⇒ 无法排除塌缩 ✗")
        bad += 1

    print("结论：%s" % ("✅ 物化数据完整且一致" if bad == 0 else "❌ 有问题（见上）"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归）
    sys.exit(main())
