# -*- coding: utf-8 -*-
"""数据覆盖报表：每个物化字段"最后有效日期"到底到哪天 ✓（回答"能不能统一到某个日期"✓）。

做法（**便宜且全池 ✓**）：qlib `.day.bin` = header(首有效位的**日历下标** float32/float64) +
float32 数据 ⇒ 行数 = 文件字节数/4 − 1 ✓ ⇒ **最后有效位** = 首下标 + 行数 − 1 ✓
⇒ 只读 4 字节头部 + 文件大小，就能对**全池**给出每个字段的最后日期 ✓（不解析数据 ✓）。

用法：
    python ai_test/report_data_coverage.py                 # 默认 data/cn_data
    python ai_test/report_data_coverage.py <qlib_dir>      # 指定数据集（如 data/cn_data2 ✓）
"""
import os
import sys

import numpy as np

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# 关心的字段（按"物化层"分组 ✓；缺失的会自动标出 ✓）
GROUPS = [
    ("原始行情", ["close", "factor", "volume", "amount"]),
    ("交易所状态", ["limit_up", "limit_down", "is_st"]),
    ("前复权", ["preclose", "preopen", "prehigh", "prelow", "prevwap"]),
    ("市值/换手", ["market_cap", "turn"]),
    ("资金流", ["mf_amount_main", "mf_pct_main", "mf_vol_main"]),
    ("筹码", ["chip_cost_5", "chip_cost_95"]),
]


def last_idx_of(path: str):
    """(首有效位日历下标, 最后有效位日历下标) 或 (None, None)。"""
    if not os.path.exists(path):
        return None, None
    try:
        n_float = os.path.getsize(path) // 4
        if n_float < 2:
            return None, None
        first = int(np.fromfile(path, dtype="<f4", count=1)[0])
        return first, first + (n_float - 1) - 1
    except Exception:
        return None, None


def main() -> int:
    if len(sys.argv) > 1:
        qdir = sys.argv[1]
    else:
        from app.config import QLIB_PROVIDER_URI
        qdir = str(QLIB_PROVIDER_URI)
    qdir = qdir.rstrip("/\\")
    fdir = os.path.join(qdir, "features")
    cal_path = os.path.join(qdir, "calendars", "day.txt")
    if not os.path.isdir(fdir) or not os.path.exists(cal_path):
        print("✗ 目录不完整: %s" % qdir)
        return 2

    with open(cal_path, "r", encoding="utf-8") as f:
        cal = [ln.strip() for ln in f if ln.strip()]
    print("数据集 %s" % qdir)
    print("日历 %s ~ %s（%d 个交易日）" % (cal[0], cal[-1], len(cal)))
    insts = sorted(n for n in os.listdir(fdir) if os.path.isdir(os.path.join(fdir, n)))
    print("股票目录 %d 个" % len(insts))
    print("")

    hdr = "%-12s %-16s %6s %12s %12s"
    print(hdr % ("层", "字段", "有值股票", "最早日期", "★ 最后日期"))
    print("-" * 66)
    for gname, fields in GROUPS:
        for fld in fields:
            n = miss = 0
            lo, hi = None, None
            his = []                                    # ★ 各股"最后日期"分布（解释"为什么有人看到更早"✓）
            for inst in insts:
                a, b = last_idx_of(os.path.join(fdir, inst, fld + ".day.bin"))
                if a is None:
                    miss += 1
                    continue
                n += 1
                his.append(b)
                lo = a if lo is None or a < lo else lo
                hi = b if hi is None or b > hi else hi
            if n == 0:
                print(hdr % (gname, fld, "0", "-", "** 无文件 **"))
                continue
            d_lo = cal[lo] if lo < len(cal) else "越界(%d)" % lo
            d_hi = cal[hi] if hi < len(cal) else "越界(%d)" % hi
            print(hdr % (gname, fld, "%d/%d" % (n, len(insts)), d_lo, d_hi))
            arr = np.asarray(his)
            n_max = int((arr == hi).sum())
            q = [cal[int(x)] if 0 <= int(x) < len(cal) else "?" for x in
                 np.percentile(arr, [10, 50, 90])]
            print("%-12s %-16s 达最大 %d 只 | p90 %s | p50 %s | p10 %s"
                  % ("", "", n_max, q[2], q[1], q[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
