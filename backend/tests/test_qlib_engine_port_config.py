# -*- coding: utf-8 -*-
"""`_build_port_config` —— **回测数字的直接来源**（★ 2026-10-19 ✓ C 项第三步）。

## 为什么这块必须钉住

这个函数把"用户在界面上填的东西"翻译成 qlib 的 `exchange_kwargs` ✓ ——
**手续费/滑点/最低手续费/每手股数/涨跌停/成交价基准** 全在这里落位 ✓。
它一旦漏传或传错一个键，回测照样跑完 ✓、界面照样出图 ✓，只是**数字悄悄变了** ✗ ——
和 `_model_config` 的默认超参是同一类风险 ✓。

## 三个"有话说"的分支（都不许吞 ✗）

1. **`volume_threshold=None`** ⇒ 无限量理想成交 ✗（结构性偏乐观 ✓）⇒ 必须**留日志** ✓；
2. **`limit_threshold=None`** ⇒ 不设涨跌停 ✗ ⇒ 同样留日志 ✓（前端默认是**开**的 ✓）；
3. **`exclude_st=True` 但本机没有 `is_st` 标签** ⇒ **必须抛** ✓ 且文案要**可操作**
   （"先执行 tools/dump_states.py"✓）—— 静默忽略 = 用户以为剔除了 ST，其实没有 ✗。
"""
from types import SimpleNamespace

import pytest

from app.engine import limits as LIM
from app.engine import qlib_engine as QE


