# -*- coding: utf-8 -*-
"""qlib 全局初始化统一入口（进程内只初始化一次，且始终携带自定义算子）。

背景（外挂方案，不动 qlib 内核）：
- qlib.init() 内部会 self.reset() 全局 C 配置。若任何模块调用 qlib.init()
  时未传 custom_ops（例如 app/datasource/qlib_source.py 曾直接调用），
  C.custom_ops 会被重置为空。
- joblib/loky 的 worker 子进程靠 unpickle C.custom_ops 触发导入
  app.factors.ops_ext（从而 patch register_all_ops 并注册 DYN_* 等外挂算子）。
  C.custom_ops 一旦为空，worker 注册不到外挂算子，解析公式时报
  "The operator [DYN_COUNT] is not registered"。
- 因此所有模块必须走本入口初始化；且每次调用都校验 C.custom_ops，
  被任何历史/未来的无 custom_ops init 清空时立即写回，保证主进程
  C.custom_ops 恒非空 → worker 恒能注册外挂算子。
"""
from __future__ import annotations

import threading
from typing import Optional

_QLIB_INIT_LOCK = threading.Lock()
_QLIB_INITIALIZED = False


def ensure_qlib_init(provider_uri: Optional[str] = None) -> None:
    """线程安全的 qlib.init：首次调用真正初始化，后续调用只做 custom_ops 兜底校验。"""
    global _QLIB_INITIALIZED
    if _QLIB_INITIALIZED:
        _restore_custom_ops()
        return

    import qlib
    from qlib.constant import REG_CN

    with _QLIB_INIT_LOCK:
        if not _QLIB_INITIALIZED:
            from ..factors.ops_ext import _ALL_OPS as _custom_ops
            from ..config import QLIB_PROVIDER_URI

            qlib.init(
                provider_uri=provider_uri or QLIB_PROVIDER_URI,
                region=REG_CN,
                custom_ops=_custom_ops,
            )
            # 双保险：qlib.init 内部 reset Operators 后再次注册
            from ..factors.ops_ext import ensure_ops_registered

            ensure_ops_registered(force=True)
            _restore_custom_ops()
            _QLIB_INITIALIZED = True


def _restore_custom_ops() -> None:
    """校验并写回 C.custom_ops（被其他 qlib.init 清空时立即恢复）。"""
    try:
        from qlib.config import C
        from ..factors.ops_ext import _ALL_OPS as _custom_ops

        if not getattr(C, "custom_ops", None):
            C.custom_ops = list(_custom_ops)
    except Exception:
        pass
