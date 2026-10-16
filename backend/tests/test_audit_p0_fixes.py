# -*- coding: utf-8 -*-
"""**审计 P0 修复**的守门单测（v2.8.2，2026-10-16 ✓）—— 只覆盖这轮真正改动的两块：

· **审计 #3**：回测缓存键必须含**事件内容指纹** ✗→✓（`_nav_bt_key` / `_nav_req_key`）——
  原来只用「条数 + 首末触发日」代表一组事件 ⇒ 同股票池 + 同触发数 + 同首末触发日的两组不同信号
  会命中同一份 `BtResult`（净值直接搬错 ✓）。
· **审计 ⑥⑦**：缺价/停牌行的**标签兜底**（`_apply_ff_labels`）——
  ⑥ 未成熟尾部行**保持 NaN** ✓（原来用"最后一天的价"当卖出价造出假标签 ✗）；
  ⑦ 价格链走**复权** ✓（原来拿不复权的 `CLOSE/OPEN` 与复权主标签混算 ⇒ 除权日假的 −50% ✗）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors.single_test import _apply_ff_labels, _ff_label_px_col
from app.routers.factors import _ev_fingerprint, _nav_bt_key, _nav_req_key

CAL = pd.bdate_range("2024-01-01", periods=8)


def _ev(pairs) -> pd.DataFrame:
    """触发事件表（`code` + `dt` ✓，与 `events_out` 同形 ✓）。"""
    return pd.DataFrame({"code": [c for c, _d in pairs],
                         "dt": pd.to_datetime([d for _c, d in pairs])})


# ---------------------------------------------------------------------------
# 审计 #3：缓存键要含事件内容
# ---------------------------------------------------------------------------

def test_same_count_and_ends_but_different_codes_must_not_collide():
    """★ 核心：条数相同、首末触发日相同，但**标的/日期不同** ⇒ 键必须不同 ✓。"""
    a = _ev([("S00", CAL[0]), ("S01", CAL[1]), ("S02", CAL[2])])
    b = _ev([("S00", CAL[0]), ("S01", CAL[1]), ("S03", CAL[2])])   # 只有最后一只不同
    assert _ev_fingerprint(a) != _ev_fingerprint(b)
    args = (["S00", "S01", "S02", "S03"], CAL[0], CAL[3])
    assert _nav_bt_key(args[0], a, args[1], args[2], 5, 0.004, 1e7, ("event_even",), "t1_open") \
        != _nav_bt_key(args[0], b, args[1], args[2], 5, 0.004, 1e7, ("event_even",), "t1_open")
    assert _nav_req_key(args[0], a, args[1], args[2]) \
        != _nav_req_key(args[0], b, args[1], args[2])


def test_same_rows_reordered_must_still_collide():
    """同一组事件**顺序不同** ⇒ 指纹必须相同 ✓（否则"同一批信号"会因为排序变化而重复重算 ✗）。"""
    rows = [("S00", CAL[0]), ("S01", CAL[1]), ("S02", CAL[2])]
    assert _ev_fingerprint(_ev(rows)) == _ev_fingerprint(_ev(list(reversed(rows))))


def test_identical_events_still_hit_cache():
    """完全相同 ⇒ 键必须一致 ✓（缓存还要能用 ✓，不能修成"永远不命中" ✗）。"""
    ev = _ev([("S00", CAL[0]), ("S01", CAL[1]), ("S02", CAL[2])])
    assert _nav_bt_key(["S00"], ev, CAL[0], CAL[3], 5, 0.004, 1e7, ("event_even",)) \
        == _nav_bt_key(["S00"], ev, CAL[0], CAL[3], 5, 0.004, 1e7, ("event_even",))
    # 既有维度仍要生效：k 变了 ⇒ 另一个键 ✓（v2.1.65 那条回归 ✓）
    assert _nav_bt_key(["S00"], ev, CAL[0], CAL[3], 5, 0.004, 1e7, ("event_even",)) \
        != _nav_bt_key(["S00"], ev, CAL[0], CAL[3], 6, 0.004, 1e7, ("event_even",))


def test_fingerprint_is_robust_on_bad_input():
    """指纹算不出来 ⇒ 退回 0（= 旧行为 ✓ 少命中一次没关系 ✓），**绝不抛** ✗。"""
    assert _ev_fingerprint(pd.DataFrame()) == 0
    assert _ev_fingerprint(None) == 0


# ---------------------------------------------------------------------------
# 审计 ⑥⑦：标签兜底
# ---------------------------------------------------------------------------

def _ff_df():
    """一只票、8 天：**第 4 天除权**（真实价 10→5，复权价恒 10 ✓）+ 主标签大部分为 NaN ✓。"""
    idx = pd.MultiIndex.from_product([["S00"], CAL], names=["instrument", "datetime"])
    raw = [10.0, 10.0, 10.0, 10.0, 5.0, 5.0, 5.0, 5.0]
    df = pd.DataFrame({
        "CLOSE": raw,                                        # 真实价（不复权 ✓）
        "CLOSE_ADJ": [10.0] * 8,                             # 复权价（除权后不变 ✓）
        "OPEN": raw,
        "OPEN_ADJ": [10.0] * 8,
        "LABEL_2": [np.nan] * 8,
    }, index=idx)
    return df


def test_immature_tail_keeps_nan():
    """★⑥ 未成熟尾部（没有 T+1 / T+1+h 的价）⇒ **保持 NaN** ✓（原来会被填成假标签 ✗）。"""
    df = _ff_df()
    _apply_ff_labels(df, {2: "LABEL_2"}, [2], 0, "$close")
    v = df["LABEL_2"].to_numpy(dtype=float)
    # h=2 ⇒ 需要 T+1 与 T+3 两根未来价 ⇒ 只有 T ≤ 4 的行能算 ✓
    assert np.isfinite(v[0]) and np.isfinite(v[4])
    assert np.isnan(v[5:]).all(), "尾部 3 行必须保持 NaN ✓（不再用'最后价'兜底 ✗）：%s" % v


def test_fallback_uses_adjusted_price_chain():
    """★⑦ 除权日必须按**复权**口径算 ⇒ 兜底值应为 0%（而不是真实价链的 −50% ✗）。"""
    df = _ff_df()
    _apply_ff_labels(df, {2: "LABEL_2"}, [2], 0, "$close")
    # T=2：T+1 = 第 3 天、T+3 = 第 5 天；复权价都是 10 ⇒ 0%；真实价是 10 → 5 ⇒ −50% ✗
    assert abs(float(df["LABEL_2"].iloc[2])) < 1e-12, df["LABEL_2"].tolist()
    assert df["LABEL_2"].iloc[2] != pytest.approx(-0.5), "不得退回真实价口径 ✗"


def test_main_label_is_not_overwritten():
    """主标签**有值**的行绝不能被兜底覆盖 ✓（原语义 ✓，别修坏 ✗）。"""
    df = _ff_df()
    df.loc[(slice(None), CAL[0]), "LABEL_2"] = 0.123
    _apply_ff_labels(df, {2: "LABEL_2"}, [2], 0, "$close")
    assert float(df["LABEL_2"].iloc[0]) == pytest.approx(0.123)


def test_px_col_prefers_adjusted_then_falls_back():
    """取价列：优先复权列 ✓；老面板没有复权列 ⇒ 退回原始列（并在测试里显式记录这件事 ✓）。"""
    df = _ff_df()
    assert _ff_label_px_col(df, "$close") == "CLOSE_ADJ"
    assert _ff_label_px_col(df, "$open") == "OPEN_ADJ"
    old = df.drop(columns=["CLOSE_ADJ", "OPEN_ADJ"])
    assert _ff_label_px_col(old, "$close") == "CLOSE"


# ---------------------------------------------------------------------------
# 审计 #1：purge（训练/验证段末退到测试起点前 h+1 个**交易日**）
# ---------------------------------------------------------------------------

@pytest.mark.datareq
def test_purged_train_end_leaves_exactly_h_trading_days_gap():
    """★ 核心口径：段末退到 `pre[-(h+1)]` ⇒ **最后一条可用样本的标签止于测试起点前一个交易日** ✓
    （即：训练/验证样本**一个价格点都不碰测试期** ✓）。

    ⚠ 这条**要日历** ⇒ 必须打 `datareq`（CI 里"无数据自动跳过" ✓）—— 2026-10-16 就是漏了这一步，
      CI 直接红 ✗（`ValueError: can't find a freq from [] ...` 空日历 ✓）；本地有数据 ⇒ 照常跑 ✓。
    """
    from app.engine.qlib_engine import _purged_train_end

    test_start = "2024-03-01"
    h = 5
    try:
        from app.services.qlib_runtime import ensure_qlib_init

        ensure_qlib_init()          # ⚠ 必须先 init，否则 `D.calendar` 直接抛 ✗（测试里踩过 ✓）
        from qlib.data import D

        cals = [str(d)[:10] for d in D.calendar(end_time=test_start)]
    except Exception as e:                                                # noqa: BLE001
        pytest.skip("无日历/未接数据（datareq 环境才跑 ✓）：%r" % (e,))
    if len(cals) < 30:
        pytest.skip("日历为空（本机/CI 无数据集）⇒ 由 datareq 环境跑 ✓")

    out = _purged_train_end(test_start, h)
    assert out < test_start
    pre = [d for d in cals if d < test_start]
    gap = len([d for d in cals if out < d < test_start])
    assert gap == h, "交易日间隔应为 h=%d（含 out 共 h+1 个位置）✓ 实际 %d" % (h, gap)
    # 最后一条可用样本（out）的标签覆盖 out..out+h ⇒ 终点 = 测试起点前**一个交易日** ✓
    assert out == pre[-(h + 1)], (out, pre[-(h + 1)])


def test_purged_train_end_falls_back_to_natural_days(monkeypatch):
    """日历不可用 ⇒ 退回**自然日** `(h+1)×2` ✓（≥ 周末两天的量 ✓），且绝不返回"前一天" ✗。"""
    import app.engine.qlib_engine as qe

    class _BadD:
        @staticmethod
        def calendar(**_kw):
            raise RuntimeError("no calendar")

    monkeypatch.setitem(__import__("sys").modules, "qlib.data", type("m", (), {"D": _BadD}))
    out = qe._purged_train_end("2024-03-01", 5)
    from datetime import datetime
    days = (datetime.strptime("2024-03-01", "%Y-%m-%d")
            - datetime.strptime(out, "%Y-%m-%d")).days
    assert days >= 6, "自然日兜底至少 (h+1) 天 ✓ 实际 %d" % days


def test_train_semantics_bumped_for_purge():
    """训练口径变了 ⇒ **必须升戳** ✓（否则旧模型产物会被静默复用 ✗）。"""
    from app.engine.qlib_engine import TRAIN_SEMANTICS
    assert TRAIN_SEMANTICS != "1.20.46", "purge 改了模型结果 ⇒ 口径戳必须升 ✓"
