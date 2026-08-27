# -*- coding: utf-8 -*-
"""训练中途可取消的外挂插件（不改 qlib / lightgbm / xgboost 内核）。

背景：
  qlib 引擎 `model.fit(dataset)` 训练 LightGBM/XGBoost 模型时，训练块内部没有取消
  检查点，用户点"取消"要等整个训练块结束才生效（可能几分钟）。

方案（纯外挂，monkey-patch）：
  patch `lightgbm.train` 和 `xgboost.train`，在传入的 callbacks 里注入"每 N 轮检查
  一次是否取消"的回调。回调里调用 `check_cancel()`，若任务被取消则抛
  `TaskCancelledError`，训练立即中断并向上传播。

类型兼容：
  - LightGBM 4.x：callback 是普通函数/`__call__` 对象（`lgb.train` 接受 Callable），
    用普通函数即可。
  - XGBoost 3.x：callback 必须继承 `xgb.callback.TrainingCallback`（校验
    `callback must be an instance of TrainingCallback`），需实现 `before_iteration`
    方法（返回 True 停止训练）。

用法（回测线程内）：
    from app.engine.patches import patch_cancel_callbacks
    patch_cancel_callbacks()
"""
from __future__ import annotations

import functools
import os
import threading

_PATCHED = threading.Lock()
_APPLIED = False

# 每多少轮迭代检查一次取消（越大越省开销，越小取消响应越快）
CHECK_EVERY_N_ITER = int(os.environ.get("QLIB_CANCEL_CHECK_ITER", "10"))


def _make_cancel_callback(check_cancel_fn):
    """构造 LightGBM 训练回调：每 N 轮调用 check_cancel_fn。普通函数即可。"""
    def _cb(env):
        try:
            iter_no = int(getattr(env, "iteration", 0))
        except Exception:
            iter_no = 0
        if iter_no % CHECK_EVERY_N_ITER == 0:
            check_cancel_fn()
    return _cb


def _make_xgb_cancel_callback(check_cancel_fn):
    """构造 XGBoost 训练回调：必须继承 TrainingCallback，实现 before_iteration。"""
    import xgboost as xgb
    from xgboost.callback import TrainingCallback

    class _XgbCancelCallback(TrainingCallback):
        def before_iteration(self, model, epoch, evals_log):
            # epoch 从 0 开始，每隔 CHECK_EVERY_N_ITER 轮检查一次
            if (epoch + 1) % CHECK_EVERY_N_ITER == 0:
                check_cancel_fn()
            return False  # 不主动停止（由异常中断）

    return _XgbCancelCallback()


def patch_cancel_callbacks():
    """monkey-patch lightgbm.train 与 xgboost.train，注入取消回调。可重复调用（只生效一次）。"""
    global _APPLIED
    with _PATCHED:
        if _APPLIED:
            return

        # 从引擎取 check_cancel（延迟导入避免循环）
        from .context import check_cancel

        # ---- LightGBM（普通函数 callback 即可）----
        try:
            import lightgbm as lgb

            _orig_lgb_train = lgb.train
            cancel_cb_lgb = _make_cancel_callback(check_cancel)

            @functools.wraps(_orig_lgb_train)
            def _patched_lgb_train(params, train_set, num_boost_round=100, *args, **kwargs):
                callbacks = list(kwargs.get("callbacks") or [])
                callbacks.append(cancel_cb_lgb)
                kwargs["callbacks"] = callbacks
                return _orig_lgb_train(params, train_set, num_boost_round, *args, **kwargs)

            lgb.train = _patched_lgb_train
        except Exception:
            pass

        # ---- XGBoost（必须是 TrainingCallback 子类）----
        try:
            import xgboost as xgb

            _orig_xgb_train = xgb.train
            cancel_cb_xgb = _make_xgb_cancel_callback(check_cancel)

            @functools.wraps(_orig_xgb_train)
            def _patched_xgb_train(params, dtrain, num_boost_round=10, *args, **kwargs):
                callbacks = list(kwargs.get("callbacks") or [])
                callbacks.append(cancel_cb_xgb)
                kwargs["callbacks"] = callbacks
                return _orig_xgb_train(params, dtrain, num_boost_round, *args, **kwargs)

            xgb.train = _patched_xgb_train
        except Exception:
            pass

        _APPLIED = True
        import logging

        logging.getLogger(__name__).info(
            "训练取消回调已启用：LightGBM/XGBoost 每 %d 轮检查一次取消（QLIB_CANCEL_CHECK_ITER）",
            CHECK_EVERY_N_ITER,
        )
