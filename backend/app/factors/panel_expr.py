# -*- coding: utf-8 -*-
"""面板级表达式求值器（做法2，第一版：替换单因子测试特征加载）。

背景（2026-09-09 v1.16.8）：qlib 的 D.features 逐股票拆 job + Python 表达式树遍历，
巨型公式（CWH_BREAK_WAIT20_F1 40746 字符 / 4630 节点）全 A 5260 只耗时 ~152s，
边际成本恒定 ~30ms/只、8 核 loky 也吃不满 → 大量时间花在 5260 次 job 调度 /
单只树遍历 / 结果回传。本模块用**一块 MultiIndex(instrument, datetime) 面板** +
pandas 向量化算子（groupby-rolling/shift/逐元素）一次算完全部股票，公共子式按
str 缓存天然只算一次。实测：450 万行 groupby-rolling 0.73s、算术 ~0.01s/op。

语义锚点（2026-09-09 实证，qlib D.features 逐位对齐）：
- 输出行集 = 全日历（含停牌 NaN 行）。
- SR(X, [M]) 语义：停牌日值不算且不占滚动窗口格 → 等价实现为"把 M（默认 X）
  为 NaN 的行值抹成 NaN"，窗口算子对"非 NaN 的有效行"滚动后回填全日历
  （停牌日结果 = NaN）。实证：Mean(SR($close),3) 在 2000-05-08 停牌日 = NaN，
  复牌日窗口只含前两个有效交易日，与此等价。
- 叶子字段从 .day.bin 批量 np.fromfile 读入（首 4 字节 = 该股日历起始 idx，
  后续 float32 每日历日一行、停牌 NaN），不经过 qlib job 调度。

第一版范围：run_single_factor_tests 的特征加载（panel_features 对接），算子集 =
CWH + label/base/tag 实际用到的全部算子。
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# CWH 这类巨型公式嵌套可达数百~上千层，提高递归上限（模块级，全局一次）
if sys.getrecursionlimit() < 20000:
    sys.setrecursionlimit(20000)

# ===========================================================================
# 1) 日历 / .day.bin 读取
# ===========================================================================

_CAL: Optional[pd.DatetimeIndex] = None


def _calendar() -> pd.DatetimeIndex:
    global _CAL
    if _CAL is None:
        from qlib.data import D

        _CAL = pd.to_datetime(D.calendar())
    return _CAL


def _feature_dir() -> str:
    try:
        from app.config import QLIB_PROVIDER_URI

        if QLIB_PROVIDER_URI:
            return os.path.join(QLIB_PROVIDER_URI, "features")
    except Exception:
        pass
    return os.path.join(os.path.abspath("."), "..", "data", "cn_data", "features")


def _read_field_bin(inst: str, field: str):
    """读 .day.bin → (start_idx, float64 数组)；缺失返回 None。"""
    p = os.path.join(_feature_dir(), inst, f"{field}.day.bin")
    if not os.path.exists(p):
        return None
    arr = np.fromfile(p, dtype="<f4")
    if arr.size < 2:
        return None
    return int(arr[0]), arr[1:].astype(np.float64)


def load_field_series(instruments, field: str, start_time, end_time) -> pd.Series:
    """读一字段为 MultiIndex(instrument, datetime) 全日历面板（各股行数=其有效日历）。"""
    cal = _calendar()
    t0 = pd.Timestamp(start_time)
    t1 = pd.Timestamp(end_time)
    req_lo = int(np.searchsorted(cal, t0, side="left"))
    req_hi = int(np.searchsorted(cal, t1, side="right")) - 1
    parts = []
    for inst in instruments:
        r = _read_field_bin(inst, field)
        if r is None:
            continue
        start_idx, vals = r
        lo = max(start_idx, req_lo)
        hi = min(start_idx + len(vals) - 1, req_hi)
        if hi < lo:
            continue
        seg = vals[lo - start_idx : hi - start_idx + 1]
        dates = cal[lo : hi + 1]
        parts.append(pd.Series(
            seg, index=pd.MultiIndex.from_arrays(
                [np.repeat(inst, len(dates)), dates],
                names=["instrument", "datetime"])))
    if not parts:
        return pd.Series(dtype=np.float64)
    return pd.concat(parts).sort_index()


def _union_index(instruments, start_time, end_time, fields=("close",)) -> pd.MultiIndex:
    """每只股票在其【参与字段】数据区间并集 ∩ [start,end] 上的全历 index。

    与 qlib D.features 对齐：不同字段 .day.bin 覆盖范围可能不同（如长期停牌股
    close 止于某日、limit_up/is_st 标签覆盖更长），qlib concat 各列取 union 行集。
    单字段索引无法代表"全历"，必须取全部参与字段的覆盖并集。
    """
    cal = _calendar()
    t0 = pd.Timestamp(start_time)
    t1 = pd.Timestamp(end_time)
    req_lo = int(np.searchsorted(cal, t0, side="left"))
    req_hi = int(np.searchsorted(cal, t1, side="right")) - 1
    parts = []
    fields = tuple(dict.fromkeys(fields))
    for inst in instruments:
        lo_max, hi_min = None, None
        for fld in fields:
            r = _read_field_bin(inst, fld)
            if r is None:
                continue
            start_idx, vals = r
            a = max(start_idx, req_lo)
            b = min(start_idx + len(vals) - 1, req_hi)
            if b < a:
                continue
            lo_max = a if lo_max is None else min(lo_max, a)
            hi_min = b if hi_min is None else max(hi_min, b)
        if lo_max is None or hi_min < lo_max:
            continue
        dates = cal[lo_max : hi_min + 1]
        parts.append(pd.MultiIndex.from_arrays(
            [np.repeat(inst, len(dates)), dates], names=["instrument", "datetime"]))
    if not parts:
        return pd.MultiIndex.from_arrays([[], []], names=["instrument", "datetime"])
    return parts[0] if len(parts) == 1 else parts[0].append(parts[1:])


# ===========================================================================
# 2) 轻量 AST
# ===========================================================================


class Node:
    __slots__ = ("op", "args", "raw")

    def __init__(self, op, args, raw):
        self.op = op
        self.args = args
        self.raw = raw

    def __repr__(self):
        return self.raw


# 中缀二元运算符 → Node op（qlib 表达式允许 A/B、A-B 等写法）
_INFIX = {"+": "Add", "-": "Sub", "*": "Mul", "/": "Div"}
_INFIX_PREC = {"+": 10, "-": 10, "*": 20, "/": 20}


def parse_expr(text: str) -> Node:
    """递归下降解析 qlib 表达式：$field / 数字 / Func(args) / 中缀 + - * /。

    自左到右按优先级处理中缀；逗号/右括号作为参数边界。
    """
    pos = 0
    n = len(text)

    def skip():
        nonlocal pos
        while pos < n and text[pos] in " \t\r\n":
            pos += 1

    def peek():
        skip()
        return text[pos] if pos < n else ""

    def parse_expr_prec(min_prec: int) -> Node:
        """Pratt：先解析一元前缀，再按优先级循环吸收中缀。"""
        nonlocal pos
        left = parse_prefix()
        while True:
            skip()
            if pos >= n:
                break
            ch = text[pos]
            if ch in _INFIX:
                prec = _INFIX_PREC[ch]
                if prec < min_prec:
                    break
                pos += 1  # consume op
                right = parse_expr_prec(prec + 1)
                left = Node(_INFIX[ch], [left, right], ch)
            else:
                break
        return left

    def parse_prefix() -> Node:
        nonlocal pos
        skip()
        c = text[pos]
        # 一元负号（仅当后随数字/字段/函数且上下文需要 → 简化：数字已含负号）
        if c == "$":
            m = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*", text[pos:])
            tok = m.group(0)
            pos += len(tok)
            return Node("field", [tok], tok)
        if c.isdigit() or (c == "-" and pos + 1 < n and text[pos + 1].isdigit()):
            m = re.match(r"-?\d+(\.\d+)?([eE][+-]?\d+)?", text[pos:])
            tok = m.group(0)
            pos += len(tok)
            return Node("const", [tok], tok)
        if c == "(":
            pos += 1
            inner = parse_expr_prec(0)
            skip()
            if pos < n and text[pos] == ")":
                pos += 1
            return inner
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[pos:])
        if not m:
            raise ValueError(f"无法解析: {text[pos:pos+40]}")
        name = m.group(0)
        pos += len(name)
        skip()
        if pos >= n or text[pos] != "(":
            # 裸标识符（非常量函数/字段）：可能是除法等残留，容错为常量 0
            return Node("const", ["0"], name)
        pos += 1
        args = []
        while True:
            skip()
            if pos < n and text[pos] == ")":
                pos += 1
                break
            args.append(parse_expr_prec(0))
            skip()
            if pos < n and text[pos] == ",":
                pos += 1
                continue
            if pos < n and text[pos] == ")":
                pos += 1
                break
            raise ValueError(f"期望 , 或 )：{text[pos:pos+30]}")
        return Node(name, args, name)

    root = parse_expr_prec(0)
    skip()
    if pos != n:
        raise ValueError(f"尾部多余: {text[pos:pos+40]}")
    return root


def reconstruct(node: Node) -> str:
    if node.op in ("field", "const"):
        return node.raw
    if node.op == "Add":
        return f"Add({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    if node.op == "Sub":
        return f"Sub({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    if node.op == "Mul":
        return f"Mul({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    if node.op == "Div":
        return f"Div({reconstruct(node.args[0])},{reconstruct(node.args[1])})"
    return f"{node.op}({','.join(reconstruct(a) for a in node.args)})"


# ===========================================================================
# 3) 求值器
# ===========================================================================

_ROLL_FUNC = {"Mean": "mean", "Max": "max", "Min": "min", "Sum": "sum", "Std": "std",
              "Var": "var"}
# qlib 的 Max/Min 是滚动窗口极值；通达信 MAX(A,B)/MIN(A,B) 元素取大/小 → Greater/Less
_BIN_ELEM = {
    "Add": "add", "Sub": "sub", "Mul": "mul", "Div": "div",
    "Greater": "max", "Less": "min",
}
_BIN_CMP = {"Gt": "gt", "Ge": "ge", "Lt": "lt", "Le": "le", "Eq": "eq", "Ne": "ne"}
_UNARY = {"Abs": "abs", "Sqrt": "sqrt", "Log": "log", "Neg": "neg", "Sgn": "sign"}


def _as_series(x, template: Optional[pd.Series]) -> pd.Series:
    """把标量或 Series 归一到 Series（常量按 template index 广播）。"""
    if isinstance(x, pd.Series):
        return x
    if template is not None:
        return pd.Series(float(x), index=template.index)
    raise ValueError("常量不能单独成为结果（缺少 Series 上下文）")


def _align(a: pd.Series, b: pd.Series):
    """两列对齐到并集。字段级面板同 index 时零开销。

    不同行集（字段覆盖范围/上市日不同）：并集（不排序——MultiIndex 混合列类型
    不能整体 sort；pandas 运算按 label 对齐与顺序无关）。
    """
    if a.index.equals(b.index):
        return a, b
    idx = a.index.union(b.index)
    # union 可能产生未排序/类型不齐的 index：算术按 label 对齐即可，无需排序
    return a.reindex(idx), b.reindex(idx)


class PanelEvaluator:
    """在一整块面板上求值；节点缓存消除公共子式重复计算。

    SR 上下文模型（对齐 qlib 2026-09-09 实证）：SR 语义 = 剔停牌(NaN)行后在
    "有效行压缩序列"上计算窗口/移位/算术，结果 scatter 回全日历（停牌日 NaN）。
    由于 _sr_wrap_expr 会把因子表达式的所有叶子字段包 SR，含 SR 的整棵表达式
    都在压缩序列上求值：eval 用 active（当前行集合）驱动——
      sr=True  → active = 各股 close 非 NaN 的有效行（剔停牌）；
      sr=False → active = 全日历（label/base/tag 等，停牌日保留前值语义）。
    压缩序列上 shift/rolling 天然按剔除后的连续行序，与 qlib 一致。
    """

    def __init__(self, instruments, start_time, end_time, union_fields=("close",),
                 read_start=None):
        self.instruments = list(instruments)
        self.start_time = start_time
        self.end_time = end_time
        # 读盘起点：默认=逻辑起点；传入 read_start（更早）时字段读取/滚动从前移起点
        # 开始（预热窗口），输出仍由 panel_features reindex 回 [start_time, end_time]。
        self.read_start = read_start or start_time
        self._union_fields = tuple(dict.fromkeys(union_fields))
        self._full = _union_index(instruments, self.read_start, end_time, self._union_fields)
        self._active_cache: Dict[bool, pd.MultiIndex] = {}
        self._field_cache: Dict[str, pd.Series] = {}
        self._node_cache: Dict[Tuple[str, bool], pd.Series] = {}

    def _active_index(self, sr: bool) -> pd.MultiIndex:
        """当前行集合：sr=True → close 有效行压缩序列；sr=False → 全日历。"""
        key = bool(sr)
        if key in self._active_cache:
            return self._active_cache[key]
        if not sr:
            self._active_cache[key] = self._full
            return self._full
        close_full = self.field("$close")  # 全历（自身非 NaN=有效）
        active = close_full[close_full.notna()].index
        self._active_cache[key] = active
        return active

    # ---- 字段 ----
    def field(self, name: str) -> pd.Series:
        # 表达式里叶子是 $close 形态，bin 文件名是 close（去 $ 前缀）
        key = name.lstrip("$")
        s = self._field_cache.get(key)
        if s is None:
            raw = load_field_series(self.instruments, key, self.read_start, self.end_time)
            # 所有字段统一对齐到全池全日历基准（缺失股/缺失行补 NaN），
            # 保证跨字段 Series 天然同 index（_align 恒等路径，避免 union 类型问题）
            s = raw.reindex(self._full)
            self._field_cache[key] = s
        return s

    # ---- 节点 ----
    def eval(self, node: Node, sr: bool = False) -> pd.Series:
        """在 active 行集上求值一个节点。

        sr=True（表达式含 SR）：active = 各股 close 有效行（剔停牌），所有窗口/
        移位/算术在压缩序列上进行（与 qlib SR 删行语义一致，无 NaN 停牌行干扰）；
        sr=False（label/base/tag）：active = 全日历，保留停牌 NaN 行（普通 shift
        在停牌日给前值，与 qlib 非 SR 字段一致）。
        """
        key = (node.raw if node.op in ("field", "const") else reconstruct(node), sr)
        hit = self._node_cache.get(key)
        if hit is not None:
            return hit

        active = self._active_index(sr)
        op = node.op
        if op == "field":
            out = self.field(node.args[0]).reindex(active)
        elif op == "const":
            return float(node.args[0])
        elif op == "SR":
            # SR 表达式：子表达式已在 sr=True 的压缩行集上求值，透传即可
            # （active 已按 close 有效行剔除停牌，无需再 where 抹 NaN）。
            inner = self.eval(node.args[0], sr=True)
            if inner is None:
                raise ValueError("SR 内层不能是常量")
            out = inner
        else:
            args = [self.eval(a, sr=sr) for a in node.args]
            out = self._apply(op, args, sr=sr, active=active)
        if out is not None:
            self._node_cache[key] = out
        return out

    def _apply(self, op: str, args, sr: bool, active=None) -> Optional[pd.Series]:
        if op == "Ref":
            s, k = args[0], int(args[1])
            if s is None:
                raise ValueError("Ref 首参不能是常量")
            # active 行集上普通 shift：SR 时 active 已剔停牌 → 语义正确
            return _ref(s, k, sr=False)
        if op in _ROLL_FUNC:
            s, N = args[0], int(args[1])
            if s is None:
                raise ValueError(f"{op} 首参不能是常量")
            # SR 时 active 已剔停牌 → 普通滚动即可（无需 _valid_rolling 二次剔除）
            return _roll(s, _ROLL_FUNC[op], N, sr=False)
        if op in _BIN_ELEM or op in _BIN_CMP:
            a, b = args
            template = a if isinstance(a, pd.Series) else b
            a = _as_series(a, template)
            b = _as_series(b, template)
            a, b = _align(a, b)
            if op in _BIN_ELEM:
                fn = _BIN_ELEM[op]
                if fn in ("add", "sub", "mul", "div"):
                    return getattr(a, fn)(b)
                if fn == "max":
                    return a.where(a >= b, b)
                if fn == "min":
                    return a.where(a <= b, b)
            return getattr(a, _BIN_CMP[op])(b).astype(np.float64)
        if op in _UNARY:
            s = args[0]
            if s is None:
                raise ValueError(f"{op} 参数不能是常量")
            fn = _UNARY[op]
            if fn == "abs":
                return s.abs()
            if fn == "sqrt":
                return np.sqrt(s)
            if fn == "log":
                return np.log(s)
            if fn == "neg":
                return -s
            if fn == "sign":
                return np.sign(s)
        if op == "If":
            cond, a, b = args
            if isinstance(cond, (int, float)) or cond is None:
                cond = _as_series(cond, a if isinstance(a, pd.Series) else b)
            if not isinstance(a, pd.Series):
                a = _as_series(a, cond)
            if not isinstance(b, pd.Series):
                b = _as_series(b, cond)
            a, b = _align(a, b)
            return a.where(cond.astype(bool), b)
        if op == "ROUND":
            # ROUND(x, ndigits)：真实价按分取整（np.round 半进偶；对整分价格与 qlib
            # ops_ext.ROUND 一致——真实价 ≈ 分整数，无 0.5 边界）
            s = args[0]
            nd = int(args[1]) if len(args) > 1 and args[1] is not None else 0
            if not isinstance(s, pd.Series):
                raise ValueError("ROUND 首参不能是常量")
            return np.round(s, nd)
        if op == "And":
            a, b = args
            tpl = a if isinstance(a, pd.Series) else b
            a = _as_series(a, tpl).fillna(0.0)
            b = _as_series(b, tpl).fillna(0.0)
            a, b = _align(a, b)
            return ((a != 0) & (b != 0)).astype(np.float64)
        if op == "Or":
            a, b = args
            tpl = a if isinstance(a, pd.Series) else b
            a = _as_series(a, tpl).fillna(0.0)
            b = _as_series(b, tpl).fillna(0.0)
            a, b = _align(a, b)
            return ((a != 0) | (b != 0)).astype(np.float64)
        if op in ("BARSLAST", "BARSSINCEN", "DYN_REF", "DYN_MIN", "DYN_MAX", "DYN_SUM", "DYN_COUNT"):
            return _panel_dyn(op, args, self)
        raise ValueError(f"panel_expr 不支持算子 {op}")

    def eval_expr(self, expr: str) -> pd.Series:
        node = parse_expr(expr)
        # SR 语义：single_test 在 suspend_remove=True 时用 _sr_wrap_expr 把因子表达式
        # 的所有叶子字段包上 SR(...)。含 SR 的表达式整棵在"close 有效行压缩序列"上
        # 求值（剔除停牌行后滚动/移位/算术，与 qlib 删行语义一致），出口把结果
        # scatter 回全日历（停牌日 NaN——实证 Mean(SR($close),3) 停牌日=NaN）。
        # label/base/tag 等不含 SR 的字段组表达式在全日历上普通求值（停牌日保留
        # 前值，实证 Ref($close,1) 停牌日=前收 0.977708 非 NaN）。
        sr = "SR(" in expr
        s = self.eval(node, sr=sr)
        if sr and s is not None and isinstance(s, pd.Series) and not s.empty:
            # 压缩序列 → 回填全日历（有效行位置保持原值，停牌日 NaN）
            full = self._full
            if len(s) != len(full):
                s = s.reindex(full)
        return s


# ===========================================================================
# 窗口 / 移位 / 动态算子 的"面板 + SR"实现
# ===========================================================================


def _ref(s: pd.Series, k: int, sr: bool) -> pd.Series:
    """Ref(X, k)：k>0 过去、k<0 未来。sr=True 时按有效行（非 NaN）shift。"""
    if sr:
        valid = s[s.notna()]
        if valid.empty:
            return pd.Series(np.nan, index=s.index, dtype=np.float64)
        r = valid.groupby(level=0, group_keys=False).shift(k)
        out = pd.Series(np.nan, index=s.index, dtype=np.float64)
        out.loc[valid.index] = r.to_numpy(dtype=np.float64)
        return out
    return s.groupby(level=0, group_keys=False).shift(k)


def _roll(s: pd.Series, func: str, N: int, sr: bool) -> pd.Series:
    """滚动算子；sr=True 剔除 NaN 行滚动后回填全日历（停牌日 NaN）。"""
    if sr:
        valid = s[s.notna()]
        if valid.empty:
            return pd.Series(np.nan, index=s.index, dtype=np.float64)
        r = getattr(valid.groupby(level=0).rolling(N, min_periods=1), func)()
        out = pd.Series(np.nan, index=s.index, dtype=np.float64)
        # groupby-rolling 输出 index 比 valid 多一层组 key：用位置赋值规避
        out.loc[valid.index] = r.to_numpy(dtype=np.float64)
        return out
    r = getattr(s.groupby(level=0).rolling(N, min_periods=1), func)()
    # groupby-rolling 对 Series 输出会保留原 MultiIndex + 组前缀；
    # 位置与 s 对齐时直接取数值回填，保证 index = s.index
    if len(r) == len(s):
        out = pd.Series(r.to_numpy(dtype=np.float64), index=s.index)
    else:
        out = s.copy()
        out.loc[:] = np.nan
    return out


def _panel_dyn(op: str, args, ev: PanelEvaluator) -> pd.Series:
    """BARSLAST/DYN_* 面板实现：逐组调用 ops_ext 的纯向量函数（组数~5000，内部 numpy）。"""
    from . import ops_ext

    if op == "BARSLAST":
        s = args[0]
        if not isinstance(s, pd.Series):
            raise ValueError("BARSLAST 参数不能是常量")
        return _by_group(s, None, lambda v: ops_ext.barslast_vec(v))
    if op == "BARSSINCEN":
        s, N = args[0], int(args[1])
        if not isinstance(s, pd.Series):
            raise ValueError("BARSSINCEN 首参不能是常量")
        return _by_group(s, None, lambda v: ops_ext.barsincen_vec(v, N))
    s, ns = args
    if not isinstance(s, pd.Series) or not isinstance(ns, pd.Series):
        raise ValueError(f"{op} 参数须为序列")
    kind = {"DYN_REF": "ref", "DYN_MIN": "min", "DYN_MAX": "max",
            "DYN_SUM": "sum", "DYN_COUNT": "count"}[op]
    return _by_group(s, ns, lambda v, w: _dyn_kernel(kind, v, w, ops_ext))


def _dyn_kernel(kind, vals, nvals, ops_ext):
    if kind == "ref":
        return ops_ext.dyn_ref_vec(vals, nvals)
    if kind == "min":
        return ops_ext._dyn_rmq_vec(vals, nvals, np.fmin)
    if kind == "max":
        return ops_ext._dyn_rmq_vec(vals, nvals, np.fmax)
    if kind == "sum":
        return ops_ext.dyn_window_sum_vec(vals, nvals)
    if kind == "count":
        return ops_ext.dyn_window_count_vec(vals, nvals)
    raise ValueError(kind)


def _by_group(s: pd.Series, ns, fn) -> pd.Series:
    """按组（股票）把 fn 应用到每个连续 segment（ns 可为 None）。"""
    inst = s.index.get_level_values(0)
    idx = s.index
    n = len(s)
    # 组界
    starts = [0]
    for i in range(1, n):
        if inst[i] != inst[i - 1]:
            starts.append(i)
    starts.append(n)
    out = np.empty(n, dtype=np.float64)
    for gi in range(len(starts) - 1):
        a, b = starts[gi], starts[gi + 1]
        v = s.iloc[a:b].to_numpy(dtype=np.float64)
        if ns is None:
            out[a:b] = fn(v)
        else:
            w = ns.iloc[a:b].to_numpy(dtype=np.float64)
            out[a:b] = fn(v, w)
    return pd.Series(out, index=idx)


def _warm_days(exprs) -> int:
    """窗口预热天数：表达式用到的最大前向历史。

    复刻 qlib 语义——Rolling(N)/Ref(±k) 需读 start 之前 N/k 个交易日数据使
    区间首日窗口完整；SR（停牌删行）另需 LOOKBACK_DAYS(250) 前扩覆盖停牌段。
    保守取 max(所有窗口常量, SR lookback) + 30 天余量。
    """
    max_k = 0
    for expr in exprs:
        e = str(expr)
        for m in re.finditer(r"(?:Ref|Mean|Max|Min|Sum|Std|Var|Abs|Sqrt)\([^,]+,\s*(-?\d+)", e):
            try:
                max_k = max(max_k, abs(int(m.group(1))))
            except ValueError:
                pass
        if "SR(" in e:
            max_k = max(max_k, 250)
    return max_k + 30


def _shift_calendar_start(start_time: str, days: int) -> str:
    """把 start_time 前移 days 个交易日（不够则取数据首日）。"""
    cal = _calendar()
    t = pd.Timestamp(start_time)
    pos = int(np.searchsorted(cal, t, side="left"))
    prev = max(0, pos - days)
    return str(cal[prev].date())


def panel_features(instruments: Sequence[str], fields: Sequence[Tuple[str, str]],
                   start_time: str, end_time: str) -> pd.DataFrame:
    """替代 D.features：一次面板求值 (expr, name) 列表 → MultiIndex × 列 DataFrame。

    与 D.features 输出对齐：行 = 各股在【全部参与字段覆盖并集】∩[start,end] 上的
    日历年（含停牌 NaN），列名 = name。读盘自动前移预热窗口（见 _warm_days），
    最终输出仅覆盖 [start_time, end_time]。
    fields: [(表达式文本, 列名), ...]。空表达式跳过。
    """
    union_fields = _collect_field_names(fields)
    warm = _warm_days([e for e, _ in fields])
    read_start = _shift_calendar_start(start_time, warm)
    ev = PanelEvaluator(instruments, start_time, end_time, union_fields,
                        read_start=read_start)
    cols = {}
    for expr, name in fields:
        e = str(expr).strip()
        if not e or e.lower() in ("", "none", "nan"):
            continue
        cols[name] = ev.eval_expr(e)
    # 输出 = 逻辑区间 [start_time, end_time] 的并集（裁剪预热段）
    full_out = _union_index(instruments, start_time, end_time, union_fields)
    if not cols:
        return pd.DataFrame(index=full_out)
    df = pd.DataFrame(cols).reindex(ev._full)
    df = df[df.index.get_level_values("datetime") >= pd.Timestamp(start_time)]
    return df.reindex(full_out)


def _collect_field_names(fields) -> tuple:
    """从 (expr, name) 列表收集全部 $field 引用（供 union index 对齐 qlib 行集）。"""
    out = []
    seen = set()
    for expr, _ in fields:
        for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)", str(expr)):
            f = m.group(1)
            if f not in seen:
                seen.add(f)
                out.append(f)
    if not out:
        out = ["close"]
    return tuple(out)
