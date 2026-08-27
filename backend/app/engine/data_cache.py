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

import threading
from typing import FrozenSet, List, Optional, Tuple

import pandas as pd

_CACHE_LOCK = threading.Lock()


class DataCache:
    """线程安全的进程内数据缓存（单例）。"""

    def __init__(self):
        self._cache: dict = {}
        self._lock = threading.Lock()

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
        return df

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
SHARED_CACHE = DataCache()
