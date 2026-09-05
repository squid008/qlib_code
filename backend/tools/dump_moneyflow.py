# -*- coding: utf-8 -*-
"""把 A 股资金流数据源转成 qlib feature bin（mf_* 前缀）。

用法：
    python tools/dump_moneyflow.py            # 全量 dump（45 个字段）
    python tools/dump_moneyflow.py --limit 5  # 冒烟：只处理前 N 只股票
    python tools/dump_moneyflow.py --force    # 已存在也覆盖重写
    python tools/dump_moneyflow.py --src-root E:\\rq\\moneyflow3 --qlib-dir D:/.../cn_data

源（推荐 moneyflow3，E:\\rq\\moneyflow3\\，字段说明见该目录 FIELDS.md）：
    mf_{yyyy}.h5 的 /data：
        [date i4, sid i4] +
        4 档（小 s / 中 m / 大 l / 特大 x）买入/卖出的量(bq/sq，手)与金额(ba/sa，万元) +
        nq/na（净流入量/额）+ change_pct（未导出，qlib $change 已有）+ imputed（反推位掩码）
    2013-01-04 ~ 2026-08-19，4 档买卖逐行严格平衡，无 NaN。

目标：qlib features/{code}/{field}.day.bin（header=日历起始行号 + float32 序列），
写入后可在特征/公式里以 $mf_* 引用。输出 45 个字段：

  兼容字段（10 个，数值 = 买-卖 派生，与 moneyflow2 的 net_amount_* 完全一致）：
    mf_amount_<main|xl|l|m|s>   档位净额（万元）      = 买入额 - 卖出额
    mf_pct_<main|xl|l|m|s>      档位净占比（%）       = 净额 / 当日总成交额 × 100

  买卖方向字段（20 个，moneyflow3 特有，L2_AMO/L2_PCT 的 b/s 参数用）：
    mf_amount_<档>_b / _s       档位买入额 / 卖出额（万元）
    mf_pct_<档>_b / _s          档位买入占比 / 卖出占比（%）

  量字段（15 个，moneyflow3 特有，L2_VOL 的 b/s 参数用；单位=手）：
    mf_vol_<main|xl|l|m|s>      档位净流入量（手）    = 买量 - 卖量
    mf_vol_<档>_b / _s          档位买入量 / 卖出量（手）

口径：
  - 档位映射：源 x→xl（超大）、l→l（大）、m→m（中）、s→s（小）；main = xl + l
  - 当日总成交额 turnover = 4 档买之和（moneyflow3 每行买卖平衡，等价卖之和）
  - 所有 pct 统一以 turnover 为分母（与旧 net_pct 同口径）→
      mf_amount_main == mf_amount_main_b - mf_amount_main_s
      mf_pct_main    == mf_pct_main_b    - mf_pct_main_s    （float32 舍入量级）
  - 净额计算顺序沿用 moneyflow2 构建路径（先算各档买-卖，main=xl_net+l_net），保证兼容值逐位一致

公式语义（translator）：
    L2_AMO(n) / L2_PCT(n)       -> 净额 / 净占比（n=0 主力 1 超大 2 大 3 中 4 小）
    L2_AMO(n,b|s) / L2_PCT(n,b|s) -> 买入 / 卖出方向

旧源兼容：若源 h5 的 /data 直接含 net_amount_*（moneyflow / moneyflow2，已折算净额），
仅导出兼容的 10 个净额字段（无买卖方向可派生，打印一次警告）。
"""
from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

# ---- 输出字段 ----
TIERS = ["main", "xl", "l", "m", "s"]
SRC_TIER = {"xl": "x", "l": "l", "m": "m", "s": "s"}  # main 由 xl+l 派生

NET_DST = []   # 10 个兼容字段（金额/占比）
DIR_DST = []   # 20 个方向字段（金额/占比 买卖）
VOL_DST = []   # 30 个量字段（手）：5 净量 + 20 买卖方向量（moneyflow3 源 _bq/_sq）
for t in TIERS:
    NET_DST += [f"mf_amount_{t}", f"mf_pct_{t}"]
