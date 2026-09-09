# -*- coding: utf-8 -*-
"""panel_expr 面板级求值器单测：
1) 解析（函数式 + 中缀）与重建
2) 合成面板数值验证（rolling/Ref/算术/逻辑/常量广播/SR 掩码语义）
3) datareq：csi300 真实数据 panel vs qlib 逐位对拍（巨型公式 + label/base/tag 字段组）
"""
import numpy as np
import pandas as pd
import pytest

from app.factors.panel_expr import parse_expr, reconstruct
from app.factors.panel_expr import _roll, _ref


# ---------------------------------------------------------------------------
# 解析 / 重建
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr,expect", [
    ("$close", "$close"),
    ("10", "10"),
    ("Ref($close, -3)/Ref($close, -1) - 1", "Sub(Div(Ref($close,-3),Ref($close,-1)),1)"),
    ("Mean(SR($close),3)", "Mean(SR($close),3)"),
    ("Add(Max($high,10),Mul(2,Min($low,5)))", "Add(Max($high,10),Mul(2,Min($low,5)))"),
])
def test_parse_reconstruct(expr, expect):
    node = parse_expr(expr)
    assert reconstruct(node) == expect


# ---------------------------------------------------------------------------
# 合成面板数值验证（不依赖真实行情数据）
# ---------------------------------------------------------------------------

def test_valid_rolling_sr_semantics():
    """SR 滚动语义：停牌(NaN)日不占窗口格、结果回填全日历停牌日 NaN。"""
    # 构造：5 个有效日 + 1 个 NaN 停牌（模拟 qlib 2000-05-08 实证窗口）
    vals = pd.Series([1.0, 2.0, 3.0, np.nan, 4.0, 5.0],
                     index=pd.MultiIndex.from_arrays(
                         [["A"] * 6, pd.date_range("2025-01-01", periods=6, freq="B")],
                         names=["instrument", "datetime"]))
    out = _roll(vals, "mean", 3, sr=True)
    arr = out.to_numpy()
    # 停牌日(NaN)不占窗口：第4日 NaN，第5日窗口=[4,3,2]→3，第6日=[5,4,3]→4
    assert np.isnan(arr[3])
    assert arr[4] == pytest.approx(3.0)
    assert arr[5] == pytest.approx(4.0)
    # 前两个有效日 min_periods=1：窗口可用
    assert arr[0] == pytest.approx(1.0)


def test_valid_ref_sr_semantics():
    """SR Ref：剔停牌行再 shift，复牌后能取到停牌前有效值。"""
    vals = pd.Series([10.0, 11.0, 12.0, np.nan, 13.0, 14.0],
                     index=pd.MultiIndex.from_arrays(
                         [["A"] * 6, pd.date_range("2025-01-01", periods=6, freq="B")],
                         names=["instrument", "datetime"]))
    out = _ref(vals, 1, sr=True)  # 前一日有效值
    arr = out.to_numpy()
    assert arr[0] != arr[0]  # NaN（首个无前值）
    assert arr[1] == pytest.approx(10.0)
    assert np.isnan(arr[3])  # 停牌日无 Ref 结果
    assert arr[4] == pytest.approx(12.0)  # 复牌日 Ref = 停牌前最后有效值
    assert arr[5] == pytest.approx(13.0)


@pytest.mark.parametrize("expr,expect_sub", [
    ("Add(1,2)", None),  # 解析 smoke，实际数值由 datareq 覆盖
])
def test_parse_smoke(expr, expect_sub):
    node = parse_expr(expr)
    assert node is not None


# ---------------------------------------------------------------------------
# datareq：真实数据 panel vs qlib 对拍
# ---------------------------------------------------------------------------

