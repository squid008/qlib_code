# -*- coding: utf-8 -*-
"""XLSX 导出/回灌的契约与**面板复刻**守卫（v2.1.0 ✓）。

**为什么锁这几件事**：
  · 导出的文件要能被**我们自己读回**（「交易信号测试」拖入 ⇒ 对齐参数 + 复现净值 ✓）
    ⇒ "写 → 读"必须逐字段对得上；这条一断，用户拿到文件却拖不进去（而且很难查 ✗）；
  · 第 1 页是**面板复刻**（用户 2026-10-11 否掉"数据转储"版并给了明确要求）
    ⇒ 关键文案/口径（分段算法、区间累计超额=净值比、起点 1 / 0、多空走次轴）都要有守卫 ✓
    —— 这些一旦漂移，页面与文件就"看着都对、数不一样" ✗（最难发现的一类问题）；
  · **每期持仓**在测试进程内算完即丢 ⇒ 靠 `HoldingsIndex` 存下来（名次有序 + 上限控制 ✓）
    ⇒ 排序/上限/预算都要有守卫，否则会静默给出半份持仓 ✗；
  · 参数**机器可读优先、标签兜底**：隐藏页被删（用户另存/清理）时仍要能读回参数 ✓。
"""
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from app.exports import spec
from app.exports.topk import build_topk_workbook
from app.exports.topk_doc import PanelSource, build_doc
from app.factors.holdings_index import MAX_CODES_PER_TASK, HoldingsIndex

# ⚠ 起止**跨过 2021/2023 两个分段起点**（页面 `SEG_YEARS`）⇒ 才能验"分段"那段文案 ✓
DATES = ["2019-01-02", "2021-01-04", "2023-01-03", "2024-01-02"]


def _source() -> PanelSource:
    """与真实任务**同结构**的一份来源（3 期 / 10 分位组 / 1 基准 / 敏感度 2 行 ✓）。"""
    row = {"id": "f1", "name": "测试因子", "expression": "$close/Ref($close,1)",
           "source_formula": "C/REF(C,1)", "horizon": 60,
           "quantile_curves": {
               "n_groups": 10, "dates": list(DATES), "best_quantile": 10, "worst_quantile": 1,
               "monotonicity": 0.94,
               "groups": [{"quantile": q, "cum": [0.01 * q / 10 * (i + 1) for i in range(4)]}
                          for q in range(1, 11)],
               "long_short": {"quantile": [10, 1], "cum": [0.0, 0.01, 0.02, 0.03]},
           },
           "topk_sensitivity": {
               "default_k": 20, "trading_days": 252.0, "note": "NOTE-文本",
               "rows": [{"k": 10, "avg_turnover": 0.5, "gross_per_period": 0.012,
                         "ann_cost": {"0.0040": 0.008, "0.0080": 0.016}, "cost_eaten": {"0.0040": 0.1}},
                        {"k": 20, "avg_turnover": 0.36, "gross_per_period": 0.011,
                         "ann_cost": {"0.0040": 0.006, "0.0080": 0.012}, "cost_eaten": {"0.0040": 0.05}}],
           }}
    item = {"kind": "topk", "k": 20, "turnover": [0.0, 0.5, 0.36, 0.4],
            "curves": {"0.0000": [0.02, 0.05, 0.08, 0.095], "0.0040": [0.018, 0.047, 0.075, 0.09],
                       "0.0080": [0.016, 0.044, 0.070, 0.085]},
            "curves_compound": {"0.0000": [0.021, 0.052, 0.083, 0.10],
                                "0.0040": [0.019, 0.049, 0.078, 0.095],
                                "0.0080": [0.017, 0.046, 0.073, 0.09]},
            "perf": {"0.0000": {"annual_return": 0.24, "max_drawdown": -0.45, "sharpe": 0.66,
                                "sortino": 0.95, "calmar": 0.47, "vol_annual": 0.44,
                                "win_rate": 0.53, "n_days": 1122},
                     "0.0040": {"annual_return": 0.23, "max_drawdown": -0.46, "sharpe": 0.64,
                                "sortino": 0.93, "calmar": 0.45, "vol_annual": 0.446,
                                "win_rate": 0.537, "n_days": 1122},
                     "0.0080": {"annual_return": 0.22, "max_drawdown": -0.47, "sharpe": 0.62,
                                "sortino": 0.91, "calmar": 0.43, "vol_annual": 0.45,
                                "win_rate": 0.53, "n_days": 1122}}}
    tc = {"rebalance_period": 60, "horizon": 60, "n": len(DATES), "dates": list(DATES),
          "default_quantile": 10, "items": [item],
          "benchmarks": {"default": "SH000300",
                         "items": [{"code": "SH000300", "name": "沪深300",
                                    "cum": [0.0, 0.01, -0.02, -0.025],
                                    "cum_compound": [0.0, 0.008, -0.015, -0.02], "n_partial": 1,
                                    "perf": {"annual_return": -0.012, "max_drawdown": -0.351,
                                             "sharpe": 0.03, "sortino": 0.04, "calmar": -0.03,
                                             "vol_annual": 0.183, "win_rate": 0.479,
                                             "n_days": 1122}}]}}
    params = {
        spec.P_KIND: spec.KIND_TOPK, spec.P_APP_VERSION: "9.9.9",
        spec.P_GENERATED_AT: "2026-10-11 10:00:00", spec.P_FACTOR_NAME: "测试因子",
        spec.P_FACTOR_EXPR: "$close/Ref($close,1)", spec.P_UNIVERSE: "csi300",
        spec.P_START_DATE: "2022-01-01", spec.P_END_DATE: "2024-06-30", spec.P_HOLD_DAYS: 60,
        spec.P_REBALANCE_PERIOD: 60, spec.P_COST: 0.004, spec.P_BENCHMARK: "SH000300",
        spec.P_BASIS: "compound", spec.P_QUANTILE: 10, spec.P_TOPK: 20,
    }
    holdings = []
    for day, n in zip(DATES, (2, 2, 1, 3)):  # noqa: E501
        for seq in range(1, n + 1):
            holdings.append({"rebalance_date": day, "seq": seq, "code": "SH60000%d" % seq,
                             "weight": 1.0 / n, "n_held": n})
    return PanelSource(row=row, item=item, tc=tc, qc=row["quantile_curves"],
                       sens=row["topk_sensitivity"], bench=tc["benchmarks"]["items"][0],
                       rq={"universe": "csi300", "start_date": "2022-01-01",
                           "end_date": "2024-06-30", "exclude_suspended": True},
                       params=params, basis="compound", tier="0.0040", holdings=holdings,
                       nav_png=_tiny_png("#0f766e"), dec_png=_tiny_png("#2563eb"),
                       subtitle="导出时间 2026-10-11 ｜ 工具版本 v9.9.9")


