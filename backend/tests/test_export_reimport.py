# -*- coding: utf-8 -*-
"""**导出 → 拖回来**的往返守卫（v2.1.0 ✓）。

用户 2026-10-11 的闭环需求：「把单因子测试导出的 XLSX 拖进交易信号测试 ⇒ 识别第 2 页的持仓
记录复现净值曲线；第一页有参数 ⇒ **自动把参数对齐**」✓。

**为什么锁**：这条链路的任何一环断了，用户都是"拖进去没反应/参数没变/曲线不对"——而每一环
单看都"好像没问题" ✗。所以这里直接把导出文件喂给 `parse_csv()`（真实入口 ✓），
断言：① 认得出是本工具导出的（`origin` ✓）；② 持仓变成可回测的买入信号 ✓；
③ 三个对齐参数（持仓周期/成本/基准）都带出来了 ✓。
"""
import pandas as pd

from app.exports import spec
from app.exports.event_study import EventStudyExportDoc, build_event_study_workbook
from app.exports.event_study import contract_params as es_params
from app.exports.topk import TopkExportDoc, build_topk_workbook
from app.signals.parsers import parse_csv

DATES = ["2024-01-02", "2024-02-01", "2024-03-01"]


def _topk_bytes() -> bytes:
    """最小可用的「持仓曲线」导出（参数 + 每期持仓 ⇒ 正是回灌要读的两样 ✓）。"""
    params = {
        spec.P_KIND: spec.KIND_TOPK, spec.P_APP_VERSION: "9.9.9",
        spec.P_GENERATED_AT: "2026-10-11 10:00:00", spec.P_FACTOR_NAME: "AR13_ID60",
        spec.P_FACTOR_EXPR: "$close/Ref($close,1)", spec.P_UNIVERSE: "csi300",
        spec.P_START_DATE: "2022-01-01", spec.P_END_DATE: "2026-08-21",
        spec.P_HOLD_DAYS: 60, spec.P_COST: 0.004, spec.P_BENCHMARK: "SH000300",
    }
    holdings = []
    for i, day in enumerate(DATES):
        for seq, code in enumerate(["SH600001", "SH600002", "SZ000001"][: i + 1], start=1):
            holdings.append({"rebalance_date": day, "seq": seq, "code": code,
                             "weight": 1.0, "n_held": i + 1})
    return build_topk_workbook(TopkExportDoc(factor_name="AR13_ID60", headline=["h"],
                                             params=params, holdings=holdings))


def _es_bytes() -> bytes:
    params = es_params(factor_name="AR13_ID60", factor_expression="$close>Ref($close,1)",
                       universe="all", start="2022-01-01", end="2026-08-21", max_k=60,
                       hold_days=20, cost=0.002, capital=1e9, benchmark="SH000905",
                       app_version="9.9.9", generated_at="2026-10-11 10:00:00")
    holds = []
    for day in DATES:
        for mode in ("event_even", "cash_even"):
            holds.append({"rebalance_date": day, "mode": mode, "code": "SH600519",
                          "shares": 100, "price": 10.0, "amount": 1000.0, "weight": 1.0})
    # v2.1.3：**真实买入事件**（事件研究=回测成交里的"买" ✓）—— 与持仓快照分开写 ✓
    buys = [{"date": d, "code": "SH600519", "mode": "event_even"} for d in DATES]
    return build_event_study_workbook(EventStudyExportDoc(factor_name="AR13_ID60", params=params,
                                                          holdings=holds, buy_signals=buys))


def test_reimport_topk_export_becomes_signals():
    res = parse_csv(_topk_bytes(), "单因子测试_持仓曲线.xlsx")
    assert res.ok and res.kind == "signal_list"          # 走**同一条**下游链路（不多一条分叉 ✓）
    assert res.origin == "qlib_export"
    assert res.aligned["horizon"] == 60 and abs(float(res.aligned["cost"]) - 0.004) < 1e-12
    # ★ 成交时点必须带出来（本工具曲线 = T+1 收盘买入 ✓；否则会按页面默认的开盘价算 ✗）
    assert res.aligned["fill"] == "t1_close"
    assert res.aligned["benchmark"] == "SH000300" and res.aligned["universe"] == "csi300"
    assert res.aligned["factor_name"] == "AR13_ID60"
    assert res.aligned["origin_label"].startswith("单因子测试")
    # 持仓 ⇒ 买入信号：3 期、每期 1/2/3 只 ⇒ 共 6 条（去重后 = 同一日同一只只发一次 ✓）
    sig = res.signals
    assert isinstance(sig, pd.DataFrame) and len(sig) == 6
    assert set(sig.columns) >= {"date", "code", "side", "name", "weight"}
    assert (sig["side"] == 1).all() and sig["code"].nunique() == 3
    assert str(sig["date"].min().date()) == DATES[0]
    assert res.stats["periods"] == 3 and res.stats["from_export"] is True