@pytest.mark.datareq
def test_panel_matches_qlib_cwh():
    """csi300 上完整 CWH 公式 + label/base 字段组：panel 与 qlib 逐位对齐。

    判定：全部样本 |panel-qlib| <= 1e-5（float32 读入 vs float64 运算的精度余量）；
    允许 label 尾部未来 Ref 用扩展 end 加载后截断。
    """
    import json
    from qlib.config import C
    from qlib.data import D

    from app.engine.qlib_engine import _ensure_qlib_init
    from app.engine.utils import _default_qlib_uri

    _ensure_qlib_init(_default_qlib_uri())
    C["joblib_backend"] = "loky"
    C["kernels"] = 8

    j = json.load(open(r"D:\quant\qlib_code\backend\workdir\custom_formulas.json", encoding="utf-8"))

    def find(o, name):
        if isinstance(o, dict):
            if name in str(o.get("name") or o.get("id") or ""):
                return o.get("expression")
            for v in o.values():
                r = find(v, name)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v, name)
                if r:
                    return r

    cwh = find(j, "CWH_BREAK_WAIT20_F1")
    assert cwh, "公式库无 CWH_BREAK_WAIT20_F1"

    insts = [str(i).upper()
             for i in D.list_instruments(D.instruments("csi300"), start_time="2025-01-01", as_list=True)]
    START, END = "2025-01-01", "2026-08-31"
    fields = [
        (cwh, "F0"),
        ("Ref($close, -3)/Ref($close, -1) - 1", "LABEL"),
        ("$close/$factor", "CLOSE"),
        ("$change", "CHANGE"),
    ]
    q = D.features(insts, [f for f, _ in fields], start_time=START, end_time=END)
    ext_end = str((pd.Timestamp(END) + pd.Timedelta(days=15)).date())
    from app.factors.panel_expr import panel_features
    p = panel_features(insts, fields, START, ext_end)
    p = p[p.index.get_level_values("datetime") <= pd.Timestamp(END)]
    assert len(p) == len(q), (len(p), len(q))

    full = p.index.union(q.index)
    # 判据：qlib 数据 float32、panel 读入转 float64，除法/减法会放大浮点尾差
    # （如 $close/$factor≈10 → 绝对差可达 ~6e-5）。用相对容差 rtol=1e-4 判定
    # "数值一致"（语义 bug 如差一天/方向反通常产生 O(1) 级差异，必然被抓出）。
    worst = 0.0
    for i, (_, name) in enumerate(fields):
        a = p[name].reindex(full).to_numpy(dtype=np.float64)
        b = q.iloc[:, i].reindex(full).to_numpy(dtype=np.float64)
        bothok = ~(np.isnan(a) | np.isnan(b))
        if not bothok.any():
            continue
        ok = np.isclose(a[bothok], b[bothok], rtol=1e-4, atol=1e-6, equal_nan=True)
        n_bad = int((~ok).sum())
        diff = np.abs(a[bothok] - b[bothok])
        worst = max(worst, float(diff.max()))
        assert n_bad == 0, (
            f"列 {name}: {n_bad}/{int(bothok.sum())} 差异超出 rtol=1e-4/atol=1e-6"
            f"（max {diff.max():.2e}）")
    assert worst < 1e-4


