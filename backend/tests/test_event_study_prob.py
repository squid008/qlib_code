# -*- coding: utf-8 -*-
"""事件研究「亏损侧镜像」单测（v1.19.80）。

背景（用户 2026-09-17）：
  「事件研究里面，最差的事件搞成跟上面两个面板一样的统计形式吧，比如上面是概率：
   触发后拿到目标收益的事件占比 k >0 >10% >20% >50% >100%，
   那下面对应的概率：触发后拿到目标亏损的事件占比 k <0 <-10% <-20% <-50% <-100%，
   然后右边亏损最大的事件面板。这样就能对照着看了。」

⇒ 后端在 `prob[]` 里补亏损侧字段（同一批样本、同一套档位），`worst_by_k[]` 补「期内最低」
  （与 Top 榜的「期内最高」对称）。这里把语义锁住，避免以后改统计时两侧口径漂移。
"""
import pandas as pd

from app.factors.event_study import build_event_stats

DATES = pd.bdate_range("2023-01-02", periods=40)


def _px(moves: dict) -> pd.DataFrame:
    """moves: {code: [每日涨跌幅...]}（从 10.0 起复利）；不足的交易日按平价延展到全表长度。"""
    out = {}
    for c in sorted(moves):
        p = [10.0]
        for m in moves[c]:
            p.append(p[-1] * (1.0 + m))
        while len(p) < len(DATES):
            p.append(p[-1])
        out[c] = p[:len(DATES)]
    return pd.DataFrame(out, index=DATES)


def _events(codes, d: int = 0) -> pd.DataFrame:
    """全部事件都落在 DATES[d]（k=1 的收益 = 买入日次日的涨跌幅）。"""
    return pd.DataFrame([{"code": c, "dt": DATES[d]} for c in codes])


def test_loss_side_mirrors_gain_side():
    """对称行情（+8% / -8% 各一半）⇒ 两侧概率同量级（= 纯波动，不是"赚大亏小"）。"""
    moves = {}
    for i in range(20):
        moves["SH600%03d" % i] = [0.0, 0.08 if i % 2 == 0 else -0.08]
    st = build_event_stats(_px(moves), _events(sorted(moves)), max_k=2)
    row = {r["k"]: r for r in st["prob"]}[1]
    assert row["gt10"] is not None and row["lt10"] is not None
    assert abs(row["gt10"] - row["lt10"]) < 0.05, "对称行情两侧应同量级"
    # <-100% 结构性为 0（价格非负 ⇒ 多头最多亏 100%）
    assert row["lt100"] in (0, 0.0, None)


def test_loss_fields_are_valid_shares():
    """十个概率字段都是 [0,1]；且 `<0` 与 `>0` 加起来不超过 1（剩下的是恰好为 0 的）。"""
    moves = {c: [0.0, -0.30, 0.10] for c in ("SH600001", "SH600002", "SH600003")}
    st = build_event_stats(_px(moves), _events(sorted(moves)), max_k=2)
    assert st["prob"], "应有概率行"
    for row in st["prob"]:
        for key in ("gt0", "gt10", "gt20", "gt50", "gt100",
                    "lt0", "lt10", "lt20", "lt50", "lt100"):
            v = row[key]
            assert v is None or 0.0 <= v <= 1.0, "%s 越界: %r" % (key, v)
        assert row["gt0"] + row["lt0"] <= 1.0 + 1e-9


def test_worst_list_has_period_min_and_is_sorted():
    """亏损榜按期末收益升序（最惨在前），且带「期内最低」= 到该 k 期为止的最大浮亏。"""
    moves = {"SH600001": [0.0, -0.20, -0.10, 0.05],
             "SH600002": [0.0, 0.02, 0.02, 0.02]}
    st = build_event_stats(_px(moves), _events(sorted(moves)), max_k=3)
    rows = st["worst_by_k"]["3"]
    assert rows, "亏损榜应有内容"
    for r in rows:
        assert "min_ret" in r
        if r["min_ret"] is not None and r["ret"] is not None:
            assert r["min_ret"] <= r["ret"] + 1e-9, "期内最低 ≤ 期末收益"
    assert rows[0]["code"] == "SH600001", "亏的那只应排在前面"
    assert rows[0]["min_ret"] is not None and rows[0]["min_ret"] < -0.25


def test_top_and_worst_lists_have_same_length():
    """Top 榜与亏损榜条数一致（对称取 20 条），前端按同样条数展示。"""
    moves = {"SH600%03d" % i: [0.0, 0.01 * (i - 5), 0.0] for i in range(30)}
    st = build_event_stats(_px(moves), _events(sorted(moves)), max_k=2)
    assert len(st["top_by_k"]["2"]) == len(st["worst_by_k"]["2"]) == 20


def test_dist_histogram_for_chart():
    """分布图数据（v1.19.81）：边界升序、各桶计数之和 = 样本数（端桶夹住极值 ⇒ 不丢样本）、
    且"一半 +12% / 一半 -12%"的合成数据应恰好落在两根柱子上。"""
    moves = {}
    for i in range(50):
        moves["SH600%03d" % i] = [0.0, 0.12 if i % 2 == 0 else -0.12]
    st = build_event_stats(_px(moves), _events(sorted(moves)), max_k=1)
    edges = st["dist_edges"]
    assert edges == sorted(edges) and len(edges) >= 10
    row = st["curve"][0]
    counts = row["counts"]
    assert len(counts) == len(edges) - 1      # 桶数 = 边界数 - 1
    assert sum(counts) == row["n"] == 50      # 夹住极值 ⇒ 计数之和 = 样本数
    hot = [c for c in counts if c > 0]
    assert len(hot) == 2 and all(c == 25 for c in hot), "±12% 应各占一根柱子"
    # 分布图要标的尾部（更细的分位）
    assert row["p05"] is not None and row["p95"] is not None
    assert row["p05"] <= row["median"] <= row["p95"]
