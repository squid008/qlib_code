# -*- coding: utf-8 -*-
"""磁盘存储治理：feature_cache / artifacts 按配额 + LRU 清理。

背景：feature_cache（特征磁盘缓存）与 artifacts（历史任务产物）长期只增不清，
mlflow.db 也持续累积，跑一段时间磁盘会失控。本模块在每次回测任务开始时
（qlib_engine.run_backtest）节流调用一次：

- feature_cache：按配额（默认 2 GB，环境变量 QLIB_FEATURE_CACHE_GB）超出后按
  文件 mtime 从旧到新删除，直到低于配额；
- artifacts：任务目录按"最近修改时间"保留最近 N 个（默认 40，QLIB_ARTIFACTS_KEEP），
  正在运行/近 1 天有写活动的目录视为活跃、绝不删除；
- mlflow.db：仅报告大小（删除需停服务，不自动删，由运维处理）。

全部静默失败（不阻塞回测）。并发安全：进程级互斥 + 节流（默认 30 分钟一次）。
"""
from __future__ import annotations

import os
import threading
import time

_FEATURE_CACHE_GB = float(os.environ.get("QLIB_FEATURE_CACHE_GB", "2.0"))
_ARTIFACTS_KEEP = int(os.environ.get("QLIB_ARTIFACTS_KEEP", "40"))
_ACTIVE_WINDOW_SEC = 24 * 3600  # 目录有文件在近 1 天写入 → 视为活跃任务，不删
_THROTTLE_SEC = 30 * 60

_lock = threading.Lock()
_last_run = [0.0]


def _work_dir() -> str:
    from ..config import WORK_DIR

    return WORK_DIR


def _dir_size_and_bytes(root: str):
    """返回 (总字节, [(path, mtime), ...])（仅该目录下直接文件，非递归）。"""
    total, files = 0, []
    if not os.path.isdir(root):
        return total, files
    try:
        for n in os.listdir(root):
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
                if os.path.isfile(p):
                    total += st.st_size
                    files.append((p, st.st_mtime))
            except OSError:
                pass
    except OSError:
        pass
    return total, files


def _dir_total(root: str) -> int:
    """目录下所有文件总字节（递归，用于配额判断）。"""
    total = 0
    if not os.path.isdir(root):
        return total
    try:
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(dirpath, n))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _clean_feature_cache(root: str) -> int:
    """超出配额后按 mtime 从旧到新删缓存文件，直到低于配额。返回删除文件数。"""
    limit = _FEATURE_CACHE_GB * 1024 ** 3
    if not os.path.isdir(root):
        return 0
    files = []
    for n in os.listdir(root):
        p = os.path.join(root, n)
        try:
            if os.path.isfile(p):
                files.append((p, os.path.getmtime(p)))
        except OSError:
            pass
    total = _dir_total(root)
    if total <= limit or not files:
        return 0
    files.sort(key=lambda x: x[1])  # 旧 → 新
    removed = 0
    for p, _m in files:
        if total <= limit:
            break
        try:
            size = os.path.getsize(p)
            os.remove(p)
            total -= size
            removed += 1
        except OSError:
            pass
    return removed


def _clean_artifacts(root: str) -> int:
    """保留最近 N 个非活跃任务目录，其余按目录最近文件 mtime 从旧到新删除。"""
    keep = max(5, _ARTIFACTS_KEEP)
    if not os.path.isdir(root):
        return 0
    now = time.time()
    infos = []  # (dir, latest_file_mtime, active)
    for n in os.listdir(root):
        d = os.path.join(root, n)
        if not os.path.isdir(d):
            continue
        _s, files = _dir_size_and_bytes(d)
        latest = max((m for _p, m in files), default=0)
        active = latest >= now - _ACTIVE_WINDOW_SEC
        infos.append((d, latest, active))
    # 删除候选：非活跃目录，按最近修改升序（旧的最先删）
    candidates = sorted([(d, m) for d, m, active in infos if not active], key=lambda x: x[1])
    removed = 0
    while len(infos) - removed > keep and candidates:
        d, _m = candidates.pop(0)
        try:
            _rmtree(d)
            removed += 1
        except OSError:
            pass
    return removed


def _rmtree(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def cleanup_storage(work_dir: str | None = None, force: bool = False) -> dict:
    """节流执行存储清理。返回本次清理统计（失败静默）。

    work_dir: 任务工作目录（含 feature_cache/artifacts）；None 用全局配置。
    """
    now = time.time()
    with _lock:
        if not force and now - _last_run[0] < _THROTTLE_SEC:
            return {}
        _last_run[0] = now
    wd = work_dir or _work_dir()
    stat = {}
    try:
        stat["feature_cache_removed"] = _clean_feature_cache(os.path.join(wd, "feature_cache"))
    except Exception:
        stat["feature_cache_removed"] = -1
    try:
        stat["artifacts_removed"] = _clean_artifacts(os.path.join(wd, "artifacts"))
    except Exception:
        stat["artifacts_removed"] = -1
    try:
        db = os.path.join(wd, "mlflow.db")
        stat["mlflow_db_mb"] = round(os.path.getsize(db) / 1024 ** 2, 1) if os.path.exists(db) else 0.0
    except Exception:
        stat["mlflow_db_mb"] = None
    return stat
