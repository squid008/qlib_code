# -*- coding: utf-8 -*-
"""qlib 外挂补丁包（不改 qlib 内核）。

统一在这里做 monkey-patch，让 qlib 具备多线程并行、训练中途可取消等能力，
同时保持项目结构干净、可独立维护、升级 qlib 不受影响。

用法（回测线程内）：
    from app.engine.patches import patch_qlib_parallel, patch_cancel_callbacks
    patch_qlib_parallel(...)
    patch_cancel_callbacks()
"""

from .qlib_parallel import patch_qlib_parallel, _ThreadLocalQlibRecorder
from .cancel_train import patch_cancel_callbacks
from .serial_load import install_serial_load, kill_and_reset_loky_pool, serial_enabled

# 取数串行化（进程级、幂等）在**导入本包时**就装上：凡是会走 joblib/loky 取数的路径
# （回测的 `D.features`/`Dataset`、分析模块 `data_cache.get_or_load`、gate 额外列…）都受保护，
# 不依赖调用方记得装 —— 它就是为了根治"多任务共享同一 loky 池"的死锁（见 serial_load.py）。
install_serial_load()

__all__ = [
    "patch_qlib_parallel",
    "_ThreadLocalQlibRecorder",
    "patch_cancel_callbacks",
    "install_serial_load",
    "kill_and_reset_loky_pool",
    "serial_enabled",
]
