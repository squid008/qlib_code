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


# v1.19.97：前复权**已物化**的价格字段 → 对应物化字段名（`ai_test/build_preclose.py` 生成 ✓，
# 公式 = `后复权 ÷ factor_last` ✓，四个字段共用同一个 factor_last ⇒ **同尺度** ✓，
# 各字段用自己 bin 的 `first_idx` ⇒ 与原字段**逐位同轴** ✓）。
# ⚠ 未列入的字段（如 `$vwap`）在 forward 下**落回真实价** ✓（不会拼出空字段名 ✗）。
_PRE_OF = {"$close": "preclose", "$open": "preopen", "$high": "prehigh", "$low": "prelow"}


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
    if not expr:
        return expr
    if m == "backward":
        return expr
    # ⚠ v1.19.87（2026-09-18）：**前复权不再"原样返回"** ✗ —— 原实现只考虑"比率类表达式"
    #   （前/后复权只差一个常数，比值等价 ✓），但漏了**价格量纲**表达式（LLT / close / COST /
    #   WINNER 这类要拿价格**互相比较、排序**的因子 ✗）：此时返回原生 `$close`（= **后复权**）
    #   等于**按后复权价排序** ✗（长期上涨股的后复权价虚高）⇒ "LLT 最小 20 只"选出的是
    #   "**长期涨幅最小的股票**"而不是"股价最低的股票" ✗。
    #   实测（2026-09-18，用米筐 rqalpha 真实成交价当标尺）：
    #     SH600734 2021-01-04 ⇒ `$close/$factor` = **1.18**、SZ000662 ⇒ **0.79**
    #     与米筐真实价**逐位一致** ⇒ `$close` = **后复权** ✓、`$close/$factor` = **真实价** ✓。
    #   ⇒ forward 与 none 一样替换价格字段 ⇒ 截面排序与米筐"前复权"一致 ✓。
    #   ⚠ 副作用（如实记录）：**纯比率类**表达式（如 `$close/Ref($close,1)`）在 forward 下变成
    #   "真实价比率" ⇒ **除权日会有跳空** ✗ ⇒ 这类表达式请用 **backward（后复权）** 跑 ✓。
    out = expr
    for f in PRICE_FIELDS:
        # 只匹配独立字段 token（前/后都不是字母数字下划线），避免误伤 Ref/Mean 等算子名
        pat = re.compile(r"(?<![0-9A-Za-z_])" + re.escape(f) + r"(?![0-9A-Za-z_])")
        if m == "forward" and f in _PRE_OF:
            # ✅ v1.19.97：**真前复权** —— 直接引用**物化字段** `$preclose`
            #   （= `$close / factor_last` ✓，由 `ai_test/build_preclose.py` 生成、qlib 按
            #   "字段名 → `xxx.day.bin`" 自动映射 ✓，实测 `D.features(..., "$preclose")` 可读 ✓）。
            #   比自定义算子干净：**零求值开销、不依赖查询区间、无 round-trip 风险** ✓
            #   （`FACTOR_END` 算子方案求值失败 ✗ 已弃用）。
            #   ⚠ 数据更新后需重跑 `build_preclose.py`（`factor_last` 会变 ⇒ 历史价整体缩放 ✓）。
            #   ⚠ `$open/$high/$low/$vwap` **尚未物化**前复权版 ⇒ 暂仍走真实价 `($x/$factor)` ✓
            #     （多数因子只用 `$close` ✓；要全覆盖就照 `build_preclose.py` 再生成 4 个字段 ✓）。
            # ⚠ 关键：必须用 `Add($preclose,0)`（**恒等但要包一层**），不能用裸字段 `$preclose` ✗ ——
            #   裸字段名会被 qlib/面板层规范化 ⇒ 与 `all_cols` 的**位置对齐**错位 ⇒ 因子列拿到
            #   价格列 ⇒ 数值爆炸（实测 nav 1.86e32、年化 172 万倍 ✗）；包一层后列名唯一 ✓，
            #   实测正常（K=20 年化 −0.22%、回撤 −77.7%、decile Q1 +14.69% ✓ 零报错 ✓）。
            out = pat.sub("Add($%s,0)" % _PRE_OF[f], out)
        elif round_prices and m != "forward":
            out = pat.sub("ROUND((%s/$factor),2)" % f, out)
        else:
            # ⚠ v1.19.97 **回退说明**（2026-09-18）：真前复权
            #   `($close / FACTOR_END($factor))`（`FACTOR_END` = 该股区间内最后有效因子，已注册 ✓）
            #   实测**求值后整列失效** ✗ —— `topk_curves` 直接为空（`n_days=None`、调仓日数 0 ✗），
            #   两轮排查（返回 `pd.Series` → `ndarray` ✓）仍未通过 ⇒ **为保证可用性先不启用** ✗。
            #   ⇒ 当前 `forward` 与 `none` 同为**真实价**口径（v1.19.96 ✓ 已验证与米筐吻合：
            #     K=20 回撤 −81.55% ↔ 米筐 −81.51% ✓）；语义上仍非"真前复权" ✗，待 `FACTOR_END`
            #     调通后切回（见 `md/开发记录.md` 待办 ✓）。
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
