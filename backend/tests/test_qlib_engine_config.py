# -*- coding: utf-8 -*-
"""`qlib_engine` 的**配置装配**（★ 2026-10-19 ✓ —— 纯 dict 拼装，不碰数据 ✓）。

## 为什么这两块值得单独钉住

1. **`_model_config` 的默认超参决定"可复现性"** ✓ ——
   `seed: 0` 固定随机种子 ✓、`num_threads: 0` 让 lightgbm 自己决定线程数 ✓、
   `num_leaves: 210 / max_depth: 8 / lr: 0.0421 …` 是**项目调过的默认值** ✓。
   任何一个被"顺手改一下"⇒ 历史回测结果**不再可复现** ✗，而界面上完全看不出来 ✗。
2. **"留空 = 用默认"是前端契约** ✓ —— 用户清空输入框时前端传的是 **`""`**（不是 `None` ✗），
   所以 `_apply` 里必须 `v != ""` 才算覆盖 ✓；漏了这句 ⇒ **清空反而把值设成空串** ✗，
   训练直接报错或被静默忽略 ✗。
3. **`_resolve_data_source` 的报错文案是给用户看的** ✓ ——
   "数据源 xx 不存在" vs "回测引擎当前仅支持 qlib" 是**两种不同处置** ✓（前者去设置页加数据源 ✓、
   后者换回 qlib ✓），混成一句就会让用户走错路 ✗。
"""
from types import SimpleNamespace

import pytest

from app.engine import qlib_engine as QE


def _req(**kw):
    base = dict(model_params={}, data_source=None, data_source_provider_uri=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ------------------------------------------------------------------ 模型配置
def test_model_config_default_is_lightgbm_with_frozen_defaults():
    cfg = QE._model_config("LightGBM", _req())
    assert cfg["class"] == "LGBModel"
    assert cfg["module_path"] == "qlib.contrib.model.gbdt"
    kw = cfg["kwargs"]
    # ⚠ 这些是**项目调过的默认值** ✓ —— 改动会让历史回测不再可复现 ✗
    assert kw["loss"] == "mse"
    assert kw["seed"] == 0, "seed 必须固定为 0 ✓（同参可复现 ✓）"
    assert kw["num_leaves"] == 210
    assert kw["max_depth"] == 8
    assert kw["num_threads"] == 0
    assert kw["learning_rate"] == pytest.approx(0.0421)
    assert kw["colsample_bytree"] == pytest.approx(0.8879)
    assert kw["subsample"] == pytest.approx(0.8789)


def test_model_config_user_override_applies():
    cfg = QE._model_config("lightgbm", _req(model_params={"num_leaves": 31, "seed": 7}))
    assert cfg["kwargs"]["num_leaves"] == 31
    assert cfg["kwargs"]["seed"] == 7
    # 未覆盖的键保持默认 ✓
    assert cfg["kwargs"]["max_depth"] == 8
    assert cfg["kwargs"]["loss"] == "mse"


@pytest.mark.parametrize("empty", ["", None])
def test_model_config_empty_value_does_not_override(empty):
    """⭐ 前端"留空"传的是 `""` ✓ —— 必须**不覆盖** ✓（否则清空会把参数设成空串 ✗）。"""
    cfg = QE._model_config("lightgbm", _req(model_params={"num_leaves": empty, "seed": empty}))
    assert cfg["kwargs"]["num_leaves"] == 210, "空值不该覆盖默认 ✓（实测 %r）" % cfg["kwargs"]["num_leaves"]
    assert cfg["kwargs"]["seed"] == 0


def test_model_config_unknown_model_falls_back_to_lightgbm():
    cfg = QE._model_config("catboost", _req())
    assert cfg["class"] == "LGBModel", "未知模型一律走 LightGBM 默认 ✓（不许静默变成别的模型 ✗）"


def test_model_config_case_insensitive():
    assert QE._model_config("LIGHTGBM", _req())["class"] == "LGBModel"
    assert QE._model_config("XGBoost", _req())["class"] == "XGBModel"
    assert QE._model_config("", _req())["class"] == "LGBModel"      # 空名 ⇒ 默认 ✓


def test_model_config_xgboost_maps_its_own_params():
    cfg = QE._model_config("xgboost", _req(model_params={"min_child_weight": 5, "max_depth": 6,
                                                         "num_leaves": 999}))
    assert cfg["class"] == "XGBModel"
    assert cfg["module_path"] == "qlib.contrib.model.xgboost"
    assert cfg["kwargs"]["min_child_weight"] == 5
    assert cfg["kwargs"]["max_depth"] == 6
    assert "num_leaves" not in cfg["kwargs"], "XGBoost 没有 num_leaves ✓（传了会被忽略/报错 ✗）"


def test_model_config_linear_ignores_user_params():
    cfg = QE._model_config("linear", _req(model_params={"num_leaves": 31, "seed": 7}))
    assert cfg["class"] == "LinearModel"
    assert cfg["kwargs"] == {"fit_intercept": True}, "线性模型无树参数 ✓（不许把种子等塞进去 ✗）"


# ---------------------------------------------------------------- 数据源解析
def _patch_sources(monkeypatch, mapping):
    monkeypatch.setattr(QE, "get_data_source", lambda name: mapping[name])
    monkeypatch.setattr(QE, "list_data_sources", lambda: mapping)


def test_resolve_data_source_defaults_to_qlib(monkeypatch):
    ds = SimpleNamespace(provider_uri="D:/data/qlib")
    _patch_sources(monkeypatch, {"qlib": ds})
    got_ds, uri = QE._resolve_data_source(_req())
    assert got_ds is ds
    assert uri == "D:/data/qlib", "未显式指定 ⇒ 用数据源自身的 provider_uri ✓"


def test_resolve_data_source_explicit_uri_wins(monkeypatch):
    ds = SimpleNamespace(provider_uri="D:/data/qlib")
    _patch_sources(monkeypatch, {"qlib": ds})
    _ds, uri = QE._resolve_data_source(_req(data_source_provider_uri="E:/other"))
    assert uri == "E:/other", "请求里显式指定的优先 ✓"


def test_resolve_data_source_unknown_lists_available(monkeypatch):
    _patch_sources(monkeypatch, {"qlib": SimpleNamespace(provider_uri="x")})
    with pytest.raises(ValueError) as ei:
        QE._resolve_data_source(_req(data_source="nope"))
    msg = str(ei.value)
    assert "不存在或未启用" in msg
    assert "qlib" in msg, "报错要列出可用数据源 ✓（否则用户不知道填什么 ✗）"


def test_resolve_data_source_non_qlib_rejected_with_hint(monkeypatch):
    """非 qlib 数据源必须报**明确**错误 ✓（不许静默降级 ✗）—— 文案要让用户知道"改回 qlib"✓。"""
    _patch_sources(monkeypatch, {
        "qlib": SimpleNamespace(provider_uri="x"),
        "rqalpha": SimpleNamespace(provider_uri="y"),
    })
    with pytest.raises(ValueError) as ei:
        QE._resolve_data_source(_req(data_source="rqalpha"))
    msg = str(ei.value)
    assert "仅支持 qlib" in msg
    assert "rqalpha" in msg, "报错要指出具体是哪个数据源 ✓"
