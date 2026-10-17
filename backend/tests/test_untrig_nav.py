# -*- coding: utf-8 -*-
"""「未触发组等权」参照线（★ 2026-10-14 ✓，用户："加一条未触发组等权参照线" ✓）。

**为什么锁**：这条线是**事件研究的真实基准**（超额曲线用的就是它 ✓），而旧图上只有「沪深300」
灰虚线 ⇒ 实测把灰线当成了"全 A 等权" ✗（沪深300 0.957 vs 池内等权 1.4125 ✓ 差 48% ⇒ 结论看反 ✓）。
口径必须与超额曲线一致 ✓：**信号日 T ⇒ 从 T+1 起把该票从"未触发组"里剔除** ✓。

这里用**合成价格**验算法本身（不依赖数据 ✓）：3 只票、日收益 +10% / 0% / −10% ✓。
"""
import numpy as np
import pandas as pd
import pytest

from app.routers import factors as F

DATES = pd.to_datetime(["2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07"])


def _fake_wide():
    """A 每日 +10%、B 持平、C 每日 −10%（4 个交易日 ✓）。"""
    return pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1],
                         "B": [100.0, 100.0, 100.0, 100.0],
                         "C": [100.0, 90.0, 81.0, 72.9]}, index=DATES)


@pytest.fixture(autouse=True)
def _clear_cache():
    F._UNTRIG_CACHE.clear()
    yield
    F._UNTRIG_CACHE.clear()


def _patch(monkeypatch, wide):
    # ⚠ `_resolve_instruments` **定义在 `single_test`** ✓（`event_study` 里的那份是函数内 import ✗
    #   ⇒ 不是模块属性 ✓，patch 它没用 ✓ —— 第一版就踩了 ✓）
    monkeypatch.setattr("app.factors.single_test._resolve_instruments",
                        lambda universe, start: ["A", "B", "C"])
    monkeypatch.setattr("app.factors.event_study.load_px_wide",
                        lambda *a, **k: wide)


def test_excludes_triggered_stock_from_next_day(monkeypatch):
    """T 日触发 A ⇒ T+1 的收益里**不含 A**（于是 T+1 = (0% + −10%)/2 = −5% ✓）。"""
    _patch(monkeypatch, _fake_wide())
    state = {"_req": {"universe": "all", "price_adjust": "backward",
                      "price_round": True, "fill": "t1_open"}}
    ev = pd.DataFrame({"dt": ["2022-01-04"], "code": ["A"]})
    out = F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07")
    assert [d for d, _ in out] == ["2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07"]
    nav = [v for _, v in out]
    assert nav[0] == pytest.approx(1.0)
    assert nav[1] == pytest.approx(0.95)         # A 被剔除 ⇒ (0% − 10%)/2 ✓
    assert nav[2] == pytest.approx(0.95)         # 三只都在 ⇒ (10% + 0% − 10%)/3 = 0% ✓
    assert nav[3] == pytest.approx(0.95)


def test_without_trigger_all_three_equal_weight(monkeypatch):
    """没有触发股 ⇒ 每日都按三只等权 ✓（第 1 天 = (10+0−10)/3 = 0% ✓）。"""
    _patch(monkeypatch, _fake_wide())
    state = {"_req": {"universe": "all", "price_adjust": "backward", "fill": "t1_close"}}
    ev = pd.DataFrame({"dt": [], "code": []})
    out = F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07")
    nav = [v for _, v in out]
    assert nav[1] == pytest.approx(1.0)
    assert nav == pytest.approx([1.0, 1.0, 1.0, 1.0])


def test_cache_hit_avoids_second_load(monkeypatch):
    """同一 (池/起止/口径/事件数) ⇒ 命中缓存 ✓（否则每次改 k 都要重载全池 ✗）。"""
    calls = {"n": 0}

    def _load(*a, **k):
        calls["n"] += 1
        return _fake_wide()

    monkeypatch.setattr("app.factors.single_test._resolve_instruments",
                        lambda universe, start: ["A", "B", "C"])
    monkeypatch.setattr("app.factors.event_study.load_px_wide", _load)
    state = {"_req": {"universe": "all", "fill": "t1_open"}}
    ev = pd.DataFrame({"dt": ["2022-01-04"], "code": ["A"]})
    F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07")
    F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07")
    assert calls["n"] == 1


def _patch2(monkeypatch, wide, codes=("A", "B")):
    monkeypatch.setattr("app.factors.single_test._resolve_instruments",
                        lambda universe, start: list(codes))
    monkeypatch.setattr("app.factors.event_study.load_px_wide", lambda *a, **k: wide)


