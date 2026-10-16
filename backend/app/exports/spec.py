# -*- coding: utf-8 -*-
"""XLSX 导出的**对外契约**：格式版本 / sheet 名 / 列名 / 参数键，以及**读取器**。

★ 为什么必须有这个文件（**别在别处写这些字面量**）：
  导出的文件会被**我们自己**回灌 —— 「交易信号测试」模块要拖入本文件、识别第一页的参数
  并自动对齐设置、再用第二页的持仓记录复现净值曲线（用户 2026-10-11 需求 ✓）。
  所以"写"和"读"必须共用同一份定义：任何一侧写死字面量 ⇒ 改列名/标签时就地埋雷 ✗。

读出策略（**机器可读优先，人可读兜底**）：
  ① 隐藏 sheet `_meta` 里有一格 JSON（`params_json`）⇒ 回灌**只认它** ✓
     —— 中文标签怎么改都不会破坏回灌 ✓；
  ② `_meta` 缺失（用户手工另存/删了隐藏页）时才退回按**中文标签**匹配 `PARAM_LABELS` ✓，
     并把兜底过程记进 `ExportDoc.warnings` ✓（不静默 ✗）。

新增 kind/列/参数时：**只改本文件**，写盘与回灌两侧自动同步 ✓。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 格式标识
FORMAT_ID = "qlib-xlsx-export"
FORMAT_VERSION = "1"                 # 契约版本：列/键有**不兼容**变更时必须 +1 ✓

KIND_TOPK = "topk"                   # 单因子测试 · 持仓期收益曲线（含三档成本）
KIND_EVENT_STUDY = "event_study"     # 事件研究（含净值曲线两种资金方案）

# ---------------------------------------------------------------- sheet 名
SHEET_PARAMS = "参数与指标"           # 第 1 页：**面板复刻**（标题/参数/绩效卡/图/表 ✓）
SHEET_HOLDINGS = "持仓明细"           # 第 2 页：每期持仓记录（回灌读这一页 ✓）
SHEET_DATA = "曲线数据"               # 第 3 页：两张图的数据源（让第 1 页保持"像面板" ✓）
# ★ v2.1.3：**买入信号**（隐藏页 ✓）—— 回灌必须用它而不是"持仓快照" ✗
#   原因（用户 2026-10-11 报"为啥测试结果不一样"，实测根因 ✓）：第 2 页是"每期持仓快照"，
#   同一只票会在它**持有的每个调仓日**都出现一次；若把这些当买入信号 ⇒ 重复买入（实测
#   event_even 8338 对 vs 真实新买入 708 ⇒ **重复 11.8×** ✗）⇒ 资金被反复摊满、曲线必然对不上 ✗。
#   ⇒ 导出时把**真实买入事件**（事件研究=回测成交里的"买"；持仓曲线=每期组合 ✓）单独写一份 ✓。
SHEET_SIGNALS = "_signals"
SHEET_NOTES = "说明"                  # 第 3 页：口径/简化假设/如何回灌（人读）
SHEET_META = "_meta"                  # 隐藏：机器可读元数据（回灌优先读 ✓）

# ---------------------------------------------------------------- 持仓表列
# (英文键 = 契约, 中文表头)；**顺序即写盘顺序**，回灌按中文表头反查英文键 ✓
HOLDING_COLS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    KIND_TOPK: (
        ("rebalance_date", "调仓日"),
        ("seq", "序号"),
        ("code", "代码"),
        ("weight", "等权权重"),
        ("n_held", "当期只数"),
    ),
    KIND_EVENT_STUDY: (
        ("rebalance_date", "调仓日"),
        ("mode", "资金方案"),
        ("code", "代码"),
        ("shares", "股数"),
        ("price", "成交价"),
        ("amount", "成交金额"),
        ("weight", "权重"),
    ),
}

# ---------------------------------------------------------------- 参数键
# ⚠ 回灌侧按**英文键**取参数（见 `PARAM_LABELS` 的中文标签只用于展示 ✓）
P_KIND = "kind"
P_APP_VERSION = "app_version"
P_FORMAT_VERSION = "format_version"
P_GENERATED_AT = "generated_at"
P_FACTOR_NAME = "factor_name"
P_FACTOR_EXPR = "factor_expression"
P_UNIVERSE = "universe"
P_START_DATE = "start_date"
P_END_DATE = "end_date"
P_HOLD_DAYS = "hold_days"                    # 持仓/调仓周期（交易日）★ 回灌要用
P_LABEL_HORIZON = "label_horizon"            # 预测周期 h
P_REBALANCE_PERIOD = "rebalance_period"      # 调仓期（交易日）
P_COST = "cost"                              # 往返成本（小数，0.004 = 0.4%）★ 回灌要用
P_BENCHMARK = "benchmark"                    # 基准代码（SH000300…）★ 回灌要用
P_BASIS = "basis"                            # 曲线口径：compound（复利）/ arithmetic（算术）
P_QUANTILE = "quantile"                      # 分位档（如 10 = 最强十分位组）
P_TOPK = "topk"                              # 固定 K 档（只数）
P_COST_TIER = "cost_tier"                    # 绩效指标用的成本档（"0.0040"）
P_CAPITAL = "capital"                        # 初始资金（事件研究净值口径）
P_MAX_K = "max_k"                            # 事件研究：最长持有（交易日）
# ★ 成交时点（v2.1.2 加、v2.1.4 更正）：
#   **两套口径要分清** ✗→✓ ——
#   ① 「持仓期/持有期收益曲线」（统计量）是 **T+1 收盘买入** 口径（`close_{T+h+1}/close_{T+1}` ✓，
#      面板标题也写着"T+1 收盘买入" ✓）；
#   ② 「**净值曲线**」（真金白银逐日估值的那条，`signals/engine.run_backtest`）用的是
#      `fill="t1_open"`（次日**开盘**成交 ✓，见 `routers/factors.py` 的调用 ✓）。
#   ⇒ 回灌"复现净值曲线"必须搬 **②** ✓。v2.1.2 时我按 ① 硬编码了 `t1_close` ✗ ⇒ 文件与面板
#     不一致、结果对不上（用户 2026-10-11："难道不该逐位一致？" ✓）⇒ 现在改为**由调用方传入面板实参** ✓。
P_FILL = "fill"
# ★ v2.1.4：净值引擎的另两个实参也必须在文件里（否则"同名方案"下仍是不同回测 ✗）
P_STRICT_LIMIT = "strict_limit"               # 严格涨跌停（true ⇒ 一字板买不进 ✓）
# ★★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径**也必须进文件 ✗→✓
#   为什么：它直接决定"开盘成交时会不会被当日**收盘**涨停挡掉" ✓ ⇒ 同一份信号能跑出两个数 ✓
#   （老口径更保守 ⇒ 买入更少 ⇒ 收益通常更低 ✓）。文件里不写 ⇒ 回灌只能按页面默认跑 ✗
#   ⇒ "导出的 ≠ 复现的" ✗（与 v2.1.54 那次剔除开关同类问题 ✓）。
#   取值：`legacy`（老口径 ✓）/ `open_only`（只拒开盘一字板 ✓）；执行侧见 `signals/engine._limit_ctx` ✓。
P_LIMIT_JUDGE = "limit_judge"
P_REBAL_BAND = "rebal_band"                   # 再平衡死区（面板 event_even 用 0 ✓）
P_CURVE_COST_TIERS = "curve_cost_tiers"      # 曲线含哪几档成本

# ★★ v2.1.54（用户 2026-10-12："要修。估计是这里有问题" ✓）：**剔除开关必须进文件** ✗→✓
#   为什么（实测根因 ✓）：事件研究弹窗的触发组是**剔除了**「信号日涨停 / 成交日涨停 / 成交日停牌 /
#   ST / 创业板 / 科创板」之后的样本 ✓，而导出文件**一个字都没记** ✗ ⇒ 谁把文件拖回「交易信号
#   测试」都只能按**页面默认**跑 ⇒ 触发组多算进"涨停那天触发、次日一字板买不进"的样本 ✗
#   ⇒ 同一份信号跑出两个数（实测 4.84% vs 5.9% ✓，且方向吻合：剔掉涨停样本 ⇒ 均值**抬升** ✓）。
#   ⇒ 现在把它们写进 `_meta`✓，回灌侧按文件里的开关执行 ⇒ "导出的 = 复现的" ✓。
P_EXCLUDE_LIMIT_UP_SIGNAL = "exclude_limit_up_signal"   # 剔除信号日(T)涨停
P_EXCLUDE_LIMIT_UP_TRADE = "exclude_limit_up_trade"     # 剔除成交日(T+1)涨停
P_EXCLUDE_SUSPENDED = "exclude_suspended"               # 剔除成交日(T+1)停牌
P_EXCLUDE_ST_T1 = "exclude_st_t1"                        # 剔除成交日(T+1) ST
P_EXCLUDE_STOCK_GEM = "exclude_stock_gem"               # 剔除创业板
P_EXCLUDE_STOCK_KCB = "exclude_stock_kcb"               # 剔除科创板
P_PRICE_ADJUST = "price_adjust"                         # 复权口径（none/forward/backward）
# ★★ v2.1.74（用户 2026-10-12："你把单因子测试每个参数都仔细检查一下…导出的时候有没有漏掉参数，
#   或者信号测试里有没有漏掉" ✓ —— 审计 `ai_test` 结果确认这几项**只写在人读页、没进 `_meta`** ✗
#   ⇒ 文件不自足 ⇒ 回灌只能按页面默认跑 ✗）：
P_PRICE_ROUND = "price_round"                           # 价格整分（**仅不复权生效** ✓）
P_SUSPEND_REMOVE = "suspend_remove"                     # 停牌行语义（True=删行 / False=NaN 占位）
P_FREEZE_SUSPENDED = "freeze_suspended_price"           # 停牌日价格冻结计入未来收益
P_WARMUP_DAYS = "warmup_days"                           # 特征预热缓冲（交易日）

# 英文键 → 中文标签（**第 1 页参数块的表头**；回灌兜底也用它反查 ✓）
PARAM_LABELS: Dict[str, str] = {
    P_KIND: "导出模块",
    P_APP_VERSION: "工具版本",
    # ★ v2.1.54：剔除开关（决定**触发组样本**⇒ 直接改变事件研究的数字 ✓）
    P_EXCLUDE_LIMIT_UP_SIGNAL: "剔除信号日涨停",
    P_EXCLUDE_LIMIT_UP_TRADE: "剔除成交日涨停",
    P_EXCLUDE_SUSPENDED: "剔除成交日停牌",
    P_EXCLUDE_ST_T1: "剔除成交日ST",
    P_EXCLUDE_STOCK_GEM: "剔除创业板",
    P_EXCLUDE_STOCK_KCB: "剔除科创板",
    P_PRICE_ADJUST: "复权口径",
    # ★ v2.1.74：补齐"影响计算但过去只在人读页"的几项 ✓（用户 2026-10-12 要求逐项检查 ✓）
    P_PRICE_ROUND: "价格整分（仅不复权）",
    P_SUSPEND_REMOVE: "停牌删行",
    P_FREEZE_SUSPENDED: "停牌价冻结计入收益",
    P_WARMUP_DAYS: "预热缓冲(交易日)",
    P_FORMAT_VERSION: "格式版本",
    P_GENERATED_AT: "导出时间",
    P_FACTOR_NAME: "因子名称",
    P_FACTOR_EXPR: "因子公式",
    P_UNIVERSE: "股票池",
    P_START_DATE: "起始日期",
    P_END_DATE: "结束日期",
    P_HOLD_DAYS: "持仓周期(交易日)",
    P_LABEL_HORIZON: "预测周期h",
    P_REBALANCE_PERIOD: "调仓期(交易日)",
    P_COST: "成本(往返)",
    P_BENCHMARK: "基准",
    P_BASIS: "曲线口径",
    P_QUANTILE: "分位档",
    P_TOPK: "固定K(只数)",
    P_COST_TIER: "绩效成本档",
    P_CAPITAL: "初始资金",
    P_MAX_K: "最长持有(交易日)",
    P_FILL: "成交时点",
    P_STRICT_LIMIT: "严格涨跌停",
    # ★ T1（v2.8.6）：判停口径也要在参数页写明 ✓（回灌才知按哪一口径复现 ✓）
    P_LIMIT_JUDGE: "涨停判定口径",
    P_REBAL_BAND: "再平衡死区",
    P_CURVE_COST_TIERS: "曲线成本档",
}
# 标签 → 英文键（兜底解析用 ✓）
_LABEL_TO_KEY: Dict[str, str] = {v: k for k, v in PARAM_LABELS.items()}

# 需要按"小数"展示成百分比的参数（人读友好；JSON 里仍是原值 ✓）
_PERCENT_KEYS = frozenset({P_COST})


def item_key(kind: str, k: Any = None, quantile: Any = None) -> str:
    """持仓索引里**一档曲线**的键（写侧/读侧**共用** ⇒ 不许各自拼字符串 ✗）。

    例：`item_key("topk", 50)` → `"topk:50:"`、`item_key("decile", 0, 10)` → `"decile:0:10"` ✓
    """
    return "%s:%s:%s" % (
        kind,
        "" if k is None else int(k),
        "" if quantile is None else int(quantile),
    )


# ---------------------------------------------------------------- 当前数据集（★ 2026-10-14 ✓）
K_DATASET = "dataset"                    # `_meta` 行：数据集目录名（cn_data / cn_data2 …）
K_DATASET_LABEL = "dataset_label"        # `_meta` 行：人读口径（如 `cn_data（米筐/qlib 口径）`）


def dataset_tag() -> Dict[str, str]:
    """读**当前生效**的数据集（`data/active_dataset.json` 解析结果 ✓）⇒ 写进导出文件 ✓。

    Why（用户 2026-10-14："**EXCEL 里好像分辨不出是米筐还是 TUSHARE**" ✗）：导出文件的
    `_meta.params_json` 有 26 个参数却**一个都不提数据集** ✗ ⇒ **米筐与 tushare 两份导出长得一模一样** ✗，
    回头对照"今天结果为什么高"时**第一步就卡住**（无法判断手上这份是哪个源 ✓）。
    ∴ ① 隐藏页 `_meta` 加 `dataset` / `dataset_label` 两行（机器可读 ✓）；
      ② 第 1 页小灰字标题行追加「数据集 X（口径）」（人读 ✓）；
      ③ 回灌时若文件的数据集与当前不一致 ⇒ 记一条 `warning` ✓（不阻断 ✓）。
    口径名来自数据集目录里的 `.dataset.json`（`name`/`label`/`convention` ✓，与 `md/数据源接入.md` 同源 ✓）。
    """
    out = {"name": "", "label": "", "convention": ""}
    try:
        import os
        from ..config import QLIB_PROVIDER_URI                     # 本地 import ✓（避免环）
        d = str(QLIB_PROVIDER_URI or "")
        out["name"] = os.path.basename(d.rstrip("/\\"))
        p = os.path.join(d, ".dataset.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
            out["label"] = str(j.get("label") or "")
            out["convention"] = str(j.get("convention") or "")
    except Exception:                                              # noqa: BLE001
        pass
    return out


def dataset_line() -> str:
    """人读一行：`数据集 cn_data（米筐/qlib 口径）` ✓（拿不到就退化成 `数据集 -` ✓）。"""
    t = dataset_tag()
    if not t["name"]:
        return "数据集 -"
    return "数据集 %s（%s）" % (t["name"], t["label"] or t["convention"] or "口径未标注")


# ---------------------------------------------------------------- 超长文本（★ 2026-10-14 ✓）
CELL_MAX = 32767                     # Excel 单元格**硬上限** ✓（超了 openpyxl 会**静默截断** ✗）
CELL_SAFE = 30000                    # 我们自己截（留余量 ✓ ⇒ 能**在文本里注明**已截断 ✓）
META_BUDGET = 32000                  # `params_json` 的写入预算 ✓
META_VALUE_CAP = 4000                # 单个字符串值在 `_meta` 里的软上限 ✓


def short_text(s: Any, limit: int = CELL_SAFE, *, where: str = "因子库／数据文件") -> str:
    """超长就截断**并打标** ✓（不静默 ✗）—— 旧行为是被 Excel 静默截到 32767 ✗。

    实测触发点（2026-10-14 ✓）：`AR13_ID60` 的表达式 **92,933 字符** ✗ ⇒ 导出文件里第 1 页的
    「原文公式」正好 32767 字符 ✓（= 单元格上限 ✓）⇒ 谁拿它去复现**必然对不上** ✗。
    """
    t = "" if s is None else str(s)
    if len(t) <= limit:
        return t
    mark = "……（已截断：原始 %d 字符，完整文本见%s）" % (len(t), where)
    return t[: max(0, limit - len(mark))] + mark


def trim_params_for_meta(params: Dict[str, Any], *,
                         budget: int = META_BUDGET,
                         cap: int = META_VALUE_CAP) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """把超长 `params` 裁成**能写进 `_meta` 且尽量可用** ✓：返回 (参数, 被截断的键, 被丢掉的键) ✓。

    ① 超长字符串值 ⇒ `short_text` 截断打标 ✓；② 仍超预算 ⇒ 从**最长的键**开始丢 ✓（记名 ✓）。
    """
    p: Dict[str, Any] = dict(params)
    trimmed: List[str] = []
    for k, v in list(p.items()):
        if isinstance(v, str) and len(v) > cap:
            p[k] = short_text(v, cap)
            trimmed.append(k)
    dropped: List[str] = []
    # ★ 丢弃顺序 ✓：**先丢"非契约键"**（批量/临时字段 ✓），契约键（`PARAM_LABELS` 里的 ✓）留到最后 ✓
    #   —— 否则可能把 `factor_expression` 这类关键字段丢掉 ✗（2026-10-14 实测踩到 ✓，单测锁住 ✓）。
    contract = set(PARAM_LABELS)
    while len(json.dumps(p, ensure_ascii=False, default=str)) > budget and p:
        cand = [k for k in p if k not in contract] or list(p)
        k = max(cand, key=lambda kk: len(json.dumps(p[kk], ensure_ascii=False, default=str)))
        dropped.append(k)
        del p[k]
    return p, trimmed, dropped


def formula_cells(source_formula: Any, expression: Any,
                  *, where: str = "因子库／数据文件") -> List[Tuple[str, str, Any]]:
    """第 1 页的「原文公式 / 具体公式（表达式）」两格 ✓ + 超长时的说明行 ✓（两处导出共用 ✓）。

    为什么要共用：单因子测试（`topk_doc.py`）与事件研究（`routers/export_xlsx.py`）**都要写**这两格 ✓，
    旧代码在两边各写一遍 ✗ ⇒ 一处修一处漏 ✗（2026-10-14 的截断问题正是两边都有 ✓）。
    """
    src = "" if source_formula is None else str(source_formula)
    expr = "" if expression is None else str(expression)
    rows: List[Tuple[str, str, Any]] = [
        ("原文公式", short_text(src or expr or "-", where=where), None),
        ("具体公式（表达式）", short_text(expr or "-", where=where), None),
    ]
    longest = max(len(src), len(expr))
    if longest > CELL_SAFE:
        rows.append(("公式长度（★ 已截断 ✗）",
                     "%d 字符 ⇒ 上面两格只展示前 %d 字符 ✓；**完整表达式见%s** ✓"
                     "（导出文件的单元格上限只有 %d ✗）" % (longest, CELL_SAFE, where, CELL_MAX), None))
    return rows


# ---------------------------------------------------------------- 写：隐藏元数据页
def write_meta_sheet(wb, params: Dict[str, Any]) -> None:
    """把**机器可读**元数据写进隐藏 sheet `_meta`（回灌唯一权威来源 ✓）。

    ⚠ 单元格上限 32767 字符：参数 JSON 正常只有几百字节 ✓；超长时**先截断长字段再写** ✓
      （`params_json_trimmed` / `params_json_dropped` 记明动了哪些键 ✓）；真写不下才退回
      `params_json_truncated=True` ⇒ 回灌按中文标签兜底 ✓（**一切都不静默** ✗→✓，见 2026-10-14 修 ✓）。
    """
    ws = wb.create_sheet(SHEET_META)
    ws["A1"] = FORMAT_ID
    ws["B1"] = "格式标识"
    rows: List[Tuple[str, Any]] = [
        (P_FORMAT_VERSION, FORMAT_VERSION),
        (P_KIND, params.get(P_KIND, "")),
        (P_APP_VERSION, params.get(P_APP_VERSION, "")),
        (P_GENERATED_AT, params.get(P_GENERATED_AT, "")),
    ]
    # ★ 数据集（2026-10-14 ✓）：写进 `_meta` ⇒ 米筐/tushare 两份导出**一眼可辨** ✓
    _ds = dataset_tag()
    rows.append((K_DATASET, _ds["name"]))
    rows.append((K_DATASET_LABEL, dataset_line()))
    # ★★ 2026-10-14 修（用户："小毛病改一下" ✓）—— 超长**不再整份丢弃** ✗→✓：
    #   旧行为：`payload > 32000` ⇒ 只写 `params_json_truncated=True` ✗ ⇒ 回灌**一个参数都读不到** ✗
    #   （实测 `AR13_ID60` 的导出就是这样 ✓：`factor_expression` **92,933 字符** ✗）。
    #   新行为：① 超长的**字符串值**先**截断并打标** ✓（`…（已截断：原始 N 字符，完整见因子库）` ✓）；
    #          ② 还超就**从最长的键开始丢** ✓ 并把丢掉的键名落进 `params_json_dropped` ✓；
    #          ③ 两行都写出来 ⇒ 回灌**能用** ✓ 且**知道少了什么** ✓（不静默 ✗）。
    trimmed_params, trimmed_keys, dropped_keys = trim_params_for_meta(params)
    payload = json.dumps(trimmed_params, ensure_ascii=False, default=str)
    if len(payload) <= 32000:
        rows.append(("params_json", payload))
        if trimmed_keys:
            rows.append(("params_json_trimmed", ",".join(trimmed_keys)))
        if dropped_keys:
            rows.append(("params_json_dropped", ",".join(dropped_keys)))
    else:                                                          # 兜底（正常走不到 ✓）
        rows.append(("params_json_truncated", True))
    for i, (k, v) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=k)
        ws.cell(row=i, column=2, value=v)
    ws.sheet_state = "hidden"


# ---------------------------------------------------------------- 读：回灌用
@dataclass
class ExportDoc:
    """读回的导出文档（回灌侧的**唯一入参**）。"""

    kind: str = ""
    format_id: str = ""
    format_version: str = ""
    app_version: str = ""
    generated_at: str = ""
    dataset: str = ""                                             # ★ 2026-10-14：导出时的数据集 ✓
    dataset_label: str = ""                                       #   人读口径（如 `cn_data（米筐/qlib 口径）`✓）
    params: Dict[str, Any] = field(default_factory=dict)          # 英文键 → 值（权威 ✓）
    params_labeled: Dict[str, Any] = field(default_factory=dict)   # 中文标签 → 值（回显用）
    holdings: List[Dict[str, Any]] = field(default_factory=list)   # 每行：英文键 → 值
    holding_labels: Tuple[str, ...] = ()                           # 实际读到的中文表头
    # ★ v2.1.3：**真实买入信号**（隐藏页 `_signals` ✓；老文件为空 ⇒ 回灌侧退回"快照差集" ✓）
    signals: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.kind)

    def p(self, key: str, default: Any = None) -> Any:
        """取参数（英文键；缺失/空串 ⇒ default ✓）。"""
        v = self.params.get(key, None)
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        return v

    def n_periods(self) -> int:
        """持仓明细覆盖的**期数**（按调仓日去重 ✓，回灌侧用来核对"每期都有持仓"）。"""
        return len({str(h.get("rebalance_date") or "") for h in self.holdings if h.get("rebalance_date")})


def _cell_str(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _as_bytes(src: Any) -> Any:
    """path / bytes / file-like ⇒ openpyxl 可吃的输入（bytes 用 BytesIO ✓）。"""
    if isinstance(src, (bytes, bytearray)):
        from io import BytesIO

        return BytesIO(bytes(src))
    return src


def read_export(src: Any, *, expect_kind: Optional[str] = None) -> ExportDoc:
    """读回**我们自己导出**的 XLSX（`src` = 路径 / bytes / 文件对象 ✓）。

    `expect_kind`：调用方期望的模块（如 `KIND_TOPK`）；不匹配时**不抛异常**，只记 warning ✓
    （用户可能把事件研究的文件拖进"复现持仓曲线"入口 ⇒ 由调用方据 `kind` 给可读提示 ✓）。
    """
    from openpyxl import load_workbook

    doc = ExportDoc(source=str(src)[:200] if not isinstance(src, (bytes, bytearray)) else "<bytes>")
    wb = load_workbook(_as_bytes(src), read_only=True, data_only=True)
    try:
        # ---------- ① 隐藏元数据页（权威）----------
        meta = wb[SHEET_META] if SHEET_META in wb.sheetnames else None
        params_json = ""
        if meta is not None:
            for row in meta.iter_rows(min_row=1, max_col=2, values_only=True):
                key, val = (_cell_str(row[0]), row[1] if len(row) > 1 else None)
                if key == "format_id" or (key == FORMAT_ID):
                    doc.format_id = FORMAT_ID
                elif key == P_FORMAT_VERSION:
                    doc.format_version = _cell_str(val)
                elif key == P_KIND:
                    doc.kind = _cell_str(val)
                elif key == P_APP_VERSION:
                    doc.app_version = _cell_str(val)
                elif key == P_GENERATED_AT:
                    doc.generated_at = _cell_str(val)
                elif key == "params_json":
                    params_json = _cell_str(val)
                elif key == "params_json_truncated":
                    doc.warnings.append("导出文件的参数元数据被截断 ⇒ 已退回按中文标签解析")
                elif key == "params_json_trimmed":                 # ★ 2026-10-14 ✓
                    doc.warnings.append(
                        "导出文件的参数里有**超长字段被截断** ✗（`%s` ✓，值结尾带"
                        "「已截断…完整文本见因子库」标记 ✓）⇒ 要用原值请回因子库取 ✓" % _cell_str(val))
                elif key == "params_json_dropped":
                    doc.warnings.append(
                        "导出文件的参数里**有字段因过长被丢弃** ✗（`%s` ✓）⇒ 回灌时这些参数走默认值 ✓，"
                        "别当成\"本来就该这样\" ✓" % _cell_str(val))
                elif key == K_DATASET:                             # ★ 2026-10-14 ✓
                    doc.dataset = _cell_str(val)
                elif key == K_DATASET_LABEL:
                    doc.dataset_label = _cell_str(val)
        # ★ 数据集对不上就**报一声** ✗→✓（不阻断 ✓）：一份 tushare 的导出拿到米筐上回灌，
        #   数值必然对不上 ✗ ⇒ 别让人以为是算法变了 ✓（2026-10-14 的教训 ✓）。
        _cur = dataset_tag()["name"]
        if doc.dataset and _cur and doc.dataset != _cur:
            doc.warnings.append(
                "导出文件来自数据集 **%s** ✓，当前生效的是 **%s** ✗ ⇒ 数值不会逐位一致 ✓"
                "（要复现请先切回 `%s` ✓）" % (doc.dataset, _cur, doc.dataset))
        if not doc.format_id:
            doc.warnings.append("未找到隐藏页 `%s`（不是本工具导出的文件，或隐藏页被删）" % SHEET_META)

        # ---------- ② 参数页（兜底解析 + 人读标签）----------
        ws1 = wb[SHEET_PARAMS] if SHEET_PARAMS in wb.sheetnames else None
        if ws1 is not None:
            # ⚠ 参数块是**一行两组/多组**（`xl.SheetWriter.kv(per_row=2)`：单元格 A/B + D/E …）
            #   ⇒ 不能只看 A/B 两列 ✗（实测：`benchmark` 落在第二组 ⇒ 兜底读不到 ✓ 被抓出来）。
            #   这里扫整行：遇到**认识的中文标签**就把紧跟其后的非空单元格当值 ✓（与布局无关 ✓）。
            for row in ws1.iter_rows(min_row=1, max_col=12, values_only=True):
                for i, cell in enumerate(row):
                    label = _cell_str(cell)
                    if label not in _LABEL_TO_KEY:
                        continue
                    for j in range(i + 1, len(row)):
                        if row[j] is not None and _cell_str(row[j]) != "":
                            doc.params_labeled[label] = _num(row[j])
                            break
            # 兜底：无 JSON（或又截断）⇒ 从中文标签块还原英文键 ✓
            if not params_json:
                for label, val in list(doc.params_labeled.items()):
                    doc.params[_LABEL_TO_KEY[label]] = val
                if doc.params_labeled:
                    doc.warnings.append("参数取自中文标签块（隐藏页缺失）⇒ 标签被改过就可能取错")

        if params_json:
            try:
                loaded = json.loads(params_json)
                if isinstance(loaded, dict):
                    doc.params.update(loaded)
                else:
                    doc.warnings.append("隐藏页参数不是 JSON 对象 ⇒ 已忽略")
            except Exception as e:                                   # noqa: BLE001
                doc.warnings.append("隐藏页参数 JSON 解析失败：%r" % (e,))

        # ---------- ③ 持仓页 ----------
        ws2 = wb[SHEET_HOLDINGS] if SHEET_HOLDINGS in wb.sheetnames else None
        if ws2 is None:
            doc.warnings.append("未找到 `%s` 页 ⇒ 无法复现持仓（请用本工具导出的文件）" % SHEET_HOLDINGS)
        else:
            _read_holdings(ws2, doc)

        # ---------- ④ 买入信号（隐藏页；v2.1.3 ✓）----------
        # ⚠ 有它就用它（**准确** ✓）：第 2 页是"每期持仓快照"，把它当信号会重复买入 ✗
        #   （实测 event_even：8338 个快照对 vs 真实新买入 708 ⇒ 重复 11.8× ✗）。
        #   老文件（v2.1.3 之前）没有这页 ⇒ 回灌侧退回"相邻期差集"并**如实提示** ✓。
        if SHEET_SIGNALS in wb.sheetnames:
            _read_signals(wb[SHEET_SIGNALS], doc)

        # 模块类型：隐藏页（权威）→ 参数块「导出模块」（兜底）→ 持仓表头形状（最后兜底 ✓）
        if not doc.kind:
            doc.kind = _guess_kind(doc)
    finally:
        try:
            wb.close()
        except Exception:                                            # noqa: BLE001
            pass
    if expect_kind and doc.kind and doc.kind != expect_kind:
        doc.warnings.append("文件来自「%s」模块，与当前入口（%s）不同" % (doc.kind, expect_kind))
    return doc


def _guess_kind(doc: ExportDoc) -> str:
    """没有 `_meta` 时按持仓表头猜模块（中文表头是契约的一部分 ✓）。"""
    labels = set(doc.holding_labels)
    for kind, cols in HOLDING_COLS.items():
        zh = {zh for _, zh in cols}
        if labels and labels <= zh:
            return kind
    return ""


def _num(v: Any) -> Any:
    """单元格值归一：数字保持数字、字符串去空白（Excel 里"0.004"这种参数很常见 ✓）。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    s = _cell_str(v)
    if not s:
        return s
    try:
        f = float(s)
    except ValueError:
        return s
    return int(f) if f.is_integer() and "." not in s and "e" not in s.lower() else f


