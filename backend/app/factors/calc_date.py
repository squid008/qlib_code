# -*- coding: utf-8 -*-
"""`DATE` 字段（公式语言里的"当前 K 线日期"）—— 口径与换算的**单一来源** ✓（2026-10-15）。

## 为什么内部值是"日序号"，不是 YYYYMMDD（实测过，勿改 ✗）

qlib 的字段 bin 是 **float32**（`<f4` 前缀 + `<f4` 值，见 `market_stat._write_bin`），
而 float32 **只能精确表示 ≤ 2**24 = 16,777,216 的整数** —— 实测：

    np.float32(20260305) → 20260304      ✗ 差 1 天
    np.float32(20160723) → 20160724      ✗
    np.float32(20070901) → 20070900      ✗ 连"截止日自己"都对不上
    np.float32(46098)    → 46098         ✓ 日序号精确

⇒ 若 bin 里直接存 YYYYMMDD，**2016-07-23 之后的所有日期都会失真** ✗；
而"日序号"（~4.6 万）远小于 2**24 ⇒ 读数、比较、减法全部精确 ✓。

## 对外语法（用户 2026-10-15 定稿 ✓）

- `DATE`（**不带括号**）= 字段，每根 K 线返回**日序号**；
- `DATE(20070901)`（带括号）= 函数，把 8 位日期字面量折成**同尺度的日序号**
  ⇒ `DATE(20071008)-DATE(20070901)` = **37**（真实天数 ✓）；
- 写 `DATE <= 20070901` 时**由编译器自动折**右侧字面量（`codegen.fold_date_literals` ✓）
  ⇒ 用户不必写成 `DATE(20070901)` ✓；
- **不支持** `2007-09-01` 这种带横杠的写法：它会先被解析成 `2007-09-01`（= 1997 ✓）
  ⇒ 编译器给**友好错**，而不是静默按 1997 比较 ✗；
- 与 `$close` 一起**按 close 掩码删行**（`engine/feature_cache._SR_FIELDS_BY_CLOSE_MASK` ✓）
  ⇒ 停牌行不会把其它字段"外对齐"回来 ✓。

## 两条求值路径（必须同值 ✓，有 `tests/test_date_field.py` 守着）

- **面板路径**（单因子测试 / 事件研究）：`panel_expr.field("$date")` 由面板 index **直接合成**
  （零 IO ✓）；
- **qlib 路径**（回测 / 训练）：读物化 bin `features/{code}/date.day.bin`
  （`tools/dump_date.py` 生成 ✓）。
  两者都用本模块的换算 ⇒ 逐位一致 ✓。
"""
from __future__ import annotations

from typing import Union

import numpy as np

#: 物化 bin 名 / 面板字段名（**不带** `$`）
DATE_FIELD = "date"
#: 公式里可写的名字（比较时先 `.upper()`；中文名不受 upper 影响 ✓）
DATE_NAMES = ("DATE", "日期")
#: 合法 YYYYMMDD 范围（折字面量时用；超出范围就报错，防"静默按整数比较"✗）
MIN_YYYYMMDD = 19000101
MAX_YYYYMMDD = 21001231
#: 日序号基准 = numpy `datetime64[D]` 的 0（1970-01-01 ✓）—— 换算必须与 `serial_array` 同源
_EPOCH_DAYS = "D"


def is_date_name(name: str) -> bool:
    """`name`（字段名/函数名，任意大小写）是不是 DATE 的写法 ✓。"""
    return str(name).upper() in DATE_NAMES


def is_date_literal(value) -> bool:
    """`value` 是不是"8 位 YYYYMMDD 形态"的整数（19000101 ~ 21001231 ✓）。

    ⚠ 只认**纯整数** ✓：`2007-09-01` 折出来是 1997 ⇒ 不在此范围 ⇒ 会被上层报友好错 ✓。
    """
    if value is None:
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(f) or not float(f).is_integer():
        return False
    i = int(f)
    return MIN_YYYYMMDD <= i <= MAX_YYYYMMDD


def to_serial(yyyymmdd: int) -> int:
    """YYYYMMDD → 日序号（距 1970-01-01 的天数；早于 1970 为负 ✓）。"""
    i = int(yyyymmdd)
    y, m, d = i // 10000, i // 100 % 100, i % 100
    try:
        return int(np.datetime64("%04d-%02d-%02d" % (y, m, d), _EPOCH_DAYS).astype(np.int64))
    except Exception as exc:                                   # noqa: BLE001
        raise ValueError(
            "不是合法日期：%s —— 请写 8 位 YYYYMMDD（如 20070901），"
            "不支持 2007-09-01 这种带横杠的写法" % (yyyymmdd,)) from exc


def from_serial(serial: Union[int, float]) -> int:
    """日序号 → YYYYMMDD（查看/测试用 ✓）。"""
    d = np.datetime64(int(serial), _EPOCH_DAYS).astype("datetime64[D]").item()
    return int(d.year) * 10000 + int(d.month) * 100 + int(d.day)


def serial_array(times) -> np.ndarray:
    """DatetimeIndex / 时间数组 → 日序号数组（float64 ✓，与 `to_serial` 同一基准）。

    ⚠ 用 `datetime64[D]` 直接转 ⇒ 天然按**交易日历上的自然日**算，与 `to_serial` 逐位一致 ✓。
    """
    return np.asarray(times, dtype="datetime64[D]").astype(np.int64).astype(np.float64)
