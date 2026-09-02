# -*- coding: utf-8 -*-
"""进程内数据共享缓存（阶段一：多任务并行）。

背景：
  多个回测任务跑在同一个 Python 进程（多线程），如果各自调用 qlib 的
  D.features() 加载相同的股票池/特征/区间，会重复做磁盘 I/O 和表达式计算，
  既慢又浪费内存。

本模块提供一个进程内单例缓存 DataCache：
  - 按 (instruments, fields, start, end) 缓存 qlib 特征 DataFrame
  - 多个回测任务命中相同键时直接复用同一份 DataFrame（只读共享）
  - 带引用计数 / 按需清理，避免无界膨胀

注意：
  - 缓存的数据是只读的；调用方不得原地修改返回的 DataFrame（如需修改应 .copy()）。
  - 缓存键要规范化（instruments 排序、日期字符串化），提高命中率。
"""
from __future__ import annotations

import os
import threading
from typing import FrozenSet, List, Optional, Tuple

import pandas as pd

_CACHE_LOCK = threading.Lock()


def _auto_cache_limit_gb() -> float:
    """共享数据缓存的自适应内存上限（GB）。

    - 环境变量 `QLIB_DATA_CACHE_GB` 显式指定则优先（服务器部署者可固定，如 8）；
    - 未指定则按【当前可用内存】动态自适应：limit = clamp(可用 × 0.15, 0.5, 4.0)。
      与并发任务共用同一内存口径（engine/resource.py）：任务并行占用内存后可用内存下降，
      缓存会自动收紧逐出；机器内存越大/越空闲，缓存越宽松（16G 家机 ≈ 2G、48G ≈ 4G、
      96G 服务器 = 4G 封顶，可再调 QLIB_DATA_CACHE_GB 放大）。
    """
    env = os.environ.get("QLIB_DATA_CACHE_GB")
    if env:
        try:
            return max(0.25, float(env))
        except ValueError:
            pass
    try:
        from . import resource

        avail = resource.memory_available_gb()
        return max(0.5, min(4.0, round(avail * 0.15, 1)))
    except Exception:
        return 1.0


class DataCache:
    """线程安全的进程内数据缓存（单例）。"""

    def __init__(self, max_gb: Optional[float] = None):
        self._cache: dict = {}
        self._lock = threading.Lock()
        # 缓存内存上限（GB）：None=按当前可用内存动态自适应（见 _auto_cache_limit_gb）；
        # 传具体数值=固定上限（≤0 表示禁用自动逐出）。每次加载后节流检查，
        # 超限按"引用最少优先"逐出，防止不同 (股票池, 区间) 的特征面板无限累积。
        self._max_gb = max_gb if max_gb is not None and max_gb > 0 else None
        self._load_count = 0

    # ------------------------------------------------------------------
    # 键规范化：提高不同调用之间的命中率
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_key(instruments, fields, start, end) -> Tuple[FrozenSet, Tuple, str, str]:
        """把调用参数规范化为稳定缓存键。"""
        inst_key = frozenset(str(i).lower() for i in instruments) if instruments else frozenset()
        fields_key = tuple(fields) if fields else ()
        return inst_key, fields_key, str(start), str(end)

    # ------------------------------------------------------------------
    # 核心：get 或 load
    # ------------------------------------------------------------------
    def get_or_load(self, instruments, fields, start, end, loader):
        """按缓存键取数据；未命中则调用 loader() 加载并缓存。

        loader 应为可调用，返回 pandas.DataFrame（MultiIndex=[instrument, datetime]）。
        返回 DataFrame（共享只读）。调用方如需修改请自行 .copy()。
        """
        key = self._norm_key(instruments, fields, start, end)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache[key]["refs"] += 1
                return hit["df"]

        # 未命中：在锁外调用 loader（避免长时间持锁阻塞其他任务）
        df = loader()
        if df is None:
            return None
        with self._lock:
            # 双重检查：避免并发下重复加载
            hit = self._cache.get(key)
            if hit is not None:
                hit["refs"] += 1
                return hit["df"]
            self._cache[key] = {"df": df, "refs": 1}
        self._maybe_evict()  # 锁外做容量检查，避免持锁扫描
        return df

    def _maybe_evict(self) -> None:
        """节流式内存逐出：每 N 次加载才做一次全量估算，超限按引用最少优先逐出。

        上限 = 构造时固定值（若有）或按当前可用内存动态自适应（_auto_cache_limit_gb）。
        删除缓存条目不影响已取出并持本地引用的调用方；下次再 get_or_load 会重新加载。
        """
        self._load_count += 1
        if self._load_count % 5 != 0:
            return
        limit = self._max_gb if self._max_gb is not None else _auto_cache_limit_gb()
        try:
            self.evict_if_over(limit)
        except Exception:
            pass

    def get(self, instruments, fields, start, end):
        """只读命中查询（不加载）。未命中返回 None。"""
        key = self._norm_key(instruments, fields, start, end)
        with self._lock:
            hit = self._cache.get(key)
            return hit["df"] if hit else None

    def drop_ref(self, instruments, fields, start, end):
        """调用方用完释放引用。引用归零时不立即删（可能被复用），由清理策略处理。"""
        key = self._norm_key(instruments, fields, start, end)
        with self._lock:
            hit = self._cache.get(key)
            if hit:
                hit["refs"] = max(0, hit["refs"] - 1)

    # ------------------------------------------------------------------
    # 统计 / 清理
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        """缓存统计（条目数 / 总引用 / 估算内存）。"""
        with self._lock:
            n = len(self._cache)
            refs = sum(v["refs"] for v in self._cache.values())
            # 估算内存：所有缓存 DataFrame 的内存占用之和
            total_mem = 0.0
            for v in self._cache.values():
                try:
                    total_mem += v["df"].memory_usage(deep=True).sum() / 1024 ** 3
                except Exception:
                    pass
        return {
            "entries": n,
            "total_refs": refs,
            "estimated_gb": round(total_mem, 3),
        }

    def clear(self):
        """清空缓存（通常只在内存告警或测试时用）。"""
        with self._lock:
            self._cache.clear()

    def evict_if_over(self, max_gb: float) -> int:
        """若缓存估算内存超过 max_gb，按引用最少优先逐出，返回逐出条目数。"""
        removed = 0
        with self._lock:
            if self._estimated_gb_locked() <= max_gb:
                return 0
            # 按引用数升序逐出，直到低于阈值
            for key in sorted(self._cache, key=lambda k: self._cache[k]["refs"]):
                if self._estimated_gb_locked() <= max_gb:
                    break
                del self._cache[key]
                removed += 1
        return removed

    def _estimated_gb_locked(self) -> float:
        """（须在持锁时调用）估算缓存总内存。"""
        total = 0.0
        for v in self._cache.values():
            try:
                total += v["df"].memory_usage(deep=True).sum() / 1024 ** 3
            except Exception:
                pass
        return total


# 全局共享的单例缓存实例（进程内所有回测任务共用）
# 上限默认按机器可用内存动态自适应（16G≈2G / 48G≈4G / 服务器96G=4G封顶），
# 可用 QLIB_DATA_CACHE_GB 显式覆盖（如服务器固定 8）。
SHARED_CACHE = DataCache(max_gb=None)
