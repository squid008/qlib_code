# -*- coding: utf-8 -*-
"""**每期持仓明细索引**（供 XLSX 导出用；**不进 HTTP 响应** ✓）。

★ 为什么单独一个模块：
  · `single_test.py` 已 1700+ 行 ✗，而这段逻辑（"名次 → 逐期**有序**代码表" + 上限控制）
    与统计无关、只服务「导出每期持仓」，独立出来便于单测 ✓；
  · 单因子测试的持仓（调仓日 → 当日按名次排序的代码清单）**算完即丢** ✗ —— 而导出要
    "选的什么档位就导什么持仓"，且导出时**不能重跑测试**（几十秒 ✗）
    ⇒ 测试时顺手存一份索引、导出时按档位零重算取用 ✓（随任务结果一起回收 ✓）。

★ 与 `exports.spec` 的关系：**档位键**用 `exports.spec.item_key()` 生成 ⇒ 写/读两侧
  永远同一个键 ✓（这里不自己拼字符串 ✗）。方向只做记录 ✓。

上限（防"多因子 × 多 K 档"把内存顶起来 ✗）：单项每期最多 `MAX_CODES_PER_PERIOD` 只；
每任务最多 `MAX_CODES_PER_TASK` 个代码（超了**不再存**并置 `truncated` ⇒ 导出时如实
告知"本次因子太多，请单独重跑该因子" ✓，而不是静默给半份持仓 ✗）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..exports.spec import item_key

MAX_CODES_PER_PERIOD = 2000      # 单项每期上限（前端 K 预设最大 20% ⇒ 全 A 约 1000 只 ✓ 留余量）
MAX_CODES_PER_TASK = 300_000     # 每任务代码总数上限（约 3~4 MB 字符串 ⇒ 可接受 ✓）
MAX_ITEMS_PER_FACTOR = 12        # 与 `topk_curves` 的档位数上限一致（默认档 + 11 个 K ✓）


def ordered_holdings(dates: Sequence[Any], instruments: Sequence[Any],
                     ranks: Sequence[float], reb_dates: Sequence[Any],
                     max_codes: int = MAX_CODES_PER_PERIOD) -> List[List[str]]:
    """「(日期, 代码, 名次) 三元组」→ **每个调仓日的代码清单（名次升序 ✓）**。

    只对**调仓日**那一小片数据排序（每期 ≤ K 只，几十期 × 几百只 ≈ 1 万行 ✓），
    不对整个样本（几十万行 ✗）排序 —— 这是能"顺手存一份"的关键 ✓。

    `dates/instruments/ranks` 取自该档位的样本（名次 ≤ K 的全部行 ✓）；
    输出与 `reb_dates` **等长同序**（某期无样本 ⇒ 空列表 ✓）。
    名次相同时按代码定序 ⇒ **跨次运行结果稳定** ✓（集合迭代序不稳定 ✗ 不能直接存）。
    """
    out: List[List[str]] = [[] for _ in reb_dates]
    if not len(dates) or not len(reb_dates):
        return out
    # ⚠⚠ 日期对齐**必须走 `pd.to_datetime` 归一**：样本索引给的是 `datetime64[ns]`
    #   （`str()` = `2024-01-02T00:00:00.000000000`），而调仓日给的是 `Timestamp`
    #   （`str()` = `2024-01-02 00:00:00`）⇒ 直接 `str()` 比较**一期都匹配不上** ✗
    #   （v2.1.0 实测踩到：第 2 页空表，而且因为"取到了空清单"连告警都不触发 ✗）。
    d = pd.to_datetime(np.asarray(dates).ravel()).to_numpy()
    rd = pd.to_datetime(np.asarray(reb_dates).ravel()).to_numpy()
    keep = np.isin(d, rd)                      # 向量化命中（只有调仓日那几片留下 ✓）
    if not keep.any():
        return out
    inst = np.asarray(instruments, dtype=object)[keep].astype(str)
    rank = np.asarray(ranks, dtype=np.float64)[keep]
    dts = d[keep]
    order = np.lexsort((inst, rank, dts))      # 主键 日期、次键 名次、再次键 代码 ✓
    inst, dts = inst[order], dts[order]
    # `dts` 已非降 ⇒ 切成"同一天的连续段"，再按 `searchsorted` 落到对应调仓日 ✓
    # （`rd` 升序、且每段的值必属于 `rd` ⇒ 一次二分定位 ✓，不用逐行 dict 查找 ✗）
    change = np.flatnonzero(dts[1:] != dts[:-1]) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [dts.size]))
    cap = int(max_codes)
    for s, e in zip(starts.tolist(), ends.tolist()):
        # ⚠ 用"取值相等"定位而不是 `searchsorted`：后者**默认 `rd` 升序**，而这里 `rd` 就是
        #   调用方给的调仓日顺序（`out` 必须与之同序 ✓）⇒ 顺序不满足时会**静默错位** ✗。
        #   `rd` 只有几十~几百个元素 ⇒ 每次线性比较的开销可忽略 ✓。
        out[int(np.flatnonzero(rd == dts[s])[0])] = inst[s:e][:cap].tolist()
    return out


@dataclass
class HoldingsIndex:
    """每任务一份的持仓索引（`factors: {表达式: {周期: {dates/side/items}}}` ✓）。"""

    factors: Dict[str, Any] = field(default_factory=dict)
    used_codes: int = 0
    truncated: bool = False

    # ------------------------------------------------------------ 写（测试进程内）
    def put(self, expression: str, horizon: Any, side: str,
            reb_str: Sequence[str],
            items: Dict[Tuple[str, Any, Any], List[List[str]]]) -> bool:
        """存一批档位的持仓；**超上限返回 False 并置 `truncated`** ✓。

        `items` 键 = `(kind, k, quantile)`（如 `("topk", 50, None)`）⇒ 这里统一转契约键 ✓。
        """
        bucket = self.factors.setdefault(str(expression), {}).setdefault(str(horizon), {})
        if not bucket:
            bucket.update(dates=[str(x) for x in reb_str], side=str(side), items={})
        budget = MAX_CODES_PER_TASK - self.used_codes
        stored: Dict[str, List[List[str]]] = {}
        ok = True
        for i, ((kind, k, quantile), per_period) in enumerate(items.items()):
            if i >= MAX_ITEMS_PER_FACTOR or sum(len(x) for x in per_period) > budget:
                ok = False
                break
            need = sum(len(x) for x in per_period)
            stored[item_key(kind, k, quantile)] = per_period
            budget -= need
            self.used_codes += need
        bucket["items"].update(stored)
        if not ok:
            self.truncated = True
        return ok

    # ------------------------------------------------------------ 读（导出路由）
    def get(self, expression: str, horizon: Any, kind: str, k: Any,
            quantile: Any = None) -> Optional[List[List[str]]]:
        """取某档位的逐期持仓（缺 ⇒ None ✓，调用方据此给可读提示 ✓）。"""
        bucket = (self.factors.get(str(expression)) or {}).get(str(horizon)) or {}
        return (bucket.get("items") or {}).get(item_key(kind, k, quantile))

    def dates(self, expression: str, horizon: Any) -> List[str]:
        bucket = (self.factors.get(str(expression)) or {}).get(str(horizon)) or {}
        return list(bucket.get("dates") or [])

    def has(self, expression: str = "", horizon: Any = None) -> bool:
        if not expression:
            return bool(self.factors)
        return bool((self.factors.get(str(expression)) or {}).get(str(horizon)))

    # ------------------------------------------------------------ 与任务状态互转
    def as_state(self) -> dict:
        """交给任务状态（导出路由按此读 ⇒ 结构变更只影响这两个方法 ✓）。"""
        return {"factors": self.factors, "truncated": bool(self.truncated)}

    @classmethod
    def from_state(cls, state: Optional[dict]) -> "HoldingsIndex":
        st = state or {}
        return cls(factors=dict(st.get("factors") or {}), truncated=bool(st.get("truncated")))
