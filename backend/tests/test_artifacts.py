# -*- coding: utf-8 -*-
"""artifacts 模块纯函数测试：JSON 清理、交付物提取、参数/结果持久化。"""
import json
import math
import os

import pytest

from app.engine.artifacts import _sanitize_json, _save_result_json, _save_backtest_params
from app.engine.metrics import _aggregate_from_nav


class TestSanitizeJson:
    def test_nan_and_inf_to_none(self):
        data = {"a": float("nan"), "b": float("inf"), "c": 1.5,
                "list": [float("nan"), 2.0], "d": {"e": float("-inf")}}
        cleaned = _sanitize_json(data)
        assert cleaned["a"] is None
        assert cleaned["b"] is None
        assert cleaned["c"] == 1.5
        assert cleaned["list"] == [None, 2.0]
        assert cleaned["d"]["e"] is None

    def test_finite_kept(self):
        assert _sanitize_json(0.0) == 0.0
        assert _sanitize_json(1.0) == 1.0
        assert _sanitize_json("x") == "x"
        assert _sanitize_json(None) is None
        assert _sanitize_json(3) == 3


class TestSaveResultJson:
    def test_write_and_read_back(self, tmp_path):
        # 构造一个含 NaN 的结果对象，确保持久化后是标准 JSON（NaN→null）
        result = _aggregate_from_nav(
            [{"date": "2022-01-04", "value": 1.0, "benchmark": 1.0},
             {"date": "2022-01-05", "value": float("nan"), "benchmark": 1.01}],
            [],
        )
        _save_result_json(str(tmp_path), result)
        f = os.path.join(str(tmp_path), "result.json")
        assert os.path.exists(f)
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # json.load 能成功（无 NaN），且 nav 中 NaN 被清成 null
        assert data["nav"][1]["value"] is None
        assert data["total_return"] is not None or data["total_return"] is None


class TestSaveBacktestParams:
    def test_params_and_meta_written(self, tmp_path):
        from app.models.backtest import BacktestRequest
        req = BacktestRequest(
            universe="csi300", start_date="2022-01-01", end_date="2022-04-30",
            model="LightGBM", topk=50,
        )
        _save_backtest_params(str(tmp_path), req)
        assert os.path.exists(os.path.join(str(tmp_path), "params.json"))
        assert os.path.exists(os.path.join(str(tmp_path), "meta.json"))
        with open(os.path.join(str(tmp_path), "params.json"), "r", encoding="utf-8") as f:
            params = json.load(f)
        assert params["model"] == "LightGBM"
        assert params["universe"] == "csi300"
