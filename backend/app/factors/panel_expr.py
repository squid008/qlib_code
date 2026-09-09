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

import contextlib
import os
import re
import sys
from collections import OrderedDict
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
_FEATURE_DIR: Optional[str] = None

# 进程级 .day.bin 读盘 LRU 缓存（v1.17.7 性能优化）：
# profile（CWH_BREAK_WAIT20_F1 300 只 20.5s）显示 np.fromfile 占 10.66s≈52%——
# 同一 (inst, field) 在一次 panel_features 内会被 union_index / 各 evaluator / 字段加载
# 重复全量读多次。缓存以文件 mtime 为失效键（重 dump 后自动失效），按字节上限从最旧
# 淘汰。容量默认 768MB（QLIB_PANEL_BIN_CACHE_MB 可调）。线程不安全但 GIL + 重复读
# 无害（最多并发重复读一次）；子进程 worker 各自独立缓存。
_BIN_CACHE_MAX_BYTES = int(os.environ.get("QLIB_PANEL_BIN_CACHE_MB", "768")) * 1024 * 1024
_BIN_CACHE: "OrderedDict[Tuple[str, str], Tuple[int, int, np.ndarray]]" = OrderedDict()
_BIN_CACHE_BYTES = 0


def _entry_bytes(entry) -> int:
    return entry[2].nbytes + 64


def _evict_bin_cache(need: int) -> None:
    global _BIN_CACHE_BYTES
    while _BIN_CACHE_BYTES + need > _BIN_CACHE_MAX_BYTES and _BIN_CACHE:
        _BIN_CACHE_BYTES -= _entry_bytes(_BIN_CACHE.popitem(last=False)[1])


def _put_bin_cache(key: Tuple[str, str], mtime: int, start: int, vals: np.ndarray) -> None:
    global _BIN_CACHE_BYTES
    old = _BIN_CACHE.pop(key, None)
    if old is not None:
        _BIN_CACHE_BYTES -= _entry_bytes(old)
    entry = (mtime, start, vals)
    _evict_bin_cache(_entry_bytes(entry))
    _BIN_CACHE[key] = entry
    _BIN_CACHE_BYTES += _entry_bytes(entry)


def clear_bin_cache() -> None:
    """清空进程级 .bin 读盘缓存（数据重 dump 后调用；测试用）。"""
    global _BIN_CACHE_BYTES
    _BIN_CACHE.clear()
    _BIN_CACHE_BYTES = 0


def set_bin_cache_mb(mb: int) -> None:
    """动态调整本进程 .bin 读盘缓存上限（MB）；超限立即逐出。"""
    global _BIN_CACHE_MAX_BYTES
    _BIN_CACHE_MAX_BYTES = max(1, int(mb)) * 1024 * 1024
    _evict_bin_cache(0)


def _system_avail_gb() -> float:
    """当前可用内存（GB）；psutil 不可用时返回一个很大的数（不限制）。"""
    try:
        import psutil

        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return 999.0


# 节点缓存字节上限（v1.18.1）：单表达式内部公共子式缓存也封顶。超巨型公式
# （如 CUP_POOL 24.6 万字符，单树数千节点 × 每节点 ~MB）即使只驻留一个公式，仍可能
# 把单 worker 撑到数 GB；写满后按 LRU 逐出最旧，普通公式近端命中不受影响。
# QLIB_SFT_PANEL_NODE_CACHE_MB 可调（默认 512MB/worker）。
_NODE_CACHE_MAX_BYTES = int(os.environ.get("QLIB_SFT_PANEL_NODE_CACHE_MB", "512")) * 1024 * 1024


def _series_mem_bytes(s: pd.Series) -> int:
    """Series 值数组的近似内存字节（用于节点缓存记账）。"""
    try:
        arr = s.array
        return int(arr.nbytes) if hasattr(arr, "nbytes") else int(s.to_numpy(dtype=np.float64).nbytes)
    except Exception:
        return 0


def set_panel_runtime(calendar=None, feature_dir=None):
    """免 qlib.init 的运行时注入（供并行 worker/外部直接使用）。

    面板求值唯一依赖 qlib 的是全局交易日历 _calendar()。子进程（ProcessPool
    worker）不执行 qlib.init，调用本函数注入 calendar（pd.DatetimeIndex）即可；
    feature_dir 默认从 app.config.QLIB_PROVIDER_URI 解析，无需注入。
    """
    global _CAL, _FEATURE_DIR
    if calendar is not None:
        _CAL = pd.to_datetime(calendar)
    if feature_dir is not None:
        _FEATURE_DIR = feature_dir


def _calendar() -> pd.DatetimeIndex:
    global _CAL
    if _CAL is None:
        from qlib.data import D

        _CAL = pd.to_datetime(D.calendar())
    return _CAL


def _feature_dir() -> str:
    global _FEATURE_DIR
    if _FEATURE_DIR:
        return _FEATURE_DIR
    try:
        from app.config import QLIB_PROVIDER_URI

        if QLIB_PROVIDER_URI:
            return os.path.join(QLIB_PROVIDER_URI, "features")
    except Exception:
        pass
    return os.path.join(os.path.abspath("."), "..", "data", "cn_data", "features")


def _read_field_bin(inst: str, field: str):
    """读 .day.bin → (start_idx, float64 数组)；缺失返回 None。

    命中进程级 LRU 缓存（_BIN_CACHE，mtime 校验）时跳过 np.fromfile；未命中读取
    后写入缓存。语义与原实现完全一致，仅消除同一文件的重复全量读盘。
    """
    p = os.path.join(_feature_dir(), inst, f"{field}.day.bin")
    try:
        st = os.stat(p)
    except OSError:
        return None
    mtime = st.st_mtime_ns
    key = (inst, field)
    hit = _BIN_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        _BIN_CACHE.move_to_end(key)  # LRU 刷新
        return hit[1], hit[2]
    arr = np.fromfile(p, dtype="<f4")
    if arr.size < 2:
        return None
    start = int(arr[0])
    vals = arr[1:].astype(np.float64)
    _put_bin_cache(key, mtime, start, vals)
    return start, vals