def _tiny_png(color: str) -> bytes:
    """一张真 PNG（120×40）—— 贴图路径必须用**真图片**验（假字节 Pillow 会拒 ✗）✓。"""
    from io import BytesIO

    from PIL import Image as PILImage

    buf = BytesIO()
    PILImage.new("RGB", (120, 40), color).save(buf, format="PNG")
    return buf.getvalue()


def test_panel_mapping_matches_frontend_rules():
    """第 1 页是**面板复刻**：摘要/口径/起点/次轴/区间累计超额，逐条对齐页面规则 ✓。"""
    doc = build_doc(_source())
    head = "\n".join(doc.headline)
    assert "调仓期 60 交易日（默认 = 预测周期 h=60）" in head
    assert "4 次调仓" in head and "分位档：最强 Q10 / 最弱 Q1" in head
    # 分段：起点年 2021/2023 各一段（2013/2018 早于数据起点 ⇒ 页面同规则丢弃 ✓）
    assert "分段（最强−最弱，算累）：2021起 +1.8pp" in head and "2023起 +0.9pp" in head
    assert "（每段都赢 ✓）" in head and "十档单调性 ρ=0.94" in head
    # 因子名与**两种公式**都要有（用户点名要求 ✓）
    labels = [p[0] for p in doc.formula_pairs]
    assert labels == ["因子名称", "因子 ID", "原文公式", "具体公式（表达式）"]
    assert doc.formula_pairs[3][1] == "$close/Ref($close,1)"
    # 图 1：起点补 1（净值口径 ✓）；列 = 三档成本 + 基准 ✓；**不含换手**（用户明确否掉 ✓）
    assert doc.nav_headers == ["调仓日", "无成本", "往返 0.004", "往返 0.008", "基准 沪深300"]
    assert doc.nav_data[0] == ["起点 = 1", 1.0, 1.0, 1.0, 1.0]
    assert doc.nav_data[1][1] == pytest.approx(1.021)          # 1 + curves_compound(0.0000)[0] ✓
    assert all("换手" not in h for h in doc.nav_headers)       # 换手只进表/数据页 ✗ 不进图 ✓
    # 区间累计超额 = **净值比**（复利口径 ✓）：(1+10.0%)/(1−2.0%)−1 = +12.2% ✓
    assert "区间累计（复利）：组合（无成本）+10.0%" in doc.nav_summary
    assert "基准 沪深300 -2.0%" in doc.nav_summary
    assert "⇒ 超额（净值比） +12.2%" in doc.nav_summary      # 页面同样是"超额（净值比） <值>" ✓
    assert "⇒ 超额（净值比）" in doc.nav_summary and "末期只持有不足 60 个交易日" in doc.nav_summary
    # 图 2：起点 0、10 条分位 + 多空（**右轴**）+ 基准 ✓；两张图都是**前端截图**（所见即所得 ✓）
    assert doc.dec_data[0][0] == "起点 = 0" and set(doc.dec_data[0][1:]) == {0.0}
    assert doc.dec_headers[0] == "调仓日" and "多空（右轴）" in doc.dec_headers
    assert "Q10（最强）" in doc.dec_headers and "Q1（最弱）" in doc.dec_headers
    assert len(doc.dec_headers) == 1 + 10 + 1 + 1        # 日期 + Q1..Q10 + 多空 + 基准 ✓
    assert "右轴 = 多空" in doc.dec_note
    assert doc.nav_png and doc.dec_png                   # 截图必须在（缺了就不是"所见即所得" ✗）
    assert "样本内最优" not in head                        # 用户要求删掉这句后缀（太长 ✗）
    # ★ 回归：**第 1 页绝不能冻结窗格** —— 敏感度表在 100 多行，一冻就等于锁死上面全部内容，
    #   用户实测"滚动条往下滚动下不去"（2026-10-11 报障 ✓）
    wb = load_workbook(BytesIO(build_topk_workbook(doc)))
    assert wb[spec.SHEET_PARAMS].freeze_panes is None, "第 1 页不能冻结（否则滚不动 ✗）"
    assert wb["曲线数据"].freeze_panes is None, "数据页也不能冻结 ✗"
    assert wb[spec.SHEET_HOLDINGS].freeze_panes == "A2", "持仓明细要冻表头（几千行 ✓）"
    # 口径说明（黄底）要带上本次 basis ✓
    assert "复利（每期收益滚入本金 = 实盘满仓复投）" in doc.note_a and "分红" in doc.note_a
    # 成本敏感度表：默认档要标出来、列序与页面一致 ✓
    assert doc.sens_headers[1] == "每期换手" and doc.sens_headers[2] == "年化成本（往返 0.004）"
    assert doc.sens_rows[0][0] == 10 and doc.sens_rows[1][0] == "20（默认 10%）"
    assert "年化按 252" in doc.sens_note


