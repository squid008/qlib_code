# -*- coding: utf-8 -*-
"""公式翻译器：代码生成（AST → qlib 表达式字符串 / 外挂算子标记）。

把已内联的表达式树翻译成 qlib 可加载的表达式字符串。
不支持的算子（Level2 深度、需外挂的有状态算子）抛出 CodeGenError 并给出清晰提示。
"""
from __future__ import annotations

import math
from typing import List

from .ast import Expr, Num, Field, Var, BinOp, UnaryOp, FuncCall
from .lexer import LexerError


# ---- ★ v1.20.55：可**常量折叠**的纯数学函数表 ----
# 只放"**无状态、逐元素、纯函数**"的 ✓（结果只依赖参数值 ⇒ 常量参数必有常量答案 ✓）。
# ⚠ 绝不放 `REF/MA/HHV/LLV/SUM/COUNT/BARSLAST/SMA/FILTER` 这类**依赖历史序列**的 ✗ ——
#   它们没有"常量答案" ✓，塞进来会让常量折叠算出错误结果 ✗。
# 语义必须与 `codegen`/`ops_ext` 里对应算子**逐位一致** ✓（例如 `MOD` 用 `math.fmod` ✓
# —— 符号随被除数 ✓，与我们的 `Mod` 算子同口径 ✓，而 Python 的 `%` 是符号随除数 ✗）。
_CONST_FOLD_FUNCS = {
    "ABS": abs,
    "SQRT": math.sqrt,                     # 负值 ⇒ ValueError ⇒ **不折** ✓
    "LOG": math.log, "LN": math.log,       # ≤0 ⇒ ValueError ⇒ 不折 ✓
    "EXP": math.exp,
    "POW": lambda a, b: a ** b, "POWER": lambda a, b: a ** b,
    "MAX": max, "MIN": min,                # 两值取大/小（与 Greater/Less 同义 ✓）
    "MOD": math.fmod,
    "INT": math.trunc,
    "SGN": lambda v: (v > 0) - (v < 0), "SIGN": lambda v: (v > 0) - (v < 0),
    "ROUND": round,
}

# ---- ★ v1.20.58：**比较 / 逻辑**（`GT/GE/LT/LE/EQ/NE/AND/OR`）也必须参与常量折叠 ----
# ⚠ 它们**不是 `FuncCall`** ✗ —— 源码里的 `<` `>=` `AND` 经词法/语法后是 **`BinOp`** ✓
#   （`BINOP_MAP` 的键就是这些 op ✓）；`Lt(...)` 那种写法只在**生成阶段**才出现 ✓。
#   ⇒ 折叠代码必须写在 `_const_fold` 的 **`BinOp` 分支**里 ✗（曾把表加到 FuncCall 分支 ⇒ 打空靶 ✓，
#     真机复现实测仍然崩 `'numpy.bool' object has no attribute 'name'` ✗）。
# 为什么必须折（用户 2026-09-23 报「深跌10（周期 60 天）：特征计算失败: 'numpy.bool' object has no
# attribute 'name'」✗）：`IF(n<3, …)` 里 n 是**常量** ⇒ 生成 `Lt(10,3)` ✗ —— 一个"**没有任何
# `$字段` 的算子子树**" ✗ ⇒ qlib 求值即崩 ✓（v1.19.90 治过 `numpy.int64` 那一版 ✓、
# v1.20.53 治过 `NOT(1)` ⇒ `Eq(1,0)` 那一版 ✓ —— 这是**同一个坑的第三次** ✗）。
# ⚠ 折成 `1.0 / 0.0` 与运行期的 `True/False` **逐位等价** ✓（bool 是 int 的子类型 ✓，
#   `If`/`Gt`/`Mul` 等下游算子对二者一视同仁 ✓）。
# ⚠ 逻辑必须照抄**运行期口径**（`ops_ext.And/Or` = `(a!=0)&(b!=0)` ✓「非 0 即真」✓），
#   **不能**用 Python 的 `and/or` ✗ —— 后者返回的是**操作数本身**（`0.5 and 2` ⇒ 2 ✗ 语义不同 ✓）。


class CodeGenError(Exception):
    pass


