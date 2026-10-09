# -*- coding: utf-8 -*-
"""公式翻译器：代码生成（AST → qlib 表达式字符串 / 外挂算子标记）。

把已内联的表达式树翻译成 qlib 可加载的表达式字符串。
不支持的算子（Level2 深度、需外挂的有状态算子）抛出 CodeGenError 并给出清晰提示。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import List

from .ast import Expr, Num, Str, Field, Var, BinOp, UnaryOp, FuncCall
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


# ★★ v1.20.59：**代码生成语义版本** —— 只要"同一段原文会生成**不同的** qlib 表达式"，就必须递增 ✗。
#
# 为什么需要（2026-09-23 实测，用户「还是报错啊」✓）：
#   公式库里存的 `expression` 是**编译产物**（缓存 ✓）；生成逻辑一改，它就是**陈旧缓存** ✗ ——
#   而 **单因子测试** 这条链是**直接拿缓存里的 `expression` 去求值**的 ✗
#   （`SingleFactorTestFactor.expression` ✓ 前端从 `/api/factors/custom-formulas` 取 ✓）；
#   回测那条链传的是**原文** ✓（`custom_formulas: List[str]` = 文本 ✓）会重译 ✓。
#   ⇒ 于是出现最误导的现象：**代码已经修好、回测能用，但单因子测试照旧报错** ✗✓
#   （`深跌10` 修完 v1.20.58 后仍报 `'numpy.bool' object has no attribute 'name'` ✓
#    —— 因为库里存的还是修复前编译的串 ✓）。
# ⇒ 处理（与 `qlib_engine.TRAIN_SEMANTICS` / `chip_store.CHIP_SEMANTICS` 同一套路 ✓）：
#   `services/custom_formulas.list_custom_formulas()` 读到条目时比对本戳 ✓，
#   **不一致（含没有戳的旧条目）⇒ 按 `text` 重新编译并写回** ✓✓。
# ⚠ 值取「**生成结果最后变化的版本**」✓（不是发版号 ✗）—— 单纯加注释/日志/性能重构**不要**动它 ✓，
#   否则 60+ 条公式会白重编一次 ✓（无害但没必要 ✓）。
# 历史：`1.20.58` = 纯常量**比较 / 逻辑**折叠 + `IF(恒定条件,…)` 整棵选支 ✓（会改变生成结果 ✓）。
#       `1.20.83` = 常量窗口 `COUNT(X,N)` 由 qlib `Count`（非 NaN 计数 ✗）改为
#                   `Sum(Gt(Abs(X),0),N)`（通达信口径：非 0 且非 NaN 的天数 ✓）——
#                   **会改变生成结果** ✓ ⇒ 存量公式（如 `深跌2/3` 用到 COUNT ✓）会被自动重编 ✓。
CODEGEN_SEMANTICS = "1.20.83"


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

# ---- 基本面 FINANCE(q)：益盟盘口那几个基本面指标的历史序列 ----
# 数据由 tools/dump_finance.py 从**米筐 pit 财报表 + 总市值**物化成字段 bin（`{qlib_dir}/features/{code}/fin_*.day.bin`）
# ⇒ FINANCE(q) 只是"字段选择器"（同 COST(q) 的做法 ✓），不要在算子层现算：
#   · 财报是**低频事件**（每股每季一条）但有 5000+ 只 ⇒ 逐股现算要反复读盘 ✗；
#   · 物化时已按**公告日**对齐（info_date ≤ 当日 ⇒ 无未来函数 ✓），算子在运行期无法更准 ✓。
# ⚠ 本表顺序 = FINANCE(q) 的 q（1..9），必须与 tools/dump_finance.py 的 `FIN_FIELDS` **逐项一致** ✗
#   （顺序错位 = 静默取错指标 ✗）⇒ 由 `tests/test_finance_func.py` 直接读那个脚本比对 ✓。
FINANCE_FIELDS = [
    ("fin_pe_ttm", "市盈率TTM"),
    ("fin_pb", "市净率"),
    ("fin_rev_yoy", "营业收入增长率"),
    ("fin_np_yoy", "净利润增长率"),
    ("fin_gross_margin", "销售毛利率"),
    ("fin_roe", "净资产收益率ROE"),
    ("fin_roa", "总资产收益率ROA"),
    ("fin_eps", "每股收益(基本)"),
    ("fin_op_yoy", "营业利润增长率"),
    # ★ 2026-10-09 追加（用户要求）：自由现金流（TTM，元）
    #   = 经营活动现金流净额TTM − 资本开支TTM（"购建固定资产、无形资产和其他长期资产支付的现金"）
    ("fin_fcf", "自由现金流TTM"),
]


def finance_help() -> str:
    """FINANCE(q) 的编号说明（错误提示/测试共用一份文案，避免两处漂移）。"""
    return "\n".join("  %d = %s" % (i + 1, d) for i, (_f, d) in enumerate(FINANCE_FIELDS))


# ---- ★ 2026-10-09：**通达信源**的基本面（`FINANCE_TDX(q)`）----
# 与 FINANCE(q) **编号/含义完全一致**（1 市盈率TTM … 9 营业利润增长率 ✓），只是数据来自
# **通达信财务包**（`D:\new_tdx\vipdoc\cw\gpcw*.zip`，会员下载 ✓）而非米筐 ——
# 用户 2026-10-09：「米筐没有秘钥 ⇒ 以后只能用通达信的数据源」⇒ 两个函数并存，便于**交叉校验** ✓。
# ⚠ 字段名由 `tools/dump_finance_tdx.py` 生成（该脚本 import 本表 ⇒ 两处不会漂移 ✓）。
# ⚠⚠ **PIT 语义不同**（关键差异，手册里必须写清）：
#   · 米筐 pit 有 `info_date`/`if_adjusted` ⇒ 严格"按公告日"生效 ✓；
#   · 通达信 gpcw **没有公告日** ⇒ 只能用**法定披露截止日**推定可用日
#     （一季报 4/30、中报 8/31、三季报 10/31、年报次年 4/30）⇒ **保守但可能晚** ✓，
#     代价是"提前披露的公司"要等到截止日才用得上（宁晚不错 ✓）；且它没有逐次修正快照 ✗。
FINANCE_TDX_FIELDS = [
    ("ftdx_pe_ttm", "市盈率TTM"),
    ("ftdx_pb", "市净率"),
    ("ftdx_rev_yoy", "营业收入增长率"),
    ("ftdx_np_yoy", "净利润增长率"),
    ("ftdx_gross_margin", "销售毛利率"),
    ("ftdx_roe", "净资产收益率ROE"),
    ("ftdx_roa", "总资产收益率ROA"),
    ("ftdx_eps", "每股收益(基本)"),
    ("ftdx_op_yoy", "营业利润增长率"),
    # ★ 2026-10-09 追加（与 FINANCE(10) 同编号同口径）：自由现金流TTM（元）
    ("ftdx_fcf", "自由现金流TTM"),
]


def finance_tdx_help() -> str:
    """FINANCE_TDX(q) 的编号说明（与 FINANCE(q) 同编号 ✓）。"""
    return "\n".join("  %d = %s" % (i + 1, d) for i, (_f, d) in enumerate(FINANCE_TDX_FIELDS))


# ============================================================================
# 横向统计（2026-10-09，通达信语义）—— 板块名/公式名 → **市场级物化字段**
# ============================================================================
# 通达信那两个函数是"**横向**（跨股票）"统计，而我们的求值链有两条：
#   · 单因子/事件研究 ⇒ 面板求值器（本来就是"日期 × 股票"整块矩阵，横截面天然可做 ✓）
#   · 回测/训练 ⇒ qlib 表达式（**逐股**时间序列，根本没有"其它股票"的概念 ✗✗）
# ⇒ 唯一能让**两条路都成立**的形态是：**先把横向统计算好，物化成"同日全市场同值"的字段 bin**
#   （`features/{code}/mkt_*.day.bin`）✓ —— 与筹码 COST/WINNER 物化同一套路（见 chip_store.py）✓。
#   代价：被调公式或板块口径变了要**重跑物化**（`tools/materialize_market.py` ✓），
#         用 `_market_meta.json` 口径戳防"拿旧口径的 bin 静默算错" ✗。
#
# ⚠ 字段名必须由**同一个函数**生成（codegen 与物化器共用 ⇒ 天然不会漂移 ✓）：
#     板块名 → `blocksetnum_field(key)` / `insum_field(key, formula, out, calc)`

# 板块名（NFKC 归一后）→ 股票池键（= `data/cn_data/instruments/<key>.txt`）。
# ⚠ 键名要能在 instruments 目录里找到对应文件；'hsa'（沪深A股，剔除北交所）由物化器现算 ✓。
BLOCK_KEYS = {
    "全部A股": "all", "全部A股(含北交所)": "all", "所有A股": "all", "A股": "all",
    "沪深A股": "hsa", "沪深两市A股": "hsa",
    "沪深300": "csi300", "沪深300指数": "csi300", "中证300": "csi300",
    "中证500": "csi500", "中证500指数": "csi500",
    "中证800": "csi800", "中证800指数": "csi800",
    "中证1000": "csi1000", "中证1000指数": "csi1000",
}
BLOCK_KEYS_HELP = "、".join(sorted(set(BLOCK_KEYS.keys())))

# INSUM 的计算类型（通达信：0累加/1平均/2最大/3最小值/4最大值序号/5最小值序号）
INSUM_CALC_TYPES = {
    0: "累加", 1: "平均", 2: "最大值", 3: "最小值",
    # 4/5（极值所处品种序号）返回的是"哪只股票"而不是数值 ⇒ 与"全市场同值字段"的物化形态不兼容 ✗，
    # 首版不做（写进错误提示 ✓，别让用户猜为什么不行）
}
INSUM_CALC_HELP = "、".join("%d=%s" % (k, v) for k, v in INSUM_CALC_TYPES.items())


def block_key(block_name: str) -> str:
    """板块名 → 股票池键；不认识的板块名给出可用清单（报错要能自助 ✓）。"""
    raw = (block_name or "").strip()
    # NFKC 兜底：公式文本已被 normalize_source 折过，但直接调用本函数（测试/物化器）时可能没折 ✓
    import unicodedata
    norm = unicodedata.normalize("NFKC", raw)
    key = BLOCK_KEYS.get(norm) or BLOCK_KEYS.get(norm.replace(" ", ""))
    if key is None:
        raise CodeGenError(
            "不认识的板块名 %r。\n目前支持：%s\n"
            "（板块成分按**当日真实成分**取，指数=当日在指数里的股票，历史变更不会穿越 ✓）"
            % (raw, BLOCK_KEYS_HELP))
    return key


def formula_key(formula_name: str) -> str:
    """被调公式名 → 可做文件名的短键（中文名也安全：保留 ASCII 部分 + 名字哈希）。"""
    name = (formula_name or "").strip()
    safe = re.sub(r"[^0-9a-zA-Z_]+", "", name).lower()[:24]
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return (safe or "f") + "_" + h


def blocksetnum_field(block_name: str) -> str:
    """`BLOCKSETNUM('全部A股')` → `$mkt_num_all`（物化字段）。"""
    return "$mkt_num_" + block_key(block_name)


def insum_field(block_name: str, formula_name: str, out_index: int, calc_type: int) -> str:
    """`INSUM('全部A股','IS_GOLD_PIT',1,0)` → `$mkt_insum_all_is_gold_pit_<hash>_1_0`。"""
    return "$mkt_insum_%s_%s_%d_%d" % (
        block_key(block_name), formula_key(formula_name), int(out_index), int(calc_type))


def insum_help() -> str:
    return ("INSUM(板块名, 指标名, 指标输出, 计算类型)\n"
            "  · 板块名：%s\n"
            "  · 指标名：**已保存的公式名**（它自己的输出名，如 IS_GOLD_PIT）\n"
            "  · 指标输出：取该公式的第几个输出线（本平台公式都是单输出 ⇒ 只能是 1）\n"
            "  · 计算类型：%s\n"
            "注意：横向统计要**物化**成市场级字段后才能用 ⇒ 保存公式后跑一次\n"
            "  `python tools/materialize_market.py`（详见公式手册）" % (BLOCK_KEYS_HELP, INSUM_CALC_HELP))


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
    "SUM": "Sum",
    # ⚠ `COUNT` 在此表里只是**占位**（保持"函数名→算子名"表完整 ✓）——常量窗口的 COUNT
    #   在下面 `_DYN_WINDOW_OPS` 分支里被**抢先**展开成 `Sum(Gt(Abs(X),0),N)` ✓，
    #   绝不能再落到 qlib `Count` 上 ✗（它是"非 NaN 计数"，对 0/1 条件恒等于窗口长度 ✗✗，
    #   详见该分支的注释 ✓）。
    "COUNT": "Count",
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
            const_n = _is_const_int_expr(e.args[1])
            if name == "COUNT" and const_n:
                # ★ v1.20.83：常量窗口的 COUNT **必须自己展开**，绝不能用 qlib 的 `Count` ✗✗
                #   qlib `Count` = `Rolling(..., "count")` = 窗口内**非 NaN 个数** ✗
                #   （`qlib/data/ops.py` 的 class Count 文档原话 ✓）。而条件序列几乎全是
                #   0/1、极少 NaN ⇒ 它**恒等于窗口长度**（min(已上市天数, N)）✗✗
                #   ⇒ `COUNT(IS_GOLD_PIT,30)>=15` 会**恒真**、选股条件静默失效 ✗
                #   （2026-10-09 实测：面板链直接报"不支持算子 Count"✗，回测链则悄悄算成 30 ✗）。
                #   通达信口径 = 窗口内**非 0 且非 NaN** 的天数 ✓ —— 必须与**变量窗口**那条
                #   （DYN_COUNT，见 `ops_ext.dyn_window_count_vec`："窗口内非 0 且非 NaN 计数" ✓）
                #   完全一致 ✓，否则同一公式换个 N 写法就换口径 ✗。
                #   展开式：`Sum(Gt(Abs(X),0),N)` ✓
                #     · X≠0 ⇒ |X|>0 ⇒ 记 1 ✓（负数、或 `IF(B点,2,0)` 这类"信号值"同样算成立 ✓ 同 TDX ✓）；
                #     · X=NaN（停牌/无数据）⇒ `Abs` → NaN ⇒ `Gt(NaN,0)` = 假 ⇒ 记 0 ✓（该日不计入 ✓）；
                #     · 只用 Sum/Abs/Gt 三个**两条链都已实现**的算子 ✓（面板 `_apply` 与 qlib 同源），
                #       不引入新算子 ⇒ 零"单因子测试 vs 回测"发散风险 ✓。
                #   ⚠ 不要写成 `Sum(X,N)` ✗：X 可能是非 0/1 的信号值（如 `量王` 的 2/-1 ✓），
                #     直接求和会把"2"当成两个成立日 ✗。
                inner = self._g(e.args[0])
                return f"Sum(Gt(Abs({inner}),0),{self._g(e.args[1])})"
            q = (_DYN_WINDOW_OPS[name] if not const_n else FUNC_QLIB[name])
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
        # 基本面（2026-10-09）：FINANCE(q) → 派生字段 `$fin_*`（物化 bin，见上方 FINANCE_FIELDS）
        if name == "FINANCE":
            if len(e.args) != 1:
                raise CodeGenError(
                    "FINANCE 需要 1 个参数：FINANCE(q)，q 为指标编号（常量整数）：\n" + finance_help())
            q = _const_fold(e.args[0], allow_div=True)
            if q is None or not float(q).is_integer():
                raise CodeGenError(
                    "FINANCE(q) 的 q 必须是**常量整数**（不能是行情字段/变量）：\n" + finance_help()
                    + "\n  例：`PE:=FINANCE(1); 因子:PE<20;`")
            q = int(q)
            if not (1 <= q <= len(FINANCE_FIELDS)):
                raise CodeGenError(
                    "FINANCE(q) 的 q 需在 1~%d 之间，当前为 %d：\n%s"
                    % (len(FINANCE_FIELDS), q, finance_help()))
            return "$" + FINANCE_FIELDS[q - 1][0]
        # 基本面（通达信源，2026-10-09）：FINANCE_TDX(q) → `$ftdx_*`（编号与 FINANCE(q) 相同 ✓）
        if name == "FINANCE_TDX":
            if len(e.args) != 1:
                raise CodeGenError("FINANCE_TDX 需要 1 个参数：FINANCE_TDX(q)，q 为指标编号（常量整数）：\n"
                                   + finance_tdx_help())
            qt = _const_fold(e.args[0], allow_div=True)
            if qt is None or not float(qt).is_integer():
                raise CodeGenError("FINANCE_TDX(q) 的 q 必须是**常量整数**：\n" + finance_tdx_help()
                                   + "\n  例：`PE:=FINANCE_TDX(1); 低估:PE<20;`")
            qt = int(qt)
            if not (1 <= qt <= len(FINANCE_TDX_FIELDS)):
                raise CodeGenError("FINANCE_TDX(q) 的 q 需在 1~%d 之间，当前为 %d：\n%s"
                                   % (len(FINANCE_TDX_FIELDS), qt, finance_tdx_help()))
            return "$" + FINANCE_TDX_FIELDS[qt - 1][0]
        # 横向统计（2026-10-09）：BLOCKSETNUM('板块') / INSUM('板块','公式',输出,类型)
        # → 市场级物化字段（见文件上方 BLOCK_KEYS 段的说明）
        if name == "BLOCKSETNUM":
            if len(e.args) != 1 or not isinstance(e.args[0], Str):
                raise CodeGenError(
                    "BLOCKSETNUM 需要 1 个**字符串**参数：BLOCKSETNUM('板块名')\n"
                    "  可用板块：%s\n  例：BLOCKSETNUM('全部A股')" % BLOCK_KEYS_HELP)
            return blocksetnum_field(e.args[0].value)
        if name == "INSUM":
            if len(e.args) != 4:
                raise CodeGenError("INSUM 需要 4 个参数：\n" + insum_help())
            a0, a1, a2, a3 = e.args
            if not isinstance(a0, Str) or not isinstance(a1, Str):
                raise CodeGenError(
                    "INSUM 的前 2 个参数必须是**字符串**（板块名 / 公式名）：\n" + insum_help())
            out_i = _const_fold(a2, allow_div=True)
            calc_i = _const_fold(a3, allow_div=True)
            if out_i is None or calc_i is None or not float(out_i).is_integer() or not float(calc_i).is_integer():
                raise CodeGenError("INSUM 的第 3/4 个参数必须是**常量整数**：\n" + insum_help())
            out_i, calc_i = int(out_i), int(calc_i)
            if out_i != 1:
                raise CodeGenError(
                    "INSUM 的第 3 个参数（取第几个输出）目前只能是 **1**（本平台公式都是单输出），"
                    "当前为 %d" % out_i)
            if calc_i not in INSUM_CALC_TYPES:
                raise CodeGenError(
                    "INSUM 的第 4 个参数（计算类型）目前支持 %s；\n"
                    "  · 4=最大值序号 / 5=最小值序号：返回的是'哪只股票'、"
                    "与'全市场同值字段'的物化形态不兼容 ⇒ 暂不支持 ✓\n"
                    "  · 当前为 %d" % (INSUM_CALC_HELP, calc_i))
            return insum_field(a0.value, a1.value, out_i, calc_i)
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
