# -*- coding: utf-8 -*-
"""`routers/signal_test.py` 的**脱数据**测试（★ v2.10.43，2026-10-21 ✓）。

## 为什么先挑这个文件

全量实测里它 **9.5% / 389 行未覆盖** ✓ —— 是所有模块里**百分比最低**的一个 ✓，
而这 830 行里有一半是**纯解析与对拍**（`parse_only` / `_records` / `_official_series` /
`_amount_match` / `_raw_of` ✓），**完全不需要行情数据** ✓。

## 钉什么（都是"错了但界面正常"的那类）

* `_amount_match` —— 判断**成交明细是否完整**的硬证据 ✓：它说"全部一致"却其实差钱 ✗，
  用户就会拿一份**残缺的流水**当完整结论 ✓；
* `_official_series` —— 官方净值与我们的日历对齐（缺日 `ffill` ✓）；
  对不齐 ⇒ 图上"官方 vs 策略"错位 ✓，看起来只是"偏差大一点" ✗；
* `_raw_of` —— base64 **优先**且**坏 base64 要给人话 400** ✓（不是 500 ✗）；
* `parse_only` —— **任何输入都不许 500** ✓：认不出来就 `ok=False` + `issues` ✓（用户上传的是
  各种奇怪导出 ✓，500 只会让人以为"系统坏了"✗）。
"""
from __future__ import annotations

import base64

import pandas as pd
import pytest
from fastapi import HTTPException

from app.routers.signal_test import (
    _amount_match,
    _official_series,
    _raw_of,
    _records,
    options,
    parse_only,
)
from app.signals.models import SignalTestRequest


# --------------------------------------------------------------------- options
def test_options_lists_are_non_empty_and_defaults_complete():
    """下拉框的四个选项表**不能空** ✓（空 ⇒ 前端只剩一个默认值，用户以为"没得选"✗）。"""
    out = options()
    for key in ("benchmarks", "pools", "fills", "allocs"):
        assert out[key], "选项表 %s 不能为空 ✗" % key
    d = out["defaults"]
    for key in ("cost", "horizon", "fill", "capital", "pool", "alloc", "benchmark", "strict_limit"):
        assert key in d, "defaults 缺 %s ✗（前端拿不到默认值）" % key


# --------------------------------------------------------------------- _records
def test_records_handles_none_and_cap():
    assert _records(None) == []                       # 没数据 ⇒ 空数组 ✓（不是 None ✗）
    assert _records(pd.DataFrame()) == []             # 空表同理 ✓
    df = pd.DataFrame({"a": [1, 2, 3, 4, 5]})
    assert len(_records(df, cap=3)) == 3              # 上限生效 ✓（否则大表会把响应撑爆 ✗）
    assert _records(df, cap=3)[0] == {"a": 1}


# -------------------------------------------------------------- _official_series
def test_official_series_returns_none_when_unusable():
    """缺列 / 全 NaN 都必须 `None` ✓ —— 调用方据此**不叠加**官方曲线 ✓（不许画一条空线 ✗）。"""
    pf = pd.DataFrame({"date": pd.to_datetime(["2024-01-02"]), "nav": [1.0]})
    assert _official_series(pf, "missing_col", pd.Index(pf["date"])) is None
    pf_nan = pd.DataFrame({"date": pd.to_datetime(["2024-01-02"]), "nav": [float("nan")]})
    assert _official_series(pf_nan, "nav", pd.Index(pf_nan["date"])) is None
    assert _official_series(pd.DataFrame(), "nav", pd.Index([])) is None


def test_official_series_fills_missing_days():
    """⭐ 缺日必须 **ffill** ✓ —— 官方文件可能漏掉停牌日 ✓，不对齐就会整体错位一格 ✗。"""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    pf = pd.DataFrame({"date": idx[[0, 2]], "nav": [1.0, 1.2]})   # 缺 01-03 ✓
    got = _official_series(pf, "nav", pd.Index(idx))
    assert got is not None
    assert got.loc[idx[1]] == pytest.approx(1.0), "缺日必须沿用前一日 ✓（NaN 就是断线 ✗）"
    assert got.loc[idx[2]] == pytest.approx(1.2)


