# -*- coding: utf-8 -*-
"""`artifacts._extract_model_artifacts` / `_extract_feature_importance`（★ v2.11.77）。

## 为什么这两块重要
它们是"**复现**"这件事的全部依据 ✓：
· `feature_names` —— 复用模型时要**逐位比对顺序** ✓（顺序错了就是静默错位预测 ✗）；
· `linear` 的 `weights` —— 线性模型的"公式"本身就是权重 ✓（导出后用户据此手算 ✓）；
· `model_file`（树结构文本）—— 复用权重时直接加载它 ✓，**不需要重训** ✓；
· `feature_importance` —— 用户判断"这个模型在靠什么"的唯一线索 ✓；
· `train_semantics` —— 口径戳 ✓（v1.20.48 的事故就是它缺失 ⇒ 混口径 ⇒ 净值累加被抬高 ✗）。

## 桩怎么造
真实对象是 qlib 的 `LGBModel`（`.model` 是 Booster ✓）与 `Dataset`（`.handler.get_cols` ✓）——
这里用同名属性的小类冒充 ✓，**不需要 qlib、不需要数据** ✓。
"""
from __future__ import annotations

import numpy as np

from app.engine import artifacts as A
from app.models.backtest import BacktestRequest


class _Handler:
    def __init__(self, cols=None, fail: bool = False):
        self.cols = cols or []
        self.fail = fail

    def get_cols(self, kind):
        if self.fail:
            raise RuntimeError("handler 没有列")
        return self.cols


class _Dataset:
    def __init__(self, cols=None, fail: bool = False):
        self.handler = _Handler(cols, fail)


class _LinearModel:
    def __init__(self, coef, intercept):
        self.coef_ = coef
        self.intercept_ = intercept


class _Booster:
    """冒充 **LightGBM** Booster ✓（`model_to_string` / `num_trees` / `feature_importance`）。

    ⚠⚠ **故意不实现 `get_score`** ✗→✓ —— 真实实现里 `get_score` **优先于**
    `feature_importance` ✓，而且"有 `get_score` 就只看它" ⇒ 我第一版给桩同时装了这两个方法，
    `get_score()` 返回空 dict ⇒ `fi = {}`（不是 None ✗）⇒ 直接返回 `None` ✓
    ⇒ 两条断言当场红（`'NoneType' object is not subscriptable`）✓。
    ⇒ **这是个真陷阱**：某个包装类如果同时暴露两者、而 `get_score` 返回空 ⇒
    特征重要性会**静默变成 None** ✗（见下面 `test_get_score_empty_short_circuits` ✓）。
    """

    def __init__(self, text="tree-dump", num_trees=7, fi_list=None, raise_str=False):
        self._text = text
        self._num = num_trees
        self._fi_list = fi_list
        self._raise_str = raise_str

    def model_to_string(self, num_iteration=None):
        if self._raise_str:
            raise RuntimeError("dump 失败")
        return self._text

    def num_trees(self):
        return self._num

    def feature_importance(self, importance_type="gain"):
        return np.array(self._fi_list if self._fi_list is not None else [])


class _XgbBooster:
    """冒充 **XGBoost** Booster（只有 `get_score` ✓）。"""

    def __init__(self, score=None):
        self._score = score if score is not None else {}

    def get_score(self):
        return self._score


class _LgbModel:
    def __init__(self, booster, params=None):
        self.model = booster
        self.params = params or {}


def _req(model="lightgbm", feature="Alpha158", topk=50):
    return BacktestRequest(model=model, feature=feature, topk=topk)


# ---------------------------------------------------------------- 特征重要性
class TestFeatureImportance:
    def test_from_model_dict_maps_names_and_sorts(self):
        """⭐ `f0/f1` 要映射成**真实特征名** ✓，并按重要性**降序** ✓（用户按顺序看 ✓）。"""
        class _M:
            def get_feature_importance(self):
                return {"f0": 1.0, "f1": 9.0}

        out = A._extract_feature_importance(_M(), None, feature_names=["a", "b"])
        assert [x["feature"] for x in out] == ["b", "a"], "必须降序 ✗：%s" % out
        assert out[0]["importance"] == 9.0

    def test_from_booster_get_score_column_names(self):
        """XGBoost 新版给的是 `Column_N` ✓ —— 取尾部数字当索引 ✓。"""
        out = A._extract_feature_importance(
            None, _XgbBooster({"Column_1": 5.0, "Column_0": 2.0}), feature_names=["a", "b"])
        assert [x["feature"] for x in out] == ["b", "a"]

    def test_get_score_empty_short_circuits(self):
        """⭐ 钉住上面那个陷阱：`get_score()` 返回空 dict ⇒ **结果 None** ✓（不再看 `feature_importance`）。
        谁以后把某个 wrapper 改成同时暴露两者，就会踩它 ✓。"""

        class _Both(_XgbBooster, _Booster):
            pass

        both = _Both({})
        both._fi_list = [1.0, 2.0]          # 明明有重要性 ✗
        assert A._extract_feature_importance(None, both, ["a", "b"]) is None

    def test_from_booster_list(self):
        """LightGBM 返回**数组** ⇒ 按位置命名 `f<i>` 再映射 ✓。"""
        out = A._extract_feature_importance(None, _Booster(fi_list=[0.5, 3.0]),
                                            feature_names=["x", "y"])
        assert out[0]["feature"] == "y" and out[0]["importance"] == 3.0

    def test_rounded_to_four_decimals(self):
        class _M:
            def get_feature_importance(self):
                return {"f0": 1.23456789}

        assert A._extract_feature_importance(_M(), None)[0]["importance"] == 1.2346

    def test_none_when_no_source(self):
        assert A._extract_feature_importance(_LgbModel(None), None) is None

    def test_none_when_empty(self):
        assert A._extract_feature_importance(None, _XgbBooster({})) is None

    def test_out_of_range_index_keeps_raw_key(self):
        """索引超出特征名长度 ⇒ **保留原 key** ✓（不能瞎映射到错的特征 ✗）。"""
        class _M:
            def get_feature_importance(self):
                return {"f9": 1.0}

        assert A._extract_feature_importance(_M(), None, feature_names=["a"])[0]["feature"] == "f9"

    def test_model_raise_falls_back_to_booster(self):
        """`get_feature_importance` 抛 ⇒ **回退** booster ✓（一侧坏不该整体失能 ✓）。"""
        class _M:
            def get_feature_importance(self):
                raise RuntimeError("boom")

        out = A._extract_feature_importance(_M(), _XgbBooster({"f0": 2.0}), ["a"])
        assert out and out[0]["feature"] == "a"

    def test_never_raises(self):
        """任何奇怪输入都只能返回 `None` ✓ —— 它在导出链路里，抛了就整份导出失败 ✗。"""
        assert A._extract_feature_importance(object(), object()) is None