def load_field_series(instruments, field: str, start_time, end_time) -> pd.Series:
    """读一字段为 MultiIndex(instrument, datetime) 全日历面板（各股行数=其有效日历）。

    性能（v1.17.7）：原实现每股构造一个 MultiIndex+Series 再 concat（profile：数百~
    数千次 pd.MultiIndex.from_arrays/factorize 占可观测时间）。改为收集 codes/dates/seg
    三组数组后**一次** MultiIndex.from_arrays + Series 构造（顺序与原 concat 后
    sort_index 完全一致）。
    """
    cal = _calendar()
    t0 = pd.Timestamp(start_time)
    t1 = pd.Timestamp(end_time)
    req_lo = int(np.searchsorted(cal, t0, side="left"))
    req_hi = int(np.searchsorted(cal, t1, side="right")) - 1
    codes = []
    dates_parts = []
    seg_parts = []
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
        codes.append(np.repeat(inst, len(dates)))
        dates_parts.append(dates)
        seg_parts.append(seg)
    if not codes:
        return pd.Series(dtype=np.float64)
    index = pd.MultiIndex.from_arrays(
        [np.concatenate(codes), np.concatenate(dates_parts)],
        names=["instrument", "datetime"])
    return pd.Series(np.concatenate(seg_parts), index=index).sort_index()


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
    # 性能（v1.17.7）：原实现每股构造 MultiIndex 后链式 append（O(k²) 拷贝 + 数千次
    # from_arrays/factorize）；改为收集 codes/dates 后一次 from_arrays，顺序等价。
    codes = []
    dates_parts = []
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
        codes.append(np.repeat(inst, len(dates)))
        dates_parts.append(dates)
    if not codes:
        return pd.MultiIndex.from_arrays([[], []], names=["instrument", "datetime"])
    return pd.MultiIndex.from_arrays(
        [np.concatenate(codes), np.concatenate(dates_parts)],
        names=["instrument", "datetime"])


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
    "Power": "pow", "Pow": "pow",  # POW(X,Y)=X^Y（Power=qlib 内建名；Pow=旧编译别名）
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
        self._node_cache: "OrderedDict[Tuple[str, bool], pd.Series]" = OrderedDict()
        self._node_bytes = 0  # 节点缓存已记账字节（配合 LRU 上限）

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
            self._node_cache.move_to_end(key)  # LRU 刷新（命中过的节点更晚被逐出）
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
            self._node_bytes += _series_mem_bytes(out)
            # LRU 逐出：记账字节超上限时从最旧开始淘汰，封顶单 worker 节点缓存
            while self._node_bytes > _NODE_CACHE_MAX_BYTES and self._node_cache:
                _k, _v = self._node_cache.popitem(last=False)
                self._node_bytes -= _series_mem_bytes(_v)
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
        if op in ("EMA", "EMA_TDX"):
            s, N = args[0], args[1]
            if s is None:
                raise ValueError(f"{op} 首参不能是常量")
            # EMA 非固定窗口（指数递归），逐股 ewm（sr=True 时 active 已是压缩序列）
            return _ema(s, float(N), tdx=(op == "EMA_TDX"), sr=sr)
        if op == "SMA":
            s, N, M = args[0], args[1], args[2]
            if s is None:
                raise ValueError("SMA 首参不能是常量")
            return _sma(s, float(N), float(M), sr=sr)
        if op == "BARSCOUNT":
            # 自数据起点累计有效交易日（BARSCOUNT($close)）；面板与 qlib 同从各自
            # read_start 起点累计 → 对齐。逐股内核 _bars_count_seg。
            s = args[0]
            if s is None:
                raise ValueError("BARSCOUNT 首参不能是常量")
            return _by_group(s, None, _bars_count_seg)
        if op == "BARSSINCE":
            # 距首次成立周期数（不限窗口）。面板与 qlib 共用 ops_ext.barsince_vec。
            s = args[0]
            if s is None:
                raise ValueError("BARSSINCE 首参不能是常量")
            from . import ops_ext

            return _by_group(s, None, lambda v: ops_ext.barsince_vec(v))
        if op == "FILTER":
            # 信号抑制：成立输出 1 后 N-1 周期抑制重复。面板与 qlib 共用 ops_ext.filter_vec。
            s, N = args[0], args[1]
            if s is None:
                raise ValueError("FILTER 首参不能是常量")
            from . import ops_ext

            return _by_group(s, None, lambda v: ops_ext.filter_vec(v, int(N)))
        if op == "TRUNC":
            # 向零截断取整（np.trunc；3.7→3、-3.7→-3），NaN 保持
            s = args[0]
            if s is None:
                raise ValueError("TRUNC 参数不能是常量")
            return np.trunc(s)
        if op == "BETWEEN":
            # BETWEEN(X,A,B)：X∈[min(A,B), max(A,B)] → 1 否则 0；X NaN 保持 NaN
            x, a, b = args
            tpl = x if isinstance(x, pd.Series) else (a if isinstance(a, pd.Series) else b)
            x = _as_series(x, tpl)
            a = _as_series(a, tpl)
            b = _as_series(b, tpl)
            a, b = _align(a, b)
            lo = a.where(a <= b, b)
            hi = a.where(a >= b, b)
            out = ((x >= lo) & (x <= hi)).astype(np.float64)
            if x.isna().any():
                out = out.mask(x.isna())
            return out
        if op in ("IdxMax", "IdxMin", "Rank", "Slope", "Rsquare", "Resi", "Quantile"):
            # Alpha158 窗口类：逐股复刻 qlib Rolling 语义（min_periods=1），面板与 qlib 同源
            s = args[0]
            if s is None:
                raise ValueError(f"{op} 首参不能是常量")
            N = int(args[1])
            q = args[2] if op == "Quantile" else None
            return _by_group(s, None, lambda v: _seg_window_op(op, v, N, q))
        if op == "Corr":
            # Alpha158 相关：两序列逐股 rolling corr + std≈0 置 NaN（同 qlib Corr）
            a, b = args[0], args[1]
            N = int(args[2])
            if not isinstance(a, pd.Series) or not isinstance(b, pd.Series):
                raise ValueError("Corr 两参数须为序列")
            a, b = _align(a, b)
            return _corr_pair_panel(a, b, N)
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
                if fn == "pow":
                    return np.power(a, b)
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
        if op in ("BARSLAST", "BARSSINCEN", "HHVBARS", "LLVBARS",
                  "DYN_REF", "DYN_MIN", "DYN_MAX", "DYN_SUM", "DYN_COUNT",
                  "DYN_HHVBARS", "DYN_LLVBARS"):
            return _panel_dyn(op, args, self)
        raise ValueError(f"panel_expr 不支持算子 {op}")

    def eval_expr(self, expr: str) -> pd.Series:
        # 内存（v1.18.1）：节点缓存按"单个表达式"为界清理——多公式共用同一 evaluator
        # 时，若中间节点缓存跨公式累积（N 公式 × 全树节点同时驻留），每块峰值随勾选
        # 公式数线性放大（×12 worker 后家里小内存会爆）。同一表达式内部的公共子式
        # 缓存收益全部保留；跨表达式的完全重复子式极罕见（字段读取走 _field_cache /
        # 读盘 LRU，不受影响）。清空对数值零影响，只改缓存生命周期。
        self._node_cache.clear()
        self._node_bytes = 0
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
        r = getattr(valid.groupby(level=0, sort=False).rolling(N, min_periods=1), func)()
        out = pd.Series(np.nan, index=s.index, dtype=np.float64)
        # groupby-rolling 输出 index 比 valid 多一层组 key：用位置赋值规避。
        # 必须 sort=False：rolling 默认按组字典序排序，组输入顺序非字典序时
        # 按位置 to_numpy 回填会整池错位（跨股票窗口混算，实测 csi300+BJ 复现）。
        out.loc[valid.index] = r.to_numpy(dtype=np.float64)
        return out
    r = getattr(s.groupby(level=0, sort=False).rolling(N, min_periods=1), func)()
    # groupby-rolling 对 Series 输出会保留原 MultiIndex + 组前缀；
    # 位置与 s 对齐时直接取数值回填，保证 index = s.index
    if len(r) == len(s):
        out = pd.Series(r.to_numpy(dtype=np.float64), index=s.index)
    else:
        out = s.copy()
        out.loc[:] = np.nan
    return out


