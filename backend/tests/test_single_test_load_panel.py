# -*- coding: utf-8 -*-
"""`_load_feature_panel` 的开关 / 校验 / 重排（★ v2.10.99）。

## 为什么这轮能"脱真数据"测这 138 行
它内部调 `panel_features(...)` 拿面板 ✓。**把那个函数 monkeypatch 成"返回形状不对的 DataFrame"**
就能打中**全部校验分支** ✓（不需要真行情 ✗）。而这些分支恰恰是**事故高发区** ✓。

## ⭐⭐⭐ 最该钉的一条：**列顺序不同时必须按名字重排，绝不许按位置改名**
源码 v1.19.97 记着一次用户报障（2026-09-18 ✓）：
> qlib 返回的列顺序**不保证**与传入一致（因子列 `F0` 被排到最后）；
> 按位置改名会把**数据也搬错位** ⇒ `F0` 列里装进 `T1_IS_ST` 的值
> ⇒ **因子变成 ST 标签（0/1）** ⇒ 信号从十几万掉到几百、收益统计整块消失。

所以测试构造"列名顺序被打乱"的返回 ⇒ 断言**值跟着名字走**（`F0` 仍是 0.5、`T1_IS_ST` 仍是 1.0 ✓），
而不只是断言列名列表 ✓ —— 后者在"按位置改名"的实现下**同样是绿的** ✗。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import panel_expr as PE
from app.factors import single_test as S

ALL_COLS = ["LABEL", "CLOSE", "T1_IS_ST", "F0"]
FIELDS = ["$close", "T1_IS_ST", "Pow($close,2)"]


def _pdf(cols, rows=4, values=None):
    """造一个面板：index 是两层 MultiIndex，列名由 `cols` 指定 ✓。"""
    idx = pd.MultiIndex.from_product([["SH600000"], pd.bdate_range("2024-01-02", periods=rows)],
                                     names=["instrument", "datetime"])
    if values is None:
        values = {c: np.arange(float(rows)) + i for i, c in enumerate(cols)}
    return pd.DataFrame({c: values[c] for c in cols}, index=idx)


def _call(**over):
    kw = dict(
        instruments=["SH600000"],
        fields=FIELDS,
        all_cols=ALL_COLS,
        start_date="2024-01-01",
        load_end="2024-02-01",
        freeze_suspended_price=False,
        end_date="2024-02-01",
        cancelled=None,
        progress_cb=None,
        factors=[],
        warmup_days=0,
    )
    kw.update(over)
    return S._load_feature_panel(**kw)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("QLIB_SFT_PANEL", raising=False)
    monkeypatch.delenv("QLIB_SFT_PANEL_EMA", raising=False)
    monkeypatch.delenv("QLIB_SFT_PANEL_MAX", raising=False)
    # `_dump_sft_error` 会往磁盘写诊断文件 ✗ ⇒ 换成记录器 ✓
    calls = []
    monkeypatch.setattr(S, "_dump_sft_error", lambda e: calls.append(e))
    yield calls


# ---------------------------------------------------------------- 开关
def test_panel_switch_off_returns_none(monkeypatch):
    """⭐ `QLIB_SFT_PANEL=0` ⇒ **直接 None**（回退 qlib）✓ —— 这是"面板出问题时的逃生口" ✓，
    它一旦失效，用户就没法绕开面板 bug ✗。"""
    monkeypatch.setenv("QLIB_SFT_PANEL", "0")
    assert _call() is None


def test_ema_escape_hatch_only_blocks_ema_fields(monkeypatch):
    """⭐⭐ `QLIB_SFT_PANEL_EMA=1` ⇒ **只有含 EMA/EMA_TDX/SMA 的字段**才回退 ✓ ——
    整块关掉会让"含一个 EMA 的公式"把所有字段都拖回慢路径 ✗。"""
    monkeypatch.setenv("QLIB_SFT_PANEL_EMA", "1")
    monkeypatch.setattr(PE, "panel_features", lambda *a, **k: _pdf(ALL_COLS))
    assert _call(fields=["EMA($close,12)"]) is None, "含 EMA ⇒ 回退 ✓"
    got = _call(fields=["$close"])          # 不含 EMA ⇒ 照走面板 ✓
    assert got is not None and list(got.columns) == ALL_COLS


# ---------------------------------------------------------------- 空结果
@pytest.mark.parametrize("empty", [None, pd.DataFrame()])
def test_empty_panel_falls_back(monkeypatch, empty):
    """面板返 None / 空表 ⇒ **None**（回退）✓ —— 不许把空表当成功返回 ✗
    （下游会算出"0 个信号"并当成真的没信号 ✓）。"""
    monkeypatch.setattr(PE, "panel_features", lambda *a, **k: empty)
    assert _call() is None


# ---------------------------------------------------------------- 列校验
def test_column_count_mismatch_raises(monkeypatch):
    """⭐⭐ 列数不符 ⇒ **抛**（不是回退 ✗）—— 长度不符**必然错位** ✓，
    默默算下去就是"结果全错但看起来正常" ✓（源码原话：nav 1.86e32、年化 172 万倍 ✗）。"""
    monkeypatch.setattr(PE, "panel_features", lambda *a, **k: _pdf(ALL_COLS[:3]))
    with pytest.raises(ValueError) as ei:
        _call()
    assert "列数不符" in str(ei.value)


def test_missing_column_raises(monkeypatch):
    """⭐⭐ 列数对但**缺某一列** ⇒ 抛 ✓ —— 只在"数量对"时才算安全 ✗，
    少一列多一列同名的会把因子列错位成价格列 ✓。"""
    bad = _pdf(["LABEL", "CLOSE", "T1_IS_ST", "OTHER"])
    monkeypatch.setattr(PE, "panel_features", lambda *a, **k: bad)
    with pytest.raises(ValueError) as ei:
        _call()
    assert "缺列" in str(ei.value)


def test_reordered_columns_are_reordered_by_name_not_position(monkeypatch, _clean_env):
    """⭐⭐⭐ **列顺序被打乱 ⇒ 按名字重排，值必须跟着名字走** ✓（v1.19.97 那次事故 ✓）。

    构造：`F0` 被排到最后、且**各列取值不同** ✓
    · 正确实现 ⇒ 返回列序 == `all_cols` ✓ 且 `F0` 的值仍是 0.5、`T1_IS_ST` 仍是 1.0 ✓；
    · 错误的"按位置改名" ⇒ 列名看着也对 ✗ 但 `F0` 里装的是 `T1_IS_ST` 的值 ✓
      ⇒ **因子变成 ST 标签（0/1）** ⇒ 信号从十几万掉到几百 ✓。
    """
    vals = {"LABEL": 9.0, "CLOSE": 8.0, "T1_IS_ST": 1.0, "F0": 0.5}
    shuffled = _pdf(["T1_IS_ST", "LABEL", "CLOSE", "F0"], values=vals)   # F0 在末尾 ✓
    monkeypatch.setattr(PE, "panel_features", lambda *a, **k: shuffled)

    got = _call()
    assert list(got.columns) == ALL_COLS, "必须按名字重排成 all_cols 的顺序 ✓"
    assert got["F0"].unique().tolist() == [0.5], "`F0` 的值必须是自己的 ✓（不是 ST 标签 ✗）"
    assert got["T1_IS_ST"].unique().tolist() == [1.0]
    assert got["CLOSE"].unique().tolist() == [8.0]
    # 顺序不一致本身要**留痕**（不是静默修好就完事 ✓）
    assert _clean_env and "列顺序" in str(_clean_env[0]), "列序不一致必须记诊断 ✓"


def test_progress_is_reported_for_panel_path(monkeypatch):
    """⭐ 进度回调要给 **6% → 30%** ✓ —— 面板求值可能几十秒 ✓，
    没有进度用户会以为"卡死了"并去点取消 ✓。"""
    monkeypatch.setattr(PE, "panel_features", lambda *a, **k: _pdf(ALL_COLS))
    seen = []
    _call(progress_cb=lambda h, p, m: seen.append(p))
    assert 6.0 in seen and 30.0 in seen


def test_exception_from_panel_falls_back_and_is_dumped(monkeypatch, _clean_env):
    """⭐ 面板内部抛异常 ⇒ **回退 + 留诊断** ✓（不是把异常甩给用户 ✗）——
    `_dump_sft_error` 是事后排查的唯一线索 ✓。"""
    def boom(*a, **k):
        raise RuntimeError("panel boom")

    monkeypatch.setattr(PE, "panel_features", boom)
    assert _call() is None
    assert _clean_env and "panel boom" in str(_clean_env[0])


# ---------------------------------------------------------------- 取消
def test_cancel_propagates_instead_of_falling_back(monkeypatch):
    """⭐⭐ 取消信号 ⇒ **抛 `FactorTestCancelled`** ✓（不能被 `except Exception` 吞成"回退 qlib" ✗）——
    源码注释专门写了这条顺序要求 ✓：吞掉就会**继续跑几分钟的 qlib 取数** ✓，
    用户点了取消却看着它继续跑 ✗。"""
    def panels(*a, **k):
        k["cancel_cb"]()          # 模拟面板内部检查取消 ✓
        return _pdf(ALL_COLS)

    monkeypatch.setattr(PE, "panel_features", panels)
    with pytest.raises(S.FactorTestCancelled):
        _call(cancelled=lambda: True)


# ---------------------------------------------------------------- 并行路径
def test_parallel_path_column_count_mismatch_raises(monkeypatch):
    """⭐⭐ **技术债 P2-#19 已清偿（v2.11.0）**：并行路径对"列数不符"也**上抛** ✓ ——
    与单进程路径**同口径** ✓。

    ⚠ 原来是**吞成 None**（静默回退 qlib）✗ ⇒ 同一个数据错 **中小池报错、全 A 静默变慢几倍** ✓，
    全 A 用户只会觉得"今天怎么这么慢"✗。源码已论证列数不符是"**必然错位**"✓
    ⇒ 静默回退等于把 bug 藏成变慢 ✗ ⇒ 现在两条路径都**响亮失败** ✓。
    """
    monkeypatch.setenv("QLIB_SFT_PANEL_MAX", "1")          # 1 只以上 ⇒ 走并行 ✓
    monkeypatch.setattr(PE, "panel_features_parallel", lambda *a, **k: _pdf(ALL_COLS[:2]))
    with pytest.raises(ValueError) as ei:
        _call(instruments=["SH600000", "SZ000001"])
    assert "列数不符" in str(ei.value)


def test_parallel_path_fetch_error_still_falls_back(monkeypatch, _clean_env):
    """⭐⭐ 但**取数本身失败**仍旧回退 ✓ —— 这是有意的降级：并行是"加速器"✓，
    它坏了应该退到 qlib 慢跑 ✓，而**不该让整次测试失败** ✗。
    这条与上面那条（列错位必须上抛 ✓）一起，把 #19 的口径钉死 ✓。"""
    monkeypatch.setenv("QLIB_SFT_PANEL_MAX", "1")

    def boom(*a, **k):
        raise RuntimeError("parallel boom")

    monkeypatch.setattr(PE, "panel_features_parallel", boom)
    assert _call(instruments=["SH600000", "SZ000001"]) is None
    assert _clean_env and "parallel boom" in str(_clean_env[0])


def test_parallel_path_success_reorders_columns(monkeypatch):
    """⭐ 并行路径成功时**同样按名字重排** ✓（不是只有单进程路径做 ✓）。"""
    monkeypatch.setenv("QLIB_SFT_PANEL_MAX", "1")
    vals = {"LABEL": 9.0, "CLOSE": 8.0, "T1_IS_ST": 1.0, "F0": 5.0}
    monkeypatch.setattr(PE, "panel_features_parallel",
                        lambda *a, **k: _pdf(["F0", "LABEL", "CLOSE", "T1_IS_ST"], values=vals))
    got = _call(instruments=["SH600000", "SZ000001"])
    assert list(got.columns) == ALL_COLS
    assert got["F0"].unique().tolist() == [5.0]
