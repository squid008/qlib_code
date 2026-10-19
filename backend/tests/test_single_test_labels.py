# -*- coding: utf-8 -*-
"""`single_test` 的**标签兜底**与**分组统计**（★ v2.9.82，2026-10-19 ✓）。

## 为什么这三块值得单独钉

`_apply_ff_labels` 是**标签的最后一道兜底** ✓ —— 它把"缺价/停牌行"的标签补上，好让样本不被
整行丢掉 ✓。但兜底一旦越界，就会**凭空造出假标签** ✗，而这些假标签照样算进 IC / 分层 / 胜率 ✓：

* **审计 ⑥**（v2.8.2）：原实现对**未成熟的尾部行**也填 —— `exit_px` 缺失时拿"最后一天的价"当卖出价 ✗
  ⇒ 尾部造出"倒数第 2 行一个小收益、倒数第 1 行 0%"✗（与审计复现一致 ✓）。
  现在那些行**保持 NaN** ✓（qlib 本来就算它们"无标签"✓ ⇒ 统计自动排除 ✓）。
* **审计 ⑦**（v2.8.2）：原兜底用**不复权** `CLOSE`/`OPEN` ✗ ⇒ **除权日**跨口径相减
  ⇒ 兜底算出假的 **−50%**（正确应 ≈0%）✓，默认 `price_adjust="backward"` 时就命中 ✓。
  现在价格链走 `CLOSE_ADJ`/`OPEN_ADJ` ✓（与主标签同源 ✓）。

`_stat_group` 则把"**剔除明细**"给前端逐项展示 ✓ —— 这里钉住一条容易改坏的口径 ✓：
**`limit_up_excluded` 只等于 T 涨停 + T+1 涨停 + 停牌 三项之和** ✓，
**不含** `extra_excluded`（ST/创业板/科创板 ✓）—— 混进去会让用户对不上"信号数 − 剔除数 = 样本数"✗。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import (
    _apply_ff_labels,
    _ff_label_px_col,
    _stat_group,
)


# --------------------------------------------------------------------------- _stat_group
def test_stat_group_empty_returns_none():
    assert _stat_group(None) is None
    assert _stat_group(pd.DataFrame({"LABEL": []})) is None


def test_stat_group_counts_and_rounding():
    g = pd.DataFrame({"LABEL": [0.01, 0.02, 0.03, 0.04]})
    s = _stat_group(g)
    assert s["count"] == 4
    assert s["mean_ret"] == pytest.approx(0.025, abs=1e-9)
    assert s["median_ret"] == pytest.approx(0.025, abs=1e-9)
    # 计数必须是 int（前端直接拼字符串 ✓，float 会显示成 4.0 ✗）
    for k in ("count", "limit_up_excluded", "limit_up_excluded_t",
              "limit_up_excluded_t1", "suspended_excluded", "extra_excluded"):
        assert isinstance(s[k], int), k
    # 6 位小数 ✓
    g2 = pd.DataFrame({"LABEL": [1 / 3]})
    assert _stat_group(g2)["mean_ret"] == 0.333333


def test_stat_group_total_exclusions_excludes_extra():
    """⭐ 总剔除 = T涨停 + T+1涨停 + 停牌；**extra（ST/创业板/科创板）不算在里面** ✓。"""
    g = pd.DataFrame({"LABEL": [0.01]})
    s = _stat_group(g, lu_t=2, lu_t1=3, susp=4, extra=5)
    assert s["limit_up_excluded"] == 2 + 3 + 4 == 9
    assert s["limit_up_excluded_t"] == 2
    assert s["limit_up_excluded_t1"] == 3
    assert s["suspended_excluded"] == 4
    assert s["extra_excluded"] == 5
    # 明确断言：总和里没有 extra ✓（混进去 = 9+5=14 ✗，用户对不上数 ✓）
    assert s["limit_up_excluded"] != 9 + 5


# ------------------------------------------------------------------- _ff_label_px_col
def test_ff_px_col_prefers_adjusted():
    df = pd.DataFrame(columns=["CLOSE_ADJ", "OPEN_ADJ", "CLOSE", "OPEN"])
    assert _ff_label_px_col(df, "$close") == "CLOSE_ADJ"
    assert _ff_label_px_col(df, "$open") == "OPEN_ADJ"


def test_ff_px_col_falls_back_to_raw_when_adjusted_missing():
    """老面板没有复权列时退回原始价 ✓ —— 但返回值**能看出退回了哪一列** ✓（调用方要记日志 ✓）。"""
    df = pd.DataFrame(columns=["CLOSE", "OPEN"])
    assert _ff_label_px_col(df, "$close") == "CLOSE"
    assert _ff_label_px_col(df, "$open") == "OPEN"


def test_ff_px_col_returns_want_when_nothing_present():
    df = pd.DataFrame(columns=["VOL"])
    assert _ff_label_px_col(df, "$close") == "CLOSE_ADJ"
    assert _ff_label_px_col(df, "$open") == "OPEN_ADJ"


# -------------------------------------------------------------------- _apply_ff_labels
def _mk(px_adj, px_raw=None, n_days=None):
    """单只股票、逐日的面板（index = [instrument, datetime] ✓）。"""
    n = n_days or len(px_adj)
    idx = pd.MultiIndex.from_arrays(
        [["SH600000"] * n, pd.bdate_range("2024-01-02", periods=n)],
        names=["instrument", "datetime"],
    )
    df = pd.DataFrame(index=idx)
    df["CLOSE_ADJ"] = px_adj
    df["OPEN_ADJ"] = px_adj
    df["CLOSE"] = px_adj if px_raw is None else px_raw
    df["OPEN"] = df["CLOSE"]
    return df


def test_ff_labels_never_invents_tail_labels():
    """⭐ 审计 ⑥：**未来价不存在的尾部行必须保持 NaN** ✓（不许拿"最后一天的价"当卖出价 ✗）。"""
    px = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5]
    df = _mk(px)
    df["label_2"] = np.nan
    _apply_ff_labels(df, {2: "label_2"}, [2], 0, "$close")

    got = df["label_2"].to_numpy()
    # i=1：entry=px[2]=10.2、exit=px[4]=10.4 ⇒ 0.0196078… ✓
    assert got[1] == pytest.approx(10.4 / 10.2 - 1.0, rel=1e-12)
    # i=3 起未来价不存在 ⇒ 必须还是 NaN ✓（旧实现会给 −? 或 0% ✗）
    assert np.isnan(got[3]) and np.isnan(got[4]) and np.isnan(got[5])
    # i=0 需要 entry=px[1]、exit=px[3] ⇒ 也应被填 ✓
    assert got[0] == pytest.approx(10.3 / 10.1 - 1.0, rel=1e-12)


def test_ff_labels_use_adjusted_chain_not_raw():
    """⭐ 审计 ⑦：价格链必须走**复权价** ✓ —— 否则除权日会算出假的 −50% ✗。"""
    adj = [10.0, 10.2, 10.4, 10.6, 10.8, 11.0]
    raw = [10.0, 10.2, 10.4, 5.3, 5.4, 5.5]      # 第 4 天除权，原始价腰斩 ✓
    df = _mk(adj, px_raw=raw)
    df["label_2"] = np.nan
    _apply_ff_labels(df, {2: "label_2"}, [2], 0, "$close")

    v = df["label_2"].to_numpy()[1]
    assert v == pytest.approx(10.8 / 10.4 - 1.0, rel=1e-12)   # ≈ +3.8% ✓
    assert v > 0, "用原始价会得 −49% ✗（审计 ⑦ 的那个假标签 ✓）"


def test_ff_labels_only_fill_nan_keep_existing():
    """只补**主标签为 NaN** 的行 ✓（已有标签一个字都不动 ✓）。"""
    px = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5]
    df = _mk(px)
    df["label_2"] = [0.5, np.nan, 0.6, np.nan, np.nan, np.nan]
    _apply_ff_labels(df, {2: "label_2"}, [2], 0, "$close")
    got = df["label_2"].to_numpy()
    assert got[0] == 0.5          # 原有值不动 ✓
    assert got[2] == 0.6
    assert got[1] == pytest.approx(10.4 / 10.2 - 1.0, rel=1e-12)   # NaN 被补上 ✓


def test_ff_labels_multiple_horizons():
    """多个持有期各自算各自的 ✓（不要互相覆盖 ✗）。"""
    px = [10.0 + 0.1 * i for i in range(10)]
    df = _mk(px)
    df["label_2"] = np.nan
    df["label_5"] = np.nan
    # ⚠ `label_cols` 的键是 **h 本身（int）** ✗ 不是 `"horizon_2"` 这种字符串 ✓（实测 KeyError: 2 ✓）
    _apply_ff_labels(df, {2: "label_2", 5: "label_5"}, [2, 5], 0, "$close")
    # i=1：h=2 ⇒ px[4]/px[2]-1 ✓；h=5 ⇒ px[7]/px[2]-1 ✓
    assert df["label_2"].to_numpy()[1] == pytest.approx(px[4] / px[2] - 1.0, rel=1e-12)
    assert df["label_5"].to_numpy()[1] == pytest.approx(px[7] / px[2] - 1.0, rel=1e-12)
    # h=5 的尾部更早变 NaN ✓（i=4 起 exit=px[10] 不存在 ✓）
    assert np.isnan(df["label_5"].to_numpy()[4])