def _ema_apply_series(x: pd.Series, N, tdx: bool) -> pd.Series:
    """单股 EWM（对齐 qlib ops.EMA / ops_ext.EMA_TDX 的逐股实现）。

    tdx=False（qlib 内建 EMA）：N==0 → expanding 指数加权；0<N<1 → ewm(alpha=N,
      min_periods=1)；N>=1 → ewm(span=N, min_periods=1)。均 adjust=True（pandas 默认）。
    tdx=True（EMA_TDX 通达信递归式）：N==0 → expanding 均值；0<N<1 → ewm(alpha=N,
      adjust=False)；N>=1 → ewm(alpha=2/(N+1), adjust=False)。
    """
    n = float(N)
    if tdx:
        if n == 0:
            return x.expanding(min_periods=1).mean()
        if 0 < n < 1:
            return x.ewm(alpha=n, min_periods=1, adjust=False).mean()
        return x.ewm(alpha=2.0 / (n + 1), min_periods=1, adjust=False).mean()
    # qlib 内建 EMA
    if n == 0:
        arr = x.to_numpy(dtype=np.float64)
        out = np.full(len(arr), np.nan, dtype=np.float64)
        for i in range(len(arr)):
            win = arr[: i + 1]
            valid = win[~np.isnan(win)]
            if valid.size == 0:
                continue
            a = 1 - 2 / (1 + len(valid))
            w = a ** np.arange(len(valid))[::-1]
            out[i] = np.nansum(w * valid) / w.sum()
        return pd.Series(out, index=x.index)
    if 0 < n < 1:
        return x.ewm(alpha=n, min_periods=1).mean()
    return x.ewm(span=n, min_periods=1).mean()


def _sma_apply_series(x: pd.Series, N, M) -> pd.Series:
    """单股通达信 SMA：Y_t = (M·X_t + (N−M)·Y_{t−1}) / N → ewm(alpha=M/N, adjust=False)。

    与 ops_ext.SMA 逐位一致：ewm(alpha=M/N, adjust=False, min_periods=1)，起点敏感
    （从序列首值起递归）。N/M 为常量（codegen 已校验）。M 越接近 N 越贴近原值。
    """
    n = max(1, int(N))
    m = max(0, min(int(M), n))
    if n == 0 or m == 0:
        return x * np.nan
    return x.ewm(alpha=m / n, adjust=False, min_periods=1).mean()


def _ema(s: pd.Series, N, tdx: bool, sr: bool) -> pd.Series:
    """EMA(X, N) / EMA_TDX(X, N) 面板实现。

    语义与逐股一致（EMA 是每股独立指数加权）：
    - sr=False：s 是全日历（含停牌 NaN 行）。qlib 内建 EMA 直接用 ewm（pandas 遇 NaN
      保持 NaN 传播）——与 qlib 在含 NaN 全日历上逐股 ewm 一致。这里按组逐段调用
      _ema_apply_series（各股自己的连续段；段内含 NaN 由 ewm 处理，与 qlib 相同）。
    - sr=True：active 已是 close 有效行压缩序列（无停牌 NaN），逐段 EWM 后
      scatter 回全日历由 eval_expr 出口统一处理（此处直接按组返回即可）。
    """
    return _by_group(s, None, lambda v: _ema_apply_series(pd.Series(v), N, tdx).to_numpy(dtype=np.float64))


