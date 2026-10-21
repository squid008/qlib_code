# -*- coding: utf-8 -*-
"""`routers/factors_single.py` 的**任务端点**与**每期名单解析**（★ v2.10.48，2026-10-21 ✓）。

## 挑这两块的理由

端点本体（1,174 行的 `single_factor_test`）要真面板 ✗，但这四个小端点
（`progress` / `cancel` / `clear` / `tasks`）**只动内存里的任务表** ✓ ——
而它们恰恰是**前端刷新页面后能不能恢复**、**取消按钮灵不灵**的关键 ✓。

`_period_roll_pick` 则决定导出 XLSX 时**能不能取到每期持仓** ✓。
"""
from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi import HTTPException

from app.factors.holdings_index import HoldingsIndex
from app.routers import factors_single as F


# ----------------------------------------------------------------- 工具 / 清理
@pytest.fixture(autouse=True)
def clean_tasks():
    """⭐ 每个用例**先备份 → 清空 → 跑 → 恢复** ✓。

    ⚠ 只备份/恢复、不**清空**是不够的 ✗（我第一版就是这样 ✓）：
    模块级 `_SFT_TASKS` 里可能已经有**别的用例**留下的条目 ✓（甚至字段不全 ✓，
    实测 `tasks` 端点因此 `KeyError: task_id` ✓、`clear` 数出 6 条而不是 2 条 ✓）。
    """
    with F._SFT_LOCK:
        backup = dict(F._SFT_TASKS)
        F._SFT_TASKS.clear()
    yield
    with F._SFT_LOCK:
        F._SFT_TASKS.clear()
        F._SFT_TASKS.update(backup)


def _put(state: str, *, ts: float = None, **kw) -> None:
    # ⚠ `task_id` 必须等于字典键 ✓（真实代码就是这么存的 ✓；写死成 "T1" 会让
    #   `tasks` 端点返回一排同名 id ✗ —— 实测就是这样红的 ✓）
    base = {"task_id": state, "status": "running", "progress": 0.0, "message": "",
            "ts": time.time() if ts is None else ts, "config": {"horizons": [1]},
            "running_h": [], "queued_h": [], "done_n": 0, "per_h_prog": {},
            "per_h_msg": {}, "load_prog": 0.0, "load_msg": "",
            "cancel_requested": False, "extra_prog": 0.0, "extra_msg": ""}
    base.update(kw)
    with F._SFT_LOCK:
        F._SFT_TASKS[state] = base


# --------------------------------------------------------------------- progress
def test_progress_unknown_task_is_404():
    with pytest.raises(HTTPException) as ei:
        F.single_factor_test_progress("nope")
    assert ei.value.status_code == 404


def test_progress_cleans_nan_and_inf_in_result():
    """⭐⭐ 结果里的 `NaN / Inf` **必须清洗成 `None`** ✓ ——
    不清洗时 FastAPI 序列化直接抛 ✗ ⇒ **整个接口 500** ✓，而用户只是某个极端样本算出了 Inf ✓。"""
    _put("T9", status="success", progress=100.0, message="完成",
         result={"ic": float("nan"), "sharpe": float("inf"), "ok": 1.5})
    out = F.single_factor_test_progress("T9")
    assert out["status"] == "success"
    assert out["result"]["ic"] is None and out["result"]["sharpe"] is None
    assert out["result"]["ok"] == 1.5


# ----------------------------------------------------------------------- cancel
def test_cancel_finished_task_is_noop(monkeypatch):
    """⭐ 已结束的任务**不许**被改成"取消中" ✓（否则前端会一直转圈 ✗）。"""
    monkeypatch.setattr(F, "get_task_manager", lambda *a, **k: pytest.fail("不该被调用"))
    _put("T2", status="success", message="已完成")
    out = F.single_factor_test_cancel("T2")
    assert out["ok"] is False and "已完成" in out["message"]
    with F._SFT_LOCK:
        assert F._SFT_TASKS["T2"]["cancel_requested"] is False


def test_cancel_running_task_sets_flag(monkeypatch):
    class _M:
        def wake_external_waiters(self):
            self.called = True
    m = _M()
    monkeypatch.setattr(F, "get_task_manager", lambda *a, **k: m)
    _put("T3", status="running")
    out = F.single_factor_test_cancel("T3")
    assert out["ok"] is True
    with F._SFT_LOCK:
        assert F._SFT_TASKS["T3"]["cancel_requested"] is True
    assert getattr(m, "called", False) is True, "必须唤醒排队 worker ✓（否则取消要等它拿到槽 ✗）"


