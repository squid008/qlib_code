# -*- coding: utf-8 -*-
"""`signals/pricing.py` 的 `fill_limits` / `_src_fp`（★ v2.9.90，2026-10-19 ✓ —— 8.5% 的模块 ✓）。

## `fill_limits` 是什么

数据源**没有交易所涨跌停标签**时的兜底 ✓：用 `limit_ratio(代码)` × **昨收真实价**推算 ✓。
它直接决定"**这笔能不能成交**"✓ —— 推高了 ⇒ 假涨停、该成交的不成交 ✗；
推低了 ⇒ 该拒的没拒 ✗。而两种错都**只体现为回测数字差异**✓（界面完全正常 ✗）。

四条必须钉的口径 ✓：

1. **`strict=False` ⇒ 什么都不做** ✓（"不设涨跌停 = 理想成交"✓ —— 这里凭空补一张表就变成
   "以为理想成交、其实被拦" ✗）；
2. **已有 `LIMIT_UP`/`LIMIT_DOWN` ⇒ 原样返回** ✓ —— **绝不覆盖交易所标签** ✗
   （标签是权威 ✓，推算版不识别 ST 5% ✓，覆盖等于把 ST 的 5% 改成 10% ✗）；
3. **没有 `CLOSE_RAW` ⇒ 原样返回** ✓（没有真实价就没法推 ✓，不许拿复权价顶 ✗）；
4. **按板块给幅度** ✓：主板 10% / 创业·科创 20% / 北交 30% ✓（用 `limit_ratio` ✓），
   且**首行必须是 NaN** ✓（昨收不存在 ✓ —— 填 0 会被当成"跌停到 0"✗）。

`_src_fp` 则把**源码指纹**拼进磁盘缓存 key ✓（用户 2026-10-12："没有加版本的哈希校验？"✓）——
它失效就意味着"改了取价口径却返回旧宽表" ✗，所以也顺手断言它**能给出指纹、且稳定** ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.signals import pricing


def _panel(codes=("SH600000", "SZ300001", "BJ430047"), n=4):
    days = pd.bdate_range("2024-01-02", periods=n)
    raw = pd.DataFrame(
        {c: [10.0 + i for i in range(n)] for c in codes}, index=days,
    )
    return {"CLOSE_RAW": raw}


def test_strict_false_is_noop():
    p = _panel()
    out = pricing.fill_limits(p, strict=False)
    assert "LIMIT_UP" not in out
    assert "_limits_inferred" not in out


def test_existing_labels_are_never_overwritten():
    """⭐ 有交易所标签时一律用标签 ✓（推算版不识别 ST 5% ✗，覆盖 = 把 5% 改成 10% ✗）。"""
    p = _panel()
    labels = pd.DataFrame(
        {c: [9.99] * 4 for c in p["CLOSE_RAW"].columns}, index=p["CLOSE_RAW"].index)
    p["LIMIT_UP"] = labels.copy()
    p["LIMIT_DOWN"] = labels.copy()
    out = pricing.fill_limits(p)
    assert out["LIMIT_UP"].equals(labels)
    assert "_limits_inferred" not in out          # 没推算过 ⇒ 不打标记 ✓


def test_missing_close_raw_is_noop():
    p = {"CLOSE": pd.DataFrame({"SH600000": [1.0, 2.0]})}
    out = pricing.fill_limits(p)
    assert "LIMIT_UP" not in out


def test_inferred_limits_use_prev_close_and_board_ratio():
    """⭐ 幅度按**板块**给 ✓：主板 10% / 创业板 20% / 北交 30% ✓；首行 NaN ✓。"""
    p = _panel(n=4)
    out = pricing.fill_limits(p)
    up, dn = out["LIMIT_UP"], out["LIMIT_DOWN"]
    assert "_limits_inferred" in out

    # 第 2 行（index 1）的昨收 = 10.0 ✓
    assert up["SH600000"].iloc[1] == pytest.approx(round(10.0 * 1.10, 2))
    assert dn["SH600000"].iloc[1] == pytest.approx(round(10.0 * 0.90, 2))
    assert up["SZ300001"].iloc[1] == pytest.approx(round(10.0 * 1.20, 2))
    assert up["BJ430047"].iloc[1] == pytest.approx(round(10.0 * 1.30, 2))

    # ⭐ 首行没有"昨收" ⇒ 必须 NaN ✓（填 0 会被当成跌停到 0 ✗）
    assert np.isnan(up["SH600000"].iloc[0])
    assert np.isnan(dn["SH600000"].iloc[0])
    # index 与 CLOSE_RAW 对齐 ✓
    assert list(up.index) == list(p["CLOSE_RAW"].index)


def test_inferred_limits_are_rounded_to_cent():
    """价格保留 **2 位** ✓（A 股最小变动单位 ✓ —— 3 位会让"刚好涨停"判定偏差 ✗）。"""
    p = _panel(n=3)
    p["CLOSE_RAW"]["SH600000"] = [10.0, 10.13, 10.13]
    out = pricing.fill_limits(p)
    v = out["LIMIT_UP"]["SH600000"].iloc[1]
    assert round(float(v), 2) == pytest.approx(float(v))     # 已是 2 位 ✓
    assert v == pytest.approx(round(10.0 * 1.10, 2))


def test_only_missing_side_is_filled():
    """只缺一侧时，另一侧**保持原样** ✓（不许把已有标签重算 ✗）。"""
    p = _panel(n=3)
    kept = pd.DataFrame({"SH600000": [11.0, 11.0, 11.0],
                         "SZ300001": [11.0, 11.0, 11.0],
                         "BJ430047": [11.0, 11.0, 11.0]}, index=p["CLOSE_RAW"].index)
    p["LIMIT_DOWN"] = kept.copy()          # 只有下限 ✓
    out = pricing.fill_limits(p)
    assert out["LIMIT_DOWN"].equals(kept)  # 不被动 ✓
    assert "LIMIT_UP" in out               # 补上 ✓


# ------------------------------------------------------------------ _src_fp
def test_src_fp_is_stable_and_not_placeholder():
    """⭐ 源码指纹必须**可用** ✓（返回 `"na"` 等于缓存永不失效 ✗ —— 正是用户 2026-10-12 问的那个坑 ✓）。"""
    a = pricing._src_fp()
    b = pricing._src_fp()
    assert a == b, "同一份源码两次调用应一致 ✓"
    assert a and a != "na", "取不到指纹 ✗（改口径将不会失效缓存 ✓）"
