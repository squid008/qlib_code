# -*- coding: utf-8 -*-
"""`_build_dataset` —— **特征与验证集口径**（★ 2026-10-19 ✓ C 项第四步）。

## 先记一个发现

这个函数（130 行 ✓）里 `from qlib.utils import init_instance_by_config` **从未被用到** ✗
（纯遗留 ✓）—— 也就是说它其实是**纯 dict 装配** ✓，不需要 mock 数据集 ✓，直接断言即可 ✓。

## 三条“回测结果会被悄悄改掉”的口径

1. **handler 选择优先级** ✓：`mixed` ⇒ `MixedHandler` ✓ > 有自定义公式 ⇒ `FormulaHandler` ✓ >
   `alpha360` ⇒ `SelectedAlpha360` ✓ > 默认 `SelectedAlpha158` ✓；
2. **`selected_features=[]`（空数组）= 真的一个特征都不要** ✓（v1.20.67，用户 2026-09-24 确认 ✓）——
   旧写法 `if req.selected_features` 把 `[]` 当"没传"⇒ **落回该特征集全量** ✗
   （实测：取消勾选全部 A158 后模型仍用 562 列 ✗）；而"空特征 + 无公式"必须**抛人话错** ✓
   （零特征会让训练/预测失败且报错难懂 ✗）；
3. **独立验证集** ✓（v1.20.46 审计 ✓）：`valid` 取训练窗**最后 ~20%** ✓ 并从 `train` 里**剔除** ✓
   （原先 `valid == train` ✗ ⇒ LightGBM 的 `early_stopping` 在**训练集自身**上做 ⇒
   模型选择失真 ✗、过拟合无约束 ✗，对最终收益影响可能是**最大的一条** ✓）。
"""
from types import SimpleNamespace

import pytest

from app.engine import qlib_engine as QE


