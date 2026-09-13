# -*- coding: utf-8 -*-
"""磁盘存储治理：feature_cache / artifacts 按配额 + LRU 清理。

背景：feature_cache（特征磁盘缓存）与 artifacts（历史任务产物）长期只增不清，
mlflow.db 也持续累积，跑一段时间磁盘会失控。本模块在每次回测任务开始时
（qlib_engine.run_backtest）节流调用一次：

- feature_cache：按配额（默认 2 GB，环境变量 QLIB_FEATURE_CACHE_GB）超出后按
  文件 mtime 从旧到新删除，直到低于配额；
- artifacts（**v1.19.12 起**）：按**容量配额**（默认 30 GB，`QLIB_ARTIFACTS_GB`）+ **条数兜底**
  （默认 300，`QLIB_ARTIFACTS_KEEP`）回收；正在运行/近 1 天有写活动的目录视为活跃、绝不删除。
  超配额时**先精简**最老的目录（删 `segment_*/` 与大体积中间产物，**保留 json/png/参数/指标/曲线**），
  精简后仍超配额才整目录删除；**每次精简/删除都写日志**（含释放空间）。
  ⚠ 旧实现（≤v1.19.11）固定删到只剩 **40 个**、且**完全静默**（无任何日志），
  导致用户"回测产物从 3 页变 2 页、早期产物找不到"（实测 40 目录占 5.1GB，最老只到 08-27）。
- mlflow.db：仅报告大小（删除需停服务，不自动删，由运维处理）。

全部静默失败（不阻塞回测）。并发安全：进程级互斥 + 节流（默认 30 分钟一次）。
"""
from __future__ import annotations

import os
import threading
import time

_FEATURE_CACHE_GB = float(os.environ.get("QLIB_FEATURE_CACHE_GB", "2.0"))
# v1.19.12：artifacts 由「固定保留 40 个」改为**容量配额优先 + 条数兜底**（详见 _clean_artifacts）。
_ARTIFACTS_GB = float(os.environ.get("QLIB_ARTIFACTS_GB", "30.0"))
_ARTIFACTS_KEEP = int(os.environ.get("QLIB_ARTIFACTS_KEEP", "300"))
# 单个文件超过该体积才纳入"精简"（连同 `segment_*/` 一起删；json/png 等轻量记录始终保留）
_ARTIFACTS_SLIM_MB = float(os.environ.get("QLIB_ARTIFACTS_SLIM_MB", "5.0"))
_ACTIVE_WINDOW_SEC = 24 * 3600  # 目录有文件在近 1 天写入 → 视为活跃任务，不删
_THROTTLE_SEC = 30 * 60

# 可精简的大体积中间产物后缀（不解包、不需要再算 ⇒ 删掉只影响"续测/模型复用"，不影响结果查看）
_SLIM_EXTS = (".pkl", ".pickle", ".joblib", ".pth", ".pt", ".bin", ".npy", ".npz", ".h5", ".model")

_lock = threading.Lock()
_last_run = [0.0]
_logger = None


def _log():
    """惰性取 logger（避免 import 环）。"""
    global _logger
    if _logger is None:
        try:
            from ..logger import get_logger

            _logger = get_logger(__name__)
        except Exception:  # pragma: no cover - 兜底
            import logging

            _logger = logging.getLogger(__name__)
    return _logger


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


def _dir_recursive_stat(root: str):
    """返回 (总字节, 最近文件 mtime) —— **递归**统计。

    ⚠ 旧实现只看**直属文件**（`_dir_size_and_bytes`）：对"重活都写在 `segment_N/` 子目录里"的
    任务目录会**误判为体积≈0、从未写入**（既不活跃、又显得不占空间）⇒ 配额判断失真。
    """
    total, latest = 0, 0.0
    if not os.path.isdir(root):
        return total, latest
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            try:
                st = os.stat(os.path.join(dirpath, n))
            except OSError:
                continue
            total += st.st_size
            if st.st_mtime > latest:
                latest = st.st_mtime
    return total, latest