def test_same_hold_keeps_position_through_trigger(monkeypatch):
    """★★ v2.9.1（用户 2026-10-17 ✓）：**同持有期** —— T 日买入的仓位要**持满 hold 天**，
    中途该票触发也**不提前踢出** ✓（这正是与"只持 1 天"的本质区别 ✓）。

    构造（就是为了放大那个差异 ✓）：A 在 **D1→D2 大涨 +50%**，而 A 恰好在 **D1 触发** ✓。
      · **旧口径（hold=1）**：A 触发 ⇒ 把 A 在 D1→D2 那一格**剔掉** ✗ ⇒ 正好错过这 +50% ✓
        （实测净值 1.00 —— 这就是"参照线天生吃亏"的机制 ✓）；
      · **同持有期（hold=2）**：D0 建仓的 A 仍在持有期内 ✓ ⇒ 吃到 +50%
        ⇒ D2→D3 那天 (A +50% + B 0%)/2 = +25% ⇒ 净值 **1.25** ✓。
    """
    wide = pd.DataFrame({"A": [100.0, 100.0, 150.0, 150.0],   # D1→D2 大涨 +50% ✓
                         "B": [100.0, 100.0, 100.0, 100.0]}, index=DATES)
    _patch2(monkeypatch, wide)
    state = {"_req": {"universe": "all", "fill": "t1_open"}}
    ev = pd.DataFrame({"dt": ["2022-01-05"], "code": ["A"]})   # D1 触发 A ✓

    nav2 = [v for _, v in F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07", hold=2)]
    assert nav2[0] == pytest.approx(1.0)
    assert nav2[1] == pytest.approx(1.0)          # D0 建的仓从 D1 起才计收益 ✓
    assert nav2[3] == pytest.approx(1.25)         # ★ 吃到触发次日的 +50%（旧口径会错过 ✓）

    F._UNTRIG_CACHE.clear()
    nav1 = [v for _, v in F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07", hold=1)]
    assert nav1[3] == pytest.approx(1.0)          # 旧口径：触发次日那一格被剔 ⇒ 错过全部涨幅 ✓


def test_hold_default_is_legacy_one_day(monkeypatch):
    """不传 `hold` ⇒ **旧口径逐位不变** ✓（老调用/老测试不受影响 ✓）。"""
    _patch(monkeypatch, _fake_wide())
    state = {"_req": {"universe": "all", "price_adjust": "backward", "fill": "t1_open"}}
    ev = pd.DataFrame({"dt": ["2022-01-04"], "code": ["A"]})
    default = [v for _, v in F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07")]
    F._UNTRIG_CACHE.clear()
    explicit = [v for _, v in F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07", hold=1)]
    assert default == pytest.approx(explicit)
    assert default == pytest.approx([1.0, 0.95, 0.95, 0.95])


def test_cache_key_includes_hold(monkeypatch):
    """★ 缓存键**必须含 hold** ✗ —— 否则 60 天口径会命中 1 天口径的旧结果 ✓（静默错 ✗）。"""
    calls = {"n": 0}

    def _load(*a, **k):
        calls["n"] += 1
        return _fake_wide()

    monkeypatch.setattr("app.factors.single_test._resolve_instruments",
                        lambda universe, start: ["A", "B", "C"])
    monkeypatch.setattr("app.factors.event_study.load_px_wide", _load)
    state = {"_req": {"universe": "all", "fill": "t1_open"}}
    ev = pd.DataFrame({"dt": ["2022-01-04"], "code": ["A"]})
    F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07", hold=1)
    F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07", hold=2)
    assert calls["n"] == 2
    F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07", hold=2)
    assert calls["n"] == 2                       # 同 hold ⇒ 命中 ✓


def test_nonfinite_prices_are_ignored(monkeypatch):
    """停牌/脏价（NaN、0、inf）不进均值 ✓（与超额曲线同一清洗 ✓）。"""
    wide = _fake_wide()
    wide.loc[DATES[1], "B"] = np.nan
    wide.loc[DATES[2], "C"] = 0.0
    _patch(monkeypatch, wide)
    state = {"_req": {"universe": "all", "fill": "t1_open"}}
    ev = pd.DataFrame({"dt": [], "code": []})
    out = F._untrig_nav_series(state, ev, "2022-01-04", "2022-01-07")
    nav = [v for _, v in out]
    assert np.isfinite(nav).all()
    # 第 1 天：B 停牌 ⇒ 只剩 A(+10%) 与 C(−10%) ⇒ 0% ✓
    assert nav[1] == pytest.approx(1.0)
