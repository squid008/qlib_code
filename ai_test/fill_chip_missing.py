# -*- coding: utf-8 -*-
"""补物化：把缺 `chip_*` 的股票补齐（2026-09-18 用户要求）。

背景：`chip_cost_{5,30,75,95}` / `chip_win_{close,high,low}` 各 6075 份，股票总数 6142
⇒ **缺 67 只**（多为退市/长期停牌股 ✓，其中 `sh990018` 连 `close.day.bin` 都没有 ✗）。
`chip_store.materialize` 的落盘段（chip_store.py:132-140）会**自动把日期轴对齐到 `$close`**
（读 close 的 header + 行数 ✓）⇒ 只需给出**足够宽**的 `[start_time, end_time]` 覆盖全历史 ✓。
`overwrite=False` ⇒ **只写缺失文件**，已有的一律跳过 ✓（安全 ✓）。
"""
import os
import sys

sys.path.insert(0, r"d:\quant\qlib_code\backend")

FEATURES = r"d:\quant\qlib_code\data\cn_data\features"
FIELDS = ("chip_cost_5", "chip_cost_30", "chip_cost_75", "chip_cost_95",
          "chip_win_close", "chip_win_high", "chip_win_low")


def main():
    # ⚠ 必须走统一入口（带 custom_ops ✓，且进程内只 init 一次 ✓）：
    #   见 `app/services/qlib_runtime.py:25`（`qlib_engine._ensure_qlib_init` 也委托它 ✓）。
    from app.services.qlib_runtime import ensure_qlib_init
    ensure_qlib_init()
    print("qlib init ✓")

    from app.factors.chip_store import materialize

    allinst = sorted(n for n in os.listdir(FEATURES)
                     if os.path.isdir(os.path.join(FEATURES, n)))
    miss = [n for n in allinst
            if not os.path.exists(os.path.join(FEATURES, n, "chip_cost_95.day.bin"))]
    todo = [n for n in miss if os.path.exists(os.path.join(FEATURES, n, "close.day.bin"))]
    skip = [n for n in miss if n not in todo]
    print("缺失 %d 只；可补（有 close）%d 只；无基础数据跳过 %d 只 %s"
          % (len(miss), len(todo), len(skip), skip))

    def _pc(*a):
        # `chip_store.materialize` 只回传 1 个字符串（`progress_cb("物化 %s：%d 只")` ✓）
        print("      %s" % (a[0] if a else ""), flush=True)

    # 起点给到 1990（早于任何数据 ✓）；落盘会 reindex 到各股自己的 close 轴 ✓
    r = materialize(todo, start_time="1990-01-01", end_time="2026-12-31",
                    fields=FIELDS, read_start="1990-01-01",
                    overwrite=False, progress_cb=_pc)
    print("写入结果:", r)

    # 核对：补齐后应为 6075 + len(todo)，且与 close 同轴 ✓
    import numpy as np
    ok = bad = 0
    for n in todo:
        p = os.path.join(FEATURES, n, "chip_cost_95.day.bin")
        c = os.path.join(FEATURES, n, "close.day.bin")
        if not (os.path.exists(p) and os.path.exists(c)):
            continue
        a = np.fromfile(p, dtype="<f4")
        b = np.fromfile(c, dtype="<f4")
        if a.size == b.size and a[0] == b[0]:
            ok += 1
        else:
            bad += 1
            print("  ✗ 不同轴 %s: chip n=%d f=%d | close n=%d f=%d"
                  % (n, a.size - 1, int(a[0]), b.size - 1, int(b[0])))
    print("同轴核对: ok=%d  bad=%d" % (ok, bad))
    print("done")


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归，见 2026-09-18 教训 ✓）
    main()