def _sma(s: pd.Series, N, M, sr: bool) -> pd.Series:
    """SMA(X, N, M) 面板实现（逐股 ewm alpha=M/N adjust=False；语义同 _ema 的分组处理）。"""
    return _by_group(s, None, lambda v: _sma_apply_series(pd.Series(v), N, M).to_numpy(dtype=np.float64))


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
    if op in ("HHVBARS", "LLVBARS"):
        s, N = args[0], int(args[1])
        if not isinstance(s, pd.Series):
            raise ValueError(f"{op} 首参不能是常量")
        is_max = op == "HHVBARS"
        return _by_group(s, None,
                         lambda v: ops_ext.HHVBARS._bars(v, N, is_max=is_max))
    s, ns = args
    if not isinstance(s, pd.Series) or not isinstance(ns, pd.Series):
        raise ValueError(f"{op} 参数须为序列")
    kind = {"DYN_REF": "ref", "DYN_MIN": "min", "DYN_MAX": "max",
            "DYN_SUM": "sum", "DYN_COUNT": "count",
            "DYN_HHVBARS": "hhvbars", "DYN_LLVBARS": "llvbars"}[op]
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
    if kind == "hhvbars":
        return ops_ext.dyn_bars_vec(vals, nvals, True)
    if kind == "llvbars":
        return ops_ext.dyn_bars_vec(vals, nvals, False)
    raise ValueError(kind)


def _bars_count_seg(v: np.ndarray) -> np.ndarray:
    """BARSCOUNT 单段内核：自数据起点累计非 NaN 交易日数（与 qlib notna().cumsum() 一致）。"""
    v = np.asarray(v, dtype=float)
    return np.cumsum(~np.isnan(v)).astype(np.float64)


# ---- Alpha158 窗口算子（逐股复刻 qlib Rolling 语义：rolling(N, min_periods=1)，
# 直接复用同一 pandas / qlib _libs 内核，保证逐位一致）----

def _seg_window_op(op: str, v: np.ndarray, N: int, q=None) -> np.ndarray:
    """单股段内核：IdxMax/IdxMin/Quantile/Rank/Slope/Rsquare/Resi。N>0 用 rolling，N=0 用 expanding。"""
    vv = np.asarray(v, dtype=np.float64)
    s = pd.Series(vv)
    if N <= 0:
        rolling = s.expanding(min_periods=1)
    else:
        rolling = s.rolling(int(N), min_periods=1)
    if op in ("IdxMax", "IdxMin"):
        is_max = op == "IdxMax"
        out = rolling.apply(lambda x: (x.argmax() if is_max else x.argmin()) + 1, raw=True)
        return out.to_numpy(dtype=np.float64)
    if op == "Quantile":
        return rolling.quantile(float(q)).to_numpy(dtype=np.float64)
    if op == "Rank":
        if hasattr(rolling, "rank"):
            return rolling.rank(pct=True).to_numpy(dtype=np.float64)
        # 旧 pandas 回退（同 qlib）：窗口内非 NaN 的百分位
        from scipy.stats import percentileofscore

        n = len(vv)
        nn = N if N > 0 else n
        out = np.full(n, np.nan)
        for i in range(n):
            w = vv[max(0, i - nn + 1): i + 1]
            wv = w[~np.isnan(w)]
            if wv.size == 0 or np.isnan(wv[-1]):
                continue
            out[i] = percentileofscore(wv, wv[-1]) / 100.0
        return out
    if op in ("Slope", "Rsquare", "Resi"):
        from qlib.data._libs.expanding import expanding_resi, expanding_rsquare, expanding_slope
        from qlib.data._libs.rolling import rolling_resi, rolling_rsquare, rolling_slope

        if N <= 0:
            fn = {"Slope": expanding_slope, "Rsquare": expanding_rsquare, "Resi": expanding_resi}[op]
            arr = np.asarray(fn(vv), dtype=np.float64)
        else:
            fn = {"Slope": rolling_slope, "Rsquare": rolling_rsquare, "Resi": rolling_resi}[op]
            arr = np.asarray(fn(vv, int(N)), dtype=np.float64)
        if op == "Rsquare":
            # qlib Rsquare 额外置 NaN：窗口内 X 滚动 std ≈ 0（atol 2e-05）
            sd = rolling.std().to_numpy(dtype=np.float64)
            arr = arr.copy()
            arr[np.isclose(sd, 0, atol=2e-05)] = np.nan
        return arr
    raise ValueError(f"panel_expr 不支持 {op}")


def _pair_seg_corr(va: np.ndarray, vb: np.ndarray, N: int) -> np.ndarray:
    """单股段相关：rolling/expanding corr(min_periods=1) + qlib Corr 的 std≈0 置 NaN。"""
    sa = pd.Series(np.asarray(va, dtype=np.float64))
    sb = pd.Series(np.asarray(vb, dtype=np.float64))
    if N <= 0:
        res = sa.expanding(min_periods=1).corr(sb)
        std_a = sa.expanding(min_periods=1).std().to_numpy(dtype=np.float64)
        std_b = sb.expanding(min_periods=1).std().to_numpy(dtype=np.float64)
    else:
        res = sa.rolling(int(N), min_periods=1).corr(sb)
        std_a = sa.rolling(int(N), min_periods=1).std().to_numpy(dtype=np.float64)
        std_b = sb.rolling(int(N), min_periods=1).std().to_numpy(dtype=np.float64)
    out = res.to_numpy(dtype=np.float64)
    out[np.isclose(std_a, 0, atol=2e-05) | np.isclose(std_b, 0, atol=2e-05)] = np.nan
    return out


def _corr_pair_panel(a: pd.Series, b: pd.Series, N: int) -> pd.Series:
    """Corr(X, Y, N) 面板实现：按 instrument 分段逐段算（两序列须同 index 对齐）。"""
    if len(a) == 0:
        return a
    idx = a.index
    arr_a = a.to_numpy(dtype=np.float64)
    arr_b = b.to_numpy(dtype=np.float64)
    lv = idx.get_level_values(0).to_numpy()
    n = len(a)
    change = np.empty(n, dtype=bool)
    change[0] = True
    np.not_equal(lv[1:], lv[:-1], out=change[1:])
    starts = np.flatnonzero(change)
    bnd = np.empty(len(starts) + 1, dtype=np.int64)
    bnd[:-1] = starts
    bnd[-1] = n
    out = np.empty(n, dtype=np.float64)
    for gi in range(len(starts)):
        s = bnd[gi]
        e = bnd[gi + 1]
        out[s:e] = _pair_seg_corr(arr_a[s:e], arr_b[s:e], N)
    return pd.Series(out, index=idx)