# ---- 行情字段映射（大写 → qlib $field）----
FIELD_MAP = {
    "CLOSE": "$close", "C": "$close",
    "HIGH": "$high", "H": "$high",
    "LOW": "$low", "L": "$low",
    "OPEN": "$open", "O": "$open",
    "VOL": "$volume", "V": "$volume",
    "VOLUME": "$volume",
    "AMOUNT": "$amount",
    "TURNOVERRATE": "$turn",
    "VWAP": "$vwap",
    "MARKET_CAP": "$market_cap",
    # 资金流向字段（moneyflow bin，tools/dump_moneyflow.py 生成）
    "MF_AMOUNT_MAIN": "$mf_amount_main", "MF_PCT_MAIN": "$mf_pct_main",
    "MF_AMOUNT_XL": "$mf_amount_xl", "MF_PCT_XL": "$mf_pct_xl",
    "MF_AMOUNT_L": "$mf_amount_l", "MF_PCT_L": "$mf_pct_l",
    "MF_AMOUNT_M": "$mf_amount_m", "MF_PCT_M": "$mf_pct_m",
    "MF_AMOUNT_S": "$mf_amount_s", "MF_PCT_S": "$mf_pct_s",
    # 资金流向买卖方向字段（moneyflow3 源派生，dump_moneyflow.py 生成；b=买入/s=卖出）
    "MF_AMOUNT_MAIN_B": "$mf_amount_main_b", "MF_AMOUNT_MAIN_S": "$mf_amount_main_s",
    "MF_PCT_MAIN_B": "$mf_pct_main_b", "MF_PCT_MAIN_S": "$mf_pct_main_s",
    "MF_AMOUNT_XL_B": "$mf_amount_xl_b", "MF_AMOUNT_XL_S": "$mf_amount_xl_s",
    "MF_PCT_XL_B": "$mf_pct_xl_b", "MF_PCT_XL_S": "$mf_pct_xl_s",
    "MF_AMOUNT_L_B": "$mf_amount_l_b", "MF_AMOUNT_L_S": "$mf_amount_l_s",
    "MF_PCT_L_B": "$mf_pct_l_b", "MF_PCT_L_S": "$mf_pct_l_s",
    "MF_AMOUNT_M_B": "$mf_amount_m_b", "MF_AMOUNT_M_S": "$mf_amount_m_s",
    "MF_PCT_M_B": "$mf_pct_m_b", "MF_PCT_M_S": "$mf_pct_m_s",
    "MF_AMOUNT_S_B": "$mf_amount_s_b", "MF_AMOUNT_S_S": "$mf_amount_s_s",
    "MF_PCT_S_B": "$mf_pct_s_b", "MF_PCT_S_S": "$mf_pct_s_s",
    # 资金流向量字段（手，moneyflow3 源 _bq/_sq 派生，L2_VOL 用）
    "MF_VOL_MAIN": "$mf_vol_main", "MF_VOL_XL": "$mf_vol_xl",
    "MF_VOL_L": "$mf_vol_l", "MF_VOL_M": "$mf_vol_m", "MF_VOL_S": "$mf_vol_s",
    "MF_VOL_MAIN_B": "$mf_vol_main_b", "MF_VOL_MAIN_S": "$mf_vol_main_s",
    "MF_VOL_XL_B": "$mf_vol_xl_b", "MF_VOL_XL_S": "$mf_vol_xl_s",
    "MF_VOL_L_B": "$mf_vol_l_b", "MF_VOL_L_S": "$mf_vol_l_s",
    "MF_VOL_M_B": "$mf_vol_m_b", "MF_VOL_M_S": "$mf_vol_m_s",
    "MF_VOL_S_B": "$mf_vol_s_b", "MF_VOL_S_S": "$mf_vol_s_s",
}

# ---- 资金流向档位选择函数 L2_PCT(n)/L2_AMO(n)/L2_VOL(n[,b|s]) ----
# n=0 主力(main) 1 超大单(xl) 2 大单(l) 3 中单(m) 4 小单(s)；
# 与 tools/dump_moneyflow.py 的 TIERS 下标一致。
# 可选第二参 b=买入/s=卖出（moneyflow3 源派生方向字段）：
#   L2_AMO(n)      -> 档位 n 净额（万元）   = mf_amount_<档>
#   L2_AMO(n,b|s)  -> 档位 n 买入/卖出额     = mf_amount_<档>_b/_s
#   L2_PCT(n)      -> 档位 n 净占比（%）     = mf_pct_<档>
#   L2_PCT(n,b|s)  -> 档位 n 买入/卖出占比   = mf_pct_<档>_b/_s
#   L2_VOL(n)      -> 档位 n 净流入量（手）  = mf_vol_<档>
#   L2_VOL(n,b|s)  -> 档位 n 买入/卖出量     = mf_vol_<档>_b/_s
# 口径：net = b − s，pct 统一以"当日总成交额（4 档买之和）"为分母，故
#   L2_AMO(n,b) − L2_AMO(n,s) == L2_AMO(n)（float32 舍入量级）、
#   L2_PCT(n) == L2_PCT(n,b) − L2_PCT(n,s)、L2_VOL 同。
L2_PCT_FIELDS = [
    "$mf_pct_main", "$mf_pct_xl", "$mf_pct_l", "$mf_pct_m", "$mf_pct_s",
]
L2_AMO_FIELDS = [
    "$mf_amount_main", "$mf_amount_xl", "$mf_amount_l", "$mf_amount_m", "$mf_amount_s",
]
L2_VOL_FIELDS = [
    "$mf_vol_main", "$mf_vol_xl", "$mf_vol_l", "$mf_vol_m", "$mf_vol_s",
]
# 方向字段（行=档位 0..4，列=[买入, 卖出]）
L2_PCT_DIR_FIELDS = [
    ["$mf_pct_main_b", "$mf_pct_main_s"],
    ["$mf_pct_xl_b", "$mf_pct_xl_s"],
    ["$mf_pct_l_b", "$mf_pct_l_s"],
    ["$mf_pct_m_b", "$mf_pct_m_s"],
    ["$mf_pct_s_b", "$mf_pct_s_s"],
]
L2_AMO_DIR_FIELDS = [
    ["$mf_amount_main_b", "$mf_amount_main_s"],
    ["$mf_amount_xl_b", "$mf_amount_xl_s"],
    ["$mf_amount_l_b", "$mf_amount_l_s"],
    ["$mf_amount_m_b", "$mf_amount_m_s"],
    ["$mf_amount_s_b", "$mf_amount_s_s"],
]
L2_VOL_DIR_FIELDS = [
    ["$mf_vol_main_b", "$mf_vol_main_s"],
    ["$mf_vol_xl_b", "$mf_vol_xl_s"],
    ["$mf_vol_l_b", "$mf_vol_l_s"],
    ["$mf_vol_m_b", "$mf_vol_m_s"],
    ["$mf_vol_s_b", "$mf_vol_s_s"],
]

# ---- 二元运算 → qlib 表达式 ----
BINOP_MAP = {
    "ADD": "Add", "SUB": "Sub", "MUL": "Mul", "DIV": "Div",
    "GT": "Gt", "GE": "Ge", "LT": "Lt", "LE": "Le",
    "EQ": "Eq", "NE": "Ne",
    "AND": "And", "OR": "Or",
}

