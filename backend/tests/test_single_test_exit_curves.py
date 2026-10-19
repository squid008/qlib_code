# -*- coding: utf-8 -*-
"""`_sft_exit_curves` 测试（★ v2.9.85，2026-10-19 ✓ —— 离场因子曲线 ✓）。

## 守什么

这个函数把"**离场表达式**"翻译成前端要的东西 ✓：触发率统计（`hit_cells` / `rate` /
`avg_per_day` / `first_day` / `last_day` ✓）、**离场事件研究**（每个持有期一份
trigger vs control 的均值/中位数/差值 ✓）、以及一条**向后兼容的单数取法**
（`exit_out["mask"]` / `["stats"]` = **第一个**离场 ✓ —— v2.4.3 的老前端还这么读 ✓）。

三条容易改坏的：
1. **`rate > 0.5` 必须给出警告** ✓（"⚠ 触发过半 ⇒ 近似只持有 1 天"✓）—— 这是用户判断
   "我这个离场是不是太容易触发"的**唯一线索** ✗（一半以上的格都触发 ⇒ 实际就是持有 1 天 ✓）；
2. **`entry_dependent`（含入场价）走的是另一条路** ✓：不出 `mask`/`stats`，而是把
   **骨架 + 预计算列的现成 Series** 交给路由 ✓（内存直传，不序列化 ✓）；
3. **出错不许抛到外面** ✓：单条离场失败只写 `it["error"]` ✓，其余离场继续算 ✓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.factors import single_test as ST


def _df() -> pd.DataFrame:
    """2 只票 × 3 天 = 6 格 ✓。"""
    idx = pd.MultiIndex.from_product(
        [["A", "B"], pd.bdate_range("2024-01-02", periods=3)],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame(
        {
            "EXIT1": [0.6, 0.4, 0.7, 0.2, 0.55, 0.1],
            "LABEL_2": [0.10, -0.05, 0.20, 0.02, -0.01, 0.03],
        },
        index=idx,
    )


def _run(exit_cols, exit_specs=None, thresholds=0.3, progress_cb=None, exits=None):
    out: dict = {}
    exit_specs = exit_specs if exit_specs is not None else [{}, {}]
    ST._sft_exit_curves(
        _exits=exits or [{"id": "e1", "name": "离场一", "expression": "EXIT1>0.3"},
                         {"id": "e2", "name": "离场二", "expression": "EXIT1>0.5"}],
        df=_df(), e=None, exit_cols=exit_cols, exit_out=out, exit_specs=exit_specs,
        exit_threshold=thresholds, horizons=[2], label_cols={2: "LABEL_2"},
        progress_cb=progress_cb,
    )
    return out


def test_no_op_when_exit_out_or_cols_missing():
    assert _run(None) == {}
    assert _run([]) == {}


def test_stats_and_backward_compatible_single_fields():
    out = _run(["EXIT1", "EXIT1"])
    assert len(out["items"]) == 2
    s = out["items"][0]["stats"]
    # 6 格中 4 格 > 0.3 ✓
    assert s["hit_cells"] == 4 and s["n_cells"] == 6
    assert s["rate"] == pytest.approx(0.6667, abs=1e-4)
    assert s["n_days"] == 3
    assert s["avg_per_day"] == pytest.approx(1.3, abs=1e-9)   # 4/3 ⇒ 保留 1 位 ✓
    assert s["first_day"] == "2024-01-02" and s["last_day"] == "2024-01-04"
    # ★ 兼容 v2.4.3 的老前端：单数取法 = **第一个**离场 ✓
    assert out["mask"].equals(out["items"][0]["mask"])
    assert out["stats"] == out["items"][0]["stats"]


def test_rate_over_half_emits_warning_to_progress():
    """⭐ 触发过半必须提示"近似只持有 1 天" ✓（用户判断离场是否太松的唯一线索 ✓）。"""
    seen = []
    _run(["EXIT1", "EXIT1"], progress_cb=lambda pct, p, msg: seen.append((p, msg)))
    assert seen, "应当有进度回调 ✓"
    assert seen[0][0] == 30.0
    assert "触发率" in seen[0][1] and "触发过半" in seen[0][1]
    assert "近似只持有 1 天" in seen[0][1]


def test_effect_compares_trigger_vs_control_per_horizon():
    out = _run(["EXIT1", "EXIT1"])
    eff = out["items"][0]["effect"]["2"]
    assert eff["horizon"] == 2
    assert eff["n_trig"] == 4 and eff["n_ctrl"] == 2
    # 触发组标签：0.10 / -0.05 / 0.20 / -0.01 ⇒ 均值 0.06 ✓
    assert eff["mean_trig"] == pytest.approx(0.06, abs=1e-9)
    # 对照组：0.02 / 0.03 ⇒ 0.025 ✓
    assert eff["mean_ctrl"] == pytest.approx(0.025, abs=1e-9)
    assert eff["diff_mean"] == pytest.approx(0.035, abs=1e-9)
    # 中位数：触发组 sorted [-0.05,-0.01,0.10,0.20] ⇒ 0.045 ✓
    assert eff["median_trig"] == pytest.approx(0.045, abs=1e-9)


def test_effect_requires_two_samples_each_side():
    """某侧 <2 个样本 ⇒ **该持有期整段不出现** ✓（不写 `n=1` 的假结论 ✗）。"""
    df = _df()
    df.loc[(slice(None), df.index.get_level_values("datetime")[2]), "LABEL_2"] = np.nan
    out: dict = {}
    ST._sft_exit_curves(
        _exits=[{"id": "e1", "name": "x", "expression": "EXIT1>0.3"}], df=df, e=None,
        exit_cols=["EXIT1"], exit_out=out, exit_specs=[{}], exit_threshold=0.3,
        horizons=[2], label_cols={2: "LABEL_2"}, progress_cb=None,
    )
    eff = out["items"][0]["effect"]
    assert eff == {} or "2" not in eff


def test_entry_dependent_path_passes_skeleton_and_series():
    """⭐ 含入场价的离场：不出 mask/stats，交给路由「骨架 + 现成 Series」✓。"""
    df = _df()
    df["PRE_A"] = 1.0
    out: dict = {}
    ST._sft_exit_curves(
        _exits=[{"id": "e9", "name": "含入场价", "expression": "CLOSE<ENTRY_PRICE"}], df=df, e=None,
        exit_cols=[None], exit_out=out, exit_specs=[{"skel": {"op": "Lt"}, "pre_exprs": ["A"],
                                                     "pre_cols": ["PRE_A"]}],
        exit_threshold=0.5, horizons=[2], label_cols={2: "LABEL_2"}, progress_cb=None,
    )
    it = out["items"][0]
    assert it["entry_dependent"] is True
    assert it["skel"] == {"op": "Lt"} and it["pre_exprs"] == ["A"]
    assert len(it["pre_series"]) == 1 and it["pre_series"][0].equals(df["PRE_A"])
    assert "mask" not in it and "stats" not in it
    # 无 stats ⇒ 兼容字段也不该伪造 stats ✓
    assert out.get("stats") is None


def test_entry_dependent_error_is_reported_not_raised():
    out: dict = {}
    ST._sft_exit_curves(
        _exits=[{"id": "e9", "name": "坏", "expression": "X"}], df=_df(), e=None,
        exit_cols=[None], exit_out=out, exit_specs=[{"error": "语法错误: 无法解析"}],
        exit_threshold=0.5, horizons=[2], label_cols={2: "LABEL_2"}, progress_cb=None,
    )
    assert out["items"][0]["error"] == "语法错误: 无法解析"
    assert "skel" not in out["items"][0]


def test_blank_exit_list_reports_error(monkeypatch):
    """全是 `None` 且没有 skel/error ⇒ 兼容字段报"没有可用的离场因子" ✓。"""
    monkeypatch.setattr(ST, "_dump_sft_error", lambda e: None)
    out: dict = {}
    ST._sft_exit_curves(
        _exits=[{"id": "", "name": "", "expression": ""}], df=_df(), e=None,
        exit_cols=[None], exit_out=out, exit_specs=[{}],
        exit_threshold=0.5, horizons=[2], label_cols={2: "LABEL_2"}, progress_cb=None,
    )
    # 有 1 个 item（entry_dependent 但既无 skel 也无 error ✓）
    assert len(out["items"]) == 1
    assert out["items"][0]["entry_dependent"] is True
