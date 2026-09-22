# -*- coding: utf-8 -*-
"""锁住「分层/IC 的收益对齐」—— 防 v1.20.46 那个 bug 回归 ✓。

背景（用户 2026-09-22 审计 + 真数据实锤 ✓）：`_get_pred_label` 里 `ret` 原为
**t 日当日收益** ✗（`$close/Ref($close,1)-1`），而分层是拿 **`score[t]`**（t 日收盘后才知道 ✓）
去分组 ⇒ **"用今天收盘才知道的分组，认领今天已经涨完的行情"** ✗ ⇒
失真方向 = **−corr(score[t], ret[t])**（反转倾向模型把**最弱组**做成最大赢家 ✗）。

⚠ 为什么现有单测锁不住 ✗：它们只在**已经构造好的 `ret` 列**上测 `_compute_layers` ✓，
   而 bug 在**表达式**里（`_get_pred_label` 取哪一天的收益 ✓）⇒ 两条都要锁 ✓：
     ① `test_ret_expr_is_forward_and_window_extended`：断言**表达式**是 t+1→t+2 ✓、末端多取 ✓；
     ② `test_layers_follow_forward_ret`：断言分层**跟的是"未来收益"的方向** ✓，
        并证明该用例**能区分**两个口径（换成同日收益方向必须反过来 ✓）。
"""
import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# ① 表达式层：`ret` 必须是 t+1→t+2，且取数末端要多留几天 ✓
# ---------------------------------------------------------------------------
def test_ret_expr_is_forward_and_window_extended(monkeypatch):
    import app.engine.analysis as A

    seen = {}
    # ⚠ 用**唯一日期** ⇔ 避开 `SHARED_CACHE`（key 含 区间+表达式 ⇒ 撞上就不会再调 loader ✗）
    _dates = pd.date_range("2031-01-06", periods=3, freq="D")
    _codes = ["SH600000", "SH600001"]

    class _Model:
        def predict(self, dataset, segment="test"):
            idx = pd.MultiIndex.from_product([_codes, _dates],
                                             names=["instrument", "datetime"])
            return pd.Series(np.arange(len(idx), dtype=float), index=idx, name="score")

    def fake_features(instruments, exprs, start_time=None, end_time=None):
        seen["exprs"] = list(exprs)
        seen["start"] = start_time
        seen["end"] = end_time
        idx = _Model().predict(None).index
        return pd.DataFrame({e: np.zeros(len(idx)) for e in exprs}, index=idx)

    class _FakeD:
        features = staticmethod(fake_features)

    # ⚠ `qlib.data.D` 在 `qlib.init()` 之前是 `Wrapper` ✗（连 `features` 属性都没有 ✓）
    #   ⇒ 必须**替换整个 `D`** ✓：`_get_pred_label` 里是 `from qlib.data import D` ✓，
    #     属于模块属性查找 ⇒ 替换模块属性即可生效 ✓（不需要真 init qlib ✓）。
    import qlib.data as _qd
    monkeypatch.setattr(_qd, "D", _FakeD, raising=False)

    out = A._get_pred_label(_Model(), object(), _codes, "test", label_horizon=20)
    assert out is not None
    exprs = seen.get("exprs") or []
    # ① ret 必须是「t+1 → t+2」✓（不是当日 ✗、也不是 t→t+1 ✗）
    assert "Ref($close, -2) / Ref($close, -1) - 1" in exprs, exprs
    assert "$close / Ref($close, 1) - 1" not in exprs, "禁止回退成『当日收益』✗"
    # ② label 与回测成交口径一致：信号 t ⇒ 从 t+1 收盘算起、持有 n 日 ✓
    assert "Ref($close, -21) / Ref($close, -1) - 1" in exprs, exprs
    # ③ 末端必须**多取**（否则最后 2 天 ret 恒 NaN ⇒ 分层少画尾巴 ✗）
    assert pd.Timestamp(seen["end"]) > pd.Timestamp("2031-01-08"), seen["end"]


# ---------------------------------------------------------------------------
# ② 行为层：分层方向必须跟"未来收益"走 ✓（并可区分"同日收益"口径 ✗）
# ---------------------------------------------------------------------------
def _mk(sign: float = +1.0):
    """构造 `score` 与 `ret` 的关系：`sign>0` 高分为赢家 ✓；`sign<0` 高分为输家（反转倾向 ✓）。

    · 分数 = 股票序号（0..N−1 ✓），每个交易日的横截面都相同 ✓；
    · 收益 = `sign × 0.01 × (序号 − 中点)` ✓ ⇒ **关于 0 对称** ⇒ 池内等权收益 = 0 ✓
      （这样 `universe` 那列可断言 ≈0 ✓，也说明"分组+配对"本身没引入偏移 ✓）。
    """
    n_dates, n_codes = 6, 20
    dates = pd.date_range("2022-01-03", periods=n_dates, freq="D")
    codes = ["SH%06d" % (600000 + i) for i in range(n_codes)]
    idx = pd.MultiIndex.from_product([codes, dates], names=["instrument", "datetime"])
    mid = (n_codes - 1) / 2.0
    score, ret = [], []
    for ci in range(n_codes):
        for _d in dates:
            score.append(float(ci))
            ret.append(sign * 0.01 * (ci - mid))
    return pd.DataFrame({"score": score, "ret": ret, "label": ret}, index=idx)


@pytest.mark.parametrize("sign", [+1.0, -1.0])
def test_layers_follow_forward_ret(sign):
    """`_compute_layers` 必须严格跟随**给定的 `ret` 列** ✓（无论方向 ✓）。

    ⇒ 配合 ① 的表达式测试，二者合起来就能把"取错交易日"这类 bug 钉住 ✓：
      表达式保证 `ret` 是"未来"的收益 ✓，本测试保证分层不把它配错 ✓。
    """
    from app.engine.analysis import _compute_layers

    df = _mk(sign)
    pts = _compute_layers(df, N=5, benchmark_ret=None, rebalance_period=1)
    assert pts is not None and len(pts) > 0
    last = pts[-1]
    if sign > 0:
        # 分数越高 ⇒ 收益越高 ⇒ 强组赢 ✓
        assert last["Group1"] > last["Group5"], last
    else:
        # 分数越高 ⇒ 收益越低 ⇒ 强组输 ✓（= "反转倾向模型"下同日口径会造出的假象 ✓）
        assert last["Group1"] < last["Group5"], last
    # 池内等权应≈0（两侧对称 ✓）⇒ 说明分组/配对本身没错 ✓
    assert abs(last["universe"]) < 1e-6, last