def _by_group(s: pd.Series, ns, fn) -> pd.Series:
    """按组（股票）把 fn 应用到每个连续 segment（ns 可为 None）。

    性能（v1.17.7）：原实现逐行 `inst[i]` 判组界 + 每股 `s.iloc[a:b]` pandas 切片
    （profile：322 万次 Index.__getitem__ + 数千次 MultiIndex _slice 是逐组执行层的
    隐藏大头）。改为 numpy 向量化组界（level 数组一次 != 比较）+ 单次 to_numpy 后
    的纯 numpy 切片，语义不变。
    """
    idx = s.index
    n = len(s)
    if n == 0:
        return pd.Series(dtype=np.float64, index=idx)
    arr = s.to_numpy(dtype=np.float64)
    if ns is None:
        ns_arr = None
    else:
        ns_arr = ns.to_numpy(dtype=np.float64)
    # 组界：level0 变化处（一次向量化比较，取代逐行 python）
    lv = idx.get_level_values(0).to_numpy()
    change = np.empty(n, dtype=bool)
    change[0] = True
    np.not_equal(lv[1:], lv[:-1], out=change[1:])
    starts = np.flatnonzero(change)
    bnd = np.empty(len(starts) + 1, dtype=np.int64)
    bnd[:-1] = starts
    bnd[-1] = n
    out = np.empty(n, dtype=np.float64)
    for gi in range(len(starts)):
        a = bnd[gi]
        b = bnd[gi + 1]
        if ns_arr is None:
            out[a:b] = fn(arr[a:b])
        else:
            out[a:b] = fn(arr[a:b], ns_arr[a:b])
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
        # 固定窗口算子 + Ref/EMA 的第二参（窗口/前移天数）；HHVBARS/LLVBARS 亦固定窗口；
        # Alpha158 窗口类（IdxMax/IdxMin/Rank/Slope/Rsquare/Resi 第二参、Quantile 第二参、
        # Corr 第三参）同样前移 N-1。
        for m in re.finditer(
            r"(?:Ref|Mean|Max|Min|Sum|Std|Var|Abs|Sqrt|EMA|EMA_TDX|HHVBARS|LLVBARS|"
            r"IdxMax|IdxMin|Rank|Slope|Rsquare|Resi|Quantile)\([^,]+,\s*(-?\d+)"
            r"|Corr\([^,]+,[^,]+,\s*(-?\d+)",
            e,
        ):
            try:
                for g in m.groups():
                    if g is not None:
                        max_k = max(max_k, abs(int(g)))
            except ValueError:
                pass
        if "SR(" in e:
            max_k = max(max_k, 250)
    return max_k + 30


# 需"精确冷启动起点"的递归算子：qlib 逐字段按 get_extended_window_size 精确前移
# N-1 天起算（EMA 是序列起点敏感的指数递归，读多了反而不对齐——固定窗口算子多读
# 无害，EMA 必须恰好 N-1）。这里只把这些算子的窗口参数也纳入 warm，供逐字段分组。
_EXT_SENSITIVE_OPS = ("EMA", "EMA_TDX")


def _tree_ext_days(node) -> int:
    """递归计算表达式的 qlib extended window（左侧交易日数）。

    复刻 qlib Expression.get_extended_window_size 的递归语义：
      - Rolling 类（Mean/Max/Min/Sum/Std/Var...）与 EMA/EMA_TDX：N>=1 → 子ext + (N-1)；
        N==0 → 子ext；0<N<1 → 子ext + log(1e-6)/log(1-N)（与 qlib 同式）。
      - Ref(x, N)：qlib 对左侧扩展 max(child_ext + N, child_ext)，即正 N（取过去）才加 N。
      - 其余（二元/If/And/Or/一元/逻辑）：两侧子 ext 取 max。
    叶子/常量 → 0。

    为什么必须递归整棵树：EMA 是"序列起点敏感"的指数递归，面板若只按最外层 EMA 的
    N-1 前移 read_start，而输入链里还嵌了 Max/Min/Ref 等固定窗口算子，qlib 会让它们
    也从各自 extended 起点起算（嵌套窗口沿树累加，如 EMA(Max($h,34),4) 总 extended
    = 33+3=36），面板起点晚 33 天 → EMA 整条序列永久偏移（实测同一日值差可达 0.37，
    0/1 阈值比较即翻面）。此递归把整棵树需要的最大前移量算准。
    """
    op = node.op
    if op in ("field", "const"):
        return 0
    args = node.args or []
    if op == "Corr":
        # qlib PairRolling：扩展 = max(左右子 ext) + N-1
        base = max(_tree_ext_days(a) for a in (args[0], args[1]) if hasattr(a, "op"))
        try:
            n = float(args[2].raw) if len(args) > 2 and getattr(args[2], "op", None) == "const" else 0.0
        except Exception:
            n = 0.0
        if n >= 1:
            return base + (int(n) - 1)
        return base
    if op in _ROLL_FUNC or op in ("EMA", "EMA_TDX", "HHVBARS", "LLVBARS",
                                  "IdxMax", "IdxMin", "Rank", "Slope", "Rsquare", "Resi", "Quantile"):
        base = _tree_ext_days(args[0]) if args else 0
        try:
            n = float(args[1].raw) if len(args) > 1 and getattr(args[1], "op", None) == "const" else 0.0
        except Exception:
            n = 0.0
        if n >= 1:
            return base + (int(n) - 1)
        if n == 0:
            return base
        # 0<N<1（qlib: alpha 型，需 ~log(1e-6)/log(1-N) 天收敛）
        import math as _math
        return base + max(0, int(_math.log(1e-6) / _math.log(1 - n)) - 1)
    if op == "Ref":
        base = _tree_ext_days(args[0]) if args else 0
        try:
            k = int(args[1].raw) if args[1].op == "const" else 0
        except Exception:
            k = 0
        return base + max(k, 0)  # 正 k=取过去 k 天前 → 左侧多读 k 天
    # 其余组合/一元：两侧取 max
    best = 0
    for a in args:
        if hasattr(a, "op"):
            best = max(best, _tree_ext_days(a))
    return best


