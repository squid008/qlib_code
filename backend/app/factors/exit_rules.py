# -*- coding: utf-8 -*-
"""入场价类离场规则（v2.6.0，用户 2026-10-15 定稿 ✓）。

**用户要的写法**（原话 ✓）——「固定止损我会写在公式里编译保存」：
    止损: CLOSE < ENTRY_PRICE*0.95;
    止盈: CLOSE >= ENTRY_PRICE*1.10;
其中（★ 2026-10-15 用户拍板的口径 ✓）：

| 字段 | 语义 | 加减仓时 |
|---|---|---|
| `ENTRY_PRICE`（入场价） | **移动平均成本**（含买入费 ⇒ = 券商"成本价" ✓） | **买入会变**（摊平/加仓 ✓）；**卖出不变** ✓ |
| `ENTRY_FIRST`（首次入场价） | **首次建仓那一笔**的每股成本（含费 ✓；清仓后再买重新起算 ✓） | 恒定 ✓ |

为什么这口径：01 信号下"买一次、重复信号只刷新持有天数"⇒ 平均成本**自动**等于那次买入价 ✓
（`engine.py` 重复信号不加仓 ✓）；连续信号下它就是"加减仓之后的平均" ✓ —— 一套口径两边都成立 ✓。
⚠ "最近一次买入价"**刻意不做** ✗：连续信号每期等权重建总有小额加仓 ⇒ 它会经常≈现价 ⇒
止损线贴着现价、下一根就触发 ✗（用户 2026-10-15 讨论确认 ✓）。

**为什么要有本模块**：这两个字段是**回测状态量**（取决于你实际在哪根成交 ✗），不能像 `$close`
那样预先算成矩阵 ✗ ⇒ 这里把它编译成「**逐日、按标的向量求值**」的规则 ✓，由
`signals/period_roll.py` 在回测里喂「当日的 C/O/H/L 行 + 当前入场价向量」✓（成本 O(T×C) 的向量
运算 ✓，量级与引擎既有的逐日向量工作一致 ✓）。

⚠ **v1 支持面**（不支持的写法**明确报错**，绝不静默算错 ✗）：
  · 允许：`C/O/H/L`（含 CLOSE/OPEN/HIGH/LOW ✓）、`ENTRY_PRICE`/`ENTRY_FIRST`、四则运算、
    比较、`And/Or/Not/If`、逐元素函数 `Max/Min/Abs/Round/Sgn/Int/Trunc/Between/Pow/Sqrt/Exp/Log/Ln/Mod` ✓；
  · **不允许**：`MA/EMA/REF/Slope/BARSLAST/...`（要历史窗口 ⇒ 无法逐日只靠当日行算 ✗）——
    请把它们写进**另一条不含入场价的**离场公式、与这条**同时勾选** ✓
    （多选就是为此留的 ✓；⚠ 两条公式之间是 **OR** 关系 ✓）。
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from .panel_expr import Node, parse_expr
from .recursion import _nan0

#: 两个"入场价"字段 → 取值下标（`0` = 平均成本 ✓、`1` = 首次入场 ✓）
ENTRY_FIELDS = {"$entry_price": 0, "$entry_first": 1}
#: 允许直接参与运算的行情字段（行向量名见 `signals/period_roll.py` 的 `rows` ✓）
# ⚠ v1 **只放 C/O** ✓ —— 回测面板（`load_price_panel(need_open=True)`）现成只有复权收盘 +
#   真实开/收 ✓，没有 H/L/V ✗ ⇒ 宁可**明确报错**，也不去猜一个可能取不到的列 ✗
#   （要扩就把 H/L 也取进面板，再往这里加 ✓）。
_PRICE_FIELDS = {"$close": "C", "$open": "O"}
#: 逐元素函数白名单（语义与面板/qlib 侧保持一致 ✓）
_FUNCS = {
    "Max": np.maximum, "Min": np.minimum, "Abs": np.abs, "Round": np.round,
    "Trunc": np.trunc, "Sgn": np.sign, "Int": np.trunc,
    "Pow": np.power, "Sqrt": np.sqrt, "Exp": np.exp, "Log": np.log, "Ln": np.log,
    "Mod": np.mod,
}
_CMP = {"Gt": np.greater, "Ge": np.greater_equal, "Lt": np.less,
        "Le": np.less_equal, "Eq": np.equal, "Ne": np.not_equal}
_ARITH = {"Add": np.add, "Sub": np.subtract, "Mul": np.multiply, "Div": np.divide}


class UnsupportedExitError(ValueError):
    """离场公式用了 v1 不支持的写法 ⇒ 报**能自助**的错 ✓（见模块 docstring 的支持面 ✓）。"""


def uses_entry(expr: str) -> bool:
    """该表达式是否用到入场价字段（⇒ 必须走"逐日求值"路径 ✓，不能预先算矩阵 ✗）。"""
    s = str(expr or "")
    return ("$entry_price" in s) or ("$entry_first" in s)


def _truthy(x) -> np.ndarray:
    """与面板一致：**先 fillna(0) 再 != 0** ✓（NaN 记 0 ⇒ 假 ✓）。"""
    return np.asarray(_nan0(x)) != 0


class EntryRule:
    """编译后的入场价离场规则 ✓（`eval_row` 逐日求值 ✓）。"""

    def __init__(self, node: Node, expr: str, threshold: float = 0.0):
        self.node = node
        self.expr = expr
        self.threshold = float(threshold)

    # ---- 校验（编译期就报错，别等回测跑一半 ✗）----
    def validate(self) -> None:
        self._check(self.node)

    def _check(self, n: Node) -> None:
        op = n.op
        if op == "field":
            tok = str(n.args[0])
            if tok in ENTRY_FIELDS or tok in _PRICE_FIELDS:
                return
            raise UnsupportedExitError(
                "离场公式里的字段「%s」不支持 ✗ —— 含入场价（ENTRY_PRICE / ENTRY_FIRST）的离场公式"
                "目前只允许直接组合 K 线字段（CLOSE/OPEN/HIGH/LOW ✓）" % tok)
        if op == "const":
            return
        if op in _ARITH or op in _CMP or op in ("And", "Or", "If", "Not"):
            for a in n.args:
                if isinstance(a, Node):
                    self._check(a)
            return
        if op in _FUNCS or op == "Between":
            for a in n.args:
                if isinstance(a, Node):
                    self._check(a)
            return
        raise UnsupportedExitError(
            "离场公式里的「%s」不支持 ✗ —— 含入场价的离场公式**不能带需要历史窗口的算子**"
            "（MA/EMA/REF/Slope/BARSLAST… ✗），因为入场价是**回测状态量**、只能逐日按当日 K 线算 ✓。\n"
            "  · 建议：把这类条件写进**另一条不含入场价的**离场公式，与本条**同时勾选** ✓\n"
            "    （⚠ 两条之间是 **OR** 关系：任一条触发就离场 ✓）" % op)

    # ---- 逐日求值（对**当日全体标的**做向量运算 ✓）----
    def eval_row(self, rows: Dict[str, np.ndarray],
                 avg: np.ndarray, first: np.ndarray) -> np.ndarray:
        """返回当日**全体标的**的离场布尔向量 ✓（`因子值 > 阈值` ✓；NaN ⇒ 假 ✓）。"""
        v = np.asarray(self._eval(self.node, rows, avg, first), dtype=np.float64)
        with np.errstate(all="ignore"):
            return np.asarray(v > self.threshold, dtype=bool)

    def _eval(self, n: Node, rows: Dict[str, np.ndarray],
              avg: np.ndarray, first: np.ndarray):
        op = n.op
        if op == "const":
            return float(n.args[0])
        if op == "field":
            tok = str(n.args[0])
            if tok in ENTRY_FIELDS:
                return avg if ENTRY_FIELDS[tok] == 0 else first
            if tok in _PRICE_FIELDS:
                key = _PRICE_FIELDS[tok]
                if key not in rows:
                    raise UnsupportedExitError(
                        "回测没有提供 %s 这一列 ⇒ 该离场公式无法求值 ✗" % key)
                return rows[key]
            raise UnsupportedExitError("离场公式里的字段「%s」不支持 ✗" % tok)
        if op in _ARITH:
            a = self._eval(n.args[0], rows, avg, first)
            b = self._eval(n.args[1], rows, avg, first)
            with np.errstate(all="ignore"):
                return _ARITH[op](a, b)
        if op in _CMP:
            a = self._eval(n.args[0], rows, avg, first)
            b = self._eval(n.args[1], rows, avg, first)
            with np.errstate(all="ignore"):
                return np.asarray(_CMP[op](a, b), dtype=np.float64)
        if op == "And":
            a = _truthy(self._eval(n.args[0], rows, avg, first))
            b = _truthy(self._eval(n.args[1], rows, avg, first))
            return np.asarray(a & b, dtype=np.float64)
        if op == "Or":
            a = _truthy(self._eval(n.args[0], rows, avg, first))
            b = _truthy(self._eval(n.args[1], rows, avg, first))
            return np.asarray(a | b, dtype=np.float64)
        if op == "Not":
            return np.asarray(~_truthy(self._eval(n.args[0], rows, avg, first)), dtype=np.float64)
        if op == "If":
            cond = _truthy(self._eval(n.args[0], rows, avg, first))
            a = self._eval(n.args[1], rows, avg, first)
            b = self._eval(n.args[2], rows, avg, first)
            return np.where(cond, a, b)
        if op == "Between":                      # 与面板同义：a ≤ x ≤ b ✓
            x = self._eval(n.args[0], rows, avg, first)
            lo = self._eval(n.args[1], rows, avg, first)
            hi = self._eval(n.args[2], rows, avg, first)
            with np.errstate(all="ignore"):
                return np.asarray((x >= lo) & (x <= hi), dtype=np.float64)
        if op in _FUNCS:
            vals = [self._eval(a, rows, avg, first) for a in n.args]
            with np.errstate(all="ignore"):
                return _FUNCS[op](*vals)
        raise UnsupportedExitError("离场公式里的「%s」不支持 ✗" % op)


def compile_entry_rule(expr: str, threshold: float = 0.0) -> Optional[EntryRule]:
    """把**翻译后**的离场表达式编译成规则 ✓。

    · 不含入场价 ⇒ 返回 `None` ✓（调用方走原来的"预计算矩阵"路径 ✓，数字逐位不变 ✓）；
    · 含入场价 ⇒ 返回 `EntryRule` ✓（**编译期就校验** ✓，不支持的写法直接抛
      `UnsupportedExitError` ✓）。
    """
    if not uses_entry(expr):
        return None
    node = parse_expr(expr)
    rule = EntryRule(node, expr, threshold)
    rule.validate()
    return rule
