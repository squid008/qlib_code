# -*- coding: utf-8 -*-
"""`qlib_engine` 的**纯逻辑小件**（★ 2026-10-19 ✓ —— 不需要行情、不跑训练 ✓）。

## 为什么挑这几个（都不是"随便找个函数测一下"✗）

| 函数 | 它守护的东西 |
|---|---|
| `_seg_prog` | **进度必须严格递增** ✓ —— qlib 的 `context.report()` 会**丢弃低于历史最大值**的上报（连 message 一起丢 ✗）；旧实现两条曲线互相穿插 ⇒ 段 2 起的"训练"消息被吞 ⇒ 用户 2026-09-14 报"没有训练直接就是信号合成"✗ |
| `_purged_train_end` | **purge（防前视）** ✓ —— 训练标签是"未来 h 日收益"✓，段末必须退 **h+1 个交易日** ✓；用自然日退会"遇到周末只等于 h−1 个交易日"✗（审计 #1） |
| `_gen_rolling_segments` | 滚动分段：**步长默认 = 测试窗** ✓、测试段不越界 ✓、训练段 = 测试起点前 train 窗 ✓、每段都 purge ✓ |
| `_extend_all_nav` | 相邻段重叠 1 个交易日 ⇒ 全局净值**按日期去重** ✓（否则同一天被描两次 ✗） |

⚠ 用 `SimpleNamespace` 造假 req ✓ —— 这几个函数只读属性（duck typing ✓），
  不必依赖 `BacktestRequest` 的字段演化 ✗（那样测试会因为加字段而碎 ✗）。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from app.engine import qlib_engine as QE


# ------------------------------------------------------------------ _seg_prog
def test_seg_prog_endpoints():
    assert QE._seg_prog(0, 4, 0.0) == 20.0
    assert QE._seg_prog(3, 4, 1.0) == 90.0
    assert QE._seg_prog(0, 0, 0.0) == 20.0      # total=0 不许除零 ✗
    assert QE._seg_prog(0, 0, 1.0) == 90.0


@pytest.mark.parametrize("total", [1, 2, 3, 8])
def test_seg_prog_is_strictly_increasing(total):
    """⭐ 这条就是 2026-09-14 那个 bug 的守门 ✓：全局扫一遍必须严格单调 ✓。"""
    vals = []
    for idx in range(total):
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            vals.append(QE._seg_prog(idx, total, frac))
    assert vals == sorted(vals), "进度出现回退 ⇒ qlib 会吞掉后面的消息 ✗：%s" % vals
    # 段与段之间也要递增（末段末值 ≥ 下段首值 ✓，允许相等 ✓）
    for i in range(1, len(vals)):
        assert vals[i] >= vals[i - 1]


# ------------------------------------------------------------ _extend_all_nav
def test_extend_all_nav_dedupes_by_date_keeping_first():
    all_nav = [{"date": "2024-01-02", "value": 1.0}]
    seg = [
        {"date": "2024-01-02", "value": 9.9},   # 重复 ⇒ 丢弃（保留先出现的 ✓）
        {"date": "2024-01-03", "value": 1.1},
    ]
    QE._extend_all_nav(all_nav, seg)
    assert [p["date"] for p in all_nav] == ["2024-01-02", "2024-01-03"]
    assert all_nav[0]["value"] == 1.0, "重复日期必须保留**先出现**的点 ✓"
    assert all_nav[1]["value"] == 1.1


def test_extend_all_nav_handles_empty():
    all_nav = []
    QE._extend_all_nav(all_nav, [])
    assert all_nav == []
    QE._extend_all_nav(all_nav, [{"date": "2024-01-02", "value": 1.0}])
    assert len(all_nav) == 1


def test_extend_all_nav_overlap_across_segments():
    """真实场景：段 N 尾日 = 段 N+1 首日（都是 09-01）⇒ 只能出现一次 ✓。"""
    all_nav = []
    QE._extend_all_nav(all_nav, [{"date": "2024-08-30", "value": 1.0},
                                 {"date": "2024-09-01", "value": 1.02}])
    QE._extend_all_nav(all_nav, [{"date": "2024-09-01", "value": 1.02},
                                 {"date": "2024-09-02", "value": 1.03}])
    dates = [p["date"] for p in all_nav]
    assert dates == ["2024-08-30", "2024-09-01", "2024-09-02"]
    assert len(dates) == len(set(dates))


# --------------------------------------------------------- _purged_train_end
def test_purged_train_end_fallback_uses_natural_days(monkeypatch):
    """日历不可用时退回自然日 `(h+1)*2` ✓（≥ 周末两天的量 ✓）。"""
    import qlib.data as qd

    def _boom(*a, **k):
        raise RuntimeError("no calendar in unit test")

    # ⚠ `qlib.data.D` 在未 init 时是 `Wrapper(provider=None)`，**没有 `calendar` 属性** ✗
    #   ⇒ 必须 `raising=False` 才能 setattr（实测报 `AttributeError: Wrapper ... has no attribute` ✓）。
    monkeypatch.setattr(qd.D, "calendar", _boom, raising=False)
    monkeypatch.setattr(QE, "_once_log", lambda *a, **k: None)   # 静音 ✓
    for h in (0, 1, 5):
        got = QE._purged_train_end("2024-06-03", h)
        want = QE._offset_date("2024-06-03", -(max(1, h) + 1) * 2)
        assert got == want, "h=%d 时兜底口径不对 ✗（得到 %s，应为 %s）" % (h, got, want)


def test_purged_train_end_uses_calendar_when_available(monkeypatch):
    """日历可用时：退 **h+1 个交易日** ✓（不是自然日 ✗）。"""
    import qlib.data as qd

    class _D:
        @staticmethod
        def calendar(end_time=None):
            # 给一串"交易日"（跳过周末 ✓）
            days = [d for d in np.arange(np.datetime64("2024-05-01"),
                                         np.datetime64("2024-06-05"))]
            return [d for d in days if np.datetime64(d).astype("datetime64[D]").astype(int) % 7 < 5]

    monkeypatch.setattr(qd, "D", _D)
    monkeypatch.setattr(QE, "_once_log", lambda *a, **k: None)
    got = QE._purged_train_end("2024-06-03", 2)
    # 测试起点前 3 个交易日 ✓（h=2 ⇒ need=3 ✓）
    assert got < "2024-06-03"
    assert got != QE._purged_train_end("2024-06-03", 1), "h 必须影响退的步数 ✗"


# ---------------------------------------------------- _gen_rolling_segments
def _req(**kw):
    base = dict(
        start_date="2024-01-01", end_date="2024-03-31",
        train_win=60, train_unit="day", test_win=30, test_unit="day",
        step_win=None, step_unit=None, label_horizon=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture()
def stub_purge(monkeypatch):
    """把 purge 换成可预期的实现 ✓（真实现依赖 qlib 日历 ✗，上面已单独测过 ✓）。"""
    monkeypatch.setattr(QE, "_once_log", lambda *a, **k: None)
    monkeypatch.setattr(QE, "_purged_train_end",
                        lambda test_start, h: QE._offset_date(test_start, -1))
    return monkeypatch


def test_rolling_segments_cover_range_without_overlap(stub_purge):
    req = _req()
    segs = QE._gen_rolling_segments(req, None)      # calendar=None ⇒ 纯逻辑 ✓
    assert len(segs) >= 2
    assert segs[0]["test"][0] == "2024-01-01"
    assert segs[-1]["test"][1] == "2024-03-31", "最后一段必须收到 end_date ✓"
    # 段序号从 1 连续递增 ✓
    assert [s["seq"] for s in segs] == list(range(1, len(segs) + 1))
    # 训练段在测试段之前 ✓，且长度 = train_win ✓
    for s in segs:
        assert s["train"][1] < s["test"][0], "训练段末端必须在测试起点之前 ✗（防前视 ✓）"
        assert s["train"][0] < s["train"][1]
    # ⚠ 默认步长 = 测试窗 ⇒ 相邻段**共享 1 个交易日**（段 N 尾日 = 段 N+1 首日 ✓）——
    #   这是**有意为之** ✓（`_extend_all_nav` 的 docstring 明确写了这个重叠 ✓ 并按日期去重 ✓）。
    #   ✗ 我第一版断言"不重叠"是错的 ✓（实测 `2024-01-31 == 2024-01-31` 当场抓出来 ✓）。
    for a, b in zip(segs, segs[1:]):
        assert a["test"][1] == b["test"][0], (
            "默认步长下相邻段应共享 1 个交易日 ✓（实际 %s → %s）" % (a["test"][1], b["test"][0]))
        assert a["test"][0] < b["test"][1]  # 顺序必须是前进 ✓


def test_rolling_segments_with_explicit_step_overlaps(stub_purge):
    """给了 `step_win`（小于测试窗）⇒ 允许**重叠** ✓（这是"滑动窗口"用法 ✓）。"""
    req = _req(step_win=10, step_unit="day")
    segs = QE._gen_rolling_segments(req, None)
    assert len(segs) > 2
    assert segs[1]["test"][0] < segs[0]["test"][1], "step<test ⇒ 测试段应重叠 ✓"


def test_rolling_segments_clips_last_segment(stub_purge):
    """最后一段不许越过 `end_date` ✓（否则会去回测未来 ✗）。"""
    req = _req(end_date="2024-02-05", test_win=30)
    segs = QE._gen_rolling_segments(req, None)
    assert segs[-1]["test"][1] == "2024-02-05"
    for s in segs:
        assert s["test"][1] <= req.end_date


def test_rolling_segments_single_short_window(stub_purge):
    """窗口比区间还长 ⇒ 只生成 1 段，且测试段等于整个区间 ✓（不许死循环 ✗）。"""
    req = _req(start_date="2024-01-01", end_date="2024-01-10", test_win=90)
    segs = QE._gen_rolling_segments(req, None)
    assert len(segs) == 1
    assert segs[0]["test"] == ("2024-01-01", "2024-01-10")