@pytest.mark.datareq
def test_panel_matches_qlib_sr_continuous():
    """SR 包裹的连续因子（面板预热 + SR active 压缩语义）panel vs qlib 逐位对齐。

    用单因子测试同款字段组构造（_sr_wrap_expr + adjust_expr + label/base/tag），
    覆盖：面板读盘前移预热（Mean/Ref 需 start 前历史）、SR 停牌行压缩、多字段
    union 行集、未来 Ref label。容差 rtol=1e-4（qlib float32 vs panel float64）。
    """
    from qlib.config import C as _C
    from qlib.data import D as _D

    from app.engine.qlib_engine import _ensure_qlib_init
    from app.engine.utils import _default_qlib_uri
    from app.engine.adjust import adjust_expr, normalize_mode
    from app.engine.feature_cache import _sr_wrap_expr
    from app.factors.panel_expr import panel_features

    _ensure_qlib_init(_default_qlib_uri())
    _C["joblib_backend"] = "loky"
    _C["kernels"] = 8

    insts = [str(i).upper()
             for i in _D.list_instruments(_D.instruments("csi300"), start_time="2025-01-01", as_list=True)]
    START, END = "2025-01-01", "2026-08-31"
    cal = pd.to_datetime(_D.calendar())
    pos = int((cal <= pd.Timestamp(END)).sum())
    load_end = str(cal[min(pos + 5, len(cal) - 1)].date())

    pa = normalize_mode("none")
    exprs = [
        "Div(Sub($close,Mean($close,20)),Mean($close,20))",  # 连续动量
        "Std($close,20)",  # 连续波动
        "If(Gt(Sub(Div($close,Ref($close,20)),1),0.1),1,0)",  # 稀疏 0/1
    ]
    fields = [(_sr_wrap_expr(adjust_expr(e, pa, round_prices=True)), f"F{i}")
              for i, e in enumerate(exprs)]
    fields += [("Ref($close, -6)/Ref($close, -1) - 1", "LABEL_5")]
    fields += [("$close/$factor", "CLOSE"), ("$change", "CHANGE")]
    names = [n for _, n in fields]

    q = _D.features(insts, [e for e, _ in fields], start_time=START, end_time=load_end)
    p = panel_features(insts, fields, START, load_end)
    p = p[p.index.get_level_values("datetime") <= pd.Timestamp(END)]
    q = q[q.index.get_level_values("datetime") <= pd.Timestamp(END)]
    assert len(p) == len(q), (len(p), len(q))
    full = p.index.union(q.index)
    worst = 0.0
    for i, name in enumerate(names):
        a = p[name].reindex(full).to_numpy(dtype=np.float64)
        b = q.iloc[:, i].reindex(full).to_numpy(dtype=np.float64)
        both = ~(np.isnan(a) | np.isnan(b))
        if not both.any():
            continue
        ok = np.isclose(a[both], b[both], rtol=1e-4, atol=1e-6, equal_nan=True)
        n_bad = int((~ok).sum())
        diff = np.abs(a[both] - b[both])
        worst = max(worst, float(diff.max()))
        assert n_bad == 0, (f"列 {name}: {n_bad}/{int(both.sum())} 差异超 rtol=1e-4"
                            f"（max {diff.max():.2e}）")
    assert worst < 1e-4


@pytest.mark.datareq
def test_panel_parallel_matches_single():
    """并行面板（按股票切块多进程）与单进程面板逐位一致 + 行集对齐 qlib。

    用 >1000 只（csi300 + 追加全 A 前段补足 1100）触发并行分支；数值上并行版与
    单进程版必须完全一致（同字段同读盘，仅求值进程不同），行数与 qlib 一致。
    """
    from qlib.config import C as _C
    from qlib.data import D as _D

    from app.engine.qlib_engine import _ensure_qlib_init
    from app.engine.utils import _default_qlib_uri
    from app.engine.adjust import adjust_expr, normalize_mode
    from app.engine.feature_cache import _sr_wrap_expr
    from app.factors.panel_expr import panel_features, panel_features_parallel

    _ensure_qlib_init(_default_qlib_uri())
    _C["joblib_backend"] = "loky"
    _C["kernels"] = 8

    base = [str(i).upper()
            for i in _D.list_instruments(_D.instruments("csi300"), start_time="2025-01-01", as_list=True)]
    extra = [str(i).upper()
             for i in _D.list_instruments(_D.instruments("all"), start_time="2025-01-01", as_list=True)
             if str(i).upper() not in set(base)][:800]
    insts = (base + extra)[:1100]
    assert len(insts) > 1000
    START, END = "2025-01-01", "2026-08-31"

    pa = normalize_mode("none")
    exprs = [
        "Div(Sub($close,Mean($close,20)),Mean($close,20))",
        "If(Gt(Sub(Div($close,Ref($close,20)),1),0.1),1,0)",
    ]
    fields = [(_sr_wrap_expr(adjust_expr(e, pa, round_prices=True)), f"F{i}")
              for i, e in enumerate(exprs)]
    fields += [("$close/$factor", "CLOSE"), ("$change", "CHANGE")]
    names = [n for _, n in fields]

    single = panel_features(insts, fields, START, END)
    par = panel_features_parallel(insts, fields, START, END, n_jobs=4)
    assert len(par) == len(single), (len(par), len(single))
    full = par.index.union(single.index)
    for name in names:
        a = par[name].reindex(full).to_numpy(dtype=np.float64)
        b = single[name].reindex(full).to_numpy(dtype=np.float64)
        both = ~(np.isnan(a) | np.isnan(b))
        d = np.abs(a[both] - b[both])
        assert int((d > 0).sum()) == 0, f"列 {name}: 并行与单进程不一致 {int((d > 0).sum())} 处"

    q = _D.features(insts, [e for e, _ in fields], start_time=START, end_time=END)
    q.columns = names
    assert len(par) == len(q), (len(par), len(q))


