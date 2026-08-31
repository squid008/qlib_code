# -*- coding: utf-8 -*-
"""把米筐 rqalpha others 目录的市值/财务 h5 转成 qlib 格式（features/{code}/{field}.day.bin）。

用法：
    python tools/dump_market_cap.py --src E:/rq/others/market-cap/market_cap.h5 \
        --qlib-dir D:/quant/qlib_code/data/cn_data --field market_cap

说明：
- 源文件为 pandas HDF5（rqalpha others 格式）：MultiIndex[(instrument, datetime)] + 数值列。
- 目标：qlib features 目录下每只股票一个 {field}.day.bin，与现有 close/volume 等字段同格式，
  写入后即可在自定义公式里用 `market_cap`（翻译为 $market_cap）。
- 幂等：目标 .bin 已存在时默认跳过（--force 覆盖重写）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def to_qlib_code(code: str) -> str:
    """rqalpha 代码（000001.XSHE / 600000.XSHG）→ qlib 小写（sz000001 / sh600000）。"""
    upper = code.strip().upper()
    digits = "".join(ch for ch in upper if ch.isdigit())
    if upper.endswith("XSHG"):
        return f"sh{digits}"
    if upper.endswith("XSHE"):
        return f"sz{digits}"
    # 已是 qlib 格式（sh600000 / SH600000）原样小写返回
    return upper.lower()


def read_rqalpha_h5(path: Path) -> pd.DataFrame:
    """读取 rqalpha others 的 pandas HDF5，返回长表 DataFrame(code, date, value)。"""
    import h5py

    with h5py.File(path, "r") as f:
        g = f["data"]
        stocks = np.array([x.decode() if isinstance(x, bytes) else str(x) for x in g["axis1_level0"][:]])
        ts = g["axis1_level1"][:]           # 唯一时间戳（纳秒）
        lab0 = g["axis1_label0"][:]         # 每行对应的股票 code
        lab1 = g["axis1_label1"][:]         # 每行对应的时间戳 code
        blk = g["block0_values"]

        # 支持多列（axis0 是多列时取全部），单列取第 0 列
        if blk.ndim == 2 and blk.shape[1] == 1:
            vals = blk[:, 0]
        else:
            vals = blk
    dates = pd.to_datetime(ts).normalize()
    df = pd.DataFrame(
        {
            "code": stocks[lab0],
            "date": dates[lab1],
            "value": vals,
        }
    )
    return df.dropna(subset=["value"])


def dump_field(src: str, qlib_dir: str, field: str, force: bool = False) -> None:
    src_path = Path(src)
    qlib_dir = Path(qlib_dir)
    cal_path = qlib_dir / "calendars" / "day.txt"
    if not src_path.exists():
        raise SystemExit(f"源文件不存在: {src_path}")
    if not cal_path.exists():
        raise SystemExit(f"qlib 日历不存在: {cal_path}（确认 qlib_dir 正确）")

    calendar = pd.read_csv(cal_path, header=None)[0].astype(str).tolist()
    cal_set = set(calendar)

    print(f"读取 {src_path} ...")
    df = read_rqalpha_h5(src_path)
    df["code"] = df["code"].map(to_qlib_code)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    # 只保留日历内的日期
    df = df[df["date"].isin(cal_set)]
    print(f"有效样本 {len(df):,} 行，股票 {df['code'].nunique()} 只，日期 {df['date'].min()} ~ {df['date'].max()}")

    features_dir = qlib_dir / "features"
    written = skipped = 0
    for code, grp in df.groupby("code"):
        grp = grp.sort_values("date")
        inst_dir = features_dir / code
        bin_path = inst_dir / f"{field}.day.bin"
        if bin_path.exists() and not force:
            skipped += 1
            continue
        # 对齐日历：缺失日期填 NaN（与 qlib dump_bin 一致）
        grp = grp.set_index("date").reindex(calendar)
        start_pos = grp["value"].first_valid_index()  # 首个有效数据日（日历位置）
        if start_pos is None:
            skipped += 1  # 该股票在源数据中无有效值
            continue
        end_pos = grp["value"].last_valid_index()
        date_index = calendar.index(start_pos)        # header：数据在日历中的起始位置
        values = grp["value"].loc[start_pos:end_pos].to_numpy(dtype=np.float64)
        # 格式与 qlib FileFeatureStorage 一致：header(float32) + float32 数据
        out = np.hstack([np.array([date_index], dtype=np.float64), values]).astype("<f")
        inst_dir.mkdir(parents=True, exist_ok=True)
        out.tofile(str(bin_path))
        written += 1
        if written <= 3:
            print(f"  写入 {bin_path}  样本 {values.size}  start_index {date_index}")
    print(f"完成：写入 {written} 只，跳过（已存在/无数据）{skipped} 只")


def main():
    ap = argparse.ArgumentParser(description="米筐 rqalpha others h5 → qlib 字段 bin")
    ap.add_argument("--src", required=True, help="源 h5 文件路径，如 E:/rq/others/market-cap/market_cap.h5")
    ap.add_argument("--qlib-dir", default=r"D:\quant\qlib_code\data\cn_data", help="qlib 数据目录")
    ap.add_argument("--field", default="market_cap", help="目标字段名（写入 {field}.day.bin）")
    ap.add_argument("--force", action="store_true", help="已存在时覆盖重写")
    args = ap.parse_args()
    dump_field(args.src, args.qlib_dir, args.field, args.force)


if __name__ == "__main__":
    main()