# ---- 一元运算 ----
# ⚠ 这里**故意为空** ✗（v1.20.54 清理）：
#   · `NOT` ⇒ 展开成 `Eq(X,0)` ✓（在 `_g` 里单独分支 ✓）；
#   · `NEG` ⇒ 数字字面量直接写负数、复杂表达式写 `Mul(-1,X)` ✓（`_g` 里单独分支 ✓）；
#   —— 原表里那个 `"NEG": "Neg"` **从来没被生成过** ✗，而 qlib 注册表里也**没有 `Neg`** ✗
#   （实测 `hasattr(Operators,'Neg')` = False ✓）⇒ 留着只会误导，且会让"算子名守卫测试"误报 ✓。
UNARY_MAP: dict = {}


def _const_fold(e: Expr, allow_div: bool = False):
    """对纯常量子树做常量折叠求值；子树含行情字段/变量/函数时返回 None。

    两个用途：
    - 默认（`allow_div=False`）：编译期检测**恒等于 0 的除数**（`OUT:CLOSE/0`、`OUT:CLOSE/(5-5)`）✓；
    - `allow_div=True`：**把纯常量子树折叠成一个字面量**（v1.19.90）—— 见 `_g` 里的详细说明：
      qlib 遇到"没有任何 `$字段` 的子树"会崩（`'numpy.int64' object has no attribute 'name'`）✗。
    """
    if isinstance(e, Num):
        return float(e.value)
    # ★ v1.20.55：**纯数学函数**且参数全是常量 ⇒ 折成字面量 ✓
    #   用户 2026-09-23 要求：「`SQRT(2)*CLOSE` 这种常量参数的函数也修掉吧」✓ ——
    #   原先会生成 `Mul(Sqrt(2),$close)` ✗，而 qlib 对"**没有任何 `$字段` 的子树**"敏感
    #   ⇒ 触发 v1.19.90 记录的那个崩（`'numpy.int64' object has no attribute 'name'` ✗）。
    if isinstance(e, FuncCall):
        name = e.name.upper()
        fn = _CONST_FOLD_FUNCS.get(name)
        if fn is None:
            # ★ v1.20.58：`IF(常量条件, 常量A, 常量B)` —— 三个参数**全是常量** ⇒ 折成被选中的分支 ✓
            #   （两边都是常量 ⇒ 丢掉一边**不会掩盖**任何"依赖于数据"的错误 ✓；也顺手让 `IF(n<3,…)`
            #    这种"参数写死的开关"直接消失 ✓）。⚠ 只在**条件与两个分支都常量**时才折 ✗ ——
            #    只折条件、留下 `If(0, A, B)` 也行 ✓（见下方真机验证 ✓），但那样仍留一个常量节点 ✓。
            if name == "IF" and len(e.args) == 3:
                c = _const_fold(e.args[0], allow_div)
                if c is not None:
                    return _const_fold(e.args[1] if c != 0 else e.args[2], allow_div)
            return None
        vals = []
        for a in e.args:
            v = _const_fold(a, allow_div)
            if v is None:
                return None
            vals.append(v)
        try:
            r = fn(*vals)
        except Exception:                                      # noqa: BLE001
            return None                     # 参数越界/类型不合（如 SQRT(-1)）⇒ 不折 ✓
        r = float(r)
        # ⚠ 非有限值（NaN/inf）也**不折** ✗ —— 否则表达式里会出现 `nan` 字面量，qlib 解析不了 ✓
        return r if math.isfinite(r) else None
    if isinstance(e, UnaryOp):
        # ⚠ 递归必须把 `allow_div` **传下去**（v1.19.90 踩坑）：否则"含除法"的父节点永远折不动 ✗
        #   ⇒ 表现成"只有最内层 `Div` 折了、外层原样输出"（LLT 的 W0 整块就是被这个卡住的）。
        v = _const_fold(e.operand, allow_div)
        if v is None:
            return None
        if e.op == "NEG":
            return -v
        if e.op == "NOT":
            # ★ v1.20.53：常量逻辑非（益盟/同花顺口径 ✓：X==0 ⇒ 1，否则 ⇒ 0）——
            #   ⚠ 必须在这里折叠 ✗：否则 `NOT(1)` 会生成 `Eq(1,0)`，那是一棵**没有任何
            #   `$字段` 的子树** ⇒ qlib 加载时会崩（`'numpy.int64' object has no attribute 'name'`
            #   ✗，见 `_g` 里 v1.19.90 的详细记录 ✓）。
            return 1.0 if v == 0 else 0.0
        return None
    if isinstance(e, BinOp):
        lv = _const_fold(e.left, allow_div)
        rv = _const_fold(e.right, allow_div)
        if lv is None or rv is None:
            return None
        if e.op == "ADD":
            return lv + rv
        if e.op == "SUB":
            return lv - rv
        if e.op == "MUL":
            return lv * rv
        if e.op == "DIV":
            if not allow_div:
                # 除法结果继续折叠会传播 inf/nan，这里直接返回 None 交给外层除零检测
                return None
            return None if rv == 0 else lv / rv      # 除零 ⇒ None（外层会报「除数为 0」）
        # ★ v1.20.58：**比较 / 逻辑**（源码里的 `<`、`>=`、`AND` … 在 AST 里是 BinOp ✓）
        #   —— 必须折成 1.0/0.0 ✗→✓，否则留下"没有任何 `$字段` 的算子子树" ⇒ qlib 崩 ✗
        #   （`'numpy.bool' object has no attribute 'name'` ✓，真机复现一致 ✓；详见文件顶部说明 ✓）。
        if e.op == "GT":
            return 1.0 if lv > rv else 0.0
        if e.op == "GE":
            return 1.0 if lv >= rv else 0.0
        if e.op == "LT":
            return 1.0 if lv < rv else 0.0
        if e.op == "LE":
            return 1.0 if lv <= rv else 0.0
        if e.op == "EQ":
            return 1.0 if lv == rv else 0.0
        if e.op == "NE":
            return 1.0 if lv != rv else 0.0
        if e.op == "AND":
            # ⚠ 运行期口径「非 0 即真」（`ops_ext.And` = `(a!=0)&(b!=0)` ✓），不是 Python 的 `and` ✗
            return 1.0 if (lv != 0 and rv != 0) else 0.0
        if e.op == "OR":
            return 1.0 if (lv != 0 or rv != 0) else 0.0
        return None
    return None

