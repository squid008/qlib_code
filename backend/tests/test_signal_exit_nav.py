# -*- coding: utf-8 -*-
"""0/1 信号因子的**含离场回测**（事件驱动口径 ✓，v2.8.0）守门单测（离线合成面板 ✓ 不依赖 qlib）。

**为什么要它**（用户 2026-10-15 实测发现 ✓）：0/1 信号因子在单因子面板里**没有"每期名单"**
（`topk_curves` 为空 ✗，它走事件研究 ✓）⇒ 「每期全仓滚动」只会报"该行没有持仓曲线档位"✗
⇒ **勾了离场也没回测** ✗（界面看着像"没生效" ✗）。现在改走**事件驱动引擎** ✓
（与「交易信号测试」同源 ✓）：出信号买入 + 持有预测周期 + 离场信号提前平仓 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.routers.factors import SingleFactorTestFactor, SingleFactorTestRequest, _attach_signal_roll, _signal_exit_nav

CODES = ["SH600000", "SZ000001"]
CAL = pd.bdate_range("2024-01-01", periods=12)


def _panel(flat: bool = True) -> dict:
    """面板：默认全平；`flat=False` ⇒ SH600000 **从 CAL[4] 起跌到 8 元**（-20% ✓）。

    ⚠ 跌点选在 CAL[4] 是**故意的**：进场在 CAL[0]（T+1 = CAL[1] 买入 ✓），持有 5 天到期约在
      CAL[6] ⇒ 若在 CAL[2] 给离场信号，成交在 CAL[3]（**价还是 10** ✓）⇒ 躲开了后面的跌 ✓
      ⇒ 两条净值必须不同 ✓。第一版我把跌点放 CAL[6]、离场放 CAL[6] ✗ ⇒ 离场与"到期卖出"撞车
      （引擎按 `sell_q` 去重 ✓）⇒ 两条一模一样 ✗，是**测试构造错了**，不是功能错 ✓。
    """
    close = pd.DataFrame(10.0, index=CAL, columns=CODES)
    if not flat:
        close.loc[CAL[4]:, "SH600000"] = 8.0
    return {"CALENDAR": pd.DataFrame(index=CAL), "CLOSE": close, "OPEN": close.copy(),
            "CLOSE_RAW": close.copy(), "OPEN_RAW": close.copy(),
            "LIMIT_UP": close * 1.1, "LIMIT_DOWN": close * 0.9, "_limits_inferred": True}


def _evs() -> dict:
    """触发事件（= 单因子面板 `events_out` 的形状 ✓：`code` + `dt` ✓）。

    ⚠ **刻意不在 CAL[5] 之后给 SH600000 再发信号** ✗：那会在离场之后**重新买入** ✓
      ⇒ 离场省下的那点收益又被赔回去 ⇒ 两条净值几乎一样 ✗（我第二版就这么写错的 ✗，
      是**造数问题**、不是功能问题 ✓ —— 用 `ai_test/probe_signal_exit_1016.py` 现场验证过引擎 ✓）。
    """
    return {"Gt($close,0)": pd.DataFrame({
        "code": ["SH600000", "SZ000001"],
        "dt": pd.to_datetime([CAL[0], CAL[1]]),
    })}


def _mask(day: int, code: str = "SH600000") -> pd.Series:
    """(日期 × 股票) 布尔 mask（索引 = (instrument, datetime) ✓，与面板侧一致 ✓）。"""
    idx = pd.MultiIndex.from_product([CODES, CAL], names=["instrument", "datetime"])
    key = (code, CAL[day])
    return pd.Series([k == key for k in idx], index=idx, dtype=bool)


def _row() -> dict:
    return {"name": "黄金坑启动", "expression": "Gt($close,0)", "horizon": 5,
            "is_binary": True, "grouping": "binary"}


def _req(*exits) -> SingleFactorTestRequest:
    return SingleFactorTestRequest(
        universe="all", start_date="2024-01-01", end_date="2024-01-31",
        factors=[SingleFactorTestFactor(id="f", name="F", expression="Gt($close,0)")],
        exit_factors=list(exits), period_roll=True, fill="t1_open", cost=0.004)


# ---------------------------------------------------------------------------
# `_signal_exit_nav`：单条（对照 / 含离场）
# ---------------------------------------------------------------------------

def test_control_run_has_nav_and_buys_only():
    """对照（不带离场）⇒ 有净值 ✓，且**只有买**（没有离场卖单 ✓）。"""
    out = _signal_exit_nav(_req(), _row(), _evs(), {}, _panel(), with_exit=False)
    assert out.get("error") is None, out
    assert out["mode"] == "signal" and out["with_exit"] is False
    assert len(out["dates"]) == len(out["nav"]) > 0
    assert out["nav"][-1] is not None and out["perf"] is not None
    assert "对照" in out["label"], out["label"]


def test_exit_run_sells_and_differs():
    """含离场 ⇒ 真正产生**卖出**（提前平仓 ✓）⇒ 与对照的净值/成交不同 ✓。"""
    base = _signal_exit_nav(_req(), _row(), _evs(), {}, _panel(flat=False), with_exit=False)
    # 离场信号打在 CAL[2]（此时 SH600000 已持有 ✓）⇒ CAL[3] 成交（价 10 ✓）⇒ 躲开 CAL[4] 起的跌 ✓
    got = _signal_exit_nav(_req(), _row(), _evs(), {"mask": _mask(2)}, _panel(flat=False),
                           with_exit=True, exit_index=0, exit_name="入场价止损")
    assert got.get("error") is None, got
    assert got["with_exit"] is True and got["exit_index"] == 0
    assert "入场价止损" in got["label"], got["label"]
    # ⚠ 对照**也会有卖单**（持有到期/信号刷新都会卖 ✓ —— 事件驱动口径本来如此 ✓）
    #   ⇒ 不断言"对照 0 卖单" ✗（我第一版就这么写错的 ✗），只比净值差异 ✓
    assert got["nav"][-1] > base["nav"][-1], \
        "提前离场应躲开后面的下跌（净值更高 ✓）：%s vs %s" % (got["nav"][-1], base["nav"][-1])


def test_partial_mask_is_reindexed_not_crashing():
    """mask 只覆盖部分日期 ⇒ `_exit_matrix` 按面板 reindex（缺的按 False ✓）⇒ **不报错、照跑** ✓。

    ⚠ 这条与「每期全仓滚动」的 `exit_mat` 形状自检**不是一回事** ✗：那条吃的是**现成的 (T×C)
      矩阵**（形状必须严丝合缝 ✓），这里吃的是**带索引的 mask**（可按面板对齐 ✓）。
    """
    idx = pd.MultiIndex.from_product([CODES, CAL[:3]], names=["instrument", "datetime"])
    part = pd.Series(True, index=idx)
    out = _signal_exit_nav(_req(), _row(), _evs(), {"mask": part}, _panel(),
                           with_exit=True, exit_index=0, exit_name="X")
    assert out.get("error") is None, out
    assert len(out["nav"]) > 0 and out["mode"] == "signal"


def test_missing_events_reports_error():
    """没有触发事件 ⇒ 明确报错（提示重跑 ✓），不静默给空曲线 ✗。"""
    out = _signal_exit_nav(_req(), _row(), {}, {}, _panel(), with_exit=False)
    assert "触发事件" in str(out.get("error")), out


# ---------------------------------------------------------------------------
# `_attach_signal_roll`：整套（对照 + 逐个离场，形状与 period_roll 一致）
# ---------------------------------------------------------------------------

def test_attach_signal_roll_shape(monkeypatch):
    """★ 形状必须与「每期全仓滚动」一致 ✓ ⇒ 前端同一套渲染/导出 ✓：
    `period_roll_items`（对照 ✓）+ `exit_rolls`（每个离场一组 ✓）+ 兼容字段 ✓。"""
    monkeypatch.setattr("app.signals.pricing.load_price_panel",
                        lambda *a, **k: _panel(flat=False))
    r2 = SingleFactorTestRequest(
        universe="all", start_date="2024-01-01", end_date="2024-01-31",
        factors=[SingleFactorTestFactor(id="f", name="F", expression="Gt($close,0)")],
        exit_factors=[SingleFactorTestFactor(id="e1", name="离场1", expression="Gt($close,0)"),
                      SingleFactorTestFactor(id="e2", name="离场2", expression="Lt($close,0)")],
        period_roll=True, fill="t1_open", cost=0.004)
    row = _row()
    evs = _evs()
    # 第 0 个离场给真信号、第 1 个给空 mask（⇒ 退化成"与对照相同"，但不该报错 ✓）
    items = [{"mask": _mask(6)}, {"mask": _mask(0, code="SZ000001")}]
    out = _attach_signal_roll(r2, row, evs, exit_items=items)
    assert out.get("error") is None, out
    assert len(row["period_roll_items"]) == 1, "0/1 信号只有一条对照（没有档位概念 ✓）"
    assert row["period_roll"]["mode"] == "signal"
    gs = row["exit_rolls"]
    assert len(gs) == 2, gs
    assert [g["exit"]["name"] for g in gs] == ["离场1", "离场2"]
    assert all(g["items"][0].get("mode") == "signal" for g in gs)
    assert row["exit_roll_items"] is gs[0]["items"], "兼容字段 = 第一个离场 ✓"
    assert row["exit_roll"] == gs[0]["first"]
