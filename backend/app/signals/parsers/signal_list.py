# -*- coding: utf-8 -*-
"""模式①「信号清单」解析（同事给的格式）：`日期,标的`，全是买入信号。

实测样例（GBK，1678 行）：
    日期,标的
    2016/1/4,新宝股份(002705.XSHE)
    …

规则：
  · 日期自动识别（`2016/1/4`、`2016-01-04`、Excel 序列号…）；
  · 标的取**括号里的代码**，中文名剥离**丢弃**（用户明确：中文名会变，不许参与识别）；
  · 可选第 3 列 `方向/买卖`（同事这版没有）—— 有就解析成 +1/-1，卖出信号在回测里当**强制离场**；
  · 指数行（`SH000300` 这类）不算信号，单独计数并提示；
  · 重复行（同日同标的）去重并计数。
"""
from __future__ import annotations

import pandas as pd

from ..codes import normalize_code, parse_date, split_multi_codes
from .base import (AMOUNT_ALIASES, CODE_ALIASES, DATE_ALIASES, FEE_ALIASES, PRICE_ALIASES,
                   QTY_ALIASES, STATUS_ALIASES, SIDE_ALIASES, ParseResult, decode_bytes,
                   pick_col, read_table, side_of, num)


def parse_signal_list(raw, filename: str = "") -> ParseResult:
    text, enc = decode_bytes(raw)
    res = ParseResult(kind="signal_list", encoding=enc)
    df, sep, had_header = read_table(text)
    res.sep = sep
    res.headers = [str(c) for c in df.columns]

    date_col = pick_col(df, DATE_ALIASES)
    code_col = pick_col(df, CODE_ALIASES)
    side_col = pick_col(df, SIDE_ALIASES)
    if date_col is None and len(df.columns) >= 1:       # 无表头：按位置
        date_col = df.columns[0]
    if code_col is None and len(df.columns) >= 2:
        code_col = df.columns[1]
    if date_col is None or code_col is None:
        res.add_issue(0, " | ".join(res.headers[:8]),
                      "找不到「日期」或「标的」列（请确认第 1 列是日期、第 2 列是代码）")
        res.stats.update(rows_total=int(len(df)), rows_valid=0, dropped=1, stocks=0)
        return res

    rows, issues, seen = [], [], set()
    n_dup = n_drop = n_index = 0
    n_buy = n_sell = 0
    n_multi_cells = 0
    unresolved: dict = {}
    for i, r in enumerate(df.itertuples(index=False), start=(2 if had_header else 1)):
        rec = dict(zip(df.columns, r))
        raw_date, raw_code = rec.get(date_col), rec.get(code_col)
        if (str(raw_date).strip().lower() in ("nan", "") and
                str(raw_code).strip().lower() in ("nan", "")):
            continue                                    # 整行空
        dt = parse_date(raw_date)
        if dt is None:
            n_drop += 1
            res.add_issue(i, "%s | %s" % (raw_date, raw_code), "日期无法识别")
            continue
        # ⚠ 一个单元格里可能塞多只（同事的 Excel 表用逗号隔开）⇒ 拆成多个信号（同一日期）
        toks = split_multi_codes(raw_code)
        if len(toks) > 1:
            n_multi_cells += 1
        for tok in toks:
            info = normalize_code(tok)
            if info.qlib_code is None:
                n_drop += 1
                for msg in (info.issues or ["标的无法识别"]):
                    key = msg.split("：")[0]
                    unresolved[key] = unresolved.get(key, 0) + 1
                res.add_issue(i, "%s | %s" % (raw_date, tok), (info.issues or ["标的无法识别"])[0])
                continue
            if info.is_index:
                n_index += 1
                res.add_issue(i, "%s | %s" % (raw_date, tok),
                              "指数（%s）：信号回测只支持股票，已忽略" % info.qlib_code)
                continue
            side = side_of(rec.get(side_col)) if side_col else 1
            if side is None:
                side = 1                                # 方向列认不出来 ⇒ 保守当买入（同事全是买入）
            if info.issues:                             # 例如"无后缀有歧义"——行仍然可用
                for msg in info.issues:
                    unresolved[msg] = unresolved.get(msg, 0) + 1
                res.add_issue(i, "%s | %s" % (raw_date, tok), info.issues[0])
            key = (dt, info.qlib_code, side)
            if key in seen:
                n_dup += 1
                continue
            seen.add(key)
            # ⚠ 买卖计数放在**去重之后**：否则 `buy_signals + sell_signals != rows_valid`，
            #   界面上会显得"信号数比有效行多"（2026-09-15 单测抓到）。
            if side > 0:
                n_buy += 1
            else:
                n_sell += 1
            rows.append({"date": dt, "code": info.qlib_code, "side": side,
                         "name": info.name, "raw_date": str(raw_date), "raw_code": str(tok)})

    sig = pd.DataFrame(rows)
    res.signals = sig
    per_day = sig.groupby("date").size() if len(sig) else pd.Series(dtype=int)
    res.stats.update({
        "rows_total": int(len(df)),
        "rows_valid": int(len(sig)),
        "dropped": int(n_drop),
        "dup_dropped": int(n_dup),
        "index_rows": int(n_index),
        "buy_signals": int(n_buy),
        "sell_signals": int(n_sell),
        "stocks": int(sig["code"].nunique()) if len(sig) else 0,
        "days": int(sig["date"].nunique()) if len(sig) else 0,
        "date_min": str(sig["date"].min().date()) if len(sig) else None,
        "date_max": str(sig["date"].max().date()) if len(sig) else None,
        "max_signals_per_day": int(per_day.max()) if len(per_day) else 0,
        "signals_per_day_avg": round(float(per_day.mean()), 2) if len(per_day) else 0.0,
        # 一个格子里塞了多只的单元格数（同事的 Excel 表；拆分后 rows_valid 会 > rows_total）
        "multi_code_cells": int(n_multi_cells),
        "warnings": dict(sorted(unresolved.items(), key=lambda kv: -kv[1])[:8]),
    })
    return res