# ---- 函数 → qlib 表达式（直接映射）----
# 说明：BARSLAST/BARSCOUNT/BARSSINCEN 及 DYN_* 是自定义外挂算子（app/factors/ops_ext.py），
# qlib 解析表达式字符串时通过 Operators 注册表查找同名类。
# 通达信语义：HHV/LLV 是滚动窗口极值；MAX/MIN 是两值取大/小（qlib 的 Greater/Less）。
# EMA 语义开关（保留两种实现，方便来回切换，见 _ema_op_name）：
#   "qlib"：qlib 内建 EMA（pandas ewm(span=N, min_periods=1, adjust=True)，整段归一化），
#           与聚宽/同事 notebook（qsdd_signal 的 ewm(span=4, adjust=True, min_periods=1)）
#           逐位一致，作为对账基准。2026-09-04 起默认。
#   "tdx" ：外挂 EMA_TDX（ewm(alpha=2/(N+1), adjust=False)，通达信递归式
#           Y_t=(2·X_t+(N-1)·Y_{t-1})/(N+1)），两者仅在序列开头（上市初期/次新股）有
#           初值差异、随后指数收敛。
# 切换方法：改下面 EMA_SEMANTICS 后，把已保存公式重新保存一遍
# （PUT /api/factors/custom-formulas/{id} 或前端编辑保存）让 expression 按新语义重新生成。
EMA_SEMANTICS = "qlib"  # "qlib"（内建 EMA，聚宽口径）| "tdx"（EMA_TDX，通达信递归式）


def _ema_op_name() -> str:
    """当前 EMA 语义对应的 qlib 算子名。"""
    return "EMA_TDX" if EMA_SEMANTICS == "tdx" else "EMA"


FUNC_QLIB = {
    "MA": "Mean", "EMA": "EMA", "WMA": "WMA",
    "HHV": "Max", "LLV": "Min",
    "SUM": "Sum", "COUNT": "Count",
    "ABS": "Abs", "SQRT": "Sqrt", "LOG": "Log", "LN": "Log",
    # v1.20.34：EXP(X)=e^X（研报公式常用：Alpha101/GTJA 系列里 EXP(POW(...)) 等组合很常见 ✓）
    #   ⚠ 之前**不支持** ⇒ 用户写 `EXP(...)` 直接 `CodeGenError: 不支持的函数：EXP` ✗
    "EXP": "Exp",
    "POW": "Power",   # qlib 内建名是 Power（曾误映射 Pow → "operator [Pow] is not registered"）
    "POWER": "Power",  # 别名：POWER(X,Y)=X^Y（Excel/部分软件写法，与 POW 等价）
    "MAX": "Greater", "MIN": "Less", "MOD": "Mod",
    "STD": "Std", "VAR": "Var", "SLOPE": "Slope",
    "REF": "Ref", "DELTA": "Delta", "MEAN": "Mean", "MED": "Med",
    "IF": "If", "IFS": "If",
    "BARSLAST": "BARSLAST",
    "BARSCOUNT": "BARSCOUNT",
    "BARSSINCEN": "BARSSINCEN",
    # 通达信基础函数：EMA_TDX 外挂算子（qlib 内建 EMA 是 adjust=True 口径，公式显式用
    # EMA_TDX 即通达信递归式）；SGN/SIGN 取符号；INT 向零截断取整；BETWEEN 区间判定
    "EMA_TDX": "EMA_TDX",
    "SGN": "SGN", "SIGN": "SGN",
    "INT": "TRUNC",
    "BETWEEN": "BETWEEN",
    # 动态窗口外挂算子（用户也可直接调用 DYN_MIN/MAX/COUNT/REF/SUM）
    "DYN_MIN": "DYN_MIN",
    "DYN_MAX": "DYN_MAX",
    "DYN_COUNT": "DYN_COUNT",
    "DYN_REF": "DYN_REF",
    "DYN_SUM": "DYN_SUM",
    # 通达信有状态算子（M3，ops_ext.py 注册；均为过去数据计算，无未来函数）：
    "FILTER": "FILTER",        # 信号过滤：成立输出 1 后 N-1 周期抑制
    "SMA": "SMA",              # 通达信递归均线 SMA(X,N,M)
    "BARSSINCE": "BARSSINCE",  # 自首次成立到当前的周期数
    "HHVBARS": "HHVBARS",      # 距 N 周期最高点的周期数
    "LLVBARS": "LLVBARS",      # 距 N 周期最低点的周期数
}

# ---- 支持动态窗口的函数：窗口参数为表达式（变量）时改用 DYN_* 外挂算子 ----
# 通达信/益盟允许 LLV(X,N)/HHV(X,N)/COUNT(X,N)/REF(X,N)/SUM(X,N)/HHVBARS(X,N)/
# LLVBARS(X,N) 的 N 是变量，每个位置用该位置的 N 值作为窗口大小。常量窗口走
# 标准 qlib 算子（性能好），变量窗口走 DYN_* 逐位置计算。
_DYN_WINDOW_OPS = {
    "HHV": "DYN_MAX",
    "LLV": "DYN_MIN",
    "COUNT": "DYN_COUNT",
    "REF": "DYN_REF",
    "SUM": "DYN_SUM",
    "HHVBARS": "DYN_HHVBARS",
    "LLVBARS": "DYN_LLVBARS",
    # ★ v1.20.55：**MA / MEAN 也允许变量周期** ✓（通达信语义 ✓）
    #   动机（2026-09-23 用户报「强龙起势：window must be an integer 0 or greater」✗）：
    #   益盟公式里 `MA5:=MA(C,MIN(BARNUM,5))`（窗口不超过已上市天数）**完全合法** ✓，
    #   而 `MA` 原来不在本表 ⇒ 生成 `Mean($close,Less(BARSCOUNT($close),5))` ✗
    #   ⇒ qlib `Rolling` 把**序列**当窗口 ⇒ `pandas.rolling(序列)` ⇒ 崩 ✗。
    #   ⚠ 常量窗口仍走 qlib 内建 `Mean`（性能更好 ✓）—— 见下方分支的 `isinstance(..., Num)` 判断 ✓。
    "MA": "DYN_MEAN",
    "MEAN": "DYN_MEAN",
}

