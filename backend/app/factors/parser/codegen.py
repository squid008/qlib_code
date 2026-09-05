# -*- coding: utf-8 -*-
"""公式翻译器：代码生成（AST → qlib 表达式字符串 / 外挂算子标记）。

把已内联的表达式树翻译成 qlib 可加载的表达式字符串。
不支持的算子（Level2 深度、需外挂的有状态算子）抛出 CodeGenError 并给出清晰提示。
"""
from __future__ import annotations

from typing import List

from .ast import Expr, Num, Field, Var, BinOp, UnaryOp, FuncCall
from .lexer import LexerError


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
UNARY_MAP = {"NEG": "Neg"}


def _const_fold(e: Expr):
    """对纯常量子树做常量折叠求值；子树含行情字段/变量/函数时返回 None。

    用于在编译期检测恒等于 0 的除数（如 OUT:CLOSE/0 或 OUT:CLOSE/(5-5)）。
    """
    if isinstance(e, Num):
        return float(e.value)
    if isinstance(e, UnaryOp):
        v = _const_fold(e.operand)
        if v is None:
            return None
        return -v if e.op == "NEG" else None
    if isinstance(e, BinOp):
        lv = _const_fold(e.left)
        rv = _const_fold(e.right)
        if lv is None or rv is None:
            return None
        if e.op == "ADD":
            return lv + rv
        if e.op == "SUB":
            return lv - rv
        if e.op == "MUL":
            return lv * rv
        if e.op == "DIV":
            # 除法结果继续折叠会传播 inf/nan，这里直接返回 None 交给外层除零检测
            return None
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
    "POW": "Pow", "MAX": "Greater", "MIN": "Less", "MOD": "Mod",
    "STD": "Std", "VAR": "Var", "SLOPE": "Slope",
    "REF": "Ref", "DELTA": "Delta", "MEAN": "Mean", "MED": "Med",
    "IF": "If", "IFS": "If",
    "BARSLAST": "BARSLAST",
    "BARSCOUNT": "BARSCOUNT",
    "BARSSINCEN": "BARSSINCEN",
    # 动态窗口外挂算子（用户也可直接调用 DYN_MIN/MAX/COUNT/REF/SUM）
    "DYN_MIN": "DYN_MIN",
    "DYN_MAX": "DYN_MAX",
    "DYN_COUNT": "DYN_COUNT",
    "DYN_REF": "DYN_REF",
    "DYN_SUM": "DYN_SUM",
}

# ---- 支持动态窗口的函数：窗口参数为表达式（变量）时改用 DYN_* 外挂算子 ----
# 通达信/益盟允许 LLV(X,N)/HHV(X,N)/COUNT(X,N)/REF(X,N)/SUM(X,N) 的 N 是变量，
# 每个位置用该位置的 N 值作为窗口大小。常量窗口走标准 qlib 算子（性能好），
# 变量窗口走 DYN_* 逐位置计算。
_DYN_WINDOW_OPS = {
    "HHV": "DYN_MAX",
    "LLV": "DYN_MIN",
    "COUNT": "DYN_COUNT",
    "REF": "DYN_REF",
    "SUM": "DYN_SUM",
}

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


# ---- 需外挂的有状态算子（M3 再实现，这里给出明确提示）----
PATCHED_OPS = {
    "FILTER": "FILTER（信号过滤）",
    "SMA": "SMA（通达信递归均线）",
    "BARSSINCE": "BARSSINCE",
    "BACKSET": "BACKSET（未来函数）",
    "ZIG": "ZIG（摆动指标）",
    "PEAK": "PEAK", "TROUGH": "TROUGH", "SAR": "SAR",
    "HHVBARS": "HHVBARS", "LLVBARS": "LLVBARS",
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
        if isinstance(e, UnaryOp):
            op = UNARY_MAP.get(e.op)
            if op is None:
                raise CodeGenError(f"不支持的一元运算：{e.op}")
            if op == "Neg":
                # 负数：若作用于数字字面量 → 直接输出负数常量（如 -100）；
                # 作用于复杂表达式 → 用 Mul(-1, expr)（不能用 Sub(0,expr)，裸 0 常量 qlib 无法加载）
                if isinstance(e.operand, Num):
                    v = -e.operand.value
                    return str(int(v)) if float(v).is_integer() else repr(v)
                return f"Mul(-1,{self._g(e.operand)})"
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
        # 动态窗口：HHV/LLV/COUNT/REF/SUM 窗口参数为表达式（变量）→ DYN_* 外挂算子
        if name in _DYN_WINDOW_OPS:
            if len(e.args) != 2:
                raise CodeGenError(f"{name} 需要 2 个参数：{name}(X, 周期)")
            # 常量窗口用标准 qlib 算子（性能好）；变量窗口逐位置计算
            q = _DYN_WINDOW_OPS[name] if not isinstance(e.args[1], Num) else FUNC_QLIB[name]
            inner = ",".join(self._g(a) for a in e.args)
            return f"{q}({inner})"
        # MAX/MIN：通达信语义是两值取大/小（Greater/Less）
        if name in ("MAX", "MIN"):
            if len(e.args) != 2:
                raise CodeGenError(f"{name} 需要 2 个参数：{name}(A,B)，取 A/B 的较大值/较小值")
            q = FUNC_QLIB[name]
            inner = ",".join(self._g(a) for a in e.args)
            return f"{q}({inner})"
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
    return CodeGen(patchable=patchable).gen(expr)
