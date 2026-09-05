# -*- coding: utf-8 -*-
"""复权方式工具：不复权 / 前复权 / 后复权。

【数据事实（2026-09-02 用东方财富价格逐笔核对确认）】
本机 qlib 数据里 close/open/high/low/vwap 均为【后复权价】= 真实收盘价 × factor：
  real_price = $close / $factor
（验证：SH600188 2023-07-14/07-17，$close/$factor 反推 = 东财不复权价 33.87/18.75，逐分精确匹配。）
$factor 为该日之前所有除权除息的累积后复权因子，只在除权日跳变（如 10 送转/大额分红）。
$change 是真实价（东财"不复权"口径）的简单涨跌幅，除权日因价格跳空会大跌（如兖矿 -44.6%）；
后复权价（=$close）序列在除权日连续，其收益率 = 含分红送转的真实可投资回报。

因此三种模式的正确取价（均基于同一份配套数据，内部自洽）：
  - none（不复权 / 真实价，= 东财"不复权"K 线）：adjusted = price / $factor
  - backward（后复权）：adjusted = price（数据原生即后复权价，无需处理）
  - forward（前复权）：adjusted = price / $factor_end
    （$factor_end = 每股最新交易日因子；只是"最新价 = 实盘价"的观感归一）

数学事实（务必知晓）：对【比率类】特征（MA/ROC/RSV/乖离率等）与收益（label/净值），
前复权与后复权完全等价——整体差一个每股常数因子，分子分母同时缩放抵消。
因此训练/特征/label 表达式层 forward 与 backward 统一用数据原生后复权价（$close），
无需额外处理；只有"绝对价格观感"（回测 quote 成交价、K 线展示）需要 forward 做
factor_end 归一（见 board_exchange.py）。
不复权（真实价）在除权日价格跳空，其"简单涨跌幅"在除权日含假跌，主要用于与行情软件
K 线对照，不建议作为训练/回测的收益口径。

历史 bug（2026-09-02 修复）：此前误以为 $close 为未复权真实价、$factor 为复权系数，
实现为 price × factor，导致：
  - forward/backward 双重复权（price × factor²），除权日出现 +63% 级别假跳空；
  - "不复权"实际用了后复权价而非真实价；
  - 涨停判定用复权价反推昨收在除权日失真（误判涨停 53%）。
修复后按上表公式处理。
"""
from __future__ import annotations

import re
from typing import Tuple

# 价格字段（volume/amount 不随复权变化；change 为涨跌幅本身与复权无关）
PRICE_FIELDS = ("$close", "$open", "$high", "$low", "$vwap")

VALID_MODES = ("none", "forward", "backward")


def normalize_mode(mode: str) -> str:
    """规范化复权方式入参；非法值回退为 none。"""
    m = (mode or "none").lower()
    return m if m in VALID_MODES else "none"


def adjust_expr(expr: str, mode: str, round_prices: bool = False) -> str:
    """把表达式中的价格字段替换为指定口径的价格表达式。

    例（mode=none）：`Mean($close, 20)` → `Mean(($close/$factor), 20)`
    mode=forward/backward：返回原表达式（$close 原生即后复权价；
    前/后复权对比率类表达式等价，见模块 docstring）。

    round_prices=True（仅 none 生效）：真实价进一步按分取整，
    `$close` → `ROUND(($close/$factor), 2)`。A 股价格本为整分，round 抹掉
    后复权 float32 bin ÷ factor 还原的浮点尾差，与益盟/聚宽（整分原始价）
    的指标口径完全对齐（消除阈值边界触发日差一天）。forward/backward 用
    后复权原生价，round 无整分语义、忽略该参数。

    说明：表达式统一基于同一份数据内部的配套因子，无论数据新旧都自洽；
    forward 的前复权观感归一只在回测 quote / K 线展示层做（见 board_exchange.py）。
    """
    m = normalize_mode(mode)
    if not expr or m in ("forward", "backward"):
        return expr
    out = expr
    for f in PRICE_FIELDS:
        # 只匹配独立字段 token（前/后都不是字母数字下划线），避免误伤 Ref/Mean 等算子名
        pat = re.compile(r"(?<![0-9A-Za-z_])" + re.escape(f) + r"(?![0-9A-Za-z_])")
        if round_prices:
            out = pat.sub("ROUND((%s/$factor),2)" % f, out)
        else:
            out = pat.sub("(%s/$factor)" % f, out)
    return out


def adjust_label(expr: str, mode: str) -> str:
    """label 表达式复权（与 adjust_expr 相同逻辑，语义化封装）。"""
    return adjust_expr(expr, mode)


def quote_adjust_factors(mode: str, end_time=None) -> Tuple[str, str]:
    """返回回测 quote 层取价方式（BoardAwareExchange 内部使用；本函数保留供参考/兼容）。

    数据 $close 原生为后复权价：
    - none:     真实价 = $close / $factor
    - backward: 后复权价 = $close（原生，不处理）
    - forward:  前复权价 = $close / 每股区间最新因子（归一化到实盘价观感）
    """
    m = normalize_mode(mode)
    return "$factor", (m == "forward")