def test_reimport_event_study_export_picks_one_mode():
    """事件研究导出含两个资金方案 ⇒ 只取一个（`event_even` 优先 ✓），并在诊断里说明 ✓。"""
    res = parse_csv(_es_bytes(), "事件研究.xlsx")
    assert res.origin == "qlib_export" and res.aligned["horizon"] == 20
    # ★ v2.1.4：文件里的成交时点 = **面板净值引擎真正用的**（`t1_open` ✓）。
    #   ⚠ 别写成 `t1_close`：那是"持有期曲线"的统计量口径，而净值引擎用的是次日开盘 ✗
    #   （v2.1.2 就是在这里想当然，导致回灌与面板对不上 ✓）。
    assert res.aligned["fill"] == "t1_open"
    assert res.aligned["strict_limit"] is True            # 严格涨跌停 ✓
    assert res.aligned["rebal_band"] == 0.0               # 死区 0 ✓
    assert res.aligned["capital"] == 1e9                  # 初始资金 ✓
    assert abs(float(res.aligned["cost"]) - 0.002) < 1e-12
    assert res.aligned["benchmark"] == "SH000905"
    # ★ v2.1.3：买入点取**隐藏页的真实买入事件**（3 次买入 ✓），不是持仓快照（那会重复买入 ✗）
    assert res.stats["signal_source"] == "file"
    assert len(res.signals) == len(DATES)                 # 3 次买入 ✓
    assert res.stats["export_mode"] == "event_even"
    assert res.stats.get("modes_in_file") == ["cash_even", "event_even"]
    assert any("资金方案" in str(i.get("reason", "")) for i in res.issues)


def test_old_file_falls_back_to_approx_buys_with_warning():
    """**老文件**（没有 `_signals` 页）⇒ 退回"快照差集"近似 + **必须如实提示** ✓。

    这里用 v2.1.3 之前的写法造文件：只有持仓快照、没有买入信号页 ✓。
    """
    params = es_params(factor_name="OLD", factor_expression="$close", universe="all",
                       start="2022-01-01", end="2026-08-21", max_k=60, hold_days=20,
                       cost=0.004, capital=1e9, benchmark="SH000300",
                       app_version="9.9.9", generated_at="2026-10-11 10:00:00")
    holds = [{"rebalance_date": d, "mode": "event_even", "code": "SH600519", "shares": 1,
              "price": 1.0, "amount": 1.0, "weight": 1.0} for d in ("2024-01-02", "2024-02-01")]
    raw = build_event_study_workbook(EventStudyExportDoc(factor_name="OLD", params=params,
                                                         holdings=holds))
    res = parse_csv(raw, "old.xlsx")
    assert res.stats["signal_source"] == "approx_diff"
    assert len(res.signals) == 1                          # 同一只票连续两期 ⇒ 差集只算 1 次买入 ✓
    assert any("重新导出" in str(i.get("reason", "")) for i in res.issues)


def test_foreign_files_still_go_the_normal_way():
    """普通 CSV / 非本工具的 xlsx **不受影响** ✓（新嗅探必须"认不出就走原管线" ✓）。"""
    res = parse_csv("date,code,side\n2024-01-02,SH600001,1\n".encode("utf-8"), "sig.csv")
    assert res.ok and res.kind == "signal_list" and not res.origin

    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["日期", "代码", "方向"])
    wb.active.append(["2024-01-02", "600001.SH", "买"])
    buf = BytesIO()
    wb.save(buf)
    res2 = parse_csv(buf.getvalue(), "other.xlsx")        # 普通 xlsx ⇒ 老路径（只读第一页 ✓）
    assert res2.ok and res2.kind == "signal_list" and not res2.origin
