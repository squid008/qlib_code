# -*- coding: utf-8 -*-
"""复权方式工具：不复权 / 前复权 / 后复权。

qlib 数据里 close/open/high/low/vwap 都是【未复权】原始价，factor 是复权因子。
本项目按"不复权 / 前复权 / 后复权"三种方式对价格做调整（**不修改 qlib 内核**，
只在表达式构造与回测 quote 层处理）：

- none（默认，历史行为）：直接用原始价。
- backward（后复权）：adjusted = price * factor。
  - 以最早价格为基准，序列连续、无除权跳空，历史收益准确，因子稳定可复现。
- forward（前复权）：adjusted = price * factor / factor_end（最新交易日因子归一化）。
  - 让最新价等于实盘价，K 线观感与行情软件一致。

数学事实（务必知晓）：对【比率类】特征（MA/ROC/RSV 等）与收益（label/净值），
前复权与后复权完全等价——整体差一个常数因子，分子分母同时缩放抵消。
因此训练特征、label、IC、净值等结果，前复权 == 后复权；唯一区别是价格绝对值
（前复权接近实盘价，后复权数值偏大）。本模块在训练表达式层统一用 `* factor`
（后复权形式），回测 quote 层对 forward 额外除以最新因子做归一化。

实现：
- 特征/label 表达式：把 $close/$open/$high/$low/$vwap 替换为复权表达式（见 adjust_expr）。
- 回测成交价：BoardAwareExchange 加载 quote 后对价格列按 factor 调整（见 board_exchange.py）。
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


def adjust_expr(expr: str, mode: str) -> str:
    """把表达式中的价格字段替换为复权表达式（forward/backward 均等价于 `price * $factor`）。

    例：`Mean($close, 20)` → `Mean(($close*$factor), 20)`
        `Ref($close, -22)/Ref($close, -1) - 1` → `Ref(($close*$factor), -22)/Ref(($close*$factor), -1) - 1`
    """
    m = normalize_mode(mode)
    if not expr or m == "none":
        return expr
    out = expr
    for f in PRICE_FIELDS:
        # 只匹配独立字段 token（前/后都不是字母数字下划线），避免误伤 Ref/Mean 等算子名
        pat = re.compile(r"(?<![0-9A-Za-z_])" + re.escape(f) + r"(?![0-9A-Za-z_])")
        out = pat.sub("(%s*$factor)" % f, out)
    return out


def adjust_label(expr: str, mode: str) -> str:
    """label 表达式复权（与 adjust_expr 相同逻辑，语义化封装）。"""
    return adjust_expr(expr, mode)


def quote_adjust_factors(mode: str, end_time=None) -> Tuple[str, str]:
    """返回回测 quote 层复权时需要的信息。

    返回 (factor_expr, divide_end_flag)：
    - backward: 价格列乘 `$factor`，不做额外归一化。
    - forward:  价格列乘 `$factor` 后再除以区间最新因子（归一化到实盘价）。
    实际复权在 board_exchange.BoardAwareExchange.get_quote_from_qlib 中执行。
    """
    m = normalize_mode(mode)
    if m == "none":
        return "$factor", False
    return "$factor", (m == "forward")
