# -*- coding: utf-8 -*-
"""`_bucket_signals` 向量化（v1.20.0）与**旧实现逐位对拍**。

为什么必须有这条：向量化只允许**改性能**，绝不允许**改语义** ✗ —— 分桶错了会静默改变
成交/被拒/净值，而且不会报错 ✓。故此处保留**旧实现的逐事件循环副本** `_ref`，
对多组边界用例逐键比对 `buys / exits / deferred / beyond / n_mapped` ✓。
"""
import pandas as pd
import pytest

from app.signals.engine import _bucket_signals


def _ref(signals, prep):
    """旧实现**原样副本**（v1.20.0 之前的逐事件 Python 循环）——对拍基准 ✓。"""
    cal, col_of = prep["cal"], prep["col_of"]
    buys, exits = {}, {}
    defer = beyond = 0
    n = len(cal)
    for dt, code, side in zip(signals["date"], signals["code"], signals["side"]):
        if code not in col_of:
            continue                                     # 不在面板 ⇒ 丢弃且不计 beyond ✓
        d = pd.Timestamp(dt)
        if n == 0 or d < cal[0]:
            beyond += 1
            continue
        p = int(cal.searchsorted(d))
        if p >= n:
            beyond += 1
            continue
        if cal[p] != d:
            defer += 1
        c = col_of[code]
        tgt = buys if side > 0 else exits
        bucket = tgt.setdefault(p, [])
        if c in bucket:
            continue
        bucket.append(c)
    return {"buys": buys, "exits": exits, "deferred": defer, "beyond": beyond,
            "n_mapped": sum(len(v) for v in buys.values()) + sum(len(v) for v in exits.values())}


def _prep(cal, codes):
    return {"cal": pd.DatetimeIndex(cal), "col_of": {c: i for i, c in enumerate(codes)},
            "pos_of": {d: i for i, d in enumerate(cal)}}


CAL = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]  # 01-06/07 休市
CODES = ["A", "B", "C"]

CASES = {
    "空信号": ([], [], []),
    "正常交易日": (["2024-01-02", "2024-01-04"], ["A", "C"], [1, 1]),
    "非交易日顺延(周六)": (["2024-01-06"], ["A"], [1]),
    "早于起始日": (["2023-12-29"], ["A"], [1]),
    "晚于末尾": (["2024-02-01"], ["A"], [1]),
    "不在面板": (["2024-01-02"], ["ZZZ"], [1]),
    "同股同日重复买": (["2024-01-02", "2024-01-02"], ["A", "A"], [1, 1]),
    "同股同日买+卖(互不影响)": (["2024-01-02", "2024-01-02"], ["A", "A"], [1, -1]),
    "同日多股混合": (["2024-01-03", "2024-01-03", "2024-01-03"],
                     ["A", "B", "ZZZ"], [1, -1, 1]),
    "越界+在面板混合": (["2023-12-29", "2024-01-02", "2024-02-01"], ["A", "B", "C"], [1, 1, 1]),
}


@pytest.mark.parametrize("name", list(CASES))
def test_bucket_matches_legacy(name):
    dts, codes, sides = CASES[name]
    signals = pd.DataFrame({"date": pd.to_datetime(dts), "code": codes, "side": sides})
    # ⚠ 空信号要给出**正确 dtype** 的列（否则 DatetimeIndex 解析会报错 ✓）
    if not dts:
        signals = pd.DataFrame({"date": pd.to_datetime([]), "code": [], "side": []})
    prep = _prep(CAL, CODES)
    got, exp = _bucket_signals(signals, prep), _ref(signals, prep)
    for k in ("deferred", "beyond", "n_mapped"):
        assert got[k] == exp[k], "%s: %s %r ≠ %r" % (name, k, got[k], exp[k])
    for k in ("buys", "exits"):
        assert {d: sorted(v) for d, v in got[k].items()} == \
               {d: sorted(v) for d, v in exp[k].items()}, "%s: %s 不一致" % (name, k)


def test_bucket_empty_calendar():
    """空交易日历 ⇒ 全部丢弃、不抛异常 ✓。"""
    signals = pd.DataFrame({"date": pd.to_datetime(["2024-01-02"]), "code": ["A"], "side": [1]})
    out = _bucket_signals(signals, _prep([], CODES))
    assert out["buys"] == {} and out["exits"] == {} and out["n_mapped"] == 0