for t in TIERS:
    DIR_DST += [
        f"mf_amount_{t}_b", f"mf_amount_{t}_s",
        f"mf_pct_{t}_b", f"mf_pct_{t}_s",
    ]
for t in TIERS:
    VOL_DST += [f"mf_vol_{t}"]  # 净流入量(手) = 买量 − 卖量
for t in TIERS:
    VOL_DST += [f"mf_vol_{t}_b", f"mf_vol_{t}_s"]
ALL_DST = NET_DST + DIR_DST + VOL_DST

# 旧源（已折算净额）字段名 -> 目标字段名
LEGACY_MAP = {
    "net_amount_main": "mf_amount_main", "net_pct_main": "mf_pct_main",
    "net_amount_xl": "mf_amount_xl", "net_pct_xl": "mf_pct_xl",
    "net_amount_l": "mf_amount_l", "net_pct_l": "mf_pct_l",
    "net_amount_m": "mf_amount_m", "net_pct_m": "mf_pct_m",
    "net_amount_s": "mf_amount_s", "net_pct_s": "mf_pct_s",
}


def to_qlib_code(code: str) -> str:
    """order_book_id（000001.XSHE / 600000.XSHG）→ qlib 小写（sz000001 / sh600000）。"""
    upper = code.strip().upper()
    digits = "".join(ch for ch in upper if ch.isdigit())
    if upper.endswith("XSHG"):
        return f"sh{digits}"
    if upper.endswith("XSHE"):
        return f"sz{digits}"
    return upper.lower()


def detect_source(src_root: str) -> str:
    """检测源 h5 结构：'new'=moneyflow3（含 x_ba 等原始买卖），'legacy'=已折算 net 字段。"""
    files = sorted(glob.glob(os.path.join(src_root, "mf_*.h5")))
    if not files:
        raise SystemExit(f"{src_root} 下没有 mf_*.h5 数据")
    with h5py.File(files[0], "r") as f:
        names = f["data"].dtype.names or ()
    if "x_ba" in names and "s_ba" in names:
        return "new"
    if "net_amount_main" in names:
        return "legacy"
    raise SystemExit(f"无法识别的源字段结构：{src_root}（需要 x_ba..s_sa 或 net_amount_*）")


def load_all_years(src_root: str):
    """把所有年份 mf h5 的 data 纵向拼接。"""
    parts = []
    for p in sorted(glob.glob(os.path.join(src_root, "mf_*.h5"))):
        with h5py.File(p, "r") as f:
            parts.append(f["data"][:])
    if not parts:
        raise SystemExit(f"{src_root} 下没有 mf_*.h5 数据")
    return np.concatenate(parts)


