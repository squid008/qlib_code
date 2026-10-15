# -*- coding: utf-8 -*-
"""**递推组**（自引用/互引的递归变量）运行时内核 —— v2.4.0，用户 2026-10-15 定稿 ✓。

## 它解决什么

公式语言原来**没有**"上一根的自己"这种东西 ✗（`A:=REF(A,1)+1;` 直接报"循环引用"✗），
于是每个递归指标（趋势线 / EMA / 各种"带状态"的算法）都得在 `ops_ext.py` 里**加一个原生算子** ✗
—— 用户 2026-10-15 明确否掉这条路："以后每个递归都要加算子那是不是不好？"
⇒ 做成**语言特性**：变量在自己（或组内别的变量）的定义式里被 `REF(x,k≥1)` 引用 ⇒ 编译成**递推节点** ✓。

## 语法与语义（用户 2026-10-15 定 ✓）

```
EMA_C:=REF(EMA_C,1)+1;              { 单变量自引用 ✓ }
输出:EMA_C;                          { 用当前值 ✓ }

{ 递推组：组内互引只允许 REF(x,k≥1)（同根互引直接报错 ✗）}
UP  :=IF(BARSCOUNT(C)<=3, 0, IF(REF(UP,1), IF(REF(LINE,1)>C,0,1), IF(REF(LINE,1)>=C,0,1)));
LINE:=IF(..., REF(LINE,1)+REF(STEP,1)*(REF(HIGH,1)-REF(LINE,1)), ...);
STEP:=IF(..., 0.02, IF(REF(STEP,1)<0.2, REF(STEP,1)+0.02, REF(STEP,1)));
BS:IF(UP AND NOT REF(UP,1), 2, IF(NOT UP AND REF(UP,1), -2, IF(UP,1,-1)));
```

- **初值默认 0** ✓（用户："首个没有值一般通行做法是不是就是默认 0？"✓）——
  即 `REF(x,k)` 在不足 k 根时取 0 ✓（与用户脚本里 `up=[] / line=[] / step` 的初值一致 ✓）。
- ⚠ **光有默认 0 不够** ✗（2026-10-15 实测）：头几根递推式会"看起来很合理"地跑出错误状态 ——
  趋势线里 `line=0 < 收盘` ⇒ 会判成"上破 ⇒ 转多" ✗，而用户脚本头 3 根是 `False` ✓。
  ⇒ 规范：公式里**用 `IF(BARSCOUNT(C)<=3, 0, …)` 显式锁住头几根** ✓（不需要新语法 ✓）。

## 三条硬约束（都是"不这样就对不上" ✗）

1. **O(N) 一遍推进** ✓：每根 K 线按**定义顺序**推进组内各成员，逐根只算一次（不是 O(N²)）；
2. **全程 float64** ✗ 不能中途降精度：用户脚本的 `STEP` 上限**恰好落在浮点误差上** ——
   `0.02` 累加到第 10 次是 `0.19999999999999998`（仍 `< 0.2` ⇒ 再加一次到 `0.21999999999999997` ✓，
   注释里"实际到 0.22"就是这么来的 ✓）；若中途降到 float32（= 我们 bin 的精度 ✗）该值会变成
   `0.2` ⇒ `< 0.2` 判据**翻转** ⇒ 趋势线翻向时机改变 ✗✗（`tests/test_recursion.py` 钉死这一处 ✓）；
3. **停牌/无 K 线的行**：状态**冻结**（不推进）、输出 NaN ✓（= 益盟"无停牌行"口径 ✓）。

## 骨架格式（编译期/求值期的中间表示 ✓）

叶子：`("c", 常量)` / `("a", 预计算数组下标)` / `("p", 成员序号 j, 回看根数 k)`
内部：`(算子名, [子节点…])` —— 只允许**逐元素**算子（窗口/序列算子请算到组外 ✗）。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np

Skel = tuple


def _cmp(ufunc):
    """比较算子：**必须 `np.asarray` 兜一层** ✗。

    ⚠ 2026-10-15 对拍抓到的真 bug ✓：首根 `("p", j, k)` 越界返回的是**标量 0**（不是数组 ✓），
    于是 `Lt(REF(STEP,1), 0.2)` 在首根就变成"两个标量比大小" ⇒ 结果是 **Python `bool`** ✗ ⇒
    `.astype(np.float64)` 报 `'bool' object has no attribute 'astype'` ✗（用户公式一跑就炸 ✗）。
    `np.asarray(..., dtype=float64)` 同时覆盖"标量 ⇒ 0 维数组"与"数组 ⇒ 数组"两种情况 ✓，
    且对 NaN 的语义与面板一致（`NaN > x` ⇒ False ⇒ 0 ✓）。
    """
    def _f(a, b):
        with np.errstate(all="ignore"):
            return np.asarray(ufunc(a, b), dtype=np.float64)
    return _f


def _logic(kind: str):
    """And/Or：与面板一致 —— **先 fillna(0) 再 !=0** ✓（NaN 记 0 ⇒ 假 ✓）。"""
    def _f(a, b):
        with np.errstate(all="ignore"):
            ma, mb = (_nan0(a) != 0), (_nan0(b) != 0)
            m = (ma & mb) if kind == "And" else (ma | mb)
            return np.asarray(m, dtype=np.float64)
    return _f

#: 组内允许的算子（逐元素；语义必须与 `panel_expr._apply` **逐位一致** ✓）
_OPS: Dict[str, object] = {
    # 算术：用 numpy ufunc（不是 Python 运算符 ✗）—— 两个标量时 Python 的 `0/0` 会抛
    # ZeroDivisionError ✗，而面板口径是"出 inf/nan、由后续清洗处理"✓（与 `_NUM_UFUNC` 一致 ✓）。
    "Add": np.add, "Sub": np.subtract, "Mul": np.multiply, "Div": np.divide,
    # 比较：qlib/面板口径 = ufunc 结果再 astype(float64) ✓（NaN 比较恒 False ⇒ 0 ✓）
    # ⚠ 一律走 `_cmp`（内部 asarray）⇒ 标量 vs 标量也不会炸 ✗（首根越界那条 ✓）
    "Gt": _cmp(np.greater), "Ge": _cmp(np.greater_equal),
    "Lt": _cmp(np.less), "Le": _cmp(np.less_equal),
    "Eq": _cmp(np.equal), "Ne": _cmp(np.not_equal),
    # 逻辑：面板口径 = **先 fillna(0) 再 !=0** ✓（NaN 记 0 ⇒ 假 ✓，与 numpy 的 NaN!=0 为真不同 ✗）
    "And": _logic("And"),
    "Or": _logic("Or"),
    # IF：面板口径 = `a.where(cond.astype(bool), b)` ✓ ⇒ cond 为 NaN 时取 **a** ✓
    "If": lambda c, a, b: np.asarray(np.where(_truthy(c), a, b), dtype=np.float64),
    "Abs": np.abs, "Sqrt": np.sqrt, "Log": np.log, "Exp": np.exp, "Sgn": np.sign,
    # MAX/MIN（通达信 = 两值取大/小 ⇒ codegen 出 Greater/Less）：
    # ⚠ 必须与面板一致：**NaN 时取另一个** ✓（`np.where(a>=b,a,b)`，不是 np.fmax ✗）
    "Greater": lambda a, b: np.where(a >= b, a, b),
    "Less": lambda a, b: np.where(a <= b, a, b),
    "Pow": np.power, "Power": np.power, "Mod": np.fmod,
    "ROUND": lambda x, nd=0: np.round(x, nd),
    "TRUNC": np.trunc,
}

#: 组内**不支持**的算子（给一条能自助的错 ✓）
_SEQ_OPS = {
    "Ref": "REF", "Mean": "MA/MEAN", "Sum": "SUM", "Max": "HHV", "Min": "LLV",
    "EMA": "EMA", "SMA": "SMA", "EMA_TDX": "EMA_TDX", "BARSLAST": "BARSLAST",
    "BARSCOUNT": "BARSCOUNT", "FILTER": "FILTER", "Count": "COUNT",
    "HHVBARS": "HHVBARS", "LLVBARS": "LLVBARS",
}


class RecursionError_(Exception):
    """递推组求值/编译期错误（消息面向用户，可直接展示 ✓）。"""


def _nan0(x):
    return np.where(np.isnan(x), 0.0, x)


def _truthy(x):
    """与面板 `cond.astype(bool)` 逐位一致 ✓（NaN ⇒ True ✓）。"""
    return x != 0.0


def eval_skeleton(node: Skel, i: int, pre: Sequence[np.ndarray],
                  out: Sequence[np.ndarray], default: float = 0.0):
    """在第 `i` 根上求一个成员表达式的值（`out[j]` = 成员 j 已算出的历史 ✓）。

    `pre[k]` = 第 k 个"非递推子树"的整条数组（`("a",k)` 叶子取 `pre[k][i]` ✓）；
    `("p",j,k)` 叶子取成员 j 的第 `i-k` 根（`i-k < 0` ⇒ `default` = 0 ✓）。
    """
    kind = node[0]
    if kind == "c":
        return node[1]
    if kind == "a":
        return pre[node[1]][i]
    if kind == "p":
        j, k = node[1], node[2]
        ii = i - k
        if ii < 0:
            return default
        return out[j][ii]
    if kind == "m":
        # 同根引用**先定义**的成员（语义层已保证顺序 ✓）⇒ 本根刚算好的值 ✓
        return out[node[1]][i]
    fn = _OPS.get(kind)
    if fn is None:
        hint = _SEQ_OPS.get(kind)
        raise RecursionError_(
            "递推组内不支持算子 %s ✗\n"
            "  · 组内只允许**逐元素**运算（+ - * / 比较 AND/OR IF ABS MAX MIN …）✓\n"
            "  · 用递推变量的上一根 ⇒ 写 `REF(变量, k)`（k≥1，会被编译成组内回看 ✓）；\n"
            "  · %s" % (kind, (
                "`%s` 是窗口/序列算子 ⇒ 请把它**算到组外**（例如组里只存原始序列，窗口在组外做 ✗）✓"
                % hint) if hint else "请改写为逐元素写法 ✓"))
    vals = [eval_skeleton(a, i, pre, out, default) for a in node[1]]
    with np.errstate(all="ignore"):
        return fn(*vals)


def run_group(skels: Sequence[Skel], n_members: int, pre: Sequence[np.ndarray],
              valid: np.ndarray, default: float = 0.0) -> List[np.ndarray]:
    """按根推进整组（**每根一次、按定义顺序** ✓），返回每个成员的 (T×C) 矩阵。

    `valid[t, c]` = False 表示"该股当日没有 K 线"（停牌/未上市）⇒ 状态**冻结**、输出 NaN ✓。
    返回的矩阵在无效行上是 NaN ✓（= 益盟"无停牌行"口径 ✓）。
    """
    t_len = int(valid.shape[0])
    c_len = int(valid.shape[1])
    out = [np.full((t_len, c_len), float(default), dtype=np.float64) for _ in range(n_members)]
    # 无输入时的日期轴长度兜底（成员全是常量也算得出 T ✓）
    if t_len == 0:
        return [np.full((0, c_len), np.nan) for _ in range(n_members)]
    for i in range(t_len):
        v = valid[i]
        for j, sk in enumerate(skels):
            val = eval_skeleton(sk, i, pre, out, default)
            arr = val if isinstance(val, np.ndarray) else np.float64(val)
            arr = np.broadcast_to(np.asarray(arr, dtype=np.float64), (c_len,))
            prev = out[j][i - 1] if i else np.float64(default)
            out[j][i] = np.where(v, arr, prev)
    return [np.where(valid, m, np.nan) for m in out]


def float_accumulation_note() -> Tuple[float, float]:
    """返回 `(第 10 次累加值, 再过一次的值)` —— 供测试/文档引用**这条浮点硬约束** ✓。

    用户脚本：`STEP_INIT=0.02 / STEP_MAX=0.2 / if step < STEP_MAX: step += STEP_INIT`
    ⇒ 第 10 次 = `0.19999999999999998`（**仍 < 0.2** ⇒ 会再加一次到 `0.21999999999999997` ✓）。
    """
    v = 0.0
    for _ in range(10):
        v += 0.02
    v10 = v
    return v10, v10 + 0.02
