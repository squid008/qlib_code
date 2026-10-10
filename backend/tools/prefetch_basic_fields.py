# -*- coding: utf-8 -*-
"""按**天**预取 tushare `daily_basic` 的 5 个字段（用户 2026-10-10：总市值/流通市值/总股本/流通股本 ✓）

为什么**按天**而不是按股票 ✗：`daily_basic` 单次返回有**行数上限**（6000 行 ✓），
按股票拉全历史（平安银行 6455 行 ✗）会被**静默截断** ✗✗；按天拉每次只有 ~5000 只 ⇒ 安全 ✓。

落盘：`data/tushare_cache/basic/<YYYYMMDD>.json` = `{ts_code: {total_mv, circ_mv, total_share, float_share, free_share}}`
  · 单位：`total_mv/circ_mv` = **元**（已 ×1e4，tushare 原值是万元 ✓）；`total_share/float_share/free_share` = **股**（×1e4 ✓）
  · **可断点续跑** ✓：已有的非空文件直接跳过 ✓（`--refresh` 才重取 ✓）

⚠ 失败必须响 ✗（用户 2026-10-10）：重试由 `ts_call` 负责（网络类错误也退避重试 ✓）；这里把
  **重试耗尽**的天记进台账 ✓、进度行显示 ✓、收尾写 `ai_test/basic_failures.json` ✓ —— 绝不静默跳过 ✗。

用法：python tools/prefetch_basic_fields.py [--start 20000104] [--end 20261009] [--rate 0.33] [--refresh]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dump_tushare_finance as M                                          # noqa: E402
from dump_finance import load_calendar                                    # noqa: E402

CACHE = ROOT / "data" / "tushare_cache" / "basic"
FIELDS = "ts_code,trade_date,total_mv,circ_mv,total_share,float_share,free_share"
LIVE = ("total_mv", "circ_mv", "total_share", "float_share", "free_share")


def main():
    ap = argparse.ArgumentParser(description="按天预取 daily_basic 5 字段（可断点续跑 ✓）")
    ap.add_argument("--qlib-dir", default=str(ROOT / "data" / "cn_data2"), help="用它的日历定日期范围 ✓")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--rate", type=float, default=0.33)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--order", choices=("desc", "asc"), default="desc",
                    help="取数顺序：desc=**由近及远**（默认 ✓ —— 万一中断，最常用的近期数据先到手 ✓）；"
                         "asc=由远及近")
    args = ap.parse_args()

    qlib = Path(args.qlib_dir)
    if M._declared_convention(qlib) not in ("tushare", ""):
        raise SystemExit("✗ 目标 %s 不是 tushare 口径 ⇒ 不接受（本工具只产 tushare 数据 ✓）" % qlib)
    cal, _ = load_calendar(qlib)
    start = args.start or cal[0].replace("-", "")
    end = args.end or cal[-1].replace("-", "")
    days = [d.replace("-", "") for d in cal if start <= d.replace("-", "") <= end]
    if args.order == "desc":
        days = days[::-1]              # ★ 由近及远 ✓（中断也先保住近期数据 ✓）
    CACHE.mkdir(parents=True, exist_ok=True)
    todo = []
    for d8 in days:
        p = CACHE / ("%s.json" % d8)
        if p.exists() and not args.refresh:
            try:
                if json.loads(p.read_text(encoding="utf-8")):
                    continue                                  # 已有非空 ⇒ 跳过 ✓（断点续跑 ✓）
            except Exception:                                     # noqa: BLE001
                pass                                              # 坏文件 ⇒ 重取 ✓（不静默 ✗）
        todo.append(d8)
    print("日期范围 %s ~ %s（%d 天）｜已有 %d 天 ✓｜待取 **%d 天**｜rate=%.2fs"
          % (start, end, len(days), len(days) - len(todo), len(todo), args.rate), flush=True)

    tok = M.token()
    done = empty = 0
    t0 = time.time()
    for i, d8 in enumerate(todo, 1):
        try:
            r = M.ts_call("daily_basic", {"trade_date": d8}, FIELDS, tok)
            items = ((r or {}).get("data") or {}).get("items") or []
        except Exception as e:                                    # noqa: BLE001
            M.FAILED.append({"day": d8, "error": "%s: %s" % (type(e).__name__, e)})
            print("  ✗ %s 取数失败（已记台账 ✓，**不是**没数据 ✗）：%s: %s"
                  % (d8, type(e).__name__, e), flush=True)
            time.sleep(args.rate)
            continue
        if not items:
            empty += 1
            (CACHE / ("%s.json" % d8)).write_text("{}", encoding="utf-8")   # 空也落盘 ⇒ 下次不再问 ✓
        else:
            # ⚠ 单位换算（tushare 原值：市值=万元、股本=万股 ✓ ⇒ ×1e4 ⇒ 元 / 股 ✓）
            out = {}
            for it in items:
                row = dict(zip(("ts_code", "trade_date") + LIVE, it))
                out[row["ts_code"]] = {k: (float(row[k]) * 1e4 if row.get(k) is not None else None)
                                       for k in LIVE}
            (CACHE / ("%s.json" % d8)).write_text(json.dumps(out), encoding="utf-8")
            done += 1
        time.sleep(args.rate)
        if i % 20 == 0 or i == len(todo):
            el = time.time() - t0
            print("  进度 %d/%d  有数据 %d / 空 %d / **失败 %d** / 重试 %d  %.0fs（%.2fs/天）  剩余 %.0fs"
                  % (i, len(todo), done, empty, len(M.FAILED), M.TS_RETRIES,
                     el, el / i, el / i * (len(todo) - i)), flush=True)

    print("完成：有数据 %d 天 / 空 %d 天；tushare 调用 %d 次、重试 %d 次"
          % (done, empty, M.TS_CALLS, M.TS_RETRIES))
    if M.FAILED:
        fp = ROOT / "ai_test" / "basic_failures.json"
        fp.write_text(json.dumps({"n": len(M.FAILED), "items": M.FAILED,
                                  "note": "重试耗尽仍失败的天（**不是**没数据 ✗）；重跑本工具会自动补 ✓"},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
        print("⚠⚠ **真失败 %d 天**（不是没数据 ✗）⇒ 台账 %s ✓；重跑自动补 ✓" % (len(M.FAILED), fp))
    else:
        print("✓ 无失败（调用 %d 次 / 重试 %d 次）" % (M.TS_CALLS, M.TS_RETRIES))


if __name__ == "__main__":
    main()
