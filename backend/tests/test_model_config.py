# -*- coding: utf-8 -*-
"""模型配置构造函数的单元测试。"""
import pytest

from app.models.backtest import BacktestRequest
from app.engine.qlib_engine import _model_config


def _req(model="LightGBM", model_params=None):
    return BacktestRequest(
        universe="csi300",
        start_date="2022-01-01",
        end_date="2022-04-30",
        model=model,
        model_params=model_params,
    )


class TestModelConfig:
    def test_lightgbm_defaults(self):
        cfg = _model_config("LightGBM", _req())
        assert cfg["class"] == "LGBModel"
        kw = cfg["kwargs"]
        # 默认超参（QLib 标准值）
        assert kw["max_depth"] == 8
        assert kw["num_leaves"] == 210
        assert kw["learning_rate"] == 0.0421

    def test_lightgbm_user_override(self):
        cfg = _model_config(
            "LightGBM",
            _req(model_params={"max_depth": 6, "num_leaves": 64, "learning_rate": 0.05}),
        )
        kw = cfg["kwargs"]
        assert kw["max_depth"] == 6
        assert kw["num_leaves"] == 64
        assert kw["learning_rate"] == 0.05
        # 未覆盖的仍用默认
        assert kw["subsample"] == 0.8789

    def test_xgboost(self):
        cfg = _model_config("XGBoost", _req(model_params={"max_depth": 4, "learning_rate": 0.05}))
        assert cfg["class"] == "XGBModel"
        kw = cfg["kwargs"]
        assert kw["max_depth"] == 4
        assert kw["learning_rate"] == 0.05

    def test_linear(self):
        cfg = _model_config("Linear", _req())
        assert cfg["class"] == "LinearModel"
        assert cfg["kwargs"]["fit_intercept"] is True

    def test_linear_ignores_model_params(self):
        """Linear 没有树超参，即使传了 model_params 也应只有 fit_intercept。"""
        cfg = _model_config("Linear", _req(model_params={"max_depth": 6}))
        assert cfg["kwargs"] == {"fit_intercept": True}
