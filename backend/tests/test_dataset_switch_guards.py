# -*- coding: utf-8 -*-
"""★ T3（审计 ④⑤ / v2.8.7，用户 2026-10-16 已拍 ✓）：**切数据集的两处保护都真的生效** ✗→✓。

审计现象：
  · ④ `datasets.clear_caches()` **假成功** ✗ —— `hasattr(..., "invalidate_all")` 的那个接口
    **根本不存在** ✓，却在 `if` 之后**无条件**写 `done[...] = True` ✗ ⇒ 报告"清干净了"、
    实际旧数据还挂在进程缓存里 ✓；
  · ⑤ `datasets._running_tasks()` **恒空** ✗ —— import 了不存在的 `..engine.task_registry` ✗
    与模块级不存在的 `single_test.running_count` ✗ ⇒ 两次都被 except 吞掉 ⇒
    "有任务在跑就拒切"的守卫**永不触发** ✗✓。
本文件把三条契约钉住 ✓：① 每个清动作**真的调到**（清单齐全 ✓）；② 缺接口 ⇒ **抛可读异常** ✗（不许假成功 ✗）；
③ 任务占用**问权威来源**（`TaskManager` ✓，且**探不到不许假装没有** ✗）。
"""
import pytest

from app.services import datasets as ds

# ---------------------------- ① 清缓存：真调 + 清单齐全 ----------------------------

def test_clear_caches_really_clears_everything():
    """★ 六个清动作都必须真的跑到 ✓（旧实现里 `feature_cache`/`event_study_cache` 是假的 ✗）。"""
    done = ds.clear_caches()
    assert set(done) == {"panel_expr", "limits", "full_wide", "bench", "nav", "shared"}, done
    assert all(done.values()), "有清动作失败却报告成功 ✗：%s" % done


def test_clear_caches_raises_when_interface_missing(monkeypatch):
    """★★ 缺接口 ⇒ **抛可读异常** ✗ —— 这正是"假成功"的对照：宁可拦住，也不许静默污染 ✓。"""
    from app.factors import panel_expr

    def _boom():
        raise AttributeError("模拟：接口改名/被删")

    monkeypatch.setattr(panel_expr, "clear_bin_cache", _boom)
    with pytest.raises(RuntimeError) as ei:
        ds.clear_caches()
    msg = str(ei.value)
    assert "缓存没清干净" in msg and "panel_expr" in msg


def test_clear_caches_is_idempotent():
    """连清两次不报错 ✓（切换按钮被连点也不会炸 ✓）。"""
    assert all(ds.clear_caches().values())
    assert all(ds.clear_caches().values())


# ---------------------------- ② 任务占用：问权威来源 ----------------------------

class _FakeManager:
    def __init__(self, info):
        self._info = info

    def concurrency_info(self):
        return self._info


def test_running_tasks_reads_task_manager(monkeypatch):
    """★ 从 `TaskManager.concurrency_info()` 读（它**已包含**单因子等外部任务 ✓）。"""
    import app.engine.task_manager as tm

    monkeypatch.setattr(tm, "get_task_manager", lambda work_dir: _FakeManager(
        {"running": 2, "queued": 1}))
    out = ds._running_tasks()
    assert "运行中 2 个" in out and "排队 1 个" in out


def test_running_tasks_empty_when_idle(monkeypatch):
    """真的空闲 ⇒ 返回空串 ✓（正常切换不该被拦 ✓）。"""
    import app.engine.task_manager as tm

    monkeypatch.setattr(tm, "get_task_manager", lambda work_dir: _FakeManager(
        {"running": 0, "queued": 0}))
    assert ds._running_tasks() == ""


def test_running_tasks_unknown_is_not_silent(monkeypatch):
    """★★ 探不到 ⇒ **明说"未知"** ✗ —— 旧实现"探不到就当没有"正是静默污染的来源 ✓。"""
    import app.engine.task_manager as tm

    def _boom(*a, **k):
        raise RuntimeError("manager 起不来")

    monkeypatch.setattr(tm, "get_task_manager", _boom)
    out = ds._running_tasks()
    assert out and "未知" in out, "探不到却返回空 ⇒ 守卫又变哑了 ✗"


def test_activate_refuses_while_running(monkeypatch, tmp_path):
    """★ 有任务在跑 ⇒ 默认**拒切** ✓（用户口径："先停任务再切" ✓）。

    ⚠ **CI 没有数据目录** ⇒ 必须把"数据集可用性"打桩 ✗ —— 否则会先撞
      `FileNotFoundError("数据集目录不存在或缺日历")` ✗（与守卫无关 ✓）。
      ⚠ 我第一版没打桩，**正是这样在 CI 红的** ✓（本机有数据 ⇒ 本地全绿 ✗）⇒ 教训：
      凡"要真实目录"的用例，要么打桩、要么打 `datareq` ✓。
    """
    monkeypatch.setattr(ds, "available", lambda: ["cn_data"])
    monkeypatch.setattr(ds, "dataset_dir", lambda name: tmp_path)
    monkeypatch.setattr(ds, "_running_tasks", lambda: "运行中 1 个")
    with pytest.raises(RuntimeError) as ei:
        ds.activate("cn_data")               # ★ 守卫在**落盘/改全局状态之前**就该拦下 ✓
    assert "正在运行的任务" in str(ei.value)