def _req(**kw):
    base = dict(
        feature="Alpha158", selected_features=None, custom_formulas=None,
        label_horizon=None, universe="csi300", price_adjust=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _ds(**kw):
    """训练窗 2023-01-01 ~ 2023-06-30（180 天 ⇒ 20% = 36 天 ≥ 27 ✓ 会启用独立验证集 ✓）。"""
    cfg = QE._build_dataset(_req(**kw), ["SH600000"], ("2023-01-01", "2023-06-30"),
                            ("2023-07-03", "2023-09-29"))
    return cfg


def _handler(cfg):
    return cfg["kwargs"]["handler"]


def _seg(cfg):
    return cfg["kwargs"]["segments"]


# ---------------------------------------------------------------- handler 选择
def test_default_handler_is_selected_alpha158():
    h = _handler(_ds())
    assert h["class"] == "SelectedAlpha158"
    assert h["module_path"] == "app.factors.handler"
    assert h["kwargs"] == {} or "fields" not in h["kwargs"]


def test_alpha360_uses_its_own_handler():
    h = _handler(_ds(feature="Alpha360"))
    assert h["class"] == "SelectedAlpha360"


def test_custom_formulas_switch_to_formula_handler():
    h = _handler(_ds(feature="Alpha158", custom_formulas=["A>1", "B>2"]))
    assert h["class"] == "FormulaHandler", "有公式且非 mixed ⇒ 只用公式 ✓（历史行为 ✓）"
    assert h["kwargs"]["formulas"] == ["A>1", "B>2"]
    assert "fields" not in h["kwargs"], "公式模式下不该再带 fields ✓"


def test_mixed_handler_passes_both_fields_and_formulas():
    h = _handler(_ds(feature="mixed", selected_features=["A158_CLOSE", "A360_ROC5"],
                     custom_formulas=["CPX"]))
    assert h["class"] == "MixedHandler"
    assert h["kwargs"]["fields"] == ["A158_CLOSE", "A360_ROC5"]
    assert h["kwargs"]["formulas"] == ["CPX"]


def test_empty_selected_features_pass_through_in_mixed():
    """⭐ v1.20.67：`[]` **不能**被当成"没传"✗ —— 传空就真的一个都不要 ✓。

    ⚠ 只有 `mixed` 分支会**原样**写 `fields` ✓（另外两个分支在空特征时会被下面的守卫拦住 ✓，
      ✗ 我第一版拿默认 Alpha158 测"透传"，结果撞上守卫抛错 ✓ —— 断言范围选错了 ✓）。
    """
    h = _handler(_ds(feature="mixed", selected_features=[], custom_formulas=["CPX"]))
    assert h["kwargs"]["fields"] == [], "空数组必须原样透传 ✓（落回全量 = bug ✗）"
    assert h["kwargs"]["formulas"] == ["CPX"]


def test_empty_features_without_formulas_raises_human_error():
    with pytest.raises(ValueError) as ei:
        _ds(selected_features=[], custom_formulas=[])
    msg = str(ei.value)
    assert "没有任何特征可训练" in msg
    assert "至少勾选一个特征" in msg, "报错要给出可操作的下一步 ✓"


def test_empty_features_with_formulas_is_allowed():
    h = _handler(_ds(selected_features=[], custom_formulas=["CPX"]))
    assert h["class"] == "FormulaHandler"
    assert h["kwargs"]["formulas"] == ["CPX"]
    # ⚠ 公式分支**根本不写** `fields` ✓（只认 formulas ✓）—— 空数组也无需出现 ✓
    assert "fields" not in h["kwargs"]


# --------------------------------------------------------------- 验证集拆分
def test_valid_is_split_from_train_tail():
    """⭐ 独立验证集（审计）：`train` 末端必须**早于** `valid` 起点 ✓（不许重叠 ✗）。"""
    s = _seg(_ds())
    train, valid, test = s["train"], s["valid"], s["test"]
    assert train[0] == "2023-01-01"
    assert train[1] < valid[0], "train 末端必须早于 valid 起点 ✓（否则仍被污染 ✗）"
    assert valid[1] == "2023-06-30", "valid 末端 = 原训练末端 ✓"
    assert train[0] <= train[1]


def test_test_segment_starts_at_test_start_no_warmup():
    """v2.8.3（用户拍板）：**首日空仓** ✓ —— test 段不许早于 `test_start` ✗（没有 `predict_start` ✓）。"""
    s = _seg(_ds())
    assert s["test"] == ["2023-07-03", "2023-09-29"]
    assert "predict_start" not in s["test"]


def test_short_train_window_falls_back_to_valid_equals_train():
    """训练窗太短（<27 天）⇒ **退回 `valid == train`** ✓（宁可退化也别让 LightGBM 训不出来 ✓）。"""
    cfg = QE._build_dataset(_req(), ["SH600000"], ("2023-01-01", "2023-01-20"),
                            ("2023-02-01", "2023-03-01"))
    s = _seg(cfg)
    assert s["valid"] == s["train"], "过短训练窗必须退回旧行为 ✓"


# ------------------------------------------------------------- 其它落位与默认
def test_handler_kwargs_defaults():
    kw = _handler(_ds())["kwargs"]
    assert kw["start_time"] == QE._offset_date("2023-01-01", -10), "handler 起点要往前留 10 天 ✓"
    assert kw["end_time"] == "2023-09-29", "handler 必须覆盖到测试段末 ✓"
    assert kw["fit_start_time"] == "2023-01-01"
    assert kw["label_horizon"] == 2, "默认预测 2 日 ✓"
    # ⚠ 断言消息里**不许**再嵌 ASCII 双引号 ✓（今天已经因此 SyntaxError 两次 ✗）—— 用「」✓
    assert kw["universe"] == "csi300", "池名必须透传 ✓（修「全期并集」未来函数 ✓）"
    assert kw["price_adjust"] == "none"
    assert kw["instruments"] == ["SH600000"]


def test_label_horizon_and_price_adjust_passthrough():
    kw = _handler(_ds(label_horizon=5, price_adjust="forward"))["kwargs"]
    assert kw["label_horizon"] == 5
    assert kw["price_adjust"] == "forward"


def test_dataset_shape():
    cfg = _ds()
    assert cfg["class"] == "DatasetH"
    assert cfg["module_path"] == "qlib.data.dataset"
    assert set(cfg["kwargs"]["segments"]) == {"train", "valid", "test"}
