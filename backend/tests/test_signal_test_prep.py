# -*- coding: utf-8 -*-
"""信号回灌的口径解析（★ v2.11.14 —— 从 `routers/signal_test.py::run` 抽出后能单测）。

## 这里钉的是**三个用户报障**的合成
| 版本 | 报障原话 | 真因 |
|---|---|---|
| v2.1.72 | 「**不复权**，这里黄金坑我测下来信号测试还是有问题」 | 取价**永远**是后复权 ✗，文件里的 `price_adjust` 没人消费 ✗ |
| v2.1.74 | 审计 #2 | `price_round` 没进文件 ⇒ 本侧硬编码 `.round(2)` ✗ |
| v2.1.79 | 「要与聚宽对齐」 | 只给事件研究真实价 ✗ ⇒ 回测/净值仍后复权 ⇒ **一次回灌两个口径** ✗ |

⇒ 现在的契约：`none` ⇒ 真实价**同时**给事件研究与 `panel`（全链同口径 ✓），
但 `CLOSE_RAW`/`OPEN_RAW` **本身不许动** ✓（停牌判定与"真实价 vs 涨跌停价"比对仍按它 ✓）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.routers.signal_test_prep import (
    EXCLUDE_KEYS,
    resolve_exclude_flags,
    resolve_price_tables,
)


def _panel():
    idx = pd.Index(["d1", "d2"])
    return {
        "CLOSE": pd.DataFrame({"A": [10.5, 11.0]}, index=idx),        # 后复权
        "OPEN": pd.DataFrame({"A": [10.0, 10.6]}, index=idx),
        "CLOSE_RAW": pd.DataFrame({"A": [10.123, np.nan]}, index=idx),  # 真实价（d2 停牌 ✓）
        "OPEN_RAW": pd.DataFrame({"A": [9.876, np.nan]}, index=idx),
    }


def test_non_none_adjust_keeps_panel_untouched():
    """⭐ 前/后复权 ⇒ **不换价表** ✓（只差整体缩放 ⇒ 比率口径等价 ✓）且无 warning ✓。"""
    p0 = _panel()
    panel, px_es, warns, pa, _pr = resolve_price_tables(p0, {"price_adjust": "backward"})
    assert px_es == {} and warns == [] and pa == "backward"
    assert panel is p0, "没有替换时不该产生新 dict ✓"


def test_none_adjust_swaps_in_raw_prices_for_event_and_whole_chain():
    """⭐⭐⭐ **不复权 ⇒ 真实价同时给事件研究与 `panel`** ✓（v2.1.79：全链同口径 ✓）。

    若只给事件研究 ✗ ⇒ 回测/净值仍后复权 ⇒ **一次回灌里两个口径** ✓
    （图与表对不上，而两者都"看着合理" ✗）。
    """
    panel, px_es, warns, _pa, _pr = resolve_price_tables(_panel(), {"price_adjust": "none"})
    assert set(px_es) == {"CLOSE", "OPEN"}
    assert panel["CLOSE"].loc["d1", "A"] == pytest.approx(10.12), "真实价 + 按分取整 ✓"
    assert px_es["CLOSE"].loc["d1", "A"] == pytest.approx(10.12), "事件研究拿到的必须是同一张 ✓"
    assert any("不复权" in w for w in warns), "必须告知用户口径已切换 ✓"


def test_raw_columns_are_not_modified():
    """⭐⭐ `CLOSE_RAW`/`OPEN_RAW` **本身不动** ✓ —— 停牌判定与"真实价 vs 涨跌停价"比对仍按它 ✓
    （拿 ffill+取整后的价去判涨跌停 ⇒ 判定会飘 ✓）。"""
    p0 = _panel()
    raw_before = p0["CLOSE_RAW"].copy()
    panel, _px, _w, _pa, _pr = resolve_price_tables(p0, {"price_adjust": "none"})
    pd.testing.assert_frame_equal(panel["CLOSE_RAW"], raw_before)
    assert panel["CLOSE_RAW"].loc["d2", "A"] != panel["CLOSE_RAW"].loc["d2", "A"], "RAW 里 d2 仍是 NaN ✓"


def test_panel_is_shallow_copied_not_mutated_in_place():
    """⭐⭐ 原 `panel` **不许被就地改** ✓（`load_price_panel` 的返回可能被别处引用 ✓）——
    就地改会**外溢**到别的分支 ✓（比如后面还要按复权价算基准 ✓）。"""
    p0 = _panel()
    orig_close = p0["CLOSE"].copy()
    resolve_price_tables(p0, {"price_adjust": "none"})
    pd.testing.assert_frame_equal(p0["CLOSE"], orig_close), "原对象必须保持后复权 ✓"


def test_price_round_false_is_respected_and_missing_key_defaults_true():
    """⭐ **`price_round` 跟着文件走** ✓（v2.1.74）：
    显式 `False` ⇒ **不取整** ✓；老文件**没这个键** ⇒ 按面板默认 `True` ✓（与当年一致 ✓）。"""
    _p, _px, _w, _pa, _pr = resolve_price_tables(_panel(), {"price_adjust": "none", "price_round": False})
    assert _pr is False
    assert _p["CLOSE"].loc["d1", "A"] == pytest.approx(10.123), "不取整 ⇒ 保留三位 ✓"
    _p2, _px2, _w2, _pa2, _pr2 = resolve_price_tables(_panel(), {"price_adjust": "none"})
    assert _pr2 is True, "老文件缺键 ⇒ 默认 True ✓"
    assert _p2["CLOSE"].loc["d1", "A"] == pytest.approx(10.12)


def test_missing_raw_columns_falls_back_with_warning():
    """⭐⭐ 文件要"不复权"但**取价里没有真实价列** ⇒ **退回复权价 + 明确 warning** ✓ ——
    静默按后复权算 ✗ 就退回 v2.1.72 那个 bug ✓（用户会以为"不复权生效了" ✓）。"""
    p = {"CLOSE": pd.DataFrame({"A": [10.5]}), "OPEN": pd.DataFrame({"A": [10.0]})}
    panel, px_es, warns, _pa, _pr = resolve_price_tables(p, {"price_adjust": "none"})
    assert panel is p and px_es == {}
    assert any("退回复权价" in w for w in warns)


def test_exclude_flags_priority_request_then_file_then_false():
    """⭐ 剔除开关：**请求显式 > 文件 > False** ✓ ——
    新文件自带开关 ⇒ 自动对齐弹窗口径 ✓；老文件没有 ⇒ 退回**不剔除** ✓（= 当年口径 ✓，不改历史结果 ✓）。"""

    class _Req:
        exclude_limit_up_signal = True        # 请求显式 ✓ ⇒ 用它
        exclude_suspended = None              # 请求没给 ⇒ 看文件 ✓
        exclude_st_t1 = None                  # 请求与文件都没给 ⇒ False ✓

    al = {"exclude_suspended": True, "exclude_st_t1": False, "exclude_stock_kcb": True}
    got = resolve_exclude_flags(_Req(), al)
    assert set(got) == set(EXCLUDE_KEYS), "六个开关都要有值 ✓（少一个下游 `KeyError` ✓）"
    assert got["exclude_limit_up_signal"] is True
    assert got["exclude_suspended"] is True
    assert got["exclude_st_t1"] is False
    assert got["exclude_stock_kcb"] is True, "请求没给 ⇒ 用文件里的 True ✓"
    assert got["exclude_stock_gem"] is False, "两边都没给 ⇒ False ✓"