# ---------------------------------------------------------------- 交付物提取
class TestExtractModelArtifacts:
    def test_basic_fields(self):
        got = A._extract_model_artifacts(_LgbModel(_Booster()), _Dataset(["f1", "f2"]),
                                         _req(), seg_label="seg3")
        assert got["model_info"]["segment"] == "seg3"
        assert got["feature_names"] == ["f1", "f2"]
        assert got["train_semantics"], "口径戳不能为空 ✓（空串会被当成'无戳'⇒缓存无效 ✗）"
        assert got["train_semantics_info"], "口径明细也不能空 ✓"

    def test_handler_failure_keeps_empty_list(self):
        got = A._extract_model_artifacts(_LgbModel(None), _Dataset(fail=True), _req())
        assert got["feature_names"] == [], "handler 抛错也要给出空列表 ✓（不能整体失败 ✗）"

    def test_dataset_without_handler(self):
        got = A._extract_model_artifacts(_LgbModel(None), object(), _req())
        assert got["feature_names"] == []

    def test_linear_weights_mapped(self):
        """⭐ 线性模型的"公式"就是权重 ✓ —— 名字与权重必须**一一对上**（错位就等于公式错 ✗）。"""
        m = _LinearModel(coef=[0.5, -1.5], intercept=0.25)
        got = A._extract_model_artifacts(m, _Dataset(["a", "b"]), _req(model="linear"))
        lin = got["linear"]
        assert lin["intercept"] == 0.25
        assert lin["weights"] == [0.5, -1.5]
        assert [x["feature"] for x in lin["feature_weights"]] == ["a", "b"]
        assert lin["feature_weights"][1]["weight"] == -1.5

    def test_linear_length_mismatch_drops_pairs(self):
        """特征数与权重数不一致 ⇒ **不硬配对** ✓（`feature_weights=None`，权重照留 ✓）。"""
        got = A._extract_model_artifacts(_LinearModel([1.0, 2.0], 0.0), _Dataset(["only-one"]),
                                         _req(model="linear"))
        assert got["linear"]["feature_weights"] is None
        assert got["linear"]["weights"] == [1.0, 2.0]

    def test_tree_model_dump_and_params_filter(self):
        """⭐⭐ 树结构文本 + **只留标量参数** ✓（`params` 里混进 dict 会让 JSON 导出难看/失败 ✗）。"""
        booster = _Booster(text="TreeStructure", num_trees=12, fi_list=[2.0, 1.0])
        m = _LgbModel(booster, params={"lr": 0.1, "n": 3, "flag": True, "obj": {"a": 1}, "none": None})
        got = A._extract_model_artifacts(m, _Dataset(["f1"]), _req())
        assert got["model_file"] == "TreeStructure"
        assert got["model_info"]["num_trees"] == 12
        assert got["params"] == {"lr": 0.1, "n": 3, "flag": True, "none": None}, got["params"]
        assert "obj" not in got["params"], "嵌套对象不该进参数快照 ✗"
        assert got["feature_importance"], "树模型应顺带给出特征重要性 ✓"

    def test_tree_without_dump_method(self):
        """booster 没有 dump 能力 ⇒ `model_file` 留 None ✓ 且不抛 ✓。"""

        class _NoDump:
            params = None

        got = A._extract_model_artifacts(_LgbModel(_NoDump()), _Dataset(["f1"]), _req())
        assert got["model_file"] is None
        assert got["params"] == {}

    def test_unknown_model_keeps_placeholders(self):
        got = A._extract_model_artifacts(_LgbModel(None), _Dataset(), _req(model="something-else"))
        assert got["linear"] is None and got["model_file"] is None
        assert got["feature_importance"] is None
