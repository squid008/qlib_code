# -*- coding: utf-8 -*-
"""COST / WINNER 接入公式引擎的接线测试（v1.19.83）。

分工：
  · 算法正确性在 `tests/test_chip_dist.py`（合成数据、8 项）；
  · 这里测**接线**：codegen 名字映射 + 面板派生字段的数值与内核**逐位一致** + 真实数据体检（datareq）。

为什么要"派生字段"这条路（而不是普通算子）：算子按逐股调用 ⇒ 筹码逐股递推全 A 要 4~8 分钟/分位 ✗；
派生字段让面板级求值器一次拿全池、按「日期 × 股票」矩阵向量化 ⇒ 几秒 ✓（详见 panel_expr._chip_field）。
"""
import numpy as np
import pytest

from app.factors.parser.codegen import CodeGen, CodeGenError
from app.factors.parser.parser import parse_formula
from app.factors.parser.semantic import inline_variables


def _gen(body: str) -> str:
    """把**输出体**（如 `COST(95)`）补成合法语句 `OUT:…;`，再走 解析 → 内联 → codegen。

    ⚠ 不碰 qlib：`translate_formula` 会走 qlib 表达式装载，单测里要避免依赖 `qlib.init`。
    """
    return CodeGen().gen(inline_variables(parse_formula("OUT:%s;" % body)))


# ---------------------------------------------------------------------------
# 1) 名字映射（不取数）
# ---------------------------------------------------------------------------
def test_cost_maps_to_derived_field():
    assert _gen("COST(95)") == "$chip_cost_95"
    assert _gen("COST(5)") == "$chip_cost_5"


def test_winner_maps_by_price_kind():
    for src, exp in (("WINNER(C)", "$chip_win_close"),
                     ("WINNER(CLOSE)", "$chip_win_close"),
                     ("WINNER(H)", "$chip_win_high"),
                     ("WINNER(L)", "$chip_win_low")):
        assert _gen(src) == exp


def test_cost_winner_inside_formula():
    """用户那种用法：四条成本线拼黏合度（MAX/MIN 都在，无需新算子）。"""
    expr = ("MAX(MAX(COST(95),COST(75)),MAX(COST(30),COST(5)))"
            "/MIN(MIN(COST(95),COST(75)),MIN(COST(30),COST(5)))")
    out = _gen(expr)
    assert "$chip_cost_95" in out and "$chip_cost_5" in out
    assert "Greater" in out and "Less" in out          # MAX/MIN → Greater/Less（qlib 命名）


def test_bad_args_raise_clear_errors():
    with pytest.raises(CodeGenError):
        _gen("COST(101)")                              # 分位越界
    with pytest.raises(CodeGenError):
        _gen("COST(C)")                                # 分位必须是常量
    with pytest.raises(CodeGenError):
        _gen("WINNER(O)")                              # 只支持 C/H/L


# ---------------------------------------------------------------------------
# 2) 面板派生字段 == 内核直算（合成面板，不取数）
# ---------------------------------------------------------------------------
def _synth_panel(n_day=90, n_stk=3, seed=5):
    """构造与真实面板同构的 input：index 为 (instrument, datetime) 的 MultiIndex。"""
    import pandas as pd
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_day)
    insts = ["SH60000%d" % i for i in range(n_stk)]
    mi = pd.MultiIndex.from_product([insts, dates], names=["instrument", "datetime"])
    close = 10.0 * np.exp(np.cumsum(rng.normal(0, 0.012, size=(n_day, n_stk)), axis=0))
    high = close * 1.015
    low = close * 0.985
    turn = np.clip(rng.normal(3.0, 0.8, size=(n_day, n_stk)), 0.05, 30.0)   # 百分数口径
    # 换手率必须由 volume × 真实价 / market_cap 反推（数据里没有 turn 字段）
    vol = np.clip(rng.normal(2e7, 5e6, size=(n_day, n_stk)), 1e5, None)
    fac = np.ones_like(close)
    mc = vol * close / turn                                                  # 反推值 ≡ turn
    turn_eff = vol * (close / fac) / mc                                      # 与面板内部同式 ⇒ 逐位可比
    flat = lambda a: pd.Series(a.reshape(-1, order="F"), index=mi)           # noqa: E731
    # ⚠ 缓存键 = 去掉 `$` 的字段名（与 `PanelEvaluator.field` 一致）
    return ({"close": flat(close), "high": flat(high), "low": flat(low),
             "volume": flat(vol), "factor": flat(fac), "market_cap": flat(mc)},
            (close, high, low, turn_eff))