def _expr_ext_days(expr: str) -> int:
    """计算单个表达式为精确复刻 qlib 需前移的交易日数（整棵树 extended，见 _tree_ext_days）。

    含嵌套固定窗口的输入链会正确累加（如 EMA(Max($h,34),4)=33+3=36，而非只认最外层 3）；
    不含敏感/窗口算子返回 0（统一 warm 宽余量对固定窗口无害）。
    """
    try:
        node = parse_expr(str(expr))
        return _tree_ext_days(node)
    except Exception:
        # 解析失败退化为旧的顶层 EMA 正则（保守兜底）
        import math as _math
        need = 0
        for m in re.finditer(r"(?:EMA|EMA_TDX)\([^,]+,\s*([\d.]+)", str(expr)):
            try:
                n = float(m.group(1))
            except ValueError:
                continue
            if n >= 1:
                need = max(need, int(n) - 1)
            elif n > 0:
                need = max(need, int(_math.log(1e-6) / _math.log(1 - n)))
        return need


def _shift_calendar_start(start_time: str, days: int) -> str:
    """把 start_time 前移 days 个交易日（不够则取数据首日）。"""
    cal = _calendar()
    t = pd.Timestamp(start_time)
    pos = int(np.searchsorted(cal, t, side="left"))
    prev = max(0, pos - days)
    return str(cal[prev].date())


def panel_features(instruments: Sequence[str], fields: Sequence[Tuple[str, str]],
                   start_time: str, end_time: str, cancel_cb=None) -> pd.DataFrame:
    """替代 D.features：一次面板求值 (expr, name) 列表 → MultiIndex × 列 DataFrame。

    与 D.features 输出对齐：行 = 各股在【全部参与字段覆盖并集】∩[start,end] 上的
    日历年（含停牌 NaN），列名 = name。读盘自动前移预热窗口（见 _warm_days），
    最终输出仅覆盖 [start_time, end_time]。
    fields: [(表达式文本, 列名), ...]。空表达式跳过。
    cancel_cb: 可选取消检查回调（主线程每求值一个表达式前调用一次）。约定：应取消时
    回调直接抛出异常中止求值（默认 None 不检查）。注意粒度 = 表达式级，单条巨型表达式
    内部仍不可中断。
    """
    union_fields = _collect_field_names(fields)
    warm = _warm_days([e for e, _ in fields])
    # 分组求值：qlib 逐字段按各自 get_extended_window_size 精确前移历史起点——
    # EMA/EMA_TDX 是序列起点敏感的指数递归，qlib 只前移 N-1 天冷启动，读多了反而不
    # 对齐（实测 EMA(5) 前移 4 天才与 qlib 全对、前移 25/56 天都偏）；固定窗口算子
    # （Mean/Max/Min/Ref 等）多读无害，统一用 warm 即可。因此把字段按各自"精确
    # 扩展天数"分桶，每桶一个 PanelEvaluator（桶内共享读盘缓存），输出拼回。
    # v1.17.8：起点敏感还包括"自起点状态类"算子（BARSCOUNT/BARSSINCE/FILTER）——
    # 它们的值取决于起点前的历史（累计/首成立/信号抑制），若被统一 warm 组多读，会与
    # qlib（自身扩展≈子树、多为 0）系统性偏移（实测 csi300 对拍差 32/37/1）→ 也走
    # 精确起点组（ext 允许为 0 = 逻辑起点，不强制最小扩展）。
    _STATE_START_OPS = ("BARSCOUNT(", "BARSSINCE(", "FILTER(")
    sensitive_fields = {}
    normal_fields = {}
    for expr, name in fields:
        e = str(expr).strip()
        if not e or e.lower() in ("", "none", "nan"):
            continue
        if "SR(" in e:
            # 含 SR 的字段：read_start 由 SR 前移主导（lookback 250），EMA/SMA 在其中
            # 从更早收敛点起算，与 qlib 一致（实测 SR(EMA) 全对）→ 归普通组。
            normal_fields.setdefault(warm, []).append((e, name))
        elif re.search(r"\b(?:EMA|EMA_TDX|SMA)\(", e):
            ext = _expr_ext_days(e)
            # EMA/EMA_TDX 是 Rolling 起点敏感（扩展>=N-1>=1）；SMA 的 qlib 扩展 =
            # 子特征透传（可为 0，如 SMA($close,5,1) 扩展为 0）→ 不强制 >=1。
            if ext < 1 and not re.search(r"\bSMA\(", e):
                ext = max(1, ext)
            # 该字段的精确起点 = 统一 warm 与"精确 N-1"的交集？不：qlib 只前移
            # N-1，故此处 read_start 前移量直接取 ext（比 warm 更晚），让 EMA
            # 恰好从 qlib 的冷启动点起算。
            sensitive_fields.setdefault(int(ext), []).append((e, name))
        elif any(_t in e for _t in _STATE_START_OPS):
            # 起点敏感状态算子：qlib 扩展 = 自身子树（可为 0），不强制最小前移
            ext = _expr_ext_days(e)
            sensitive_fields.setdefault(int(ext), []).append((e, name))
        else:
            # 固定窗口/普通算子：多读无害，统一 warm 组
            normal_fields.setdefault(warm, []).append((e, name))

    cols = {}
    # 普通组：统一 read_start（保持原语义，含 SR/固定窗口）
    if normal_fields:
        rs = _shift_calendar_start(start_time, warm)
        ev = PanelEvaluator(instruments, start_time, end_time, union_fields,
                            read_start=rs)
        for e, name in normal_fields[warm]:
            if cancel_cb is not None:
                cancel_cb()  # 取消检查点：表达式级（单条巨型表达式内不可中断）
            cols[name] = ev.eval_expr(e)
    # 敏感组：每桶按各自精确扩展量建独立 evaluator（桶共享 read_start/读盘缓存）
    for ext, items in sensitive_fields.items():
        rs = _shift_calendar_start(start_time, ext)
        ev = PanelEvaluator(instruments, start_time, end_time, union_fields,
                            read_start=rs)
        for e, name in items:
            if cancel_cb is not None:
                cancel_cb()
            cols[name] = ev.eval_expr(e)
    # 输出 = 逻辑区间 [start_time, end_time] 的并集（裁剪预热段）
    full_out = _union_index(instruments, start_time, end_time, union_fields)
    if not cols:
        return pd.DataFrame(index=full_out)
    df = pd.DataFrame(cols)
    # 不同 read_start 的列 index 覆盖范围不同 → 统一 reindex 到各列并集再裁剪
    df = df.reindex(df.index.union(full_out))
    df = df[df.index.get_level_values("datetime") >= pd.Timestamp(start_time)]
    df = df.reindex(full_out)
    return _cast_output_f32(df)


