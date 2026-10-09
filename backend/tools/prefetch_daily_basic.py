# -*- coding: utf-8 -*-
"""预取 tushare `daily_basic` 的 `total_mv`（按**交易日**一次拿全市场 ✓）—— 2026-10-09。

## 为什么需要
`dump_tushare_finance.py` 的 `fin_pe_ttm = 当日总市值 ÷ 归母净利TTM`、`fin_pb = 总市值 ÷ 归母权益` ✓，
市值来源是"**本地 `market_cap` bin 优先** ✓，只有 `--mc-tushare` 才用 tushare 续更晚的日期" ✓。
之前几次 FINANCE 跑的是 `--force` **没带** `--mc-tushare` ✗ ⇒ 市值停更 ⇒ **PE/PB 在最近日期为空** ✗
（实测 002487：`FINANCE(1)/(2)` 全 NaN ✗，其余 3~10 都有值 ✓ ⇒ 带 FINANCE>0 的公式恒假 ✗）。

## 本工具做什么
把 `daily_basic(trade_date=d)`（**一次调用返回全市场** ✓）的结果写进它用的那份缓存 ✓
（键 = `"<YYYYMMDD>|<ts_code>"`，与 `_mc_cache` 同格式 ✓，单位 万元 ⇒ ×1e4 转元 ✓）
⇒ 之后跑 `dump_tushare_finance.py --force --mc-tushare` 时**全部命中缓存**、不需要 17 万次 API 调用 ✓。

用法：python tools/prefetch_daily_basic.py [--days-from 2026-08-24] [--days-to 2026-10-09] [--apply]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_finance import load_calendar                                    # noqa: E402
from dump_tushare_finance import _mc_cache, to_qlib_code, token, ts_call   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description="按交易日预取 daily_basic.total_mv 进缓存 ✓")
    ap.add_argument("--qlib-dir", default=os.environ.get("QLIB_PROVIDER_URI")
                    or os.path.join(ROOT, "data", "cn_data"))
    ap.add_argument("--days-from", default="2026-08-24")
    ap.add_argument("--days-to", default="")
    ap.add_argument("--rate", type=float, default=0.31)
    ap.add_argument("--apply", action="store_true", help="真的写缓存（默认只看要拉哪些日 ✓）")
    args = ap.parse_args()

    from pathlib import Path
    cal, _ = load_calendar(Path(args.qlib_dir))       # ⚠ load_calendar 要 Path，不是 str ✗
    days = [d for d in cal if d >= args.days_from and (not args.days_to or d <= args.days_to)]
    print("要预取 %d 个交易日：%s ~ %s" % (len(days), days[0] if days else "-", days[-1] if days else "-"))
    if not days:
        return
    if not args.apply:
        print("dry-run（加 --apply 才真的拉取并写缓存 ✓；每个交易日 1 次 API 调用 ✓）")
        return

    cache = _mc_cache()
    tok = token()
    added = 0
    for d in days:
        key = d.replace("-", "")
        r = ts_call("daily_basic", {"trade_date": key}, "ts_code,total_mv", tok)
        items = (r.get("data") or {}).get("items") or []
        n = 0
        for it in items:
            if len(it) >= 2 and it[1] is not None:
                cache["%s|%s" % (key, it[0])] = float(it[1]) * 1e4
                n += 1
        added += n
        print("   %s：写入 %d 只（累计 %d 条）" % (d, n, added), flush=True)
        time.sleep(args.rate)
    _mc_cache(save=cache)
    print("完成：缓存新增 %d 条 ⇒ 接着跑 python tools/dump_tushare_finance.py --force --mc-tushare ✓" % added)


if __name__ == "__main__":
    main()
