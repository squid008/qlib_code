# -*- coding: utf-8 -*-
"""`app/factors/benchmark_curves.py` 纯函数单测（合成序列手算对拍，不碰数据）。

为什么要锁（**两代用户报障**都出在这个函数的缺失语义上）：
  · 2026-09-12「中证1000 的基准曲线只到 2025-04」= 面板里一个 NaN（`SH000852` 2025-04-16）
    被当成"整条线断掉" ⇒ 现在**中间空洞只断当期**；
  · 2026-09-13「负市值对数的基准曲线 2026-08-14 就没了，分组曲线倒是有」= 卖点 `T+h+1`
    越界时基准给 `None`，而组合侧 label 走「冻结价兜底」（`exit_px.where(notna, last_c)`，
    见 `factors/single_test.py`）⇒ 组合/分组末期照常有值（**部分持有期**）。
    现在基准同口径：**卖点越界 ⇒ 用该指数最后一个收盘价结算该期**（并计 `n_partial`）。
"""
import numpy as np
import pandas as pd

from app.factors import benchmark_curves as bc


def _frame(vals, start="2024-01-01"):
    days = pd.bdate_range(start, periods=len(vals))
    return pd.DataFrame({"SH000300": vals}, index=days), days


class TestPeriodReturnCums:
    def test_full_window_and_arithmetic_sum(self):
        """h=2：每期 = close[T+1] → close[T+3]，各期**算术**累加（非复利）。"""
        close, days = _frame([100.0, 110.0, 121.0, 133.1, 146.41])
        cum = bc.period_return_cums(close, [days[0], days[1]], 2)["SH000300"]["cum"]
        assert cum == [0.21, 0.42]          # 复利会是 0.4641

    def test_tail_overflow_uses_last_price_partial_period(self):
        """⚠ 卖点越界 ⇒ 最后价兜底（部分持有期），**不是 None**。"""
        close, days = _frame([100.0, 110.0, 121.0, 133.1, 146.41])
        out = bc.period_return_cums(close, [days[0], days[1], days[2]], 2)["SH000300"]
        # days[2]：i1=3(133.1)、i2=5 越界 ⇒ 用尾部价 146.41 ⇒ +10%
        assert out["cum"] == [0.21, 0.42, 0.52]
        assert out["n_partial"] == 1

    def test_tail_overflow_does_not_truncate_later_periods(self):
        """越界只影响**该期**：乱序调仓日也不会把后面的期连带置 None。"""
        close, days = _frame([100.0, 110.0, 121.0, 133.1, 146.41])
        cum = bc.period_return_cums(close, [days[0], days[2], days[1]], 2)["SH000300"]["cum"]
        assert cum == [0.21, 0.31, 0.52]

    def test_entry_beyond_tail_is_none(self):
        """买点 T+1 就越界 ⇒ None（组合侧 entry_px 同为 NaN ⇒ label 亦 NaN），且不计 partial。"""
        close, days = _frame([100.0, 110.0, 121.0, 133.1, 146.41])
        out = bc.period_return_cums(close, [days[4]], 2)["SH000300"]
        assert out["cum"] == [None]
        assert out["n_partial"] == 0

    def test_h_zero(self):
        """h=0 ⇒ T+1 买、T+1 卖 ⇒ 每期恒 0。"""
        close, days = _frame([100.0, 110.0, 121.0, 133.1, 146.41])
        assert bc.period_return_cums(close, [days[0], days[1]], 0)["SH000300"]["cum"] == [0.0, 0.0]

    def test_non_trading_day_is_none(self):
        """调仓日不在交易日历（周末）⇒ None（不猜最近交易日）。"""
        close, days = _frame([100.0, 110.0, 121.0])
        assert bc.period_return_cums(
            close, [days[0] + pd.Timedelta(days=5)], 1)["SH000300"]["cum"] == [None]

    def test_mid_hole_breaks_only_that_period(self):
        """⚠ 回归 2026-09-12：中间某期缺价只断**当期**，后续照算（曾整条断线）。"""
        close, days = _frame([100.0, 110.0, np.nan, 133.1, 146.41])
        # T=d0：i1=1(110)、i2=3(133.1) ⇒ +21%（空洞在 index=2，不落在本期的买/卖点上）
        # T=d1：i1=2(NaN) ⇒ 买点无价 ⇒ 该期 None（**只有这一期**）
        # T=d2：i1=3(133.1)、i2=4(146.41) ⇒ +10% ⇒ 累计 0.31（后续照算 ⇒ 不再整条断线）
        # T=d3：i1=4、i2=5 越界 ⇒ 尾部价兜底 ⇒ +0% ⇒ 仍 0.31
        cum = bc.period_return_cums(close, [days[0], days[1], days[2], days[3]], 2)["SH000300"]["cum"]
        assert cum == [0.21, None, 0.31, 0.31]

    def test_user_2026_09_13_repro(self):
        """⚠ 复现用户报障现场：数据尾 2026-08-21、调仓日 2026-08-14、h=40 ⇒ 只持有 4 个交易日。"""
        dd = pd.bdate_range("2026-06-01", "2026-08-21")
        px = np.linspace(100.0, 120.0, len(dd))
        close = pd.DataFrame({"SH000300": px}, index=dd)
        t0, t1 = pd.Timestamp("2026-06-18"), pd.Timestamp("2026-08-14")
        out = bc.period_return_cums(close, [t0, t1], 40)["SH000300"]
        e_full = px[dd.get_loc(t0) + 41] / px[dd.get_loc(t0) + 1] - 1.0
        e_part = px[-1] / px[dd.get_loc(t1) + 1] - 1.0     # 08-17 买、数据尾 08-21 卖
        assert out["cum"][1] == round(e_full + e_part, 6)
        assert out["n_partial"] == 1
        assert None not in out["cum"]                      # 旧实现在这里是 None（基准提前断线）

    def test_compound_and_arithmetic_share_missing_semantics(self):
        """算术 `cum` 与复利 `cum_compound` 的缺失位置/部分持有期必须完全一致。"""
        dd = pd.bdate_range("2026-06-01", "2026-08-21")
        close = pd.DataFrame({"SH000300": np.linspace(100.0, 120.0, len(dd))}, index=dd)
        out = bc.period_return_cums(close, [pd.Timestamp("2026-06-18"),
                                            pd.Timestamp("2026-08-14")], 40)["SH000300"]
        assert [(x is None) for x in out["cum"]] == [(x is None) for x in out["cum_compound"]]

    def test_multi_code_independent(self):
        """多指数各自独立累加（平盘 ⇒ 全 0）。"""
        days = pd.bdate_range("2024-01-01", periods=5)
        close = pd.DataFrame({"SH000300": [100.0, 110.0, 121.0, 133.1, 146.41],
                              "SH000852": [100.0] * 5}, index=days)
        out = bc.period_return_cums(close, [days[0], days[1]], 2)
        assert out["SH000852"]["cum"] == [0.0, 0.0]
        assert out["SH000300"]["cum"][0] == 0.21


class TestCatalog:
    def test_codes_are_real_indices_in_dataset(self):
        assert bc.BENCH_CODES == ("SH000300", "SH000852", "SH000905", "SH000906", "SH000985")

    def test_default_mapping_reuses_backtest_page(self):
        assert bc.default_benchmark("csi300") == "SH000300"
        assert bc.default_benchmark("csi1000") == "SH000852"
        assert bc.default_benchmark("csi500") == "SH000905"
        assert bc.default_benchmark("csi800") == "SH000906"
        assert bc.default_benchmark("all") == "SH000300"      # 兜底与回测页一致
        assert bc.default_benchmark("") == "SH000300"
