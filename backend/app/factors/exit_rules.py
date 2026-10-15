# -*- coding: utf-8 -*-
"""**含入场价**的离场规则（v2.6.0 起，v2.7.0 改成"预计算 + 逐日代入" ✓）。

**用户要的写法**（原话 ✓）：
    止损: CLOSE < ENTRY_PRICE*0.95;
    止盈: CLOSE >= ENTRY_PRICE*1.10;
混用任意字段也**必须支持**（用户 2026-10-15 明确指出 ✓）：
    止损: CLOSE < ENTRY_PRICE*0.95 AND TURNOVERRATE > 2;
    离场: MARKET_CAP < 100000000;        { 这条其实**不含入场价** ⇒ 走普通面板路径 ✓ }
    离场: MA(CLOSE,5) < ENTRY_PRICE;      { 含入场价 + 带历史窗口的算子 ✓ }

| 字段 | 语义 | 加减仓时 |
|---|---|---|
| `ENTRY_PRICE`（入场价） | **移动平均成本**（含买入费 ⇒ = 券商"成本价" ✓） | 买入会变（摊薄/加仓）；**卖出不变** ✓ |
| `ENTRY_FIRST`（首次入场价） | **首次建仓那一笔**的每股成本 ✓ | 恒定 ✓（清仓后再买重新起算 ✓） |

## 怎么做到的（★ v2.7.0 的关键改动）

入场价是**回测状态量**（取决于你实际在哪一根成交 ✗）⇒ 它**不能**像 `$close` 那样整块预计算 ✗；
但它**只出现在少数几个位置** ✓ ⇒ 按"**能预计算的全预计算、剩下的小骨架逐日算**"来拆：

```
CLOSE < ENTRY_PRICE*0.95 AND TURNOVERRATE > 2
        └── 骨架里只剩：Lt(("P",0), Mul(("E",0), 0.95)) ── Pre_0 = Div($close,$factor) ✓（面板算）
                                                    └── And(…, ("P",1)) ── Pre_1 = $turnoverrate ✓（面板算）
```
- **与入场价无关的子树** ⇒ 序列化成普通 qlib 表达式 ⇒ 当**附加字段列**交给面板求值 ✓
  ⇒ 于是 **H/L/V、换手率、市值、`MA/EMA/递推组`… 全都能用** ✓（这一步由
  `single_test` 的既有取数链完成 ✓，本模块只负责"拆"与"拼" ✓）；
- **剩下的骨架**（比较 / 逻辑 / 算术 / `If` / `Between` + 两个入场价叶子）⇒ 回测里
  **逐日代入当天的入场价向量**求值 ✓（`signals/period_roll.py` 传进来 ✓，逐日一次
  (T×C) 向量运算 ✓，量级与引擎既有的逐日工作一致 ✓）；
- 拆出来的列**只在真的用了入场价时才多算** ✓（不用 ⇒ 一个列都不加 ✓ = 零额外成本 ✓）。

骨架里**不支持**的只有"对入场价做需要历史窗口的运算"✗（`REF(ENTRY_PRICE,1)` / `MA(ENTRY_PRICE,5)`
/ `SLOPE(ENTRY_PRICE,10)` …）—— 那种写法要"过去 N 根的入场价"✗，语义上也不清楚（期间可能已换仓 ✓）
⇒ 编译期直接报错，并把**能怎么写**说清楚 ✓。
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .panel_expr import Node, parse_expr
# ⚠ 逐元素算子表**直接复用递推内核那张**（`recursion._OPS` ✓）—— 两边语义必须逐位一致 ✓
#   （NaN 比较恒假、AND/OR 先 fillna(0)、IF 的 NaN 取 a…都是同一条口径 ✓）。
from .recursion import _nan0, _OPS

#: 两个"入场价"字段 → 取值下标（`0` = 平均成本 ✓、`1` = 首次入场 ✓）
ENTRY_FIELDS = {"$entry_price": 0, "$entry_first": 1}
_ENTRY_TOKENS = ("$entry_price", "$entry_first")


class UnsupportedExitError(ValueError):
    """离场公式用了不支持的写法 ⇒ 报**能自助**的错 ✓。"""


# ===========================================================================
# ① AST ⇄ 表达式文本
# ===========================================================================

def _ast_to_expr(n: Node) -> str:
    """把一棵子树**序列化回 qlib 表达式** ✓（`Node.raw` 只有算子名 ✗ ⇒ 必须自己拼 ✓）。

    ⚠ 字段写成 `$xxx` ✓（面板/qlib 认的是这个形态 ✓），常量按原样 ✓。
    """
    if n.op == "field":
        return str(n.args[0])
    if n.op == "const":
        v = float(n.args[0])
        return str(int(v)) if float(v).is_integer() else repr(v)
    inner = ",".join(_ast_to_expr(a) if isinstance(a, Node) else str(a) for a in n.args)
    return "%s(%s)" % (n.op, inner)


def uses_entry(expr: str) -> bool:
    """该表达式是否用到入场价字段（⇒ 必须走"拆分 + 逐日代入"路径 ✓）。"""
    s = str(expr or "")
    return any(t in s for t in _ENTRY_TOKENS)


def _has_entry(n: Node) -> bool:
    if n.op == "field" and str(n.args[0]) in ENTRY_FIELDS:
        return True
    return any(_has_entry(a) for a in n.args if isinstance(a, Node))


# ===========================================================================
# ② 拆分：预计算列 + 骨架
# ===========================================================================

#: 骨架允许的算子（其余一律编译期报错 ✓）—— 与递推内核用的是同一张表 ✓（+ BETWEEN ✓）
_SKEL_OK = set(_OPS) | {"Between"}


def _split(n: Node, pre_exprs: List[str]) -> object:
    """递归拆：能预计算的子树 ⇒ 串成表达式存进 `pre_exprs` 并返回 `("P", i)` ✓。"""
    if n.op == "field":
        tok = str(n.args[0])
        if tok in ENTRY_FIELDS:
            return ("E", ENTRY_FIELDS[tok])
        return _emit(n, pre_exprs)
    if n.op == "const":
        return ("c", float(n.args[0]))
    if not _has_entry(n):
        return _emit(n, pre_exprs)                 # 整棵子树与入场价无关 ⇒ 全交给面板 ✓
    if n.op not in _SKEL_OK:
        raise UnsupportedExitError(
            "「%s」不能作用在**入场价**上 ✗ —— 入场价是**回测状态量**，只能逐日按当天的值参与\n"
            "  · 允许：四则运算 / 比较 / AND / OR / NOT / IF / BETWEEN，以及"
            "「与入场价无关的部分用任何算子」（那部分走面板，MA/EMA/递推组都能用 ✓）\n"
            "  · 例：`CLOSE < ENTRY_PRICE*0.95 AND MA(CLOSE,5) < MA(CLOSE,10)` ✓\n"
            "  · 不支持：`REF(ENTRY_PRICE,1)` / `MA(ENTRY_PRICE,5)`（要过去 N 根的入场价，"
            "语义也不清：期间可能已换仓 ✗）\n"
            "  · 真需要「上一次的入场价」这类逻辑 ⇒ 请用递推组把它显式写出来 ✓"
            % n.op)
    return (n.op, [_split(a, pre_exprs) if isinstance(a, Node) else a for a in n.args])


def _emit(n: Node, pre_exprs: List[str]):
    """把子树当**普通表达式**登记（去重 ✓），返回骨架叶子 `("P", i)` ✓。"""
    s = _ast_to_expr(n)
    if s not in pre_exprs:
        pre_exprs.append(s)
    return ("P", pre_exprs.index(s))


class EntryRule:
    """拆好的入场价离场规则 ✓（`skel` + 预计算列 ✓；回测里逐日代入入场价 ✓）。"""

    def __init__(self, skel, pre_exprs: Optional[List[str]] = None,
                 threshold: float = 0.0, pre_mats: Optional[List] = None):
        self.skel = skel
        self.pre_exprs = list(pre_exprs or [])
        self.threshold = float(threshold)
        self.pre_mats = list(pre_mats or [])

    # ---- 回测侧：把"对齐到本轮代码轴"的矩阵塞进来 ✓ ----
    def bind(self, pre_mats: List) -> "EntryRule":
        if len(pre_mats) != len(self.pre_exprs):
            raise UnsupportedExitError(
                "离场规则预计算列数不符（%d vs %d）⇒ 请把公式反馈给开发 ✓"
                % (len(pre_mats), len(self.pre_exprs)))
        self.pre_mats = list(pre_mats)
        return self

    # ---- 逐日求值：第 i 根、全体标的的离场布尔向量 ✓ ----
    def eval_row(self, i: int, avg: np.ndarray, first: np.ndarray) -> np.ndarray:
        if not self.pre_mats and self.pre_exprs:
            raise UnsupportedExitError("离场规则还没绑定预计算列 ✗（内部错误，请反馈 ✓）")
        v = np.asarray(_eval(self.skel, i, self.pre_mats, avg, first), dtype=np.float64)
        if v.ndim == 0:
            # ⚠ 骨架里既没有预计算列也没有入场价（纯常量条件，如 `1>0` ✓）⇒ 结果是**标量** ✗
            #   ⇒ 广播成"按标的"的向量 ✓（否则引擎那边 `_sig[c]` 会炸 ✗）。
            v = np.full(np.shape(avg), float(v), dtype=np.float64)
        with np.errstate(all="ignore"):
            return np.asarray(v > self.threshold, dtype=bool)


def _eval(node, i: int, pre: List, avg: np.ndarray, first: np.ndarray):
    """骨架求值的**唯一实现**（`("P",j)` ⇒ 第 j 个预计算列的第 i 行 ✓；`("E",k)` ⇒ 入场价向量 ✓）。"""
    kind = node[0]
    if kind == "c":
        return node[1]
    if kind == "E":
        return avg if node[1] == 0 else first
    if kind == "P":
        j = node[1]
        try:
            return pre[j][i]
        except Exception as e:                                     # noqa: BLE001
            raise UnsupportedExitError("预计算列 %d 取第 %d 行失败：%r" % (j, i, e)) from e
    vals = [_eval(a, i, pre, avg, first) if isinstance(a, tuple) else a for a in node[1]]
    if kind == "Between":                      # 与面板同义：a ≤ x ≤ b ✓
        x, lo, hi = vals
        with np.errstate(all="ignore"):
            return np.asarray((x >= lo) & (x <= hi), dtype=np.float64)
    op = _OPS.get(kind)
    if op is None:
        raise UnsupportedExitError("离场骨架里的「%s」不支持 ✗（内部错误，请反馈 ✓）" % kind)
    with np.errstate(all="ignore"):
        return op(*vals)


def split_entry_rule(expr: str, threshold: float = 0.0) -> Optional[EntryRule]:
    """把**翻译后**的离场表达式拆成规则 ✓。

    · 不含入场价 ⇒ 返回 `None` ✓（调用方走原来的"整列预计算"路径 ✓，数字逐位不变 ✓）；
    · 含入场价 ⇒ 返回 `EntryRule` ✓（**编译期就校验** ✓，不支持的写法直接抛
      `UnsupportedExitError` ✓，绝不静默算错 ✗）。
    """
    if not uses_entry(expr):
        return None
    node = parse_expr(expr)
    pre: List[str] = []
    skel = _split(node, pre)
    return EntryRule(skel, pre, threshold)


__all__ = ["ENTRY_FIELDS", "EntryRule", "UnsupportedExitError", "split_entry_rule",
           "uses_entry"]