def _cast_output_f32(df: pd.DataFrame) -> pd.DataFrame:
    """把输出列统一 cast 到 float32——对齐 qlib D.features 的返回 dtype。

    背景（v1.17.5 后全 A 复现定位）：qlib D.features 所有字段最终输出整列为
    float32（内部 float64 求值、出口 cast，见 qlib data.py dtype=np.float32）；
    而面板此前全程 float64 输出。两者对 CLOSE/CHANGE 等列存在 ~4~8e-6 尾差
    （float32 ulp 级），本不影响因子值，但会传导到涨停/停牌剔除判定
    （mark_limit_up 用 close >= limit_up - 1e-6 的 1e-6 容差与面板尾差同量级）
    → 全 A 端到端触发集出现零星差异（实测 15 只各多/少 1 个触发、daily 差
    ~0.002pp）。内部求值保持 float64（更精确、EMA 递归收敛一致），仅出口
    cast float32 与 qlib 对齐后逐列 0 差（实测 base/tag 全部 cast 后 0 差）。
    """
    return df.astype(np.float32)


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


# ===========================================================================
# 4) 并行版（v1.16.9：全 A 大池。面板求值跨股票独立 → 按股票切块多进程并行）
# ===========================================================================

def _worker_init(calendar, feature_dir, bin_cache_mb=None):
    """子进程初始化：注入日历与数据目录（免 qlib.init）；可选收紧 bin 读盘缓存上限。"""
    set_panel_runtime(calendar=calendar, feature_dir=feature_dir)
    if bin_cache_mb is not None:
        set_bin_cache_mb(bin_cache_mb)


def _panel_features_chunk(payload):
    """子进程执行体：对一份股票子集跑 panel_features。payload: (insts, fields, start, end)。"""
    insts, fields, start, end = payload
    return panel_features(insts, fields, start, end)


@contextlib.contextmanager
def _nowin_spawn():
    """Windows 下让 multiprocessing spawn 的 python 子进程不弹黑色控制台窗口。

    根因：后端若以无控制台方式运行（如被 DETACHED_PROCESS 启动），spawn 的
    python.exe（console 子系统）子进程会被 Windows 分配【各自新的控制台窗口】
    → 用户看到弹出多个黑色命令行窗口。解法：把 spawn 的可执行文件临时切成
    同目录 pythonw.exe（GUI 子系统、天然无控制台），池用完即还原。

    仅影响 multiprocessing.spawn._python_exe（ProcessPoolExecutor 走这里）；
    qlib loky 用自己的 _python_exe（joblib externals loky.backend.spawn），互不影响。
    非 Windows 平台为空操作。
    """
    if os.name != "nt":
        yield
        return
    import sys as _sys

    old = None
    pythonw = None
    try:
        pythonw = os.path.join(os.path.dirname(_sys.executable), "pythonw.exe")
        if os.path.exists(pythonw):
            import multiprocessing.spawn as _sp

            old = _sp._python_exe
            _sp.set_executable(pythonw)
        yield
    finally:
        if old is not None and pythonw is not None:
            import multiprocessing.spawn as _sp

            _sp._python_exe = old


def _panel_executor(max_workers, initializer, initargs):
    """构造 ProcessPoolExecutor（Windows 下在 _nowin_spawn 上下文内创建/使用，免弹窗）。"""
    import concurrent.futures as cf

    return cf.ProcessPoolExecutor(max_workers=max_workers, initializer=initializer, initargs=initargs)


def _force_terminate_executor(ex):
    """取消兜底：强杀进程池所有 worker（含在跑块），立即归还内存。

    ProcessPoolExecutor.shutdown(wait=False, cancel_futures=True) 只能让主进程不再等待
    ——已派发给 worker 的块无法中途取消，worker 会跑完当前块才退出。全 A 大公式单块可跑
    数分钟，仅 shutdown 时取消后 worker 仍 100% CPU + 峰值内存驻留。这里直接 terminate
    全部 worker 进程（executor 私有 API `_processes`，CPython 3.7+ 稳定存在；terminate
    后 OS 立即回收 worker 内存）。仅供取消/异常路径使用：executor 随后不再 submit/收块。
    """
    try:
        procs = getattr(ex, "_processes", None)
        if procs:
            for p in list(procs.values()):
                try:
                    if p.is_alive():
                        p.terminate()
                except Exception:
                    pass
    except Exception:
        pass