# ---------------------------------------------------------------- 写/读：隐藏页「买入信号」
SIGNAL_COLS: Tuple[Tuple[str, str], ...] = (("date", "日期"), ("code", "代码"),
                                          ("mode", "资金方案"), ("bucket", "批次"))


def write_signals_sheet(wb, rows: Sequence[Dict[str, Any]]) -> None:
    """把**真实买入信号**写进隐藏 sheet `_signals`（回灌复现净值曲线的权威来源 ✓）。

    ⚠ 与第 2 页「持仓明细」的区别（**这是 v2.1.3 修的那个坑** ✓）：持仓页是"每期快照"，
      同一只票在它持有的每个调仓日都会出现 ⇒ 把快照当信号 = **重复买入**（实测 11.8× ✗）；
      而这里只写"**这一天才真正买进**"的事件 ✓。
    """
    if not rows:
        return
    ws = wb.create_sheet(SHEET_SIGNALS)
    ws.append([zh for _k, zh in SIGNAL_COLS])
    for r in rows:
        ws.append([r.get(k) for k, _zh in SIGNAL_COLS])
    ws.sheet_state = "hidden"


def _read_signals(ws, doc: ExportDoc) -> None:
    """读隐藏页 `_signals`（按**中文表头**反查 ⇒ 列序无关 ✓；坏行跳过 ✓）。"""
    zh2key = {zh: k for k, zh in SIGNAL_COLS}
    rows = ws.iter_rows(values_only=True)
    header: Optional[Sequence[Any]] = None
    for row in rows:
        if row and any(_cell_str(c) for c in row):
            header = row
            break
    if header is None:
        return
    idx = [(i, zh2key[zh]) for i, zh in enumerate(header) if _cell_str(zh) in zh2key]
    if not idx:
        doc.warnings.append("买入信号页表头不匹配（期望：%s）"
                            % "、".join(zh for _k, zh in SIGNAL_COLS))
        return
    for row in rows:
        if not row or not any(_cell_str(c) for c in row):
            continue
        rec = {k: _cell_str(row[i]) for i, k in idx if i < len(row)}
        if rec.get("date") and rec.get("code"):
            doc.signals.append(rec)