@pytest.mark.datareq
def test_panel_matches_qlib_ema():
    """EMA / EMA_TDX（含裸 EMA 精确冷启动、SR+EMA、MACD 组合）panel vs qlib 逐位对齐。

    回归锚点：EMA 是序列起点敏感的指数递归，qlib 逐字段只前移 N-1 天冷启动；
    面板曾用统一 warm（max+30）让 EMA 从过早点起算 → 前段偏差。修复 = 面板按
    字段精确扩展量分组建 evaluator（含 SR 走 250 主导、裸 EMA 精确 N-1）。
    """
    from qlib.config import C as _C
    from qlib.data import D as _D

    from app.engine.qlib_engine import _ensure_qlib_init
    from app.engine.utils import _default_qlib_uri
    from app.factors.panel_expr import panel_features

    _ensure_qlib_init(_default_qlib_uri())
    _C["joblib_backend"] = "loky"
    _C["kernels"] = 8

    insts = [str(i).upper()
             for i in _D.list_instruments(_D.instruments("csi300"), start_time="2025-01-01", as_list=True)]
    START, END = "2025-01-01", "2026-08-31"
    exprs = [
        "EMA($close, 5)",                       # 裸 EMA：精确 N-1 冷启动
        "EMA($close, 12)",
        "Sub(EMA($close, 12), EMA($close, 26))",  # MACD 快慢线差（不同 N 各自扩展）
        "EMA(SR($close), 5)",                    # SR 场景（250 主导）
        "Gt(EMA($close, 20), EMA($close, 60))",  # 均线多头（Gt 布尔）
    ]
    fields = [(e, f"F{i}") for i, e in enumerate(exprs)]
    names = [n for _, n in fields]
    p = panel_features(insts, fields, START, END)
    q = _D.features(insts, [e for e, _ in fields], start_time=START, end_time=END)
    q.columns = names
    assert len(p) == len(q), (len(p), len(q))
    full = p.index.union(q.index)
    worst = 0.0
    for name in names:
        a = p[name].reindex(full).to_numpy(dtype=np.float64)
        b = q[name].reindex(full).to_numpy(dtype=np.float64)
        both = ~(np.isnan(a) | np.isnan(b))
        if not both.any():
            continue
        ok = np.isclose(a[both], b[both], rtol=1e-4, atol=1e-6, equal_nan=True)
        n_bad = int((~ok).sum())
        diff = np.abs(a[both] - b[both])
        worst = max(worst, float(diff.max()))
        assert n_bad == 0, (f"列 {name}: {n_bad}/{int(both.sum())} 差异超 rtol=1e-4"
                            f"（max {diff.max():.2e}）")
    assert worst < 1e-4


