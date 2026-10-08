# -*- coding: utf-8 -*-
"""物化横向统计字段（`BLOCKSETNUM` / `INSUM` → `mkt_*` 字段 bin，2026-10-09）。

用法（在 backend 目录下）：
    python tools/materialize_market.py                 # 自动扫描公式库，物化用到的横向统计
    python tools/materialize_market.py --dry-run       # 只列出"会物化什么"，不写盘
    python tools/materialize_market.py --overwrite     # 全量重写（改了口径/改了被调公式后必须这样跑）
    python tools/materialize_market.py --start 2015-01-01 --end 2026-08-21
    python tools/materialize_market.py --text-file ai_test/纯度公式.txt   # 临时公式（未保存）也一起扫

为什么要单独一条命令：横向统计（跨股票）在 qlib 侧无法现算 ⇒ 只能**先物化**成
`features/{code}/mkt_*.day.bin`（同日全市场同值）。详见 `app/factors/market_stat.py` 顶部说明。

⚠ 被调公式（如 `IS_GOLD_PIT`）必须是**已保存的公式**（INSUM 按公式名引用它）；
   改了那个公式或改了板块口径 ⇒ 重跑本脚本（`--overwrite`）✓，物化结束会刷新
   `features/_market_meta.json` 口径戳（`/api/version` 里能看到）✓。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.factors import market_stat  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="物化 BLOCKSETNUM/INSUM 横向统计字段（mkt_*）")
    ap.add_argument("--start", default=market_stat.DEFAULT_START, help="物化起始日（默认 2010-01-01）")
    ap.add_argument("--end", default=None, help="物化结束日（默认 = 数据日历最后一天）")
    ap.add_argument("--overwrite", action="store_true", help="已存在也覆盖重写（改口径后必跑）")
    ap.add_argument("--dry-run", action="store_true", help="只列出规格，不写盘")
    ap.add_argument("--text-file", action="append", default=[],
                    help="额外扫描的公式文本文件（可多次；用于还没保存的公式）")
    args = ap.parse_args()

    extra = []
    for p in args.text_file:
        try:
            with open(p, "r", encoding="utf-8") as f:
                extra.append(f.read())
            print("附加扫描：%s" % p, flush=True)
        except OSError as e:
            print("[warn] 读不了 %s：%s" % (p, e), flush=True)

    specs = market_stat.discover_specs(extra_texts=extra)
    print("发现横向统计用法 %d 条：" % len(specs), flush=True)
    for sp in specs:
        print("  · %-46s → %s" % (repr(sp), sp.field), flush=True)
    if not specs:
        print("（公式库里没有 BLOCKSETNUM/INSUM ⇒ 无事可做）", flush=True)
        return 0
    if args.dry_run:
        print("\n--dry-run：未写盘", flush=True)
        return 0

    t0 = time.time()
    out = market_stat.materialize(specs, start_time=args.start, end_time=args.end,
                                  overwrite=args.overwrite,
                                  progress_cb=lambda m: print("  " + m, flush=True))
    print("\n完成：用时 %.0fs" % (time.time() - t0), flush=True)
    for k, v in out.items():
        print("  %-42s %d 只" % (k, v), flush=True)
    print("口径戳：%s" % market_stat.market_meta_state().get("message"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