def _req(**kw):
    base = dict(
        volume_threshold=None, limit_threshold=0.1, deal_price="close",
        exclude_st=False, exclude_stock_gem=False, exclude_stock_kcb=False,
        open_cost=0.0013, close_cost=0.0013, min_cost=5.0, impact_cost=0.002,
        trade_unit=100, price_adjust="none",
        topk=50, topk_ratio=None, n_days_hold=10,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture()
def no_qlib(monkeypatch):
    """绕开两处外部依赖 ✓：基准兜底（要 qlib 数据 ✗）与标签文件探测（要 dump ✗）。"""
    monkeypatch.setattr(QE, "_fallback_benchmark", lambda bm, s, e, ins: bm)
    monkeypatch.setattr(QE, "_once_log", lambda *a, **k: None)

    def _fba(name):
        return False          # 默认：本机没有 dump 标签 ✓

    monkeypatch.setattr(LIM, "field_bin_available", _fba)
    return monkeypatch


def _kw(cfg):
    return cfg["backtest"]["exchange_kwargs"]["exchange"]["kwargs"]


def test_costs_and_contract_land_in_exchange_kwargs(no_qlib):
    """⭐ 手续费 / 滑点 / 最低手续费 / 每手股数 —— **回测数字的直接来源** ✓。"""
    cfg = QE._build_port_config(_req(), "SH000300", "2024-01-01", "2024-03-31")
    kw = _kw(cfg)
    assert kw["open_cost"] == 0.0013
    assert kw["close_cost"] == 0.0013
    assert kw["impact_cost"] == 0.002
    assert kw["min_cost"] == 5.0
    assert kw["trade_unit"] == 100
    assert kw["limit_threshold"] == 0.1
    assert kw["price_adjust"] == "none"
    assert kw["freq"] == "day" and kw["start_time"] == "2024-01-01"


def test_no_volume_cap_is_explicit_none(no_qlib):
    cfg = QE._build_port_config(_req(volume_threshold=None), "SH000300", "2024-01-01", "2024-02-01")
    assert _kw(cfg)["volume_threshold"] is None, "不限量必须是 None ✓（不是 0 ✗：0 = 一笔都不成交 ✗）"


def test_volume_cap_is_translated_to_qlib_expression(no_qlib):
    cfg = QE._build_port_config(_req(volume_threshold=0.25), "SH000300", "2024-01-01", "2024-02-01")
    assert _kw(cfg)["volume_threshold"] == {"all": ("current", "0.25 * $volume")}


def test_exclude_st_without_bin_raises_actionable_error(no_qlib):
    """⭐ 勾了"剔除ST"却没有 `is_st` 标签 ⇒ **必须抛** ✓，且要说清怎么补 ✓。"""
    with pytest.raises(ValueError) as ei:
        QE._build_port_config(_req(exclude_st=True), "SH000300", "2024-01-01", "2024-02-01")
    msg = str(ei.value)
    assert "is_st" in msg
    assert "dump_states.py" in msg, "报错要给出**可操作**的下一步 ✓（否则用户只能来问 ✓）"


def test_exclude_st_with_bin_subscribes_is_st(no_qlib, monkeypatch):
    monkeypatch.setattr(LIM, "field_bin_available", lambda name: name == "is_st")
    cfg = QE._build_port_config(_req(exclude_st=True), "SH000300", "2024-01-01", "2024-02-01")
    assert "$is_st" in _kw(cfg)["subscribe_fields"]
    assert _kw(cfg)["exclude_st"] is True


def test_avg_deal_price_switches_to_close_and_subscribes_prices(no_qlib):
    """`avg_co`/`avg_ohlc` 是**复合均价** ✓ ⇒ qlib 的 deal_price 只能给 `close` ✓，
    真实均价由 `avg_mode` + 注入列算 ✓（否则 qlib 初始化就报错 ✗）。"""
    for mode in ("avg_co", "avg_ohlc"):
        cfg = QE._build_port_config(_req(deal_price=mode), "SH000300", "2024-01-01", "2024-02-01")
        kw = _kw(cfg)
        assert kw["deal_price"] == "close"
        assert kw["avg_mode"] == mode
        for f in ("$open", "$high", "$low"):
            assert f in kw["subscribe_fields"], "%s 未订阅 ✗" % f


def test_plain_deal_price_has_no_avg_mode(no_qlib):
    for price in ("close", "open", "vwap"):
        cfg = QE._build_port_config(_req(deal_price=price), "SH000300", "2024-01-01", "2024-02-01")
        kw = _kw(cfg)
        assert kw["deal_price"] == price
        assert "avg_mode" not in kw, "%s 不该带 avg_mode ✗" % price


def test_factor_always_subscribed(no_qlib):
    """`$factor` 是复权还原的前提 ✓ —— 任何配置下都必须订阅 ✓（漏了 ⇒ 真实价算错 ✗）。"""
    for req in (_req(), _req(deal_price="vwap"), _req(deal_price="avg_co")):
        cfg = QE._build_port_config(req, "SH000300", "2024-01-01", "2024-02-01")
        assert "$factor" in _kw(cfg)["subscribe_fields"]


def test_limit_bins_subscribed_only_when_available(no_qlib, monkeypatch):
    # 没有 dump ⇒ 不订阅，但**不许抛** ✓（自动降级到"昨收×板块幅度"倒推 ✓）
    cfg = QE._build_port_config(_req(limit_threshold=0.1), "SH000300", "2024-01-01", "2024-02-01")
    assert "$limit_up" not in _kw(cfg)["subscribe_fields"]

    monkeypatch.setattr(LIM, "field_bin_available", lambda name: name in ("limit_up", "limit_down"))
    cfg2 = QE._build_port_config(_req(limit_threshold=0.1), "SH000300", "2024-01-01", "2024-02-01")
    sf = _kw(cfg2)["subscribe_fields"]
    assert "$limit_up" in sf and "$limit_down" in sf


def test_strategy_and_executor_structure(no_qlib):
    cfg = QE._build_port_config(_req(topk=50, topk_ratio=0.01, n_days_hold=7),
                                "SH000300", "2024-01-01", "2024-02-01", rebalance_base=42)
    assert cfg["executor"]["class"] == "SimulatorExecutor"
    st = cfg["strategy"]
    assert st["class"] == "PeriodicTopKStrategy"
    assert st["module_path"] == "app.engine.periodic_strategy"
    assert st["kwargs"]["topk"] == 50
    assert st["kwargs"]["topk_ratio"] == 0.01, "topk_ratio 非空 ⇒ 按比例选股 ✓（与 topk 互斥 ✓）"
    assert st["kwargs"]["n_days_hold"] == 7
    assert st["kwargs"]["rebalance_base"] == 42
    ex = cfg["backtest"]["exchange_kwargs"]["exchange"]
    assert ex["class"] == "BoardAwareExchange"
    assert ex["module_path"] == "app.engine.board_exchange"
