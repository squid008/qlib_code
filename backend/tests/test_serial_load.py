# -*- coding: utf-8 -*-
"""取数串行化 + 池重置的单元测试（`app/engine/patches/serial_load.py`）。

背景（2026-09-14 实测）：joblib/loky 的 reusable executor 是**进程内单例** ⇒ 两个回测同时取数
（各自 `n_jobs=12`）会协调状态打架、**死锁**；且杀掉 worker 也解不开卡在 `Parallel._retrieve`
里的线程。本模块的补丁用一把进程级锁把所有 `Parallel` 调用串起来（根治），并提供"杀 + 重置池"。
"""
import threading
import time

import pytest

from app.engine.patches import serial_load as sl


def _sleep_job(sec: float):
    """模块级可 pickle 的 job（loky 子进程要能 import 到它）。"""
    time.sleep(sec)
    return sec


_INTERVALS = []
_IV_LOCK = threading.Lock()


class _RecordingLock:
    """包一层真锁，记录每次**成功获取**的持有区间（(t_acquired, t_released)）。

    ⚠ 必须量"锁的持有区间"，不能量线程里 Parallel 调用前后的时间 —— 后者含**等锁时间**，
    两次调用会"看起来重叠"（2026-09-14 我第一版断言就错在这，误判成串行化失效）。
    """

    def __init__(self, inner):
        self._inner = inner
        self.spans = []
        self._t0 = {}

    def acquire(self, timeout=None):
        ok = self._inner.acquire(timeout=timeout) if timeout is not None else self._inner.acquire()
        if ok:
            self._t0[threading.get_ident()] = time.time()
        return ok

    def release(self):
        t0 = self._t0.pop(threading.get_ident(), None)
        if t0 is not None:
            with _IV_LOCK:
                self.spans.append((t0, time.time()))
        self._inner.release()


def _call_parallel_in_thread(n_jobs: int = 2):
    from joblib import Parallel, delayed

    Parallel(n_jobs=n_jobs)(delayed(_sleep_job)(0.4) for _ in range(2))


class TestSerialLoad:
    def test_installed_and_idempotent(self):
        """补丁已装（导入 patches 包时自动装）且可重复调用。"""
        assert sl.install_serial_load() is True
        assert sl.install_serial_load() is True
        import joblib.parallel as jp

        assert getattr(jp.Parallel.__call__, "_serial_load_patched", False) is True

    def test_parallel_calls_do_not_overlap(self, monkeypatch):
        """两个线程同时发起 Parallel ⇒ 被闸门串行（**锁的持有区间**不重叠、都跑完不死锁）。"""
        assert sl.install_serial_load() is True
        _INTERVALS.clear()
        rec = _RecordingLock(sl._LOAD_LOCK)
        monkeypatch.setattr(sl, "_LOAD_LOCK", rec)   # 补丁里是全局查找 ⇒ 换掉即被观测
        ths = [threading.Thread(target=_call_parallel_in_thread) for _ in range(2)]
        t0 = time.time()
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=180)
        total = time.time() - t0
        assert all(not t.is_alive() for t in ths), "有线程没跑完（可能死锁）"
        spans = sorted(rec.spans)
        assert len(spans) == 2, "两次取数都应拿到过闸门：%r" % (rec.spans,)
        assert spans[0][1] <= spans[1][0] + 0.05, \
            "两次取数在闸门内重叠了 —— 串行化没生效：%r" % (spans,)
        # 每次调用至少 0.4s（2 个 job 并行）⇒ 串行后总耗时 ≥ 0.8s
        assert all(b - a >= 0.35 for a, b in spans), "持有区间过短，疑似没真正执行：%r" % (spans,)
        assert total >= 0.8, "总耗时过短：%.2fs" % total

    def test_nested_same_thread_passes_through(self, monkeypatch):
        """同线程嵌套（已在闸门内）必须直接放行，否则会自锁。"""
        from joblib import Parallel, delayed

        monkeypatch.setattr(sl._depth, "n", 1, raising=False)
        t0 = time.time()
        Parallel(n_jobs=1)(delayed(_sleep_job)(0.05) for _ in range(1))
        assert time.time() - t0 < 30, "嵌套调用没有放行（疑似自锁）"

    def test_kill_and_reset_clears_cached_pool(self):
        """杀 + 重置：返回被杀 PID 列表；**清掉 joblib 缓存的池**（否则下次会复用损坏的池）。"""
        from joblib.externals.loky import reusable_executor as re_mod
        from joblib.externals.loky.reusable_executor import get_reusable_executor

        get_reusable_executor()                      # 确保存在一个池（懒创建）
        assert re_mod._executor is not None
        killed = sl.kill_and_reset_loky_pool()       # 不抛异常即可，PID 列表可能为空/有值
        assert isinstance(killed, list)
        assert re_mod._executor is None, "池缓存没被清掉 ⇒ 下一个任务会复用已损坏的池"

    def test_kill_and_reset_safe_without_pool(self):
        """没有池时也要安全（不能抛异常）。"""
        from joblib.externals.loky import reusable_executor as re_mod

        re_mod._executor = None
        assert isinstance(sl.kill_and_reset_loky_pool(), list)

    def test_switch_can_disable(self, monkeypatch):
        """QLIB_SERIAL_LOAD=0 时不算"启用"（仅验证判定函数，不改已装的补丁）。"""
        monkeypatch.setenv("QLIB_SERIAL_LOAD", "0")
        assert sl.serial_enabled() is False
        monkeypatch.setenv("QLIB_SERIAL_LOAD", "1")
        assert sl.serial_enabled() is True


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
