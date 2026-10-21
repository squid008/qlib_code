# -*- coding: utf-8 -*-
"""`routers/factors_single.py` 的**入队校验**与**进度视图**（★ v2.10.44，2026-10-21 ✓）。

## 为什么挑这两块（不碰那个 1,174 行的端点本体）

全量实测里这个文件 **37.3% / 396 行未覆盖** ✓，但**大头在端点本体**（要真面板 ✗）。
真正能脱数据测、且**错了后果很严重**的是两处 ✓：

* `_apply_exit_validation` —— **入队前**的校验 ✓：拦不住 ⇒ 任务跑几分钟才炸 ✗，
  或者更糟：**照做** ⇒ 净值恒 1 / 被手续费磨光 ✓（用户两种都看不到原因 ✗）；
* `_sft_render_view` —— **进度与文案的唯一出口** ✓（多线程只写状态、不写 message ✓）：
  算错 ⇒ 进度条回跳 / 顶在 100% 干等 ✓ —— 这两条都是**用户报过**的 ✓。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers.factors_models import SingleFactorTestFactor, SingleFactorTestRequest
from app.routers.factors_single import (
    _apply_exit_validation,
    _exit_list,
    _sft_render_view,
    _sft_touch,
)


def _f(expr: str, name: str = "f", fid: str = "1") -> SingleFactorTestFactor:
    return SingleFactorTestFactor(id=fid, name=name, expression=expr)


def _req(**kw) -> SingleFactorTestRequest:
    base = {"factors": [_f("CLOSE > 10", "进场A")]}
    base.update(kw)
    return SingleFactorTestRequest(**base)


# ------------------------------------------------------------------ _exit_list
def test_exit_list_drops_empty_and_dedups_keeping_order():
    """空表达式丢掉 ✓、**按表达式去重** ✓、**保序** ✓（同一公式选两次只算一遍 ✓）。"""
    req = _req(exit_factors=[
        _f("", "空1", "e0"),
        _f("CLOSE < 5", "止损", "e1"),
        _f("  CLOSE < 5  ", "重复(带空格)", "e2"),
        _f("MA(CLOSE,5) > MA(CLOSE,20)", "破位", "e3"),
    ])
    got = _exit_list(req)
    assert [g.name for g in got] == ["止损", "破位"], "去重 + 保序都不许错 ✗"


def test_exit_list_merges_singular_form():
    """旧的单数形态 `exit_factor` 要**合并进列表** ✓（老前端/老请求仍能用 ✓）。"""
    req = _req(exit_factors=[_f("CLOSE < 5", "止损", "e1")], exit_factor=_f("CLOSE < 3", "硬止损", "e2"))
    assert [g.name for g in _exit_list(req)] == ["止损", "硬止损"]


def test_exit_list_handles_none():
    assert _exit_list(_req()) == []


# -------------------------------------------------------- _apply_exit_validation
def test_no_exit_keeps_period_roll_untouched():
    """没勾离场 ⇒ **不许**偷偷打开 `period_roll` ✓（那是分钟级附加回测 ✗）。"""
    req = _req(period_roll=False)
    _apply_exit_validation(req)
    assert req.period_roll is False


def test_exit_normalises_and_forces_period_roll():
    """勾了离场 ⇒ 规范化写回 ✓ 且**自动打开 `period_roll`** ✓（离场只能作用在真金白银那条回测上 ✓）。"""
    req = _req(exit_factors=[_f("CLOSE < 5", "止损", "e1")], exit_factor=_f("CLOSE < 5", "重复", "e2"),
               period_roll=False)
    _apply_exit_validation(req)
    assert len(req.exit_factors) == 1
    assert req.exit_factor is None, "单数形态归一后必须置 None ✓（下游只看一个字段 ✓）"
    assert req.period_roll is True


def test_same_formula_as_entry_and_exit_is_rejected():
    """⭐⭐ 同公式既进又出 ⇒ **必须 400** ✓ —— 照做的话按"同日进出不交易"规则
    **永远开不了仓**（净值恒 1）✓，硬跑则会**买完就卖、被手续费磨光** ✓。"""
    req = _req(factors=[_f("CLOSE > 10", "进场A")],
               exit_factors=[_f("CLOSE > 10", "离场B", "e1")])
    with pytest.raises(HTTPException) as ei:
        _apply_exit_validation(req)
    assert ei.value.status_code == 400
    assert "永远开不了仓" in str(ei.value.detail), "必须把**后果**说清楚 ✓（只说'不允许'没用 ✗）"


@pytest.mark.parametrize("expr", [
    "CLOSE < ENTRY_PRICE*0.95",     # 用户**手写**的形态 ✓（面板/文档/报错文案全是这个 ✓）
    "CLOSE < $entry_price*0.95",    # 归一化后的形态 ✓（前端可能已经转好 ✓）
    "CLOSE < ENTRY_FIRST",
])
def test_entry_price_in_entry_factor_is_rejected(expr):
    """⭐⭐ `ENTRY_PRICE` 是**回测状态量** ✓ ⇒ 写在进场因子里算不出意义 ✓ ⇒ 必须 400 且告诉他写哪 ✓。

    ⚠ **两种写法都必须拦** ✗→✓（v2.10.44 修）：原来 `uses_entry` 只认归一化后的 `$entry_price` ✗，
    而用户写的是 `ENTRY_PRICE` ✓ ⇒ **这道闸根本不触发** ✗（实测返回 False ✓）。
    """
    req = _req(factors=[_f(expr, "错放进场")])
    with pytest.raises(HTTPException) as ei:
        _apply_exit_validation(req)
    assert ei.value.status_code == 400
    assert "离场公式" in str(ei.value.detail), "错误消息要说**该怎么改** ✓"


# --------------------------------------------------------------- _sft_render_view
def _state(**kw) -> dict:
    base = {
        "status": "running", "progress": 0.0, "message": "",
        "config": {"horizons": [1, 5, 20]},
        "running_h": [], "queued_h": [], "done_n": 0,
        "per_h_prog": {}, "per_h_msg": {},
        "load_prog": 0.0, "load_msg": "", "cancel_requested": False,
        "extra_prog": 0.0, "extra_msg": "", "ts": 0.0,
    }
    base.update(kw)
    return base


def test_terminal_states_pass_through():
    for st in ("success", "failed", "cancelled"):
        prog, msg = _sft_render_view({"status": st, "progress": 100.0, "message": "已取消"})
        assert prog == 100.0 and msg == "已取消"


def test_cancel_requested_shows_cancelling():
    prog, msg = _sft_render_view(_state(cancel_requested=True))
    assert prog == 0.0 and "取消" in msg


def test_queued_stage_is_zero_and_explains_why():
    """⭐ 排队阶段进度 **= 0** ✓ 且文案要说"**等并发单元**" ✓ ——
    否则用户看到"0% 不动"以为是卡死了 ✗（实测这条正是为了解释"为什么还没开始"✓）。"""
    prog, msg = _sft_render_view(_state(queued_h=[1, 5], done_n=0))
    assert prog == 0.0
    assert "排队" in msg and "并发" in msg


def test_loading_stage_uses_load_progress_in_0_30():
    prog, msg = _sft_render_view(_state(load_prog=12.5, load_msg="正在加载特征..."))
    assert prog == 12.5 and "加载" in msg
    assert 0.0 <= prog <= 30.0, "共享加载阶段必须落在 0~30 ✓（否则后面统计段会越过 100 ✗）"


def test_stats_stage_is_monotonic_in_done_count():
    """⭐⭐ **进度不许随完成数回跳** ✓（用户报过 ✓）——逐周期加完成数，进度必须不减 ✓。"""
    prev = -1.0
    for done in (0, 1, 2):
        prog, _msg = _sft_render_view(_state(running_h=[20], done_n=done,
                                             per_h_prog={20: 50.0}))
        assert prog >= prev, "进度回跳 ✗（done=%d 时 %.1f < 上一个 %.1f）" % (done, prog, prev)
        prev = prog
    assert 30.0 <= prev <= 95.0, "统计阶段必须落在 30~95 ✓（封顶 95 是给尾巴阶段留的 ✓）"


def test_all_periods_done_never_reports_100():
    """⭐⭐ 统计跑完**不许报 100%** ✓ —— 后面还有"取全池行情 + 逐档回测"的尾巴 ✓。
    旧实现直接给 100% ⇒ **进度条顶在 100% 干等** ✓（用户 2026-10-15 反馈 ✓）。"""
    prog, msg = _sft_render_view(_state(done_n=3, running_h=[], extra_prog=50.0))
    assert prog < 100.0, "统计完成就报 100% ✗（后面还有附加阶段 ✓）"
    assert 95.0 <= prog <= 99.5, "尾巴阶段必须落在 95~99.5 ✓"
    assert msg, "尾巴阶段必须有文案 ✓（空串 ⇒ 界面只剩一个 100% ✗）"


def test_touch_updates_timestamp_only():
    st = _state(ts=0.0, message="不许动我")
    _sft_touch(st)
    assert st["ts"] > 0.0
    assert st["message"] == "不许动我", "写侧**不许**改 message ✓（消息统一在读侧合成 ✓）"
