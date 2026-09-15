# -*- coding: utf-8 -*-
"""聚宽「收益概述」解析（`result_1.csv` 这类**逐日**绩效序列，2026-09-15 用户提供）。

实测格式（GBK，2187 行 = 2016-01-04 ~ 2024-12-31 每个交易日一行）：

    时间,基准收益,策略收益,当日盈利,当日亏损,当日买入,当日卖出,超额收益(%)
    2016-01-04 16:00:00,-8.33,-9.15,0,-914744.04,9993467,0,-0.89
    …
    2024-12-31 16:00:00,-24.84,767.62,0,-1627596,0,0,1054.36

口径（列名容易误解，实测确认）：
  · `策略收益` / `基准收益` = **相对初始资金的累计收益率（百分数）** ⇒ 净值 = 1 + 值/100。
    实测首日：`当日亏损 -914744` ÷ 本金 1000 万 = **−9.15%** = 首行 `策略收益` ✓ 口径确认；
    末日 `策略收益 767.62%` ⇒ 净值 **8.6762**（用它反查我的重建：C = 8.6603，差 **0.18%** ✓）；
  · `当日买入/当日卖出` = 当日成交金额（卖出为负）⇒ 用来**逐日核对成交明细是否完整**；
  · `当日盈利/当日亏损` = 当日浮盈/浮亏金额。

价值：**它让"重建净值"可被校准** —— 官方净值直接叠加在图上，并给出与重建值的偏差；
没有成交明细时，它自己也能单独出图（官方净值 + 官方基准，纯 CSV，秒出、不需要行情）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParseResult, decode_bytes, pick_col, read_table, num

COL_ALIAS = {
    "date": ("时间", "日期", "date", "time", "交易日"),
    "strat_cum": ("策略收益", "策略累计收益", "策略收益率", "累计收益", "收益"),
    "bench_cum": ("基准收益", "基准累计收益", "基准收益率", "基准"),
    "profit": ("当日盈利", "盈利", "当日收益"),
    "loss": ("当日亏损", "亏损"),
    "buy_amt": ("当日买入", "买入金额", "买入额"),
    "sell_amt": ("当日卖出", "卖出金额", "卖出额"),
    "excess": ("超额收益(%)", "超额收益", "超额"),
}


def parse_jq_perf(raw, filename: str = "") -> ParseResult:
    text, enc = decode_bytes(raw)
    res = ParseResult(kind="jq_perf", encoding=enc)
    df, sep, had_header = read_table(text)
    res.sep = sep
    res.headers = [str(c) for c in df.columns]
    cols = {k: pick_col(df, v) for k, v in COL_ALIAS.items()}
    if cols["date"] is None or cols["strat_cum"] is None:
        res.add_issue(0, " | ".join(res.headers[:8]),
                      "找不到「时间」或「策略收益」列（这不是聚宽《收益概述》？）")
        res.stats.update(rows_total=int(len(df)), rows_valid=0, dropped=1)
        return res

    rows, n_drop = [], 0
    for i, r in enumerate(df.itertuples(index=False), start=(2 if had_header else 1)):
        rec = dict(zip(df.columns, r))
        dt = pd.to_datetime(str(rec.get(cols["date"])), errors="coerce")
        if pd.isna(dt):
            n_drop += 1
            res.add_issue(i, str(rec.get(cols["date"])), "时间无法识别")
            continue
        rows.append({
            "date": pd.Timestamp(dt).normalize(),
            "strat_cum": num(rec.get(cols["strat_cum"])),
            "bench_cum": num(rec.get(cols["bench_cum"])) if cols["bench_cum"] else None,
            "profit": num(rec.get(cols["profit"])) if cols["profit"] else None,
            "loss": num(rec.get(cols["loss"])) if cols["loss"] else None,
            "buy_amt": num(rec.get(cols["buy_amt"])) if cols["buy_amt"] else None,
            "sell_amt": abs(num(rec.get(cols["sell_amt"])) or 0.0) if cols["sell_amt"] else None,
            "excess": num(rec.get(cols["excess"])) if cols["excess"] else None,
        })
    pf = pd.DataFrame(rows)
    if len(pf):
        pf = pf.sort_values("date").reset_index(drop=True)
        pf["nav"] = 1.0 + pf["strat_cum"] / 100.0
        pf["bench_nav"] = (1.0 + pf["bench_cum"] / 100.0) if pf["bench_cum"].notna().any() else np.nan
    res.perf = pf
    res.stats.update(_perf_stats(pf, n_drop, int(len(df))))
    return res


def _perf_stats(pf: pd.DataFrame, n_drop: int, n_total: int) -> dict:
    st = {"rows_total": n_total, "rows_valid": int(len(pf)), "dropped": int(n_drop)}
    if not len(pf):
        return st
    nav = pf["nav"].to_numpy(dtype=float)
    dd = float((nav / np.maximum.accumulate(nav) - 1.0).min())
    st.update({
        "date_min": str(pf["date"].min().date()), "date_max": str(pf["date"].max().date()),
        "nav_end": round(float(nav[-1]), 4),
        "total_return": round(float(nav[-1] - 1.0), 4),
        "max_drawdown": round(dd, 4),
        "bench_nav_end": (round(float(pf["bench_nav"].iloc[-1]), 4)
                          if pf["bench_nav"].notna().any() else None),
        "excess_end_pct": (round(float(pf["excess"].iloc[-1]), 2)
                           if pf["excess"].notna().any() else None),
        "buy_total": round(float(pf["buy_amt"].fillna(0).sum()), 2),
        "sell_total": round(float(pf["sell_amt"].fillna(0).sum()), 2),
    })
    return st
