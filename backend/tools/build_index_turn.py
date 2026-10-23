# -*- coding: utf-8 -*-
"""按 **tushare `index_dailybasic.turnover_rate`** 重建**指数**的 `turn` 字段 ✓（2026-10-13 用户定稿 ✓）。

## 为什么必须"整条重建"、不能"续尾" ✗（实测证据 ✓）
`ai_test/cmp_index_daily_1009.py` / 首次对拍（2026-10-13 ✓）实测：本地指数 `turn` 与
`index_dailybasic.turnover_rate` **根本不是一个口径** ✗ —— `sh000852` 重叠 118 天的比值散在
**0.0007~0.0015 且逐日漂移** ✗（`sh000905` 同理 ✓）⇒ 若只把尾巴续上，一条序列里就有**两种口径** ✗
（筹码类因子 `COST/WINNER` 直接吃 `turn` ✓ ⇒ 结论会随区间漂移 ✗）⇒ 只能**整条覆盖** ✓。

## 用户口径（2026-10-13 原话："有源重建，无源为空吧" ✓）
| 指数 | 源 | 动作 |
|---|---|---|
| `sh000300` 沪深300 / `sz399300` | `000300.SH` / `399300.SZ` ✓ | **重建**（原本**没有** turn bin ⇒ 本次新建 ✓）|
| `sh000852` 中证1000 / `sh000905` 中证500 | `000852.SH` / `000905.SH` ✓ | **重建**（整条覆盖旧口径 ✓）|
| `sh000906` 中证800 / `sh000985` 中证全指 | **三个后缀全 0 行** ✗ | **置空**（旧 bin 口径不明 ⇒ 删掉、备份 ✓）|

⚠ 单位 ✓：`turnover_rate` 是**百分数**（`2.5` = 2.5% ✓），与本地 `turn` 既有约定一致 ✓
（`build_turn_field.py`："turn 为**百分数**（1.23 = 1.23%）" ✓）。

## 闸门（不满足就**不写** ✗，交人工 ✓）
① 有效天数 ≥ `MIN_DAYS`（默认 2000 ✓，防"只拿到一小段也敢覆盖" ✗）；
② 越界值（`≤0` 或 `> MAX_TURN`）占比 ≤ 5% ✓（超了就报出样例、拒绝写 ✗）；

用法：
  python backend/tools/build_index_turn.py --qlib-dir data/cn_data2            # dry-run ✓
  python backend/tools/build_index_turn.py --qlib-dir data/cn_data2 --apply    # 写盘（带备份 ✓）
  ... --no-empty-no-source                                                    # 不删无源指数的旧 bin
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dump_tushare_finance import read_bin, token, ts_call  # noqa: E402

# qlib 代码 → tushare 代码（**后缀必须实测挑** ✓：`sh000985` 只认 `000985.CSI` ✓ 但那一个也无源 ✓）
INDEX_SRC = {
    "sh000300": "000300.SH",
    "sz399300": "399300.SZ",
    "sh000852": "000852.SH",
    "sh000905": "000905.SH",
    "sh000906": "000906.SH",
    "sh000985": "000985.CSI",
}
MIN_DAYS = 2000
MAX_TURN = 300.0                # %；指数换手率不可能到 300%（>5% 越界即拒绝写 ✓）


def fetch_year(ts_code: str, year: int, tok: str, rate: float) -> list:
    """按**年**取（一次调用有行数上限 ⇒ 分年最稳 ✓）。返回 [(YYYY-MM-DD, turnover_rate)] ✓。"""
    r = ts_call("index_dailybasic",
                {"ts_code": ts_code, "start_date": "%d0101" % year, "end_date": "%d1231" % year},
                "ts_code,trade_date,turnover_rate", tok)
    time.sleep(rate)
    out = []
    for it in (r.get("data") or {}).get("items") or []:
        d, v = str(it[1]), it[2]
        if d and v is not None:
            out.append(("%s-%s-%s" % (d[:4], d[4:6], d[6:]), float(v)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qlib-dir", required=True, help="★ 必填（防写错数据集 ✗，见 deploy.md）")
    ap.add_argument("--codes", default="", help="默认全部 6 只指数 ✓（逗号分隔）")
    ap.add_argument("--start-year", type=int, default=2005)
    ap.add_argument("--rate", type=float, default=0.25)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-empty-no-source", action="store_true",
                    help="无源指数的旧 bin **不删**（默认删并备份 ✓，用户口径「无源为空」✓）")
    a = ap.parse_args()

    qlib = Path(a.qlib_dir)
    feats = qlib / "features"
    cal = [ln.strip() for ln in open(qlib / "calendars" / "day.txt", encoding="utf-8") if ln.strip()]
    cal_pos = {d: i for i, d in enumerate(cal)}
    end_year = int(cal[-1][:4])
    codes = ([c.strip() for c in a.codes.split(",") if c.strip()] if a.codes else list(INDEX_SRC))
    backup = qlib / "_backup_index_turn_20261013"
    tok = token()
    print("数据集 %s ｜ 日历 %s ~ %s（%d 天）｜ 指数 %d 只 ｜ 模式 %s"
          % (qlib, cal[0], cal[-1], len(cal), len(codes), "**APPLY**" if a.apply else "dry-run"))

    plan, empty = {}, []
    for c in codes:
        ts = INDEX_SRC.get(c)
        if ts is None:
            print("  ⚠ %s 不在已知映射里 ✗ 跳过" % c, flush=True)
            continue
        rows = []
        for y in range(a.start_year, end_year + 1):
            rows += fetch_year(ts, y, tok, a.rate)
        rows.sort()
        old = read_bin(feats / c / "turn.day.bin") if (feats / c).is_dir() else None
        des = ("旧 bin hdr=%d 长度=%d" % (old[0], old[1].size)) if old is not None else "**旧 bin 不存在** ✓"
        if not rows:
            print("  %s / %s：源 **0 行** ✗ ⇒ 无源 ⇒ %s ｜ %s"
                  % (c, ts, "将**置空**（备份后删 ✓）" if not a.no_empty_no_source else "保留旧 bin ✓", des),
                  flush=True)
            empty.append((c, old))
            continue
        days = [d for d, _v in rows]
        vals = np.asarray([v for _d, v in rows], dtype=float)
        bad = int(((vals <= 0) | (vals > MAX_TURN)).sum())
        pos = [cal_pos[d] for d in days if d in cal_pos]
        missed = len(days) - len(pos)
        print("  %s / %s：源 %d 天（%s ~ %s）｜ 中位 %.4f ｜ 越界 %d（%.2f%%）｜ 不在日历 %d ｜ %s"
              % (c, ts, len(rows), days[0], days[-1], float(np.median(vals)),
                 bad, 100.0 * bad / len(vals), missed, des), flush=True)
        if len(rows) < MIN_DAYS:
            print("     ⇒ 有效天数 %d < %d ✗ **拒绝写**（防「只拿到一小段就覆盖」✗）" % (len(rows), MIN_DAYS))
            continue
        if bad > 0.05 * len(vals):
            print("     ⇒ 越界占比过高 ✗ **拒绝写** ｜ 样例 %s"
                  % [(d, v) for d, v in rows if (v <= 0 or v > MAX_TURN)][:5])
            continue
        hdr = min(pos)
        arr = np.full(len(cal) - hdr, np.nan)
        for i, d in enumerate(days):
            j = cal_pos.get(d)
            if j is not None:
                arr[j - hdr] = vals[i]
        if old is not None and old[0] == hdr and old[1].size == arr.size:
            ov = np.isfinite(old[1]) & np.isfinite(arr)
            if ov.any():
                print("     与旧 bin 重叠 %d 天 ｜ 旧/新 比值中位 %.4f（**口径不同 ⇒ 预期不一致 ✓**）"
                      % (int(ov.sum()), float(np.median(old[1][ov] / arr[ov]))), flush=True)
        plan[c] = (hdr, arr)

    print("⇒ 计划：重建 %d 只 %s ｜ 置空 %d 只 %s"
          % (len(plan), sorted(plan), len(empty), sorted(c for c, _ in empty)))
    if not a.apply:
        print("dry-run ✓（`--apply` 才写 ✓；旧 bin 备份到 %s ✓）" % backup)
        return
    backup.mkdir(parents=True, exist_ok=True)
    for c, (hdr, arr) in plan.items():
        d = feats / c
        d.mkdir(parents=True, exist_ok=True)
        p = d / "turn.day.bin"
        if p.exists():
            (backup / c).mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup / c / "turn.day.bin")
        tmp = str(p) + ".tmp"
        np.hstack([np.asarray([hdr], dtype="<f4"), arr.astype("<f4")]).astype("<f4").tofile(tmp)
        os.replace(tmp, p)
        print("  写入 ✓ %s（hdr=%d 长度=%d 有限=%d ✓）"
              % (p, hdr, arr.size, int(np.isfinite(arr).sum())), flush=True)
    for c, old in empty:
        if a.no_empty_no_source or old is None:
            continue
        p = feats / c / "turn.day.bin"
        if p.exists():
            (backup / c).mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup / c / "turn.day.bin")
            os.remove(p)
            print("  置空 ✓ %s（备份 %s ✓）" % (p, backup / c / "turn.day.bin"), flush=True)
    if plan or (empty and not a.no_empty_no_source):
        try:
            b = Path(__file__).resolve().parents[1]
            if str(b) not in sys.path:
                sys.path.insert(0, str(b))
            from app.engine.feature_cache import bump_data_version
            print("数据戳已落 ✓ %s" % bump_data_version(str(qlib), "build_index_turn"))
        except Exception as e:                                                # noqa: BLE001
            print("⚠⚠ 数据戳**没落上**（%r）⇒ 手工清 backend/workdir/feature_cache/ ✗" % e)
    print("完成 ✓ %s" % json.dumps({"rebuilt": sorted(plan), "emptied": sorted(c for c, _ in empty)},
                                  ensure_ascii=False))


if __name__ == "__main__":
    main()
