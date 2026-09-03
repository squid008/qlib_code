# -*- coding: utf-8 -*-
"""把东财"资金流向"日频数据（moneyflow h5，按年×按日组织）转成 qlib feature bin。

用法：
    python tools/dump_moneyflow.py            # 全量 dump 默认 10 个字段
    python tools/dump_moneyflow.py --limit 5  # 冒烟：只处理前 N 只股票（校验格式用）
    python tools/dump_moneyflow.py --force    # 已存在也覆盖重写

说明：
- 源：E:\\rq\\moneyflow\\ 下 mf_{yyyy}.h5（struct：(date i4, sid i4, 11 个字段 f4)，按日+按日 index）
      与 sid.h5（sid → order_book_id，跨年份一致）。
- 目标：qlib features/{code}/{field}.day.bin，与 close/volume/market_cap 同格式
  （header=日历起始行号 + float32 序列），写入后可在特征/公式里以 $mf_* 引用。
- 字段口径（与源数据一致，档位为"互斥分档"）：
      超大单 xl：单笔 ≥50万股 或 ≥100万元
      大单   l ：单笔 ≥10万股 或 ≥20万元 且 <50万股 和 <100万元
      中单   m ：单笔 ≥2万股  或 ≥4万元  且 <10万股 和 <20万元
      小单   s ：单笔 <2万股 和 <4万元
      主力   main = xl + l（互斥档求和）
  change_pct 字段不 dump（qlib 已有 $change）。
- 将来公式语义：L2_pct(0..4)/L2_amo(0..4)（0=main 1=xl 2=l 3=m 4=s）。
- 幂等：目标 .bin 已存在时跳过（--force 覆盖重写）。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

# 需要 dump 的字段（net_前缀去掉，改 mf_amount_* / mf_pct_*；change_pct 不 dump）
# 下标即 L2_pct/L2_amo 的档位编号：0=main 1=xl 2=l 3=m 4=s
MF_FIELDS = [
    ("net_amount_main", "mf_amount_main"),  # 0 主力净额(万)
    ("net_pct_main", "mf_pct_main"),        # 0 主力净占比(%)
    ("net_amount_xl", "mf_amount_xl"),      # 1 超大单净额(万)
    ("net_pct_xl", "mf_pct_xl"),            # 1 超大单净占比(%)
    ("net_amount_l", "mf_amount_l"),        # 2 大单净额(万)
    ("net_pct_l", "mf_pct_l"),              # 2 大单净占比(%)
    ("net_amount_m", "mf_amount_m"),        # 3 中单净额(万)
    ("net_pct_m", "mf_pct_m"),              # 3 中单净占比(%)
    ("net_amount_s", "mf_amount_s"),        # 4 小单净额(万)
    ("net_pct_s", "mf_pct_s"),              # 4 小单净占比(%)
]
SRC_FIELDS = [s for s, _ in MF_FIELDS]


def to_qlib_code(code: str) -> str:
    """order_book_id（000001.XSHE / 600000.XSHG）→ qlib 小写（sz000001 / sh600000）。"""
    upper = code.strip().upper()
    digits = "".join(ch for ch in upper if ch.isdigit())
    if upper.endswith("XSHG"):
        return f"sh{digits}"
    if upper.endswith("XSHE"):
        return f"sz{digits}"
    return upper.lower()


def load_all_years(src_root: str, years: range):
    """把所有年份 mf h5 的 data 纵向拼接成一个结构化数组。"""
    parts = []
    for y in years:
        p = os.path.join(src_root, f"mf_{y}.h5")
        if not os.path.exists(p):
            print(f"  跳过缺失文件: {p}")
            continue
        with h5py.File(p, "r") as f:
            parts.append(f["data"][:])
    if not parts:
        raise SystemExit(f"{src_root} 下没有 mf_*.h5 数据")
    return np.concatenate(parts)


def dump(src_root: str, qlib_dir: str, force: bool = False, limit: int | None = None) -> None:
    src_root = Path(src_root)
    qlib_dir = Path(qlib_dir)
    cal_path = qlib_dir / "calendars" / "day.txt"
    if not cal_path.exists():
        raise SystemExit(f"qlib 日历不存在: {cal_path}（确认 qlib-dir 正确）")

    calendar = pd.read_csv(cal_path, header=None)[0].astype(str).tolist()
    # qlib 日历的 int(YYYYMMDD) 数组（已升序），用于按 date 二分定位日历行号
    cal_int = np.array([int(d.replace("-", "")) for d in calendar], dtype=np.int64)
    cal_pos = {int(d.replace("-", "")): i for i, d in enumerate(calendar)}

    # sid 映射
    with h5py.File(os.path.join(src_root, "sid.h5"), "r") as f:
        sid_list = [s.decode() for s in f["sid"][:]]
    print(f"sid 总数: {len(sid_list)}")

    # 读全量数据（按 sid 内部无序 → 排序）
    print("读取全部年份 data ...")
    arr = load_all_years(str(src_root), range(2016, 2027))
    print(f"总行数: {len(arr):,}   date {arr['date'].min()} ~ {arr['date'].max()}")

    # 过滤：moneyflow 中有但不在 qlib 日历内的日期（理论上 A 股日历一致，防御性处理）
    in_cal = np.isin(arr["date"], list(cal_pos.keys()))
    if not in_cal.all():
        n = int((~in_cal).sum())
        print(f"警告: {n:,} 行日期不在 qlib 日历内，已剔除（可能有节假日口径差异）")
        arr = arr[in_cal]

    # 按 (sid, date) 排序：先 date 后 sid（lexsort 末键为主键）
    print("排序 (sid, date) ...")
    order = np.lexsort((arr["date"], arr["sid"]))
    arr = arr[order]
    sid_arr = arr["sid"]

    features_dir = qlib_dir / "features"
    written = skipped = 0
    n_sid = len(sid_list) if limit is None else min(limit, len(sid_list))
    print(f"开始 dump {n_sid} 只股票 × {len(MF_FIELDS)} 字段 ...")

    # 每个 sid 的起始/结束位置（sid 已排序 → 用 searchsorted 定位）
    sid_bounds = np.searchsorted(sid_arr, np.arange(n_sid))
    for sid in range(n_sid):
        lo = sid_bounds[sid]
        hi = sid_bounds[sid + 1] if sid + 1 < len(sid_bounds) else len(sid_arr)
        if lo >= hi:
            skipped += 1
            continue
        sub = arr[lo:hi]  # 该 sid 按 date 升序
        code = to_qlib_code(sid_list[sid])
        if code.startswith("bj"):  # 源数据无北交所
            skipped += 1
            continue

        pos = cal_pos_sub(sub["date"], cal_pos)  # 每个交易日对应的日历行号
        if len(pos) == 0:
            skipped += 1
            continue
        start_idx = int(pos[0])
        end_idx = int(pos[-1])
        length = end_idx - start_idx + 1
        offset = pos - start_idx

        inst_dir = features_dir / code
        for src_field, qlib_field in MF_FIELDS:
            bin_path = inst_dir / f"{qlib_field}.day.bin"
            if bin_path.exists() and not force:
                skipped += 1
                continue
            values = np.full(length, np.nan, dtype=np.float32)
            values[offset] = sub[src_field].astype(np.float32)
            # qlib FileFeatureStorage 格式：header(float32=日历行号) + float32 序列
            out = np.hstack([np.array([start_idx], dtype=np.float64), values]).astype("<f")
            inst_dir.mkdir(parents=True, exist_ok=True)
            out.tofile(str(bin_path))
            written += 1
        if written <= 30 and (limit is None or limit <= 5):
            print(f"  写入 {code}  {len(sub):,} 行  start_index {start_idx}")

    print(f"完成：写入 {written} 个字段文件，跳过（已存在/无数据）{skipped} 个")


def cal_pos_sub(dates: np.ndarray, cal_pos: dict) -> np.ndarray:
    """把 moneyflow date(int) 数组映射到 qlib 日历行号；缺日返回空（防御）。"""
    try:
        return np.array([cal_pos[int(d)] for d in dates], dtype=np.int64)
    except KeyError:
        # 极端情况：个别日期不在日历 → 过滤
        out = [cal_pos[int(d)] for d in dates if int(d) in cal_pos]
        return np.array(out, dtype=np.int64)


def main():
    ap = argparse.ArgumentParser(description="资金流向 h5 → qlib 字段 bin（mf_* 前缀）")
    ap.add_argument("--src-root", default=r"E:\rq\moneyflow", help="moneyflow h5 目录")
    ap.add_argument("--qlib-dir", default=r"D:\quant\qlib_code\data\cn_data", help="qlib 数据目录")
    ap.add_argument("--force", action="store_true", help="已存在时覆盖重写")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 只股票（冒烟用）")
    args = ap.parse_args()
    dump(args.src_root, args.qlib_dir, force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