def test_build_and_read_roundtrip():
    """写 → 读：模块、参数（回灌要用的三个）、持仓明细、图表，全部对得上 ✓。"""
    doc = build_doc(_source())
    data = build_topk_workbook(doc)
    assert data[:2] == b"PK"                       # 真的是 xlsx（zip 魔数 ✓）

    wb = load_workbook(BytesIO(data))
    assert wb.sheetnames[:4] == [spec.SHEET_PARAMS, spec.SHEET_HOLDINGS, "曲线数据", spec.SHEET_NOTES]
    assert spec.SHEET_META in wb.sheetnames and wb[spec.SHEET_META].sheet_state == "hidden"
    # 第 1 页的两张图 = **前端截图**（不是 openpyxl 原生图：原生图在部分 Excel/WPS 里渲染不出来 ✗）
    assert len(wb[spec.SHEET_PARAMS]._images) == 2, "第 1 页要有两张曲线截图 ✓"
    assert not wb[spec.SHEET_PARAMS]._charts, "不应再有原生图表（会被渲染成空白 ✗）"
    assert wb[spec.SHEET_HOLDINGS].max_row == 1 + len(doc.holdings)

    out = spec.read_export(data)
    assert out.kind == spec.KIND_TOPK and out.format_id == spec.FORMAT_ID
    assert out.app_version == "9.9.9"
    # ★ 回灌真正要用的三项（缺一项就没法对齐设置/复现曲线 ✗）
    assert out.p(spec.P_HOLD_DAYS) == 60
    assert abs(float(out.p(spec.P_COST)) - 0.004) < 1e-12
    assert out.p(spec.P_BENCHMARK) == "SH000300"
    assert out.n_periods() == len(DATES)
    assert {h["code"] for h in out.holdings} == {"SH600001", "SH600002", "SH600003"}
    assert len(out.holdings) == len(doc.holdings)
    assert not [w for w in out.warnings if "JSON" in w]


def test_shot_decoding_only_accepts_real_png():
    """截图是唯一"用户侧原样进文件"的输入 ⇒ 只收真 PNG + 大小上限，失败给**可读原因** ✓。"""
    import base64

    from app.routers.export_xlsx import MAX_SHOT_BYTES, _decode_shot

    png = _tiny_png("#111827")
    got, err = _decode_shot("data:image/png;base64," + base64.b64encode(png).decode())
    assert got == png and err is None
    got, err = _decode_shot(base64.b64encode(b"not a png").decode())
    assert got is None and err and "不是 PNG" in err
    got, err = _decode_shot(base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_SHOT_BYTES + 1)).decode())
    assert got is None and err and "过大" in err
    assert _decode_shot(None) == (None, None) and _decode_shot("") == (None, None)   # 不带图 ⇒ 静默 ✓


