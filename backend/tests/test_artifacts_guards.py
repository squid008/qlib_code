# -*- coding: utf-8 -*-
"""`engine/artifacts.py` 的**复用守卫**与 JSON 清洗（★ v2.9.95，2026-10-19 ✓）。

## 为什么这两类东西必须测

**复用守卫**（`_verify_reuse_train_semantics` / `_verify_reuse_feature_order`）决定
「**能不能拿旧模型跑新回测**」✓。它写错的方向只有一个 —— **放行** ✗，而后果是：

* 2026-09-22 真出过事 ✓：v1.20.46 把 `valid` 改成**独立验证集**后，用**旧口径**模型混进新回测
  ⇒ 同一张净值图混两套训练口径 ✓，而净值是**累乘**的 ⇒ **整条曲线被抬高** ✗（界面一切正常 ✓）。
* 特征顺序不一致却放行 ⇒ 预测时**特征错位** ✗（模型还在跑、结果还能出，只是全是错的 ✓）。

所以这里的断言全部围绕一句话：**宁可挡住，也别静默复用** ✓ ——
包括「**没有戳**（v1.20.48 之前的产物）也要挡」✓，以及「**源目录不存在时不挡**」✓
（那是"任务被删了"✓，不该误报成"口径不一致"✗）。

`_sanitize_json` 则是**写盘前最后一步** ✓：结果里只要有一个 `NaN`，`json.dump` 就会写出
`NaN` 这种**非法 JSON** ✗ ⇒ 前端 `JSON.parse` 直接失败 ✓（整页白屏 ✓，而日志里只有一个语法错 ✗）。
"""
from __future__ import annotations

import math

import pytest

from app.engine import artifacts as A

# ------------------------------------------------------------------ _sanitize_json
def test_sanitize_replaces_nan_and_inf_with_none():
    assert A._sanitize_json(float("nan")) is None
    assert A._sanitize_json(float("inf")) is None
    assert A._sanitize_json(float("-inf")) is None
    assert A._sanitize_json(1.5) == 1.5


def test_sanitize_walks_nested_structures():
    out = A._sanitize_json({
        "a": [1.0, float("nan"), {"b": (float("inf"), 2)}],
        "c": {"d": float("-inf")},
    })
    assert out == {"a": [1.0, None, {"b": [None, 2]}], "c": {"d": None}}
    # 元组被统一成 list ✓（JSON 没有元组 ✓）
    assert isinstance(out["a"][2]["b"], list)


def test_sanitize_keeps_non_float_types_intact():
    assert A._sanitize_json(3) == 3
    assert A._sanitize_json("x") == "x"
    assert A._sanitize_json(None) is None
    assert A._sanitize_json(True) is True


def test_sanitize_result_is_json_serializable():
    """⭐ 清洗后必须能真的 `json.dumps` ✓（这是这一步存在的**唯一**理由 ✓）。"""
    import json

    raw = {"metrics": {"sharpe": float("nan"), "ret": 0.12},
           "nav": [{"v": 1.0}, {"v": float("inf")}]}
    json.dumps(A._sanitize_json(raw), allow_nan=False)   # 不许出现 NaN ✓


# ------------------------------------------------- _verify_reuse_feature_order
def _patch_feature_names(monkeypatch, names):
    monkeypatch.setattr(A, "_read_train_feature_names", lambda task, seg=None: names)


def test_feature_order_skips_when_unverifiable(monkeypatch):
    """读不到训练特征名 / 当前没特征名 ⇒ **跳过** ✓（不阻断 ✓）。"""
    _patch_feature_names(monkeypatch, None)
    A._verify_reuse_feature_order(["a", "b"], "T1")          # 不抛 ✓
    _patch_feature_names(monkeypatch, ["a", "b"])
    A._verify_reuse_feature_order(None, "T1")                # 不抛 ✓
    A._verify_reuse_feature_order([], "T1")                  # 不抛 ✓


def test_feature_order_equal_passes(monkeypatch):
    _patch_feature_names(monkeypatch, ["f1", "f2", "f3"])
    A._verify_reuse_feature_order(["f1", "f2", "f3"], "T1")   # 不抛 ✓


def test_feature_order_mismatch_raises_with_both_counts(monkeypatch):
    """⭐ 不一致必须抛 ✓，且报错里**两个数量**都要有（否则用户不知道该改哪边 ✗）。"""
    _patch_feature_names(monkeypatch, ["f1", "f2", "f3"])
    with pytest.raises(ValueError) as ei:
        A._verify_reuse_feature_order(["f1", "f9"], "T1")
    msg = str(ei.value)
    assert "特征与模型训练时不匹配" in msg
    assert "3 个" in msg and "2 个" in msg
    assert "取消复用模型权重" in msg          # 给出可执行的下一步 ✓


def test_feature_order_long_list_is_truncated(monkeypatch):
    """特征很多时只展示前 8 个 ✓（否则一屏报错读不动 ✗）。"""
    names = ["f%03d" % i for i in range(40)]
    _patch_feature_names(monkeypatch, names)
    with pytest.raises(ValueError) as ei:
        A._verify_reuse_feature_order(["other"], "T1")
    assert "..." in str(ei.value)


# --------------------------------------------- _verify_reuse_train_semantics
def _patch_semantics(monkeypatch, found_base, got, want="want-v"):
    monkeypatch.setattr(A, "_find_artifact_base", lambda task: found_base)
    monkeypatch.setattr(A, "_read_train_semantics", lambda task, seg=None: got)
    monkeypatch.setattr(A, "_current_train_semantics", lambda: want)


def test_semantics_skip_when_source_missing(monkeypatch):
    """⭐ 源任务目录不存在 ⇒ **不报错** ✓（那是"任务被删了"✗，不该说成"口径不一致"✗）。"""
    _patch_semantics(monkeypatch, None, None)
    A._verify_reuse_train_semantics("T-gone")     # 不抛 ✓


def test_semantics_pass_when_equal(monkeypatch):
    _patch_semantics(monkeypatch, "/p", "want-v")
    A._verify_reuse_train_semantics("T1")
    A._verify_reuse_train_semantics("T1", seg_no=2)   # 分段也一样 ✓


def test_semantics_missing_stamp_raises(monkeypatch):
    """⭐ **没有戳**（v1.20.48 之前的产物）⇒ 也要挡住 ✓（无法证明一致 ✗）。"""
    _patch_semantics(monkeypatch, "/p", None)
    with pytest.raises(ValueError) as ei:
        A._verify_reuse_train_semantics("T1")
    msg = str(ei.value)
    assert "没有训练口径戳" in msg
    assert "v1.20.48" in msg
    assert "关掉【复用模型权重】" in msg or "关掉" in msg


def test_semantics_mismatch_raises_with_both_values(monkeypatch):
    _patch_semantics(monkeypatch, "/p", "old-v", want="new-v")
    with pytest.raises(ValueError) as ei:
        A._verify_reuse_train_semantics("T1", seg_no=3)
    msg = str(ei.value)
    assert "训练口径不一致" in msg
    assert "old-v" in msg and "new-v" in msg
    assert "段3" in msg, "分段复用时要指出是哪一段 ✓"
