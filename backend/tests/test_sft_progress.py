# -*- coding: utf-8 -*-
"""单因子测试**进度模型**回归（v2.4.2，用户 2026-10-15 截图反馈 ✓）。

用户原话：「这个 BS 点算出来 100% 了还要等很久才显示下面的净值曲线面板，要么进度条显示 98%，
  还是怎么处理？」

现场（`routers/factors.py::_run`）：统计跑完 ⇒ `_sft_render_view` 直接给 **100%** ✗，而**之后**
才去跑「每期全仓滚动（实盘口径）」（先取全池行情面板、再逐档逐日回测，全 A 十秒~分钟级 ✗）；
若只选了 1 个预测周期，那段窗口连 `message` 都是**空串** ✗ ⇒ 界面只剩"进度 100%"顶着不动 ✗。

修法（本条测试守着 ✓）：
  · 统计阶段**封顶 95%** ✓；统计之后的"整理 + 每期全仓滚动"走 `extra_prog` ⇒ **95 → 99.5%** ✓
    （只有真正 `status=success` 才到 100 ✓）；
  · 该阶段必须有**实时文案**（含"第 k/n 档"✓），并且**绝不为空** ✓（空文案是本次的坑之一 ✗）。
"""
from __future__ import annotations

import types

import pytest

from app.routers import factors as F


def _running_state(**kw) -> dict:
    st = {
        "status": "running",
        "config": {"horizons": [60]},
        "running_h": [],
        "queued_h": [],
        "done_n": 1,
        "per_h_prog": {60: 100.0},
        "per_h_msg": {},
        "load_prog": 30.0,
        "load_msg": "特征数据就绪",
        "extra_prog": 0.0,
        "extra_msg": "",
    }
    st.update(kw)
    return st


def test_stats_done_does_not_jump_to_100():
    """★ 核心：统计完成后**不能**直接 100% ✗（尾巴要留给"每期全仓滚动"✓）。"""
    p, m = F._sft_render_view(_running_state())
    assert p <= 95.0, "统计完成后进度就顶到 100%% 了（用户报的正是这个 ✗）：%s" % p
    assert m.strip(), "该阶段文案不能为空 ✗（单周期时以前是空串 ⇒ 界面只剩『进度 100%』✗）"


def test_extra_phase_moves_within_tail():
    """附加阶段按 `extra_prog` 在 95 → 99.5 之间推进 ✓（并且**单调**、**不到 100** ✓）。"""
    st = _running_state(extra_msg="整理结果（1/2）：每期全仓滚动（实盘口径）：第 1/2 档回测中…")
    vals = []
    for pct in (0.0, 25.0, 50.0, 99.0, 100.0):
        st["extra_prog"] = pct
        p, m = F._sft_render_view(st)
        vals.append(p)
        assert "档" in m, "文案必须带上『第 k/n 档』（用户据此判断在动 ✓）：%r" % m
    assert vals == sorted(vals), "进度必须单调不减 ✗：%s" % vals
    assert vals[0] >= 95.0 and vals[-1] <= 99.5, "尾巴应落在 95~99.5 ✓：%s" % vals


def test_success_is_the_only_way_to_100():
    """只有终态才 100% ✓（前端据此把进度条"钉死"当作完成信号 ✓）。"""
    st = _running_state(status="success", extra_prog=100.0)
    p, m = F._sft_render_view(st)
    assert p == 100.0 and m == "success"


def test_attach_period_roll_reports_progress(monkeypatch):
    """★ 接线：`_attach_period_roll` 必须把 `progress_cb` 报出来 ✓（否则上面的尾巴永远不动 ✗）。"""
    monkeypatch.setattr(F, "_period_roll_pick", lambda *a, **k: (None, ["2024-01-02"], []))
    seen: list = []
    row = {"topk_curves": {"items": [{"k": 5}]}}
    F._attach_period_roll(types.SimpleNamespace(start_date="2024-01-01", end_date="2024-02-01"),
                          {}, row, progress_cb=lambda f, m: seen.append((f, m)))
    assert seen, "一次回调都没有 ✗"
    assert seen[0][0] <= 0.1, "起步应在 10% 以内 ✓：%s" % (seen[0],)
    assert seen[-1][0] == 1.0, "结束必须报 1.0 ✓：%s" % (seen[-1],)
    assert all(m.strip() for _f, m in seen), "文案不能为空 ✗"


def test_progress_cb_failure_is_swallowed(monkeypatch):
    """报进度本身出问题**绝不能**拖垮回测 ✓（它是附加信息 ✓，与 `perf` 同规矩 ✓）。"""
    monkeypatch.setattr(F, "_period_roll_pick", lambda *a, **k: (None, ["2024-01-02"], []))

    def _bad(_f, _m):
        raise RuntimeError("炸了")

    row = {"topk_curves": {"items": [{"k": 5}]}}
    F._attach_period_roll(types.SimpleNamespace(start_date="2024-01-01", end_date="2024-02-01"),
                          {}, row, progress_cb=_bad)      # 不得抛出 ✓
    assert "period_roll_items" in row


@pytest.mark.parametrize("h,n", [(60, 1), (60, 3)])
def test_queued_and_load_phases_unchanged(h, n):
    """队列/加载两段行为不变 ✓（回归保护：别把 0-30 的加载口径改坏 ✗）。"""
    st = _running_state(config={"horizons": [h] * n}, done_n=0, per_h_prog={h: 0.0},
                        queued_h=[h], load_prog=0.0, load_msg="")
    p, m = F._sft_render_view(st)
    assert p == 0.0 and "排队" in m
    st["queued_h"] = []
    st["load_prog"] = 12.0
    st["load_msg"] = "加载特征数据 3/8..."
    p2, m2 = F._sft_render_view(st)
    assert p2 == 12.0 and m2 == "加载特征数据 3/8..."