# ★ v1.20.55：**窗口只能是整数常量**的滚动算子 —— 变量窗口在**编译期**就报清楚 ✗。
#   为什么必须挡（而不是让它跑到运行期 ✗）：qlib 会把窗口原样交给 `pandas.rolling`，
#   报的是 `window must be an integer 0 or greater`（用户 2026-09-23 就是被这句难住的 ✓
#   —— 完全看不出是"哪个函数、哪一行的周期写错了" ✓）。
#   ⚠ `MA/MEAN` 不在此列 ✓（它们已支持动态窗口 ✓）；`EMA_TDX/SMA` 另有常量校验 ✓。
_CONST_WINDOW_ONLY = {"EMA", "WMA", "STD", "VAR", "SLOPE", "MED", "DELTA"}

# ---- 函数 → 组合表达式（用已有算子展开）----
def _expand_cross(args: List[Expr], code) -> str:
    """CROSS(A,B) = And(Gt(A,B), Le(Ref(A,1),Ref(B,1)))"""
    if len(args) != 2:
        raise CodeGenError("CROSS 需要 2 个参数：CROSS(A,B)")
    a, b = code(args[0]), code(args[1])
    return f"And(Gt({a},{b}),Le(Ref({a},1),Ref({b},1)))"


def _expand_abs(args: List[Expr], code) -> str:
    return f"Abs({code(args[0])})"


def _expand_sign(args: List[Expr], code) -> str:
    return f"Sign({code(args[0])})"


# ---- 未来函数/高级摆动算子（涉及未来数据，本项目不支持，给出明确提示）----
PATCHED_OPS = {
    "BACKSET": "BACKSET（未来函数）",
    "ZIG": "ZIG（摆动指标，含未来确认）",
    "PEAK": "PEAK（含未来确认）", "TROUGH": "TROUGH（含未来确认）",
    "SAR": "SAR（含未来确认）",
}

# ---- Level2 深度函数（留接口，暂不可用）----
LEVEL2_OPS = {
    "BIGORDER", "ORDER", "ORDERAMT", "ORDERNUM", "ORDERNWP", "ORDERVOL",
    "TRANSACTNUM", "TRANSACTVOL", "ALLASKVOL", "ALLBIDVOL",
}

# ---- 绘图/颜色函数（忽略，不生成因子）----
IGNORED_OPS = {
    "STICKLINE", "DRAWICON", "DRAWTEXT", "DRAWLINE", "DRAWGBK", "DRAWFLAGTEXT",
    "PARTLINE", "POLYLINE", "FILLRGN", "VERTLINE", "DRAWBMP", "RGB",
    "COLORRED", "COLORBLUE", "COLORGREEN", "COLORSTICK", "DOTLINE",
    "CIRCLEDOT", "DASHLINE",
}


def _is_const_int_expr(e: Expr) -> bool:
    """`e` 能否**常量折叠成整数** ✓（`Num` ✓，或纯常量算术如 `2*3` ✓、参数代入后的表达式 ✓）。

    用途：① 决定"滚动算子走 qlib 内建还是 `DYN_*`"；② `_CONST_WINDOW_ONLY` 的常量窗口守卫。
    ⚠ **不能只看 `isinstance(e, Num)`** ✗（2026-09-23 实测）：`参数 N=2*3;` 代入后窗口的 AST 是
    `BinOp` ✗（但值确实是常量 ✓）⇒ 只看类型会 ① 白白走慢路径（生成 `DYN_MEAN($close,6)` 而不是
    `Mean($close,6)` ✓ 数值相同但慢 ✓）；② 在 `EMA/WMA/...` 上**误报**"周期必须是常量整数" ✗
    （用户明明写的就是常量 ✓）。
    """
    v = _const_fold(e, allow_div=True)
    return v is not None and math.isfinite(v) and float(v).is_integer()


