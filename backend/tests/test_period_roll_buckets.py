# -*- coding: utf-8 -*-
"""`build_targets`：批次 → 调仓日 → 目标名单（★ v2.11.7 抽出后终于能直接单测）。

## 为什么这个纯函数值得单独测
它是**分批语义的唯一实现** ✓：改这里会同时影响「每期重建」的口径与持仓明细导出 ✓。
而它埋在 438 行的逐日循环前面时，谁也不敢动 ✗。

## ⭐⭐ 最该钉的一条：同一执行日被多个批次命中 ⇒ **必须去重合并**
`lag` 相同、批次日期不同 ⇒ 执行日可能撞在一起 ✓（如 `bucket` 把同一天拆成两批 ✓）。
若直接 `extend(cols)` 不去重 ⇒ 名单里同一只票出现两次 ✓ ⇒ 全仓等权时
**它被加了两次仓** ✗，而总仓位仍是 100% ⇒ **别的票被挤少** ✓
⇒ 净值变化"看起来正常"但**权重全错** ✓，事后极难发现 ✗。
"""
from __future__ import annotations

import pandas as pd

from app.signals.period_roll_buckets import build_targets

POS = {pd.Timestamp("2024-01-02"): 0, pd.Timestamp("2024-01-03"): 1,
       pd.Timestamp("2024-01-04"): 2}
COL_OF = {"AAA": 0, "BBB": 1, "CCC": 2}


def _sig(rows):
    """rows = [(date, code)] ⇒ 只含 date/code 两列（无 bucket ✓）。"""
    return pd.DataFrame({"date": [d for d, _ in rows], "code": [c for _, c in rows]})


def test_without_bucket_each_signal_date_is_its_own_batch():
    """⭐ 没有 `bucket` 列 ⇒ **每个"有信号的日期"自成一个批次** ✓ ——
    导出的"每期组合"正是这种形状（只在调仓日有信号 ✓）。
    若不分日期地把所有信号当一批 ⇒ 不同调仓日的名单混成一锅 ✗。"""
    sig = _sig([("2024-01-02", "AAA"), ("2024-01-03", "BBB")])
    got = build_targets(sig, pos=POS, lag=1, n_row=3, col_of=COL_OF)
    assert got == {1: [0], 2: [1]}, "两个批次分别落到次日 ✓（不是并成一批 ✗）"


def test_bucket_column_groups_rows_into_one_batch():
    """有 `bucket` ⇒ **同一批次的多行合成一份名单** ✓（同批同日一起执行 ✓）。"""
    sig = _sig([("2024-01-02", "AAA"), ("2024-01-02", "BBB")]).assign(bucket=["b1", "b1"])
    got = build_targets(sig, pos=POS, lag=1, n_row=3, col_of=COL_OF)
    assert got == {1: [0, 1]}


def test_same_exec_day_merges_without_duplicates():
    """⭐⭐⭐ **同一执行日多个批次 ⇒ 去重合并** ✓（不去重 = 同一只票被加两次仓 ✗）。"""
    # 两个不同批次、但 lag=1 都落到第 2 行（2024-01-03 的信号 ⇒ 2024-01-04 执行 ✓）
    sig = pd.DataFrame({"date": ["2024-01-03", "2024-01-03"],
                        "code": ["AAA", "AAA"],
                        "bucket": ["b1", "b2"]})
    got = build_targets(sig, pos=POS, lag=1, n_row=3, col_of=COL_OF)
    assert got == {2: [0]}, "AAA 只能出现一次 ✓（两次 = 权重翻倍 ✗）"


def test_date_not_in_calendar_is_skipped():
    """⭐ 信号日不在交易日历里（停牌/脏数据）⇒ **整批跳过** ✓，不许"顺延到下一根" ✗
    （顺延会让成交价与信号日的对应关系悄悄偏移 ✓）。"""
    sig = _sig([("2024-01-06", "AAA")])          # 周六，不在 POS 里 ✓
    assert build_targets(sig, pos=POS, lag=1, n_row=3, col_of=COL_OF) == {}


def test_exec_day_beyond_calendar_is_skipped():
    """⭐ 执行日越界（最后一天 + lag）⇒ 跳过 ✓（否则逐日循环下标越界 ✗）。"""
    sig = _sig([("2024-01-04", "AAA")])          # 最后一天 + lag=1 ⇒ 越界 ✓
    assert build_targets(sig, pos=POS, lag=1, n_row=3, col_of=COL_OF) == {}


def test_unknown_codes_ignored_and_duplicates_deduped():
    """⭐ 代码不在行情里 ⇒ 跳过 ✓（`col_of` 没有它 ✓）；**同批次内重复 ⇒ 去重保序** ✓。"""
    sig = _sig([("2024-01-02", "BBB"), ("2024-01-02", "BBB"),
                ("2024-01-02", "ZZZ"), ("2024-01-02", "AAA")])
    got = build_targets(sig, pos=POS, lag=0, n_row=3, col_of=COL_OF)
    assert got == {0: [1, 0]}, "保序去重 ✓（BBB,AAA；ZZZ 被忽略 ✓）"