# ---------------------------------------------------------------- _amount_match
def _trades() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"]),
        "side": [1, -1, 1],
        "amount": [1000.0, 500.0, 300.0],
    })


def test_amount_match_counts_consistent_days():
    """一致 ⇒ `(2, 0)` ✓（两天的买/卖金额都对得上 ✓）。"""
    pf = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "buy_amt": [1000.0, 300.0],
        "sell_amt": [500.0, 0.0],
    })
    assert _amount_match(_trades(), pf) == (2, 0)


def test_amount_match_flags_missing_flow():
    """⭐ 少记一笔就必须**判成不一致** ✓ —— 这正是"明细是否完整"的硬证据 ✓。"""
    pf = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "buy_amt": [1000.0, 0.0],       # 01-03 少记 300 ✓
        "sell_amt": [500.0, 0.0],
    })
    ok, bad = _amount_match(_trades(), pf)
    assert ok == 1 and bad == 1, "少记一天必须计入不一致 ✓（否则残缺流水会被当完整 ✗）"


def test_amount_match_empty_inputs_are_safe():
    assert _amount_match(None, None) == (0, 0)
    assert _amount_match(pd.DataFrame(), pd.DataFrame()) == (0, 0)


# --------------------------------------------------------------------- _raw_of
def test_raw_of_prefers_base64_over_text():
    """⭐ base64 **优先** ✓ —— 用户文件常是 GBK ✓，前端只能传字节 ✓（传文本已经乱码 ✗）。"""
    raw = "日期,代码\n2024-01-02,000001\n".encode("gbk")
    req = SignalTestRequest(content="这段文本应该被忽略", content_b64=base64.b64encode(raw).decode())
    assert _raw_of(req) == raw


def test_raw_of_bad_base64_raises_400_with_human_message():
    """坏 base64 ⇒ **400 + 人话** ✓（不是 500 ✗ —— 那是"服务端 bug"，会误导排查 ✓）。"""
    req = SignalTestRequest(content_b64="@@not-base64@@")
    with pytest.raises(HTTPException) as ei:
        _raw_of(req)
    assert ei.value.status_code == 400
    assert "base64" in str(ei.value.detail)


def test_raw_of_falls_back_to_plain_text():
    req = SignalTestRequest(content="a,b\n1,2\n")
    assert _raw_of(req) == "a,b\n1,2\n"


# ------------------------------------------------------------------ parse_only
def _b64(text: str, encoding: str = "utf-8") -> str:
    return base64.b64encode(text.encode(encoding)).decode()


def test_parse_only_recognises_signal_list():
    """最小信号清单 ✓ ⇒ `ok=True` 且回显 headers ✓（前端靠它显示"识别到 N 列"✓）。"""
    csv = "日期,代码,名称\n2024-01-02,000001.SZ,平安银行\n2024-01-03,600000.SH,浦发银行\n"
    out = parse_only(SignalTestRequest(content_b64=_b64(csv), filename="sig.csv"))
    assert out["ok"] is True, "issues=%s" % out.get("issues")
    assert out["mode"] == "signal_list"
    assert out["headers"], "必须回显表头 ✓（前端要展示识别结果 ✓）"
    assert out["elapsed"] >= 0


def test_parse_only_never_500_on_garbage():
    """⭐⭐ **任何输入都不许抛** ✓ —— 认不出来就 `ok=False` + `issues` ✓。
    用户上传的是各种奇怪导出 ✓，500 只会让人以为"系统坏了"✗（其实是文件格式问题 ✓）。
    """
    out = parse_only(SignalTestRequest(content_b64=_b64("这不是CSV\n\x00\x01乱码\n"),
                                       filename="junk.csv"))
    assert out["ok"] is False
    assert out["issues"], "必须给出可读的诊断 ✓（空 issues = 用户不知道该改什么 ✗）"


def test_parse_only_honours_forced_kind():
    """显式 `kind` 必须**覆盖自动识别** ✓（用户能手动纠偏 ✓）。"""
    csv = "日期,代码,名称\n2024-01-02,000001.SZ,平安银行\n"
    out = parse_only(SignalTestRequest(content_b64=_b64(csv), kind="jq_trades"))
    assert out["mode"] == "jq_trades"