def test_derived_field_matches_kernel():
    """面板派生字段与直接调内核逐位一致（接线不能改数）——这是本文件最关键的一条。"""
    from app.factors.chip_dist import chip_run
    from app.factors.panel_expr import PanelEvaluator

    fields, mats = _synth_panel()
    close, high, low, turn = mats
    ev = object.__new__(PanelEvaluator)                 # 只借用字段解析，不读盘
    ev._field_cache = dict(fields)
    ev.read_start = "2023-01-02"

    for q in (5, 30, 75, 95):
        got = ev.field("$chip_cost_%g" % q).to_numpy(dtype=float)
        exp = chip_run(close, high, low, turn, qs=(q,))["cost_%g" % q].reshape(-1, order="F")
        both = np.isfinite(got) & np.isfinite(exp)
        assert both.any()
        assert np.array_equal(got[both], exp[both]), "COST(%g) 必须逐位一致" % q

    got_w = ev.field("$chip_win_close").to_numpy(dtype=float)
    exp_w = chip_run(close, high, low, turn, prices=close)["winner"].reshape(-1, order="F")
    both = np.isfinite(got_w) & np.isfinite(exp_w)
    assert both.any() and np.array_equal(got_w[both], exp_w[both]), "WINNER(C) 必须逐位一致"


def test_field_cache_reuses_result():
    """同名字段必须命中缓存（公式里写多次不该重复算）。"""
    from app.factors.panel_expr import PanelEvaluator

    fields, _ = _synth_panel()
    ev = object.__new__(PanelEvaluator)
    ev._field_cache = dict(fields)
    a = ev.field("$chip_cost_95")
    b = ev.field("$chip_cost_95")
    assert a is b, "第二次取同名字段应直接命中 _field_cache"


# ---------------------------------------------------------------------------
# 3) datareq：真实行情上跑一次（无数据自动 skip）
# ---------------------------------------------------------------------------
@pytest.mark.datareq
def test_chip_fields_on_real_data():
    """真实行情上跑：换手率反推量级合理、COST 单调、WINNER ∈ [0,1]、与内核逐位一致、耗时。"""
    import time

    from app.factors.chip_dist import chip_run
    from app.factors.panel_expr import _CHIP_BASE_FIELDS, PanelEvaluator
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()          # ⚠ 必须走统一入口（裸调 qlib.init 会清空 custom_ops）
    insts = ["SH600000", "SH600519", "SZ000001"]
    ev = PanelEvaluator(insts, "2023-01-02", "2023-12-31", union_fields=_CHIP_BASE_FIELDS)
    t0 = time.time()
    c5 = ev.field("$chip_cost_5")
    c95 = ev.field("$chip_cost_95")
    win = ev.field("$chip_win_close")
    el = time.time() - t0
    assert np.isfinite(c5.to_numpy(dtype=float)).any(), "真实数据上应算出 COST 值"
    assert (c5.dropna() <= c95.reindex(c5.dropna().index) + 1e-9).all(), "COST(5) ≤ COST(95)"
    w = win.dropna()
    assert w.between(0.0, 1.0).all(), "WINNER 必须是 [0,1] 占比"
    # 真实数据上换手率反推的量级体检（日换手率中位数应在 0.1%~15% 之间）
    C = ev.field("$close").unstack(level=0)
    H = ev.field("$high").unstack(level=0)
    L = ev.field("$low").unstack(level=0)
    V = ev.field("$volume").unstack(level=0)
    MC = ev.field("$market_cap").unstack(level=0)
    F = ev.field("$factor").unstack(level=0)
    AMT = ev.field("$amount").unstack(level=0)
    # 与 `_chip_field` 同一套单位自校准：均价/真实价 ≈1 ⇒ volume 是"股"；≈0.01 ⇒ 是"手"(×100)
    _ratio = float(np.nanmedian((AMT / V).to_numpy() / (C / F).to_numpy()))
    scale = 100.0 if (np.isfinite(_ratio) and _ratio < 0.1) else 1.0
    T = (V * scale * (C / F) / MC).where(MC > 0)
    med = float(np.nanmedian(T.to_numpy(dtype=float)))
    assert 0.001 < med < 0.15, f"换手率反推量级异常：中位数 {med:.4f}（应落在 0.001 ~ 0.15）"
    # 3 只 × ~240 日：面板级向量化（非逐股循环）
    assert el < 30.0, "3 只一年应远快于 30s（实测 %.1fs）—— 若超了说明退化成逐股循环" % el
    # 与内核直算逐位一致（同一批输入矩阵）
    ref = chip_run(C.to_numpy(float), H.to_numpy(float), L.to_numpy(float),
                   T.to_numpy(float), qs=(95,))["cost_95"].reshape(-1, order="F")
    got = c95.to_numpy(dtype=float)
    both = np.isfinite(ref) & np.isfinite(got)
    assert both.any() and np.array_equal(got[both], ref[both]), "真实数据上也要逐位一致"