def test_reader_falls_back_to_chinese_labels():
    """隐藏页被删（用户另存）⇒ 仍能从第 1 页的中文标签读回参数，并**如实告警** ✓。"""
    wb = load_workbook(BytesIO(build_topk_workbook(build_doc(_source()))))
    wb.remove(wb[spec.SHEET_META])
    buf = BytesIO()
    wb.save(buf)

    out = spec.read_export(buf.getvalue())
    assert out.p(spec.P_HOLD_DAYS) == 60 and out.p(spec.P_BENCHMARK) == "SH000300"
    assert out.kind == spec.KIND_TOPK                       # 按持仓表头猜出模块 ✓
    assert any("标签" in w for w in out.warnings), "兜底解析必须留痕（不静默 ✗）"


def test_read_export_flags_foreign_file():
    """不是本工具导出的表（没有那两页）⇒ 读得出来但如实告警，不抛异常 ✓。"""
    wb = Workbook()
    wb.active["A1"] = "hello"
    buf = BytesIO()
    wb.save(buf)
    out = spec.read_export(buf.getvalue())
    assert not out.ok and out.holdings == []
    assert any(spec.SHEET_HOLDINGS in w for w in out.warnings)


def test_ordered_holdings_is_rank_sorted_and_capped():
    """`ordered_holdings`：按名次升序 ✓、与调仓日同序等长 ✓、每期受上限约束 ✓。"""
    from app.factors.holdings_index import ordered_holdings

    reb = ["2024-01-02", "2024-01-09"]
    dates = ["2024-01-02"] * 4 + ["2024-01-09"] * 3
    insts = ["SH600004", "SH600001", "SH600003", "SH600002", "SH600009", "SH600008", "SH600007"]
    ranks = [4.0, 1.0, 3.0, 2.0, 3.0, 1.0, 2.0]
    got = ordered_holdings(dates, insts, ranks, reb)
    assert got[0] == ["SH600001", "SH600002", "SH600003", "SH600004"]     # 名次 1,2,3,4 ✓
    assert got[1] == ["SH600008", "SH600007", "SH600009"]                # 名次 1,2,3 ✓
    assert ordered_holdings(dates, insts, ranks, reb, max_codes=2)[0] == ["SH600001", "SH600002"]
    assert ordered_holdings([], [], [], reb) == [[], []]                 # 空样本 ⇒ 空列表（不抛 ✓）


def test_ordered_holdings_aligns_datetime64_with_timestamp():
    """★ 回归（v2.1.0 实测踩到）：样本索引给 `datetime64[ns]`、调仓日给 `Timestamp`
    ⇒ 直接用 `str()` 比较会**一期都匹配不上**（第 2 页空表且不触发任何告警 ✗）✓。"""
    import numpy as np
    import pandas as pd

    from app.factors.holdings_index import ordered_holdings

    reb = [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-09")]
    days = np.array(["2024-01-02", "2024-01-02", "2024-01-09"], dtype="datetime64[ns]")
    got = ordered_holdings(days, np.array(["B", "A", "C"], dtype=object),
                           np.array([2.0, 1.0, 1.0]), reb)
    assert got == [["A", "B"], ["C"]], got


def test_holdings_index_budget_and_lookup():
    """索引：按 (表达式, 周期, 档位) 取得到 ✓；超预算时置 `truncated` 而不是静默少给 ✓。"""
    idx = HoldingsIndex()
    idx.put("$close", 60, "high", DATES, {("topk", 2, None): [["A", "B"], ["A"], ["C"]]})
    idx.put("$close", 60, "high", DATES, {("decile", 0, 10): [["A", "B"], ["A", "B"], ["A", "B"]]})
    assert idx.get("$close", 60, "topk", 2) == [["A", "B"], ["A"], ["C"]]
    assert idx.get("$close", 60, "decile", 0, 10) == [["A", "B"]] * 3
    assert idx.get("$close", 20, "topk", 2) is None                    # 周期不同 ⇒ 取不到 ✓
    assert idx.dates("$close", 60) == DATES

    # 预算耗尽 ⇒ 之后只置 truncated，不再存（导出侧如实提示 ✓）
    big = HoldingsIndex()
    big.used_codes = MAX_CODES_PER_TASK
    assert big.put("$x", 20, "high", DATES, {("topk", 1, None): [["A"]]}) is False
    assert big.truncated and big.get("$x", 20, "topk", 1) is None

    st = idx.as_state()
    back = HoldingsIndex.from_state(st)
    assert back.get("$close", 60, "topk", 2) == [["A", "B"], ["A"], ["C"]]
