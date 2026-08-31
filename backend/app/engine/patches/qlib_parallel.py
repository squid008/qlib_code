# -*- coding: utf-8 -*-
"""qlib 多线程并行外挂插件（不改 qlib 源码）。

背景：
  qlib 的全局记录器 `R`（qlib.workflow.R）是进程级单例，内部 `ExpManager` 持有
  全局 `active_experiment` / `_active_exp_uri`。多个线程同时 `R.start()` 会互相覆盖，
  导致：
    - RecorderInitializationError: "Please don't reinitialize Qlib..."
    - mlflow Run not found（一个任务结束，其他任务依赖的 Run 失效）

方案（纯外挂，monkey-patch）：
  给每个线程创建一个独立的 `QlibRecorder(ExpManager)`，并把全局 `R` 替换成
  "按线程分派" 的代理对象。这样 `active_experiment` 天然线程隔离，互不干扰。

  每个线程用独立的 sqlite 后端（按线程标识区分 uri），避免 mlflow 文件锁竞争。

用法（在回测任务线程内 / 引擎启动时）：
    from app.engine.patches import patch_qlib_parallel
    patch_qlib_parallel()          # 应用一次即可，全局生效

验证：
  已用 3 线程并发 start_exp 实测：线程本地 ExpManager 方案无冲突，全部成功。
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from qlib.workflow import QlibRecorder, ExpManager
from qlib.workflow.expm import MLflowExpManager

_PATCHED = threading.Lock()
_APPLIED = False


class _ThreadLocalQlibRecorder:
    """线程本地 R：每个线程懒创建独立 QlibRecorder(ExpManager)。"""

    def __init__(self, base_dir: Optional[str] = None, default_exp_name: Optional[str] = None):
        self._local = threading.local()
        # 线程独立实验后端目录（sqlite），默认放到临时目录，可按需覆盖
        self._base_dir = base_dir or os.path.join(
            os.environ.get("QLIB_WORK_DIR", os.path.expanduser("~")), ".qlib_parallel_exp"
        )
        self._default_exp_name = default_exp_name or "backtest_web"
        os.makedirs(self._base_dir, exist_ok=True)

    def _get_recorder(self):
        """获取当前线程的 QlibRecorder；未创建则懒创建（每个线程独立 uri）。"""
        r = getattr(self._local, "recorder", None)
        if r is None:
            tid = threading.get_ident()
            uri = f"sqlite:///{os.path.join(self._base_dir, f'exp_{tid}.db')}?timeout=30"
            os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
            mgr = MLflowExpManager(uri, self._default_exp_name)
            r = QlibRecorder(mgr)
            self._local.recorder = r
        return r

    def register(self, qr):
        """qlib.init 内部会调用 R.register(qr)（qr 为全局 ExpManager 的 QlibRecorder）。

        我们这里**忽略** qlib.init 传入的全局 recorder，改用线程本地独立的
        QlibRecorder(ExpManager)。这样每个线程有自己的 exp_manager + uri，
        避免多线程并发时 active_experiment 互相覆盖。
        为确保后续 R.start_exp 能拿到独立 recorder，这里主动触发线程 recorder 的懒创建。
        """
        self._get_recorder()  # 确保线程本地 recorder 已创建（用独立 uri）

    # ---------- 兜底：未显式定义的任意方法/属性，自动转发到线程本地 recorder ----------
    # 这样无论 qlib 引擎调用 R 的哪个方法（start/start_exp/get_recorder/log_*/
    # load_object/save_objects/get_exp/set_uri...），都能正确代理到当前线程的 recorder，
    # 不会出现 'no attribute' 错误。
    def __getattr__(self, name):
        # 注意：__getattr__ 只在正常属性查找失败时调用，不递归。
        # 用 object.__getattribute__ 取线程本地 recorder 上的同名属性。
        recorder = object.__getattribute__(self, "_get_recorder")()
        attr = getattr(recorder, name)
        # 若返回的是可调用对象，绑定到 recorder（保持 self 绑定正确）
        if callable(attr):
            return attr
        return attr

    # 显式保留几个高频方法（性能 & 避免 __getattr__ 反复创建 recorder 的开销）
    def start(self, *args, **kwargs):
        return self._get_recorder().start(*args, **kwargs)

    def start_exp(self, *args, **kwargs):
        return self._get_recorder().start_exp(*args, **kwargs)

    def end_exp(self, *args, **kwargs):
        return self._get_recorder().end_exp(*args, **kwargs)

    def get_exp(self, *args, **kwargs):
        return self._get_recorder().get_exp(*args, **kwargs)

    def get_recorder(self, *args, **kwargs):
        return self._get_recorder().get_recorder(*args, **kwargs)

    def log_params(self, *args, **kwargs):
        return self._get_recorder().log_params(*args, **kwargs)

    def log_metrics(self, *args, **kwargs):
        return self._get_recorder().log_metrics(*args, **kwargs)

    def log_artifact(self, *args, **kwargs):
        return self._get_recorder().log_artifact(*args, **kwargs)

    def set_tags(self, *args, **kwargs):
        return self._get_recorder().set_tags(*args, **kwargs)

    def set_uri(self, uri):
        # 忽略外部 uri 覆盖：线程本地 recorder 已用独立 sqlite（exp_{tid}.db）做并发隔离。
        # 若允许覆盖为主 mlflow.db，多任务并发写同一个 db 会触发 SQLite "database is locked"。
        # mlflow run 记录写在线程独立 db 即可（回测结果/历史都读 artifacts 文件，不依赖 mlflow db）。
        return None

    def get_uri(self):
        return self._get_recorder().get_uri()

    # 兼容 Wrapper 层需要访问的属性（部分下游会读 R.exp_manager）
    @property
    def exp_manager(self):
        return self._get_recorder().exp_manager


def patch_qlib_parallel(base_dir: Optional[str] = None, default_exp_name: Optional[str] = None) -> None:
    """把全局 qlib.workflow.R 替换为线程本地版本。可安全重复调用（只生效一次）。"""
    global _APPLIED
    with _PATCHED:
        if _APPLIED:
            return
        import qlib.workflow as W

        W.R = _ThreadLocalQlibRecorder(base_dir=base_dir, default_exp_name=default_exp_name)
        _APPLIED = True
        import logging

        logging.getLogger(__name__).info(
            "qlib 并行补丁已应用：R 已替换为线程本地版本（支持多线程并发回测）"
        )
