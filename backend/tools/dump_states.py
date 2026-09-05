# -*- coding: utf-8 -*-
"""把米筐 rqalpha bundle 的交易所涨跌停价与 ST 状态 dump 成 qlib bin。

字段（features/{code}/xxx.day.bin）：
  - limit_up / limit_down ：交易所口径当日涨跌停价（真实价，整分）。
    ST 股 5%、退市整理 10%、创业板/科创板 20%、北交所 30% 均由交易所直接给价，
    无需按板块/名称倒推规则。用于涨停判定 close >= limit_up - 1e-6（替代倒推法，
    倒推法不识别 ST 5% 涨停会漏判）。
  - is_st ：当日是否处于 ST/*ST/退市整理（0/1）。源 bundle/st_stock_days.h5。

源：
  - E:/rq/bundle/stocks.h5        每 key=代码(000001.XSHE)：datetime(i8 YYYYMMDDHHMMSS)
                                  + limit_up/limit_down 等 float64（2005-01 起）
  - E:/rq/bundle/st_stock_days.h5 每 key=代码：int32 日期数组 YYYYMMDD（ST 覆盖交易日）

说明：
- 只 dump 到 qlib 现有 features 目录里已有的股票（不新建股票目录），与 close/volume 同集。
- bin 格式与 qlib FileFeatureStorage 一致：header(float64 日历起始位) + float32 数据，
  按 qlib calendars/day.txt 对齐（缺失日 NaN），每字段取各自 first~last 有效段。
- 幂等：已存在且非 --force 时跳过。
用法：
  python tools/dump_states.py [--qlib-dir D:/quant/qlib_code/data/cn_data] [--force]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

FIELDS = ("limit_up", "limit_down", "is_st")


def qlib_to_rq_code(code: str) -> str:
    """qlib 小写 sz000001/sh600000/bj430047 → bundle 代码 000001.XSHE/600000.XSHG。"""
    c = code.strip().lower()
    digits = "".join(ch for ch in c if ch.isdigit())
    if c.startswith("sz"):
        return f"{digits}.XSHE"
    return f"{digits}.XSHG"


def _fmt_date(ts_int) -> str:
    """datetime int(YYYYMMDDHHMMSS) → 'YYYY-MM-DD'。"""
    s = str(int(ts_int)).ljust(14, "0")[:8]
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _write_bin(bin_path: Path, calendar, date_idx, values: np.ndarray, field: str) -> bool:
    """对齐 qlib 日历写 bin。date_idx/values 为源数据序列（含 NaN），返回是否写入。"""
    ser = pd.Series(values, index=date_idx)
    g = ser.reindex(calendar)          # 对齐到 qlib 交易日（缺失日 NaN）
    first = g.first_valid_index()
    last = g.last_valid_index()
    if first is None or last is None:
        return False
    i0 = calendar.get_loc(first)
    i1 = calendar.get_loc(last)
    vals = g.iloc[i0:i1 + 1].to_numpy(dtype=np.float64)
    out = np.hstack([np.array([float(i0)], dtype=np.float64), vals]).astype("<f")
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    out.tofile(str(bin_path))
    return True


def dump_states(qlib_dir: str, force: bool = False) -> None:
    qlib_dir = Path(qlib_dir)
    src_root = os.environ.get("RQ_BUNDLE_PATH", r"E:/rq/bundle")
    stocks_path = Path(src_root) / "stocks.h5"
    st_path = Path(src_root) / "st_stock_days.h5"
    cal_path = qlib_dir / "calendars" / "day.txt"
    if not stocks_path.exists():
        raise SystemExit(f"源文件不存在: {stocks_path}（可用环境变量 RQ_BUNDLE_PATH 指定）")
    if not cal_path.exists():
        raise SystemExit(f"qlib 日历不存在: {cal_path}")

    calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(cal_path, header=None)[0].astype(str)))
    print(f"日历 {calendar[0].date()} ~ {calendar[-1].date()}，共 {len(calendar)} 交易日")

    # 目标股票集 = 现有 features 目录里已有行情字段的股票（不新建目录）
    feat_root = qlib_dir / "features"
    target = {}
    for code in sorted(os.listdir(feat_root)):
        d = feat_root / code
        if not d.is_dir():
            continue
        if not any((d / f).exists() for f in ("close.day.bin", "factor.day.bin")):
            continue
        rq = qlib_to_rq_code(code)
        target[rq] = code
    print(f"目标股票 {len(target)} 只")

    done = skipped = nofield = 0
    with h5py.File(stocks_path, "r") as fs, h5py.File(st_path, "r") as fst:
        keys = set(fs.keys())
        for rq_code, qlib_code in sorted(target.items()):
            if rq_code not in keys:
                skipped += 1
                continue
            arr = fs[rq_code]
            if arr.shape[0] == 0 or "limit_up" not in arr.dtype.names:
                skipped += 1
                continue
            dts = arr["datetime"]
            dates = [_fmt_date(v) for v in dts]
            date_idx = pd.DatetimeIndex(pd.to_datetime(dates))
            limit_up = np.asarray(arr["limit_up"], dtype=np.float64)
            limit_down = np.asarray(arr["limit_down"], dtype=np.float64)

            # ST 状态集合（int YYYYMMDD），无记录视为从不 ST
            st_set = set()
            if rq_code in fst:
                sa = fst[rq_code]
                if sa.shape[0] > 0:
                    st_set = set(map(int, np.asarray(sa).ravel()))
            is_st = np.array([1.0 if int(d.replace("-", "")) in st_set else 0.0 for d in dates],
                             dtype=np.float64)

            inst_dir = feat_root / qlib_code
            wrote_any = False
            for field, vals in zip(FIELDS, (limit_up, limit_down, is_st)):
                bin_path = inst_dir / f"{field}.day.bin"
                if bin_path.exists() and not force:
                    continue
                if _write_bin(bin_path, calendar, date_idx, vals, field):
                    wrote_any = True
                else:
                    nofield += 1
            if wrote_any:
                done += 1
            else:
                skipped += 1
            if done % 200 == 0 and done:
                print(f"  ... {done} 只完成（skip {skipped}）", flush=True)
    print(f"完成：写入 {done} 只，跳过 {skipped} 只（无有效段 {nofield} 个字段）")


def _default_qlib_dir() -> str:
    env = os.environ.get("QLIB_PROVIDER_URI")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "cn_data")


def main():
    ap = argparse.ArgumentParser(description="rqalpha bundle 涨跌停价/ST 状态 → qlib bin")
    ap.add_argument("--qlib-dir", default=_default_qlib_dir(),
                    help="qlib 数据目录（默认 QLIB_PROVIDER_URI 或 data/cn_data）")
    ap.add_argument("--force", action="store_true", help="已存在时覆盖重写")
    args = ap.parse_args()
    dump_states(args.qlib_dir, args.force)


if __name__ == "__main__":
    main()
