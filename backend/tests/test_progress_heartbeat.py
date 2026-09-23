# -*- coding: utf-8 -*-
"""★ v1.20.64：长阶段「原地刷进度」（`context.heartbeat`）单测。

动机（2026-09-23 用户两次报「进度还是没动啊」，排障时实测）：
    `段1/7 分阶段耗时：建数据集+训练+预测 11.1s | 信号合成 216.1s | …`
    其中 **`gate 训练 211.1s`** ✗ —— 该区间内 `report()` 只在阶段首尾各调一次
    ⇒ 前端进度与消息 **211 秒纹丝不动** ✗ ⇒ 用户只能判断为"卡死" ✗✓
    （这是本次排障最大的时间黑洞：反复去查死锁，其实只是**没上报** ✓）。
"""
from __future__ import annotations

from app.engine import context


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, p, msg):
        self.calls.append((p, msg))


def _setup(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(context, "_progress_cb", context.contextvars.ContextVar("t_cb", default=rec))
    monkeypatch.setattr(context, "_max_progress", context.contextvars.ContextVar("t_max", default=0.0))
    monkeypatch.setattr(context, "_last_report", context.contextvars.ContextVar("t_last", default=None))
    return rec


def test_heartbeat_refreshes_message_keeping_percent(monkeypatch):
    """★ 核心语义：**百分数不变、消息更新** ✓ —— 这正是"看起来在动"的关键 ✓。"""
    rec = _setup(monkeypatch)
    context.report(20.0, "段1/7: 训练 2020-07-01~2020-12-31")
    context.heartbeat("训练中 第 120 轮")
    assert rec.calls == [
        (20.0, "段1/7: 训练 2020-07-01~2020-12-31"),
        (20.0, "段1/7: 训练 2020-07-01~2020-12-31（训练中 第 120 轮）"),
    ]


def test_heartbeat_does_not_chain(monkeypatch):
    """★ 反复心跳**不得套娃**（"…（…）（…）" ✗）—— `heartbeat` 不写 `_last_report` ✓。"""
    rec = _setup(monkeypatch)
    context.report(23.0, "信号合成（滚动段1）...")
    for i in (10, 20, 30):
        context.heartbeat("训练中 第 %d 轮" % i)
    assert rec.calls[-1][1] == "信号合成（滚动段1）...（训练中 第 30 轮）"       # ★ 只有一层 ✓
    assert rec.calls[-1][0] == 23.0


def test_heartbeat_keeps_parens_in_original_message(monkeypatch):
    """★ 原消息里的"（）"必须**原样保留** ✓（早期想用 `split("（")` 砍尾巴 ✗ ⇒ 会把
    「信号合成（滚动段1）」里的正文砍掉 ✗ —— 故改为"基于 `report` 存的原消息 + 后缀" ✓）。"""
    rec = _setup(monkeypatch)
    context.report(23.0, "信号合成（滚动段1）...")
    context.heartbeat("训练中 第 10 轮")
    assert "信号合成（滚动段1）" in rec.calls[-1][1]


def test_heartbeat_without_report_is_noop(monkeypatch):
    """没正式报过进度 ⇒ 心跳**什么都不做** ✓（否则会出现"无上下文的裸消息" ✗）。"""
    rec = _setup(monkeypatch)
    context.heartbeat("训练中 第 10 轮")
    assert rec.calls == []


def test_heartbeat_without_callback_is_noop(monkeypatch):
    """没有进度回调（如单因子测试路径 ✓）⇒ 不报 ✓ 不抛 ✓。"""
    monkeypatch.setattr(context, "_progress_cb", context.contextvars.ContextVar("t_cb2", default=None))
    context.heartbeat("x")            # 不得抛 ✓


def test_report_records_last_for_heartbeat(monkeypatch):
    """`report` 必须把**原消息**记下来供心跳使用 ✓；且**倒退的进度**仍被丢弃 ✓（不污染心跳基准 ✓）。"""
    rec = _setup(monkeypatch)
    context.report(50.0, "A")
    context.report(40.0, "B")          # 倒退 ⇒ 丢弃 ✓
    context.heartbeat("h")
    assert rec.calls[-1][0] == 50.0
    assert rec.calls[-1][1].startswith("A")


def test_lightgbm_callback_invokes_heartbeat():
    """★ 接线验证：LightGBM 取消回调查到 `heartbeat_fn` ✓（否则本功能形同虚设 ✗）。"""
    from app.engine.patches import cancel_train

    seen = []
    cb = cancel_train._make_cancel_callback(lambda: None, lambda msg: seen.append(msg))

    class _Env:
        iteration = 10

    cb(_Env())                          # 第 10 轮 ⇒ 每 CHECK_EVERY_N_ITER(默认 10) 触发 ✓
    assert seen and seen[0] == "训练中 第 10 轮"
    seen.clear()
    cb(type("E", (), {"iteration": 11})())      # 非检查轮 ⇒ 不打（避免每轮都刷 ✓）
    assert seen == []