def _derive_new(sub):
    """moneyflow3 结构 → {目标字段: float32 数组}（60 列：金额/占比 30 + 量 30）。

    计算顺序与 moneyflow2 构建路径一致，保证兼容 10 字段逐位相同：
    main_net = (x_ba-x_sa) + (l_ba-l_sa)，而非 (x_ba+l_ba)-(x_sa+l_sa)。
    量（手）字段由源 _bq/_sq（买量/卖量）派生，与金额同构。
    """
    x_ba, x_sa = sub["x_ba"], sub["x_sa"]
    l_ba, l_sa = sub["l_ba"], sub["l_sa"]
    m_ba, m_sa = sub["m_ba"], sub["m_sa"]
    s_ba, s_sa = sub["s_ba"], sub["s_sa"]
    # 量（手）
    x_bq, x_sq = sub["x_bq"], sub["x_sq"]
    l_bq, l_sq = sub["l_bq"], sub["l_sq"]
    m_bq, m_sq = sub["m_bq"], sub["m_sq"]
    s_bq, s_sq = sub["s_bq"], sub["s_sq"]
    # 净额（float32 同序运算）
    xl_net = x_ba - x_sa
    l_net = l_ba - l_sa
    m_net = m_ba - m_sa
    s_net = s_ba - s_sa
    main_net = xl_net + l_net
    # 买入/卖出额（main 派生）
    main_b = x_ba + l_ba
    main_s = x_sa + l_sa
    # 净量 / 买卖量（main 派生）
    xl_netv = x_bq - x_sq
    l_netv = l_bq - l_sq
    m_netv = m_bq - m_sq
    s_netv = s_bq - s_sq
    main_netv = xl_netv + l_netv
    main_bv = x_bq + l_bq
    main_sv = x_sq + l_sq
    # 当日总成交额（4 档买之和，float32；moneyflow3 每行买卖平衡）
    turnover = s_ba + m_ba + l_ba + x_ba
    good = turnover > 0

    def _pct(v):
        p = np.full(len(v), np.nan, dtype=np.float32)
        p[good] = (v[good] / turnover[good] * np.float32(100.0)).astype(np.float32)
        return p

    cols = {}
    for t in ("xl", "l", "m", "s"):
        net = {"xl": xl_net, "l": l_net, "m": m_net, "s": s_net}[t]
        b = {"xl": x_ba, "l": l_ba, "m": m_ba, "s": s_ba}[t]
        s_ = {"xl": x_sa, "l": l_sa, "m": m_sa, "s": s_sa}[t]
        netv = {"xl": xl_netv, "l": l_netv, "m": m_netv, "s": s_netv}[t]
        bv = {"xl": x_bq, "l": l_bq, "m": m_bq, "s": s_bq}[t]
        sv = {"xl": x_sq, "l": l_sq, "m": m_sq, "s": s_sq}[t]
        cols[f"mf_amount_{t}"] = net.astype(np.float32)
        cols[f"mf_pct_{t}"] = _pct(net)
        cols[f"mf_amount_{t}_b"] = b.astype(np.float32)
        cols[f"mf_amount_{t}_s"] = s_.astype(np.float32)
        cols[f"mf_pct_{t}_b"] = _pct(b)
        cols[f"mf_pct_{t}_s"] = _pct(s_)
        cols[f"mf_vol_{t}"] = netv.astype(np.float32)
        cols[f"mf_vol_{t}_b"] = bv.astype(np.float32)
        cols[f"mf_vol_{t}_s"] = sv.astype(np.float32)
    cols["mf_amount_main"] = main_net.astype(np.float32)
    cols["mf_pct_main"] = _pct(main_net)
    cols["mf_amount_main_b"] = main_b.astype(np.float32)
    cols["mf_amount_main_s"] = main_s.astype(np.float32)
    cols["mf_pct_main_b"] = _pct(main_b)
    cols["mf_pct_main_s"] = _pct(main_s)
    cols["mf_vol_main"] = main_netv.astype(np.float32)
    cols["mf_vol_main_b"] = main_bv.astype(np.float32)
    cols["mf_vol_main_s"] = main_sv.astype(np.float32)
    return cols


def _derive_legacy(sub):
    """旧源（已折算 net 字段）→ {目标字段: 数组}，只含兼容 10 字段。"""
    cols = {}
    for src, dst in LEGACY_MAP.items():
        if src in sub.dtype.names:
            cols[dst] = sub[src].astype(np.float32)
    return cols