def _slim_task_dir(d: str) -> int:
    """**精简**单个任务目录：回收大体积中间产物、保留轻量记录。返回释放字节数。

    - 删：`segment_*/`（滚动各段的 `seg_result.json` / 预测缓存 / 模型对象，体积大头）
      + 大于 `_ARTIFACTS_SLIM_MB` 的中间产物（`.pkl/.npy/.bin/…`）；
    - **留**：`result.json`（指标/净值/分层/IC/调仓）、`params.json`、`meta.json`、`seq.json`、
      `train_signature.json`、`model_artifacts.json`、`*.png` ⇒ 参数、结果、曲线都还能查看；
    - 目录内写 `.slimmed` 标记（时间 + 释放量），供排查/将来 UI 标注"已精简"。
      标记文件的 mtime 会**回填成目录原有最新时间** ⇒ 不干扰"活跃目录保护"判定。
    """
    freed = 0
    slim_bytes = _ARTIFACTS_SLIM_MB * 1024 ** 2
    _size, latest = _dir_recursive_stat(d)
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for n in names:
        p = os.path.join(d, n)
        try:
            if os.path.isdir(p) and n.startswith("segment_"):
                freed += _dir_total(p)
                _rmtree(p)
            elif (os.path.isfile(p) and n.lower().endswith(_SLIM_EXTS)
                    and os.path.getsize(p) > slim_bytes):
                freed += os.path.getsize(p)
                os.remove(p)
        except OSError:
            pass
    try:
        marker = os.path.join(d, ".slimmed")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("slimmed_at=%s\nfreed_bytes=%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), freed))
        stamp = latest or time.time()
        os.utime(marker, (stamp, stamp))
    except OSError:
        pass
    return freed


def _clean_artifacts(root: str, detail: bool = False):
    """按配额回收旧任务目录；返回**整目录删除数**（`detail=True` 时返回统计 dict）。

    **v1.19.12 重写**（用户报"回测产物从 3 页变 2 页、早期产物丢失"）：
    - **旧实现**：固定保留最近 `_ARTIFACTS_KEEP`（原写死 40）个目录，超出的**直接 `rmtree`**，
      且**完全静默、无任何日志**；体积/活跃度还只看直属文件 ⇒ 用户"产物凭空消失"且无从察觉
      （实测：仅剩 40 个目录、占 5.1GB、最老只到 08-27）。
    - **新实现**：
      ① 配额从"个数"改为"**容量**"（默认 30GB，`QLIB_ARTIFACTS_GB`）—— 空间够就**一个都不删**；
         `_ARTIFACTS_KEEP`（默认 300）仅作**条数兜底**；
      ② 超配额时**先精简**最老的**非活跃**目录（删 `segment_*/` 与大体积中间产物，**保留参数/结果/
         曲线**）⇒ 历史列表条目与结果仍在，只回收中间产物；
      ③ 精简一遍后仍超配额才**整目录删除**（同样只删非活跃、按最近修改从旧到新）；
      ④ 精简/删除**全部写日志**（含释放空间），不再静默。
    """
    stat = {"removed": 0, "slimmed": 0, "freed_mb": 0.0, "total_mb": 0.0,
            "kept": 0, "quota_gb": _ARTIFACTS_GB, "keep": max(5, _ARTIFACTS_KEEP)}
    if not os.path.isdir(root):
        return stat if detail else 0
    now = time.time()
    infos = []  # [dir, latest_mtime, active, size_bytes, slimmed]
    total_bytes = 0
    for n in os.listdir(root):
        d = os.path.join(root, n)
        if not os.path.isdir(d):
            continue
        size, latest = _dir_recursive_stat(d)
        total_bytes += size
        infos.append([d, latest, latest >= now - _ACTIVE_WINDOW_SEC, size, False])
    quota_bytes = _ARTIFACTS_GB * 1024 ** 3
    keep = max(5, _ARTIFACTS_KEEP)
    # 候选：**非活跃**目录，按最近修改升序（最旧的优先）；条数兜底按"多出几个"计数
    queue = sorted([i for i, x in enumerate(infos) if not x[2]], key=lambda i: infos[i][1])
    need_removals = max(0, len(infos) - keep)
    while (total_bytes > quota_bytes or need_removals > 0) and queue:
        i = queue.pop(0)
        d = infos[i][0]
        if not infos[i][4]:
            # 第一轮：先精简（保留 json/png/参数/结果），**放回队尾** —— 全部精简完才考虑整删
            before = infos[i][3]
            freed = _slim_task_dir(d)
            infos[i][3] = max(0, before - min(freed, before))
            total_bytes = max(0, total_bytes - freed)
            infos[i][4] = True
            stat["slimmed"] += 1
            stat["freed_mb"] = round(stat["freed_mb"] + freed / 1024 ** 2, 1)
            _log().warning(
                "产物治理：已精简旧任务目录 %s（保留参数/指标/曲线，仅删中间产物），释放 %.1fMB",
                os.path.basename(d), freed / 1024 ** 2)
            queue.append(i)
            continue
        # 第二轮：已精简过仍超配额 → 整目录删除
        try:
            real = _dir_total(d)
            _rmtree(d)
            total_bytes = max(0, total_bytes - real)
            stat["removed"] += 1
            stat["freed_mb"] = round(stat["freed_mb"] + real / 1024 ** 2, 1)
            need_removals -= 1
            _log().warning("产物治理：已删除旧任务目录 %s，释放 %.1fMB",
                           os.path.basename(d), real / 1024 ** 2)
        except OSError:
            pass
    stat["kept"] = len([x for x in infos if os.path.isdir(x[0])])
    stat["total_mb"] = round(max(0, total_bytes) / 1024 ** 2, 1)
    return stat if detail else stat["removed"]


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
        # v1.19.12：detail=True → 同时拿到"精简 / 删除 / 释放空间"明细并写日志
        # （旧实现删了也不吭声，用户只看到"产物少了"）。stat 保留原键 `artifacts_removed`，
        # 便于既有调用方/测试不受影响。
        a = _clean_artifacts(os.path.join(wd, "artifacts"), detail=True)
        stat["artifacts_removed"] = a["removed"]
        stat["artifacts_slimmed"] = a["slimmed"]
        stat["artifacts_freed_mb"] = a["freed_mb"]
        stat["artifacts_total_mb"] = a["total_mb"]
        stat["artifacts_kept"] = a["kept"]
        if a["removed"] or a["slimmed"]:
            _log().warning(
                "产物治理汇总：已精简 %d 个、删除 %d 个旧任务目录，释放 %.1fMB；"
                "当前 artifacts 共 %d 个、%.1fMB（配额 %.0fGB、条数兜底 %d）",
                a["slimmed"], a["removed"], a["freed_mb"], a["kept"], a["total_mb"],
                a["quota_gb"], a["keep"])
    except Exception:
        stat["artifacts_removed"] = -1
    try:
        db = os.path.join(wd, "mlflow.db")
        stat["mlflow_db_mb"] = round(os.path.getsize(db) / 1024 ** 2, 1) if os.path.exists(db) else 0.0
    except Exception:
        stat["mlflow_db_mb"] = None
    return stat