@pytest.mark.datareq
def test_panel_matches_qlib_ema_nested():
    """嵌套固定窗口的 EMA（趋势顶底类）panel vs qlib 逐位对齐。

    回归锚点：EMA 输入链内嵌 Max/Min/Ref 等固定窗口算子时，qlib 的 extended
    window 沿树递归累加（如 EMA(Max($high,34),4) = 33+3=36 天），面板 read_start
    若只按最外层 EMA N-1 前移则 EMA 起点晚 33 天 → 整条序列永久偏移（实测同一日
    值差 0.37，0/1 阈值比较翻面，趋势顶底离开底部全 A ~447 处）。修复 =
    _expr_ext_days 递归整棵树（_tree_ext_days）。
    """
    from qlib.config import C as _C
    from qlib.data import D as _D

    from app.engine.qlib_engine import _ensure_qlib_init
    from app.engine.utils import _default_qlib_uri
    from app.engine.adjust import adjust_expr, normalize_mode
    from app.engine.feature_cache import _sr_wrap_expr
    from app.factors.panel_expr import panel_features

    _ensure_qlib_init(_default_qlib_uri())
    _C["joblib_backend"] = "loky"
    _C["kernels"] = 8

    insts = [str(i).upper()
             for i in _D.list_instruments(_D.instruments("csi300"), start_time="2025-01-01", as_list=True)]
    extra = [str(i).upper()
             for i in _D.list_instruments(_D.instruments("all"), start_time="2025-01-01", as_list=True)
             if str(i).upper() not in set(insts)][:100]
    insts = insts + extra
    START, END = "2025-01-01", "2026-08-31"

    pa = normalize_mode("none")
    exprs = [
        # 嵌套 Max/Min 固定窗口 + EMA（趋势顶底离开底部核心子式）
        "EMA(Add(Div(Mul(-100,Sub(Max($high,34),$close)),"
        "Sub(Max($high,34),Min($low,34))),100),4)",
        # 嵌套 Ref + EMA
        "EMA(Div($close,Ref($close,20)),5)",
        # SR 包裹叶子 + 嵌套窗口 EMA（真实 suspend_remove 链路形态）
        "And(Eq(Ref(Sub(EMA(SR($close),4),SR($close)),1),0),"
        "Gt(EMA(Add(Div(Mul(-100,Sub(Max(SR($high),34),SR($close))),"
        "Sub(Max(SR($high),34),Min(SR($low),34))),100),4),0))",
    ]
    wrapped = [_sr_wrap_expr(adjust_expr(e, pa, round_prices=True))
               for e in exprs[:2]]
    # 第三个已手动含 SR（真实 suspend_remove 链路 = 叶子被 _sr_wrap 包裹的形态），
    # 只做 adjust（_sr_wrap 会对已含 SR 的字段重复包裹，属另一场景）
    wrapped.append(adjust_expr(exprs[2], pa, round_prices=True))
    fields = [(w, f"F{i}") for i, w in enumerate(wrapped)]
    names = [n for _, n in fields]

    p = panel_features(insts, fields, START, END)
    q = _D.features(insts, [e for e, _ in fields], start_time=START, end_time=END)
    q.columns = names
    full = p.index.union(q.index)
    worst = 0.0
    for name in names:
        a = p[name].reindex(full).to_numpy(dtype=np.float64)
        b = q[name].reindex(full).to_numpy(dtype=np.float64)
        both = ~(np.isnan(a) | np.isnan(b))
        if not both.any():
            continue
        ok = np.isclose(a[both], b[both], rtol=1e-4, atol=1e-6, equal_nan=True)
        n_bad = int((~ok).sum())
        diff = np.abs(a[both] - b[both])
        worst = max(worst, float(diff.max()))
        assert n_bad == 0, (f"列 {name}: {n_bad}/{int(both.sum())} 差异超 rtol=1e-4"
                            f"（max {diff.max():.2e}）")
    assert worst < 1e-4