def test_cancel_unknown_task_is_404():
    with pytest.raises(HTTPException):
        F.single_factor_test_cancel("nope")


# ------------------------------------------------------------------------ clear
def test_clear_only_removes_finished():
    """⭐ 只删**已结束**的 ✓；运行中 / 排队中**必须留** ✓（前端还在轮询 ✓）。"""
    _put("done1", status="success")
    _put("done2", status="cancelled")
    _put("run1", status="running")
    out = F.single_factor_test_clear()
    assert out == {"ok": True, "cleared": 2}
    with F._SFT_LOCK:
        assert set(F._SFT_TASKS) == {"run1"}


# ------------------------------------------------------------------------ tasks
def test_tasks_sorted_by_ts_desc():
    _put("old", ts=1000.0)
    _put("new", ts=2000.0)
    _put("mid", ts=1500.0)
    ids = [t["task_id"] for t in F.single_factor_test_tasks()["tasks"]]
    assert ids == ["new", "mid", "old"], "必须按时间倒序 ✓（刷新后最先看到最近的 ✓）"


def test_tasks_limit_semantics():
    """⭐ 实测：`limit=0` **走默认 20** ✓ —— 因为代码是 `int(limit or 20)` ✓，而 **`0` 是 falsy** ✗。

    ⚠ 这与 `build_event_stats` 的 `max_k=0 ⇒ 默认 40` ✓ 是**同一个惯用法坑** ✓
    （本项目里 `x or <默认>` 出现多次 ✓）：`0` 从来不会被当成"用户真的想要 0" ✓。
    所以这里只钉两件确定的事 ✓：① `0` ⇒ 默认（不是 1 ✓）；② 上限 100 ✓。
    """
    for i in range(5):
        _put("t%d" % i, ts=float(i))
    assert len(F.single_factor_test_tasks(limit=0)["tasks"]) == 5, "0 ⇒ 默认 20 ⇒ 全返回 ✓"
    assert len(F.single_factor_test_tasks(limit=999)["tasks"]) == 5, "上限 100，但只有 5 条 ✓"


def test_tasks_include_cancel_flag():
    _put("T5", cancel_requested=True)
    row = F.single_factor_test_tasks()["tasks"][0]
    assert row["cancel_requested"] is True, "前端靠这个显示「正在取消」✓"


# ------------------------------------------------------------ _period_roll_pick
def _state_with(expr: str, horizon: int, items: dict, dates=None) -> dict:
    hi = HoldingsIndex()
    hi.put(expr, horizon, "high", dates or ["2024-01-02", "2024-01-09"], items)
    return hi.as_state()


def test_pick_hits_exact_key():
    st = _state_with("CLOSE>10", 5, {("topk", 50, None): [["A", "B"], ["C"]]})
    err, dates, lists = F._period_roll_pick(
        st, {"expression": "CLOSE>10", "horizon": 5}, {"kind": "topk", "k": 50})
    assert err is None and lists == [["A", "B"], ["C"]]
    assert dates == ["2024-01-02", "2024-01-09"]


def test_pick_falls_back_when_decile_k_was_rewritten():
    """⭐⭐ 分位档的 `k` 在结果里被改写成**中位持有只数** ✗ ⇒ 拿它去查必然 miss ✗
    ⇒ 必须多键回退（`(kind, 0, quantile)` ✓）—— 旧实现就是"报没有该档位的每期持仓" ✓。"""
    st = _state_with("F1", 5, {("decile", 0, 3): [["A"], ["B"]]})
    err, _dates, lists = F._period_roll_pick(
        st, {"expression": "F1", "horizon": 5}, {"kind": "decile", "k": 7, "quantile": 3})
    assert err is None and lists == [["A"], ["B"]], "回退没生效 ⇒ 导出会缺这一档 ✗"


def test_pick_falls_back_by_horizon_when_expression_differs():
    """表达式对不上（结果里存的是别名 ✓）⇒ 按 horizon 找**唯一**那个桶 ✓。"""
    st = _state_with("REAL_EXPR", 5, {("topk", 50, None): [["A"]]})
    err, _d, lists = F._period_roll_pick(
        st, {"expression": "别名", "horizon": 5}, {"kind": "topk", "k": 50})
    assert err is None and lists == [["A"]]


def test_pick_reports_readable_error_when_absent():
    err, dates, lists = F._period_roll_pick({}, {"expression": "X", "horizon": 5},
                                            {"kind": "topk", "k": 50})
    assert err and "没有该档位的每期持仓" in err
    assert dates == [] and lists == []
