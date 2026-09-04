# -*- coding: utf-8 -*-
"""e2e golden：真实回测主流程回归（最慢但最有价值，低频手动/拆分前后对跑）。

定位：后续对 qlib_engine 做大文件拆分等纯重构时，先跑本用例记下基线，
重构后重跑对比，防止主流程（滚动训练/回放/分层/交易/结果序列化）被静默破坏。

运行（需本机 cn_data，无数据自动 skip）：
    python -m pytest tests/test_e2e_backtest.py -q
    python -m pytest tests -m "datareq and e2e" -q
默认全量（scripts/check.ps1）已排除 e2e，避免日常 +3 分钟。

锁定内容：
- custom 滚动两段跑通：rolling_segments == 2
- 净值曲线存在且首段不平（回归"首段预热"缺陷——首段曾整段是水平直线）
- 分层 5 组 / 交易记录齐全
- 关键指标有限且在合理范围（LightGBM 数值有平台/线程差异，用宽窗口，
  只抓"流程断裂/结果为空/NaN"类破坏；精确 golden 不可行）
"""
import math

import pytest

pytestmark = pytest.mark.datareq


class TestE2ERollingBacktest:
    @pytest.mark.e2e
    def test_two_segment_rolling_run(self, tmp_path):
        from app.engine import qlib_engine
        from app.models.backtest import BacktestRequest

        req = BacktestRequest(
            universe="csi300",
            # 6 只上市早的蓝筹，数据覆盖 2019+，确保训练/测试段完整
            instruments=["SH600000", "SH600036", "SZ000001", "SZ000002", "SH600030", "SH601318"],
            start_date="2021-01-01",
            end_date="2021-04-30",
            model="LightGBM",
            feature="Alpha158",
            selected_features=["KMID", "ROC5", "MA5"],
            topk=3,
            n_days_hold=10,
            layer_rebalance=1,
            split_mode="custom",
            train_win=6,
            train_unit="month",
            test_win=2,
            test_unit="month",
            initial_capital=100000000.0,
        )

        r = qlib_engine.run_backtest(req, work_dir=str(tmp_path), task_id="golden_e2e")

        assert r is not None
        # 滚动两段跑通
        assert r.report_df.get("rolling_segments") == 2

        # 净值曲线完整（应覆盖回测区间约 80+ 交易日）
        nav = r.nav or []
        assert len(nav) > 30
        assert all(p.get("date") for p in nav)

        # 首段不平（防"首段预热/首段水平直线"缺陷复发）：
        # 前 1/3 净值段内至少出现 5 个不同数值
        first_part = [p["value"] for p in nav[: max(5, len(nav) // 3)] if p.get("value") is not None]
        assert len(first_part) >= 5
        assert len({round(v, 6) for v in first_part}) >= 5

        # 关键指标有限且量级合理（宽窗口；0.5 对应 50% 区间总收益，远宽于任何真实回测）
        assert r.total_return is not None and math.isfinite(r.total_return)
        assert -0.5 < r.total_return < 0.5
        assert r.annualized_return is not None and math.isfinite(r.annualized_return)
        assert r.benchmark_return is not None and math.isfinite(r.benchmark_return)

        # 分层与交易齐全：段数 2、每个分层点含 Group1..Group5 五组键
        layers = r.layer_returns or {}
        assert len(layers.get("segments") or []) == 2
        merged = layers.get("merged") or {}
        merged_groups = merged.get("groups") or [{}]
        assert merged_groups
        for key in ["Group1", "Group2", "Group3", "Group4", "Group5"]:
            assert key in merged_groups[0]
        assert len(r.trades or []) > 0