def dump(src_root: str, qlib_dir: str, force: bool = False, limit: int | None = None) -> None:
    src_root = Path(src_root)
    qlib_dir = Path(qlib_dir)
    mode = detect_source(str(src_root))
    print(f"源结构: {mode}（{'moneyflow3 原始买卖' if mode == 'new' else '已折算净额，仅 10 字段'}）")

    cal_path = qlib_dir / "calendars" / "day.txt"
    if not cal_path.exists():
        raise SystemExit(f"qlib 日历不存在: {cal_path}（确认 qlib-dir 正确）")

    calendar = pd.read_csv(cal_path, header=None)[0].astype(str).tolist()
    cal_int = np.array([int(d.replace("-", "")) for d in calendar], dtype=np.int64)
    cal_pos = {int(d.replace("-", "")): i for i, d in enumerate(calendar)}

    with h5py.File(os.path.join(src_root, "sid.h5"), "r") as f:
        sid_list = [s.decode() for s in f["sid"][:]]
    print(f"sid 总数: {len(sid_list)}")

    print("读取全部年份 data ...")
    arr = load_all_years(str(src_root))
    print(f"总行数: {len(arr):,}   date {arr['date'].min()} ~ {arr['date'].max()}")

    in_cal = np.isin(arr["date"], list(cal_pos.keys()))
    if not in_cal.all():
        n = int((~in_cal).sum())
        print(f"警告: {n:,} 行日期不在 qlib 日历内，已剔除")
        arr = arr[in_cal]

    print("排序 (sid, date) ...")
    order = np.lexsort((arr["date"], arr["sid"]))
    arr = arr[order]
    sid_arr = arr["sid"]

    dst_fields = NET_DST + (DIR_DST + VOL_DST if mode == "new" else [])
    if mode == "legacy":
        print("注意: 旧源无买卖方向数据，本次只更新 10 个净额字段；方向字段待换 moneyflow3 源")
    features_dir = qlib_dir / "features"
    written = skipped = 0
    n_sid = len(sid_list) if limit is None else min(limit, len(sid_list))
    print(f"开始 dump {n_sid} 只股票 × {len(dst_fields)} 字段 ...")

    sid_bounds = np.searchsorted(sid_arr, np.arange(n_sid))
    for sid in range(n_sid):
        lo = sid_bounds[sid]
        hi = sid_bounds[sid + 1] if sid + 1 < len(sid_bounds) else len(sid_arr)
        if lo >= hi:
            skipped += 1
            continue
        sub = arr[lo:hi]
        code = to_qlib_code(sid_list[sid])
        if code.startswith("bj"):
            skipped += 1
            continue

        pos = cal_pos_sub(sub["date"], cal_pos)
        if len(pos) == 0:
            skipped += 1
            continue
        start_idx = int(pos[0])
        end_idx = int(pos[-1])
        length = end_idx - start_idx + 1
        offset = pos - start_idx

        cols = _derive_new(sub) if mode == "new" else _derive_legacy(sub)
        inst_dir = features_dir / code
        for field in dst_fields:
            if field not in cols:
                continue  # 旧源无方向字段
            bin_path = inst_dir / f"{field}.day.bin"
            if bin_path.exists() and not force:
                skipped += 1
                continue
            values = np.full(length, np.nan, dtype=np.float32)
            values[offset] = cols[field]
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
        out = [cal_pos[int(d)] for d in dates if int(d) in cal_pos]
        return np.array(out, dtype=np.int64)


def _default_qlib_dir() -> str:
    """qlib 数据目录默认值：优先环境变量 QLIB_PROVIDER_URI，其次仓库内 data/cn_data。"""
    env = os.environ.get("QLIB_PROVIDER_URI")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "cn_data")


def _default_moneyflow_root():
    """moneyflow 源目录默认值：MONEYFLOW_SRC_ROOT > E:\\rq\\moneyflow3 > E:\\rq\\moneyflow。"""
    env = os.environ.get("MONEYFLOW_SRC_ROOT")
    if env:
        return env
    for p in (r"E:\rq\moneyflow3", r"E:\rq\moneyflow"):
        if os.path.isdir(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description="资金流 h5 → qlib 字段 bin（mf_* 前缀）")
    ap.add_argument(
        "--src-root",
        default=_default_moneyflow_root(),
        help="moneyflow h5 目录（默认 MONEYFLOW_SRC_ROOT 或 E:\\rq\\moneyflow3）",
    )
    ap.add_argument(
        "--qlib-dir",
        default=_default_qlib_dir(),
        help="qlib 数据目录（默认 QLIB_PROVIDER_URI 或仓库 data/cn_data）",
    )
    ap.add_argument("--force", action="store_true", help="已存在时覆盖重写")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 只股票（冒烟用）")
    args = ap.parse_args()
    if not args.src_root or not os.path.isdir(args.src_root):
        ap.error("moneyflow 源目录不存在，请用 --src-root 指定（或设 MONEYFLOW_SRC_ROOT）")
    dump(args.src_root, args.qlib_dir, force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
