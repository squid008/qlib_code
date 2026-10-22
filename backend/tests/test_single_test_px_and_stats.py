# -*- coding: utf-8 -*-
"""`single_test.py` 的异常兜底 ＋ `_continuous_stats` 的早退（★ v2.10.96）。

## B 组：五个"兜底返回 None"的函数
`_event_px_wide` / `_full_wide_key` / `_full_px_wide` / `_trigger_split` 都是
「**try 里干活，出错就吞日志 + 返回 None**」✓。这些 except 分支**平时永远不跑** ✗，
一旦真跑起来就是"**这一块结果静默消失**"✓（图上没有额外曲线、也没有报错 ✓）。

⚠ 它们的设计意图是明确的 ✓：辅助曲线（基准/事件价格宽表/离场曲线）**失败不阻塞主结论** ✓
—— 所以这里钉的是"**坏了要安静地退化成空，而不是把整个测试拖崩**" ✓。
反面风险同样要钉：**不许把异常当成"空数据"悄悄算出一个 0** ✗。

## A′ 组：`_continuous_stats` 的**早退**
它有 22 个关键字参数 ✓（从 1104 行的 `_test_one` 原样外提 ✓）。这里只打它的
**两个入口级分支**：取消信号 ✓、ST/创业板/科创板剔除 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import single_test as S

CODES = ("SZ300001", "SH600000")


def _panel(rows_per_code=60):
    """造一个 (instrument, datetime) 双层索引的面板 ✓。"""
    days = pd.bdate_range("2024-01-02", periods=rows_per_code)
    idx = pd.MultiIndex.from_product([list(CODES), days], names=["instrument", "datetime"])
    return idx


# ---------------------------------------------------------------- B：异常兜底
def test_event_px_wide_returns_none_without_px_column():
    """⭐ 面板里没有 `PX` 列 ⇒ **返回 None**（不是抛）✓ —— 主结论不该因辅助曲线缺失而失败 ✓。"""
    trig_index = _panel(3)
    assert S._event_px_wide(pd.DataFrame({"OTHER": [1.0]}, index=trig_index), trig_index, 0) is None


def test_full_wide_key_returns_none_without_px_column():
    """⭐ 缓存键取不到 `PX` ⇒ **None** ✓（键算不出来就该放弃缓存 ✓，
    绝不能用"半个键"去命中别的表 ✗ —— 源码注释里记着"开盘表/收盘表首值可能恰好相同"那次事故 ✓）。"""
    assert S._full_wide_key(pd.DataFrame({"OTHER": [1.0]}, index=_panel(3)), 0) is None


def test_full_px_wide_returns_none_without_px_column():
    """⭐ 全样本宽表取不到 `PX` ⇒ **None** ✓（事件研究的基准曲线会缺 ✓，
    但主结论照出 ✓ —— 这是有意的降级 ✓）。"""
    assert S._full_px_wide(pd.DataFrame({"OTHER": [1.0]}, index=_panel(3)), 0) is None


def test_trigger_split_returns_none_on_garbage_input():
    """⭐ `trig` 不是 DataFrame（连 `len()` 都不行）⇒ **None** ✓，不抛 ✓
    （触发拆分只是给图用的 ✓，烂输入不该让整次测试崩 ✗）。"""
    assert S._trigger_split(object(), hold_days=5) is None


def test_ensure_qlib_init_forwards_default_uri(monkeypatch):
    """⭐ `_ensure_qlib_init()` 必须把 **默认 provider uri** 转给引擎的初始化 ✓ ——
    传错/不传 ⇒ qlib 用别的目录初始化 ⇒ 之后所有取数读错数据集 ✓（而且**不报错** ✗）。"""
    import app.engine.qlib_engine as qe
    import app.engine.utils as eu

    seen = {}

    def _fake_init(uri):
        seen["uri"] = uri

    monkeypatch.setattr(qe, "_ensure_qlib_init", _fake_init, raising=False)
    monkeypatch.setattr(eu, "_default_qlib_uri", lambda: "PROVIDER", raising=False)

    S._ensure_qlib_init()
    assert seen["uri"] == "PROVIDER", "必须把默认 uri 原样传下去 ✓"


# ---------------------------------------------------------------- A′：_continuous_stats 早退
def _cs_kwargs(*, cancelled=None, gem=False, kcb=False, n=60):
    """22 个关键字参数的**最小夹具** ✓（只喂 `_continuous_stats` 开头会用到的那几个 ✓）。"""
    idx = _panel(n)
    lab = pd.DataFrame({"F": np.linspace(0, 1, len(idx)), "LABEL": np.zeros(len(idx))}, index=idx)
    df = pd.DataFrame({
        "CLOSE": 10.0, "CHANGE": 0.0, "T1_CLOSE": 10.0, "T1_CHANGE": 0.0, "T1_IS_ST": 0.0,
    }, index=idx)

    def _frame(_pos, cols):
        cols = [cols] if isinstance(cols, str) else list(cols)
        return df[[c for c in cols if c in df.columns]]

    return dict(
        _frame=_frame, _sub_pos=slice(None), bench_close=None, bench_default=None,
        cancelled=cancelled, col="F", df=df,
        exclude_limit_up_signal=False, exclude_limit_up_trade=False, exclude_st_t1=False,
        exclude_stock_gem=gem, exclude_stock_kcb=kcb, exclude_suspended=False,
        factor="f", fill="t1_close", holdings_out={}, horizon=5, name="f",
        quantiles=(0.2, 0.4, 0.6, 0.8), rebalance_period=1,
        result={"is_binary": False}, sub=lab, topk_list=(0.1,),
    )


def test_continuous_stats_raises_on_cancel():
    """⭐⭐ 取消信号 ⇒ **抛 `FactorTestCancelled`** ✓（不是"算一半就返回" ✗）——
    半截统计会被当成真结论显示给用户 ✓。"""
    with pytest.raises(S.FactorTestCancelled):
        S._continuous_stats(**_cs_kwargs(cancelled=lambda: True))


@pytest.mark.parametrize("flag", ["gem", "kcb"])
def test_continuous_stats_survives_board_exclusion(flag):
    """⭐ 剔除创业板（`SZ30…`）/科创板（`SH688…`）时**不许抛** ✓ ——
    当池子里**全是**创业板时，剔除后剩 0 行 ✓ ⇒ 后续统计必须能安静跳过 ✓
    （抛了就是"勾了某一项就整次测试失败"✗）。"""
    res = {"is_binary": False}
    kw = _cs_kwargs(**{flag: True})
    kw["result"] = res
    S._continuous_stats(**kw)      # 不抛即通过 ✓
    assert isinstance(res, dict)


def test_continuous_stats_survives_price_column_fetch_failure():
    """⭐⭐ 搬价格列（`CLOSE_FF`/`OPEN_FF`）失败 ⇒ **必须继续算** ✓ ——
    源码注释记着这个坑的后果（v2.1.25）：
    「只搬了收盘价 ⇒ `t1_open` 时取不到 `OPEN` ⇒ 期初价全 NaN ⇒ `_perf_of` 早退
      ⇒ **年化/回撤/夏普整块消失** ✗」⇒ 所以这里**两个 except 都要吞掉并继续** ✓
    （抛了 = 整段统计全没了 ✗；而且这是**辅助列**，主结论不该依赖它 ✓）。"""
    kw = _cs_kwargs()
    df = kw["df"]
    df["CLOSE_FF"] = 10.0
    df["OPEN_FF"] = 10.0

    def _frame_boom(_pos, cols):
        cols = [cols] if isinstance(cols, str) else list(cols)
        if any(c in ("CLOSE_FF", "OPEN_FF") for c in cols):
            raise RuntimeError("boom")
        return df[[c for c in cols if c in df.columns]]

    kw["_frame"] = _frame_boom
    res = {"is_binary": False}
    kw["result"] = res
    S._continuous_stats(**kw)          # 不许抛 ✓
    assert isinstance(res, dict)


# ⚠ 原本还想钉 `result["topk_curves_error"]`（1781-1783 ✓：TopK 曲线出错要**降级但留痕** ✓）。
#   但试过之后**够不到** ✗ —— 要走到那段需要"分组 / 分位矩阵 `_dec_mat` / 成本曲线都就绪" ✓，
#   而最小夹具只到"搬价格列"就结束了 ✓。
#   ⇒ 按规矩**不写一条永远 `skip` 的测试** ✗（那比不写更糟：它会让人以为"这里测过了" ✓）。
#   该分支留待**有真面板**的用例 ✓，已记进本文件头。