def panel_features_parallel(instruments: Sequence[str], fields: Sequence[Tuple[str, str]],
                            start_time: str, end_time: str,
                            n_jobs: Optional[int] = None,
                            progress_cb=None,
                            progress_lo: float = 6.0, progress_hi: float = 30.0,
                            cancel_cb=None) -> pd.DataFrame:
    """并行面板求值（替代 panel_features 用于大池）。

    panel_features 的计算对每只股票独立（rolling/shift 按 instrument 分组、字段各读各的
    bin），因此把 instruments 切成 n_jobs 块后每块在独立进程求值，concat 结果等价于
    单块整体求值——但每进程只持有 1/n_jobs 的面板，内存与墙钟都大幅下降（全 A 实测
    单进程 ~300s / 8.6GB；12 核并行预期 ~20-40s / 单进程内存 1/12）。

    worker 进程通过 _worker_init 注入 calendar（免 qlib.init，calendar 与
    D.calendar() 完全一致：6455 行 2000-01-04 ~ 2026-08-21）。
    Windows 子进程用 pythonw.exe spawn，不弹黑色命令行窗口（_panel_executor）。

    progress_cb: 可选回调 progress_cb(pct, msg)。每完成一块报一次，pct 从
    progress_lo 线性推进到 progress_hi（默认 6→30，对应 single_test 的加载阶段
    0-30 进度）。回调只在主进程收集结果时触发，不进入子进程计算路径，不拖慢求值。
    cancel_cb: 可选取消检查回调（主进程每收一块前调用一次）。约定：应取消时回调直接
    抛异常中止收集；已提交给进程池的块无法中途终止，但剩余未收结果不再等待
    （最坏多等一块运行时间）。worker 内 panel_features 不传（无法感知主进程标志）。
    """
    import concurrent.futures as cf
    import math

    n = len(instruments)
    if n == 0:
        return pd.DataFrame()
    if n_jobs is None:
        # CPU 维度：每块 ≥300 只、≤12 worker
        jobs_cpu = min(12, max(1, math.ceil(n / 300)))
        # 内存维度（v1.18.1）：worker 峰值 ≈ 块内(公式节点 + 字段 + bin LRU) 的倍数，
        # 小内存机器开满 12 worker 会爆（多公式 × 并行 × 缓存驻留）。按可用内存 ÷
        # 单 worker 预算估算上限；QLIB_SFT_PANEL_MEM_PER_JOB_GB 可调（默认 3GB），
        # QLIB_SFT_PANEL_JOBS 显式指定则完全覆盖。
        avail_gb = _system_avail_gb()
        per_gb = float(os.environ.get("QLIB_SFT_PANEL_MEM_PER_JOB_GB", "3"))
        jobs_mem = max(1, int(avail_gb / per_gb))
        # 低水位加固（v1.18.1）：可用内存已紧张（默认 <6GB）再收敛到 ≤ avail/2 个 worker
        low = float(os.environ.get("QLIB_SFT_PANEL_LOW_MEM_GB", "6"))
        if avail_gb < low:
            jobs_mem = min(jobs_mem, max(1, int(avail_gb / 2)))
        n_jobs = max(1, min(jobs_cpu, jobs_mem, n))
    n_jobs = max(1, min(n_jobs, n))
    if n_jobs == 1 or n <= 1000:
        # 小池/单块：直接单进程（避免进程池固定开销）；同样支持取消检查点
        return panel_features(instruments, fields, start_time, end_time, cancel_cb=cancel_cb)

    cal = _calendar()
    fdir = _feature_dir()

    # 切块：把 instruments 均分成 n_jobs 份
    chunk_size = math.ceil(n / n_jobs)
    chunks = [instruments[i:i + chunk_size] for i in range(0, n, chunk_size)]
    n_chunk = len(chunks)
    payloads = [(c, fields, start_time, end_time) for c in chunks]

    # 每 worker bin 读盘缓存预算（v1.18.1 加固）：显式 QLIB_PANEL_BIN_CACHE_MB 优先；
    # 否则按可用内存均分（全部 worker 盘缓存合计 ≈ 可用内存 40%），避免
    # "12 worker × 768MB" 再叠数 GB。
    bin_cache_mb = None
    try:
        env_mb = os.environ.get("QLIB_PANEL_BIN_CACHE_MB")
        if env_mb is not None:
            bin_cache_mb = int(env_mb)
        else:
            _avail_mb = _system_avail_gb() * 1024.0
            bin_cache_mb = max(64, min(768, int(_avail_mb * 0.4 / n_jobs)))
    except Exception:
        pass

    parts: dict = {}
    done = 0
    with _nowin_spawn():
        cm = _panel_executor(len(chunks), _worker_init, (cal, fdir, bin_cache_mb))
        ex = cm.__enter__()
        try:
            futs = {ex.submit(_panel_features_chunk, p): i for i, p in enumerate(payloads)}
            pending = set(futs)
            # 轮询式收块：每 0.25s 查一次取消标志 + 收已完成块。相对 as_completed 阻塞
            # 等待，取消无需等"下一块完成"才被感知——用户点取消后 ≤0.25s 即响应。
            while pending:
                if cancel_cb is not None:
                    cancel_cb()  # 命中即抛，由调用方转为 FactorTestCancelled
                done_set, pending = cf.wait(pending, timeout=0.25, return_when=cf.FIRST_COMPLETED)
                for fut in done_set:
                    i = futs[fut]
                    r = fut.result()
                    if r is not None and len(r):
                        parts[i] = r
                    done += 1
                    if progress_cb:
                        # 线性映射 lo→hi；msg 显示已完块数（真实计算进度，非字节/耗时估算）
                        pct = progress_lo + (progress_hi - progress_lo) * (done / n_chunk)
                        progress_cb(pct, f"计算特征数据（面板并行，{done}/{n_chunk} 块完成）...")
        except BaseException:
            # 取消（异常路径）：强杀全部 worker 立即释放内存，不等在跑块。
            # 仅 shutdown(wait=False, cancel_futures=True) 只能停主进程收块——已派发的
            # 块无法取消，worker 会跑完当前块才退出（全 A 大公式单块可跑数分钟，取消后
            # worker 仍 100% CPU + 峰值内存驻留，全 A 多公式场景实测取消 7 分钟内存不减、
            # 接近 OOM）。terminate 后 OS 立即回收 worker 内存；executor 随即不再使用
            # （调用方已中止），安全。
            _force_terminate_executor(ex)
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            # 正常路径：等待全部块完成（等价原 with 语义）
            cm.__exit__(None, None, None)
    if not parts:
        return pd.DataFrame()
    # 每块 panel_features 已自行裁剪到该块股票的 union index（字段覆盖 ∩ [start,end]），
    # 跨块股票不重叠 → concat + sort 即完整（无需全局 reindex）。
    ordered = [parts[i] for i in range(n_chunk) if i in parts]
    return pd.concat(ordered).sort_index()
