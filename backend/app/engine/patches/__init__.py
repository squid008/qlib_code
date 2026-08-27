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

__all__ = [
    "patch_qlib_parallel",
    "_ThreadLocalQlibRecorder",
    "patch_cancel_callbacks",
]
