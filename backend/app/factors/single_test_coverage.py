# -*- coding: utf-8 -*-
"""单因子测试的**覆盖率列**计算（★ v2.11.5 从 `single_test._test_one` 抽出）。

## 这个数字为什么值得单独一块
它是用户**唯一能看到的"数据够不够"信号** ✓，而它曾经**错过一次口径** ✗
（2026-10-10 追查实锤 ✓）—— 错的方式很隐蔽：**同一因子、同一参数，只因同桌多测了一个
用 `fin_*` 的因子，覆盖率就从 99.75% 掉到 98.16%** ✓（cn_data 96.04% ✓）。
用户拿它对比两套数据源时会以为「**数据有毛病**」✗（实际发生过 ✓）。

## 旧口径为什么错
旧算法 = `因子非空行 ÷ 面板总行数` ✗。而面板行集是**本次请求全部参与字段覆盖区间的并集** ✓
（`panel_expr._union_layout` ✓，为对齐 qlib `D.features` 的 union 行集 ✓）；
`fin_*` 物化 bin 的区间**铺满整条历法** ✓（起点 = 该股 pit 最早公告日、**含上市前**报告期 ✓：
招股书披露日如宁德时代 2017-11-10 披露 2014q4~2017q2 ✓；001267 甚至有 1996-1997 的前身财务 ✗）
⇒ 面板里存在大量"有财务值、**没有行情**"的死行 ✓
（实测 cn_data 三因子 203,594 行 / cn_data2 92,420 行 ✗）⇒ 分母被同表其它因子撑大 ✗。

## 新口径
**因子非空行 ÷ `CLOSE` 有效行** ✓ ＝「在有行情的交易日里，该因子有多少比例有值」✓：
· 分母与"同桌因子 / 字段并集"**无关** ✓ ⇒ 同一因子在任何数据集、任何组合下**可直接对比** ✓；
· 真正有信息量的缺失仍照报 ✓：金融股无毛利 ⇒ `FINANCE(5)` < 100% ✓；
  历史不足（预热关掉）⇒ < 100% ✓；退市/停牌（CLOSE 空）行不再参与分母 ✓。
⚠ 统计量**本来就不受死行影响** ✓（`_sub_pos` = 因子有效 ∩ LABEL 有效 ✓）
⇒ 这只影响**这一列显示值** ✓，触发/收益/IC/事件研究一律不变 ✓。
· `CLOSE` = `$close/$factor`（后复权真实价，与复权方式无关 ✓）⇒ 分母在前/后复权下一致 ✓。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


def coverage_of(df, col) -> Tuple[Optional[float], Optional[int]]:
    """→ `(覆盖率, 分母)` ✓；分母为 `None` 表示走了**兜底口径**（没有 CLOSE 列 ✓）。

    · 有 `CLOSE` 且分母 > 0 ⇒ 新口径 ✓（分母一并返回，便于自查/测试 ✓）；
    · 有 `CLOSE` 但**全空** ⇒ `(None, 0)` ✓ —— **不是 0.0** ✗：
      0.0 会被读成"这个因子一行都没值"✓，而真相是"这段区间根本没有行情"✓，两者处置完全不同 ✗；
    · 没有 `CLOSE` 列 ⇒ 退回旧口径（`因子非空行 ÷ 面板总行数` ✓），分母给 `None` ✓。
    """
    fv = df[col]
    if "CLOSE" in df.columns:
        _cl = df["CLOSE"].to_numpy(copy=False)
        _ok_cl = ~pd.isna(_cl)
        _den = int(np.count_nonzero(_ok_cl))
        if _den > 0:
            _num = int(np.count_nonzero(_ok_cl & ~pd.isna(fv.to_numpy(copy=False))))
            return round(_num / _den, 4), _den
        return None, 0
    return round(float(fv.notna().mean()), 4), None