def _read_holdings(ws, doc: ExportDoc) -> None:
    """读持仓页：按**中文表头**反查英文键（列顺序/多余的列都不影响 ✓）。"""
    rows = ws.iter_rows(values_only=True)
    header: Optional[Sequence[Any]] = None
    for row in rows:
        if row and any(_cell_str(c) for c in row):
            header = row
            break
    if header is None:
        doc.warnings.append("持仓页为空 ⇒ 没有可复现的持仓记录")
        return
    labels = [_cell_str(c) for c in header]
    # 表头可能带 `kind` 前缀映射（契约同版本 ⇒ 直接用 HOLDING_COLS 反查 ✓）
    zh2key: Dict[str, str] = {}
    for cols in HOLDING_COLS.values():
        for key, zh in cols:
            zh2key.setdefault(zh, key)
    idx: List[Tuple[int, str]] = [(i, zh2key[zh]) for i, zh in enumerate(labels) if zh in zh2key]
    unknown = [zh for zh in labels if zh and zh not in zh2key]
    if unknown:
        doc.warnings.append("持仓页有未识别的列（已忽略）：%s" % "、".join(unknown))
    if not idx:
        doc.warnings.append("持仓页表头不匹配（期望：%s）"
                            % "、".join(zh for kind in HOLDING_COLS for _, zh in HOLDING_COLS[kind]))
        return
    doc.holding_labels = tuple(labels[i] for i, _ in idx)
    for row in rows:
        if not row or not any(_cell_str(c) for c in row):
            continue
        rec = {}
        for i, key in idx:
            if i < len(row):
                rec[key] = _num(row[i])
        if any(_cell_str(v) for v in rec.values()):
            doc.holdings.append(rec)


# ---------------------------------------------------------------- 人读辅助
def param_display_rows(params: Dict[str, Any]) -> List[Tuple[str, Any, Optional[str]]]:
    """参数 → 第 1 页参数块的行：`(中文标签, 值, 数字格式)`（多一个"人读友好"格式 ✓）。"""
    out: List[Tuple[str, Any, Optional[str]]] = []
    for key, label in PARAM_LABELS.items():
        if key not in params or params.get(key) in (None, ""):
            continue
        val = params[key]
        fmt = "0.0000" if key in _PERCENT_KEYS else None
        if isinstance(val, (list, tuple)):
            val = "、".join(str(x) for x in val)
        out.append((label, val, fmt))
    return out
