# -*- coding: utf-8 -*-
"""`_compute_layers` 的 `long_average` **口径一致性**回归测试（v1.20.42）。

背景（用户 2026-09-21 报障）：「第一段收益曲线跑赢基准，怎么分层回测反而跑不过基准？」
排查中发现：`rebalance_period > 1` 分支里 `Group1` 由**每日 `ret`** 累加 ✓，
而 `long_average` 原来减的是 **`label`（N 日远期收益）** ✗ ⇒ **口径混用** ✗✗
⇒ 实测段1 首点 `long_average = +8.50%`（一天内不可能 ✗）、20 日累计 `+23.79%` ✗。

本测试用**量纲明显不同**的合成数据把这个错误钉死 ✓：
`label` 故意取 0.2~0.5（若被误用，结果会离谱地负 ✗），`ret` 取 ±2% ✓。
"""
import numpy as np
import pandas as pd

from app.engine.analysis import _compute_layers


def _mk(dates, codes, rets, labels, scores):
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "instrument"])
    return pd.DataFrame({
        "score": np.tile(scores, len(dates)),
        "ret": np.tile(rets, len(dates)),
        "label": np.tile(labels, len(dates)),
    }, index=idx)


def test_long_average_uses_ret_not_label():
    """`long_average` 必须与 `Group1` **同源**（都用每日 `ret`）✓。"""
    d1, d2 = pd.Timestamp("2021-01-04"), pd.Timestamp("2021-01-05")
    codes = ["A", "B", "C", "D"]
    # score 降序 A>B>C>D；N=2 ⇒ Group1={A,B}、Group2={C,D}
    scores = [4.0, 3.0, 2.0, 1.0]
    rets = [0.04, 0.00, -0.02, -0.02]          # 每日 ret（两日相同）
    labels = [0.5, 0.4, 0.3, 0.2]              # ★ 量纲完全不同（20 日远期量级 ✗）
    df = _mk([d1, d2], codes, rets, labels, scores)

    pts = _compute_layers(df, N=2, benchmark_ret=None, rebalance_period=2)
    assert pts is not None and len(pts) == 2, pts

    # Group1 每日 = mean(0.04, 0.00) = +2%；全样本均值 = mean(ret) = 0.0
    # ⇒ long_average 每日 = +2% ⇒ 两日复利 = 1.02^2 - 1 = 0.0404
    assert pts[0]["Group1"] == round(0.02, 6), pts[0]
    assert pts[0]["long_average"] == round(0.02, 6), pts[0]
    assert pts[-1]["long_average"] == round(1.02 ** 2 - 1, 6), pts[-1]

    # Group2 每日 = -2% ⇒ long_short 每日 = +4% ⇒ 两日复利 = 1.04^2 - 1 = 0.0816
    assert pts[-1]["Group2"] == round(0.98 ** 2 - 1, 6), pts[-1]
    assert pts[-1]["long_short"] == round(1.04 ** 2 - 1, 6), pts[-1]


def test_long_average_first_point_is_one_day_scale():
    """首点必须是**单日量级** ✓（旧 BUG 下会是 label 量级 ✗）。"""
    d1, d2 = pd.Timestamp("2021-01-04"), pd.Timestamp("2021-01-05")
    codes = ["A", "B", "C", "D"]
    df = _mk([d1, d2], codes, [0.04, 0.00, -0.02, -0.02], [0.5, 0.4, 0.3, 0.2],
             [4.0, 3.0, 2.0, 1.0])
    pts = _compute_layers(df, N=2, benchmark_ret=None, rebalance_period=2)
    # 若误用 label（均值 0.35）⇒ 首点会是 -0.33 级 ✗；正确值 = +0.02 ✓
    assert abs(pts[0]["long_average"]) < 0.05, pts[0]