class CodeGen:
    def __init__(self, patchable: bool = False):
        # patchable=True 时，遇到外挂算子返回特殊占位（形如 PATCH:FILTER(...)），供后续 M3 接入
        self.patchable = patchable

    def gen(self, expr: Expr) -> str:
        return self._g(expr)

    def _g(self, e: Expr) -> str:
        if isinstance(e, Num):
            v = e.value
            if float(v).is_integer():
                return str(int(v))
            return repr(v)
        if isinstance(e, Field):
            if e.name not in FIELD_MAP:
                raise CodeGenError(f"不支持的行情字段：{e.name}")
            return FIELD_MAP[e.name]
        if isinstance(e, Var):
            raise CodeGenError(f"未展开的变量引用：{e.name}")
        # ★ 常量折叠（v1.19.90）：**只含常量的子树**必须先折成一个字面量再输出。
        #   为什么（用户 2026-09-17 报：LLT / 黏合强突破 / 过顶0 / 过顶 / 蹦极新生
        #   「特征计算失败: 'numpy.int64' object has no attribute 'name'」）：
        #   qlib 加载表达式时，遇到**没有任何 `$字段` 的子树**会在内部对常量取 `.name` ⇒ 直接崩 ✗。
        #   实测（`ai_test/dbg_llt.py`）：
        #     `Add(2,3)` / `Div(2,Add(30,1))` ❌      而 `Add($close,1)` / `Mul(2,$close)` ✅
        #   LLT 正好含 `Div(2,Add(30,1))`、`Mul(3,…)`、`Mul(Sub(1,…),Sub(1,…))` 这类纯常数子树 ⇒ 中招。
        #   ⇒ 折叠后统一是**单个数字字面量**，作为算子操作数完全没问题 ✓（顺带表达式更短 ✓）。
        # ★ v1.20.55：`FuncCall` 也纳入常量折叠入口 ✓ —— 否则"表里有纯数学函数、却永远走不到" ✗
        #   （实测：`CLOSE+ABS(-3)` 仍生成 `Add($close,Abs(-3))` ✗）。安全 ✓：真正能折什么
        #   由 `_const_fold` 内部的 `_CONST_FOLD_FUNCS` 白名单决定 ✓（`Mean/Ref/BARSLAST` 等
        #   依赖历史序列的**不在表里** ⇒ 永远不会被折 ✓）。
        if isinstance(e, (BinOp, UnaryOp, FuncCall)):
            v = _const_fold(e, allow_div=True)
            if v is not None:
                return str(int(v)) if float(v).is_integer() else repr(v)
        # ★★ v1.20.58：`IF(恒定条件, A, B)` ⇒ **直接生成被选中的那一支** ✓✗
        #   为什么不能只折条件 ✗（真机实测 ✓）：`Lt(10,3)` 折成 `0` 后剩 `If(0,A,B)` ⇒ qlib 报
        #   **`'int' object has no attribute 'load'`** ✗ —— 它要求条件本身是**可 `load` 的表达式** ✗，
        #   纯字面量不行 ✓。⇒ 唯一出路是让这棵 `If` **整个消失** ✓（`If` 与 `Div`/`Mean` 不同：
        #   它的"参数"里有分支语义 ✓，必须由我们替它选 ✓）。
        #   ⚠ 代价（如实记录 ✗）：**未被选中的那支会被丢掉** ⇒ 死支里的错误不再被报出 ✓。
        #     可接受 ✓ —— 条件由**常量**决定 ⇒ 那支本来就是死代码 ✓（等价于通达信里"参数写死的
        #     开关" ✓；用户 `n=10` 时 `IF(n<3, 原始值, MA(...))` 本来就只该走 MA 那支 ✓）。
        if isinstance(e, FuncCall) and e.name.upper() == "IF" and len(e.args) == 3:
            _c = _const_fold(e.args[0], allow_div=True)
            if _c is not None:
                return self._g(e.args[1] if _c != 0 else e.args[2])
        if isinstance(e, UnaryOp):
            # ★★ v1.20.53：益盟/同花顺/通达信 `NOT(X)` = **逻辑非**（官方口径：`X=0` ⇒ 1，否则 ⇒ 0 ✓）
            #   ⇒ 直接生成 qlib 内建 **`Eq(X,0)`** ✓（不新增外挂算子 ✗）。
            #   ⚠ **性能说明（用户特别要求 ✓）**：
            #     ① 只有**一个** elementwise 算子 ✓ —— 与手写 `X=0` **完全同路径、同开销** ✓
            #        （没用 `If(Eq(X,0),1,0)` 那种两步写法 ✗，也没注册新算子 ⇒ 无查找/派发开销 ✓）；
            #     ② 表达式照旧**按字段求值一次并进缓存** ✓（`panel_expr` 的字段缓存 ✓）⇒ NOT 不引入额外扫描 ✓；
            #     ③ 纯常量子树（`NOT(1)` 之类）被上面的 `_const_fold` 折掉 ✓ ⇒ 不会产生
            #        "没有任何 `$字段` 的子树"（qlib 会崩 ✗）；
            #     ④ 语义边界：`X` 为 NaN（停牌）时 `Eq(NaN,0)` ⇒ 0 ✓（"非 X 不成立" ✓，可接受 ✓）。
            if e.op == "NOT":
                return f"Eq({self._g(e.operand)},0)"
            if e.op == "NEG":
                # ★ v1.20.54：`NEG` 由 `UNARY_MAP` 改为**本处显式处理** ✓ ——
                #   原先走 `UNARY_MAP["NEG"]="Neg"`，但 qlib **没有 `Neg`** ✗
                #   （其实从来不会生成它 ✓：下面两种情况都覆盖了 ✓）⇒ 现在不再依赖那张表 ✓。
                # 负数：若作用于数字字面量 → 直接输出负数常量（如 -100）；
                # 作用于复杂表达式 → 用 Mul(-1, expr)（不能用 Sub(0,expr)，裸 0 常量 qlib 无法加载）
                if isinstance(e.operand, Num):
                    v = -e.operand.value
                    return str(int(v)) if float(v).is_integer() else repr(v)
                return f"Mul(-1,{self._g(e.operand)})"
            op = UNARY_MAP.get(e.op)
            if op is None:
                raise CodeGenError(f"不支持的一元运算：{e.op}")
            return f"{op}({self._g(e.operand)})"
        if isinstance(e, BinOp):
            op = BINOP_MAP.get(e.op)
            if op is None:
                raise CodeGenError(f"不支持的运算：{e.op}")
            if e.op == "DIV":
                # 编译期拦截"分母恒等于 0"的公式，避免回测时整列出现 inf/NaN
                denom = _const_fold(e.right)
                if denom is not None and denom == 0:
                    raise CodeGenError("除数为 0：公式分母恒等于 0，请修改公式")
            return f"{op}({self._g(e.left)},{self._g(e.right)})"
        if isinstance(e, FuncCall):
            return self._gen_func(e)
        raise CodeGenError(f"无法生成的表达式节点：{type(e).__name__}")

    def _gen_func(self, e: FuncCall) -> str:
        name = e.name
        # 忽略绘图/颜色
        if name in IGNORED_OPS:
            raise CodeGenError(f"函数 {name} 是绘图/颜色指令，不生成因子（已忽略）")
        # 资金流向档位选择：L2_PCT(n[,b|s])/L2_AMO(n[,b|s])/L2_VOL(n[,b|s])
        # → 无第二参：净占比/净额/净量字段（向后兼容）；有 b/s：买入/卖出方向字段
        if name in ("L2_PCT", "L2_AMO", "L2_VOL"):
            if not e.args or not isinstance(e.args[0], Num):
                raise CodeGenError(
                    f"{name} 需要档位参数：{name}(n[,b|s])，"
                    f"n=0 主力 / 1 超大单 / 2 大单 / 3 中单 / 4 小单；b=买入 / s=卖出")
            if len(e.args) > 2:
                raise CodeGenError(f"{name} 最多 2 个参数：{name}(n[,b|s])")
            n = int(e.args[0].value)
            if n not in range(5):
                raise CodeGenError(f"{name} 档位参数越界：{name}({n})，n 只能取 0~4")
            if name == "L2_AMO":
                fields, dirs = L2_AMO_FIELDS, L2_AMO_DIR_FIELDS
            elif name == "L2_PCT":
                fields, dirs = L2_PCT_FIELDS, L2_PCT_DIR_FIELDS
            else:
                fields, dirs = L2_VOL_FIELDS, L2_VOL_DIR_FIELDS
            if len(e.args) == 1:
                return fields[n]  # 净额/净占比/净量
            a1 = e.args[1]
            if not isinstance(a1, Var) or a1.name.upper() not in ("B", "S"):
                raise CodeGenError(
                    f"{name} 的第 2 个参数只能是 b（买入）或 s（卖出），当前写法不支持")
            return dirs[n][0 if a1.name.upper() == "B" else 1]
        # Level2
        if name in LEVEL2_OPS:
            raise CodeGenError(
                f"函数 {name} 需要 Level2 深度数据，当前数据源未提供，暂不可用")
        # 需外挂算子
        if name in PATCHED_OPS:
            if not self.patchable:
                raise CodeGenError(
                    f"函数 {name} 属于有状态算子（{PATCHED_OPS[name]}），"
                    f"当前阶段未实现，请先实现外挂算子或改用其他函数")
            inner = ",".join(self._g(a) for a in e.args)
            return f"PATCH:{name}({inner})"
        # 参数个数 / 常量校验
        if name == "COUNT":
            if len(e.args) != 2:
                raise CodeGenError("COUNT 需要 2 个参数：COUNT(条件, 周期)，例如 COUNT(CLOSE>OPEN,5)")
        elif name == "BARSLAST":
            if len(e.args) != 1:
                raise CodeGenError("BARSLAST 需要 1 个参数：BARSLAST(条件)，例如 BARSLAST(CLOSE/REF(CLOSE,1)>=1.1)")
        elif name == "BARSCOUNT":
            if len(e.args) != 1:
                raise CodeGenError("BARSCOUNT 需要 1 个参数：BARSCOUNT(CLOSE)，表示上市以来交易日数")
        elif name == "BARSSINCEN":
            if len(e.args) != 2:
                raise CodeGenError("BARSSINCEN 需要 2 个参数：BARSSINCEN(条件, 周期)，例如 BARSSINCEN(HIGH>10,10)")
            if not isinstance(e.args[1], Num):
                raise CodeGenError("BARSSINCEN 的第 2 个参数 N 必须为常量整数（如 10）")
        elif name == "BARSSINCE":
            if len(e.args) != 1:
                raise CodeGenError("BARSSINCE 需要 1 个参数：BARSSINCE(条件)，例如 BARSSINCE(CLOSE>MA(CLOSE,20))")
        elif name == "FILTER":
            if len(e.args) != 2:
                raise CodeGenError("FILTER 需要 2 个参数：FILTER(条件, N)，例如 FILTER(CROSS(MA(CLOSE,5),MA(CLOSE,20)),5)")
            if not isinstance(e.args[1], Num):
                raise CodeGenError("FILTER 的第 2 个参数 N 必须为常量整数（如 5）")
        elif name == "SMA":
            if len(e.args) != 3:
                raise CodeGenError("SMA 需要 3 个参数：SMA(X,N,M)，通达信递归均线，例如 SMA(CLOSE,5,1)")
            for idx, lbl in ((1, "N"), (2, "M")):
                if not isinstance(e.args[idx], Num):
                    raise CodeGenError(f"SMA 的第 {idx + 1} 个参数 {lbl} 必须为常量整数")
        elif name in ("HHVBARS", "LLVBARS"):
            if len(e.args) != 2:
                raise CodeGenError(f"{name} 需要 2 个参数：{name}(X, N)，例如 {name}(CLOSE,34)")
            # N 可为变量（通达信允许 HHVBARS(X, AT+1) 这类窗口随行变化的写法）→
            # 交下方 _DYN_WINDOW_OPS 分支：常量用 HHVBARS/LLVBARS，变量用 DYN_HHVBARS/DYN_LLVBARS
        # 动态窗口：HHV/LLV/COUNT/REF/SUM/HHVBARS/LLVBARS 窗口参数为表达式（变量）→ DYN_* 外挂算子
        if name in _DYN_WINDOW_OPS:
            if len(e.args) != 2:
                raise CodeGenError(f"{name} 需要 2 个参数：{name}(X, 周期)")
            # 常量窗口用标准 qlib 算子（性能好）；变量窗口逐位置计算
            # ⚠ v1.20.57：用 `_is_const_int_expr`（**常量折叠**后判断 ✓）而不是 `isinstance(Num)` ✗
            #   —— 参数代入后的常量表达式（`参数 N=2*3;` ⇒ AST 是 BinOp ✓）也能走内建快路径 ✓。
            q = (_DYN_WINDOW_OPS[name] if not _is_const_int_expr(e.args[1])
                 else FUNC_QLIB[name])
            inner = ",".join(self._g(a) for a in e.args)
            return f"{q}({inner})"
        # ★ v1.20.55：**只能是常量窗口**的滚动算子 —— 变量窗口在这里就报清楚 ✗
        #   否则会生成 `Mean(X, 表达式)` 之类 ⇒ 运行期被 pandas 顶回来，报
        #   `window must be an integer 0 or greater` ✗（用户 2026-09-23 就是被这句难住的 ✓：
        #   既没说是哪个函数、也没说哪一行的周期写错了 ✓）。
        if name in _CONST_WINDOW_ONLY and len(e.args) >= 2 and not _is_const_int_expr(e.args[-1]):
            raise CodeGenError(
                "%s 的周期必须是**常量整数** ✗（当前写成了表达式）\n"
                "  · 想按位置用不同窗口 ⇒ 用 MA/MEAN（已支持动态窗口 ✓）或 HHV/LLV/COUNT/REF/SUM ✓；\n"
                "  · 想让窗口不超过已上市天数 ⇒ 直接写 MA(X, N) 就行 ✓\n"
                "    （MA 会逐位置取 min(已上市天数, N) ✓，等价于你写的 MIN(BARNUM,N) ✓）；\n"
                "  · 例：`%s(C, MIN(BARNUM, 20))` ⇒ 改成 `MA(C, MIN(BARNUM, 20))` ✓。"
                % (name, name)
            )
        # MAX/MIN：通达信语义是两值取大/小（Greater/Less）
        if name in ("MAX", "MIN"):
            if len(e.args) != 2:
                raise CodeGenError(f"{name} 需要 2 个参数：{name}(A,B)，取 A/B 的较大值/较小值")
            q = FUNC_QLIB[name]
            inner = ",".join(self._g(a) for a in e.args)
            return f"{q}({inner})"
        # SGN/SIGN/INT/BETWEEN/EMA_TDX：参数个数校验（错误提示友好）
        if name in ("SGN", "SIGN"):
            if len(e.args) != 1:
                raise CodeGenError(f"{name} 需要 1 个参数：{name}(X)，取 X 的符号（X>0→1，X<0→-1，X=0→0）")
        elif name == "INT":
            if len(e.args) != 1:
                raise CodeGenError("INT 需要 1 个参数：INT(X)，返回 X 的整数部分（向零截断）")
        elif name == "BETWEEN":
            if len(e.args) != 3:
                raise CodeGenError("BETWEEN 需要 3 个参数：BETWEEN(X,A,B)，X 在 A 与 B 之间（含边界）为真")
        elif name == "EMA_TDX":
            if len(e.args) != 2:
                raise CodeGenError("EMA_TDX 需要 2 个参数：EMA_TDX(X,N)，通达信递归语义的指数移动平均")
        # 筹码分布（v1.19.83）：COST(q) / WINNER(P) → **派生字段** `$chip_cost_*` / `$chip_win_*`
        # ⚠ 为什么映射成字段而不是算子：两条求值路径（qlib ops / panel_expr）的算子都是**逐股调用**的
        #   ⇒ 筹码递推若逐股跑，全 A 约 4~8 分钟/每个分位 ✗✗；而字段可让面板级求值器
        #   一次拿到整块面板、按「日期 × 股票」矩阵向量化递推（`panel_expr._chip_field`）⇒ 几秒 ✓。
        if name == "COST":
            if len(e.args) != 1 or not isinstance(e.args[0], Num):
                raise CodeGenError("COST 需要 1 个**常量**参数：COST(q)，q 为成本分位（百分数），如 COST(95)")
            q = float(e.args[0].value)
            if not (0.0 < q < 100.0):
                raise CodeGenError("COST 的参数需在 (0,100) 之间，例如 COST(95)")
            return "$chip_cost_%g" % q
        if name == "WINNER":
            if len(e.args) != 1:
                raise CodeGenError("WINNER 需要 1 个参数：WINNER(P)，P 用现价/最高价/最低价（如 WINNER(C)）")
            a = e.args[0]
            nm = None
            if isinstance(a, Field):
                nm = {"CLOSE": "close", "C": "close", "HIGH": "high", "H": "high",
                      "LOW": "low", "L": "low"}.get(a.name.upper())
            if nm is None:
                raise CodeGenError("WINNER(P)：目前只支持 P = C/H/L（现价/最高价/最低价）；"
                                   "其它价（开盘价、均价等）暂不支持")
            return "$chip_win_%s" % nm
        # 直接映射
        if name in FUNC_QLIB:
            q = _ema_op_name() if name == "EMA" else FUNC_QLIB[name]
            inner = ",".join(self._g(a) for a in e.args)
            return f"{q}({inner})"
        # 组合展开
        if name == "CROSS":
            return _expand_cross(e.args, self._g)
        raise CodeGenError(f"不支持的函数：{name}")


def generate(expr: Expr, patchable: bool = False) -> str:
    """把内联后的表达式树生成 qlib 表达式字符串。"""
    # 整条公式**不含任何行情字段**（纯常量）⇒ 明确报错：qlib 加载这种列会崩，
    # 而且它作为因子毫无意义（v1.19.90，避免用户看到 `'numpy.int64' … 'name'` 那种天书 ✗）。
    if _const_fold(expr, allow_div=True) is not None:
        raise CodeGenError(
            "公式不含任何行情字段（计算结果恒为常量），无法作为因子；"
            "请检查是否漏写 CLOSE / HIGH / VOL 等字段")
    return CodeGen(patchable=patchable).gen(expr)
