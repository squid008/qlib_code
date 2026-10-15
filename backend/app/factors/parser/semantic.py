# -*- coding: utf-8 -*-
"""公式翻译器：语义分析 + 变量内联。

- 校验单输出（每个公式只允许 1 个输出因子）
- 把中间变量（Assign）递归内联展开进输出表达式，得到无变量的单棵表达式树
- 检测未定义变量 / 循环引用
"""
from __future__ import annotations

from typing import Dict

from .ast import (
    Formula, Expr, Var, BinOp, UnaryOp, FuncCall, Num, Field,
)

# ===========================================================================
# 递推组（自引用/互引递归变量，v2.4.0 —— 用户 2026-10-15 定稿 ✓）
#
# 背景：用户否掉了"每个递归都往 ops_ext 加一个原生算子"的路 ✗（YMTREND() 那个提案已撤 ✓），
#   要求做成**语言特性**：`EMA_C:=REF(EMA_C,1)+1;` 这种写法直接可用 ✓。
#
# 规则（用户定的口径 ✓）：
#   · 某变量在自己（或组内别的变量）的定义式里被 `REF(x, k≥1)` 引用 ⇒ 编辑成**递推组**；
#   · **组内互引只允许 REF(x,k≥1)**（同根互引 ⇒ 直接报错 ✗，语义会不明确）；
#   · 初值：`REF` 越界（不足 k 根）取 **0** ✓（用户："首个没有值一般通行做法就是默认 0" ✓）；
#   · 性能：O(N) 一遍推进（见 factors/recursion.py ✓）。
#
# 编译产物（函数名带 `__` 前缀 ⇒ 与用户可写的函数名不可能撞车 ✓）：
#   `__RECUR_PREV(j, k)` = 组内成员 j 的上一根（k 根前）
#   `__RECUR_OUT(j, N, body_0 … body_{N-1})` = **取值**：算整组、返回成员 j 的当前值 ✓
#   （每个"用到成员"的地方各写一份整组定义 ⇒ 面板按**表达式结构**缓存 ⇒ 实际只算一次 ✓）
# ===========================================================================
RECUR_PREV = "__RECUR_PREV"
RECUR_OUT = "__RECUR_OUT"
RECUR_CUR = "__RECUR_CUR"        # 组内**同根**引用先定义的成员（= 本根已算出的值 ✓）


def _expr_repr(e: Expr) -> str:
    """把 AST 粗略还原成文本（只用于报错提示 ✓）。"""
    if isinstance(e, Num):
        return str(int(e.value)) if float(e.value).is_integer() else repr(e.value)
    if isinstance(e, Var):
        return e.name
    if isinstance(e, Field):
        return e.name
    return type(e).__name__


def _const_int(e: Expr):
    """`e` 能否折成常量整数（只支持字面量与纯算术；参数代入后就是 Num ✓）。"""
    if isinstance(e, Num):
        v = float(e.value)
        return int(v) if float(v).is_integer() else None
    if isinstance(e, UnaryOp) and e.op == "NEG":
        v = _const_int(e.operand)
        return None if v is None else -v
    if isinstance(e, BinOp) and e.op in ("ADD", "SUB", "MUL", "DIV"):
        a, b = _const_int(e.left), _const_int(e.right)
        if a is None or b is None:
            return None
        if e.op == "ADD":
            return a + b
        if e.op == "SUB":
            return a - b
        if e.op == "MUL":
            return a * b
        return int(a / b) if b and a % b == 0 else None
    return None


def _scan_deps(expr: Expr, imm: set, delayed: dict, ref_compound: set) -> None:
    """扫一棵树，分三类收集引用（见上方规则 ✓）：
    `imm` = 非延迟引用（同根）｜`delayed` = `REF(变量, 常量k≥1)` 的 (名, k)｜
    `ref_compound` = 出现在 REF 里但**不是**"裸变量 + 常量k"的变量名（⇒ 若是递推成员就报错 ✓）。
    """
    if isinstance(expr, Var):
        imm.add(expr.name.upper())
        return
    if isinstance(expr, FuncCall):
        if expr.name.upper() == "REF" and len(expr.args) == 2:
            k = _const_int(expr.args[1])
            a0 = expr.args[0]
            if isinstance(a0, Var) and k is not None and k >= 1:
                delayed.add((a0.name.upper(), k))
                return
            # REF 的写法不合规（第一参不是裸变量，或 k 不是常量/小于 1）
            if isinstance(a0, Var):
                ref_compound.add(a0.name.upper())
            for sub in (a0, expr.args[1]):
                _scan_names_only(sub, ref_compound)
            return
        for a in expr.args:
            _scan_deps(a, imm, delayed, ref_compound)
        return
    if isinstance(expr, BinOp):
        _scan_deps(expr.left, imm, delayed, ref_compound)
        _scan_deps(expr.right, imm, delayed, ref_compound)
        return
    if isinstance(expr, UnaryOp):
        _scan_deps(expr.operand, imm, delayed, ref_compound)
        return


def _scan_names_only(expr: Expr, sink: set) -> None:
    """把一棵子树里所有变量名收进 `sink`（只用于"REF 写法不合规"的定位 ✓）。"""
    if isinstance(expr, Var):
        sink.add(expr.name.upper())
    elif isinstance(expr, BinOp):
        _scan_names_only(expr.left, sink)
        _scan_names_only(expr.right, sink)
    elif isinstance(expr, UnaryOp):
        _scan_names_only(expr.operand, sink)
    elif isinstance(expr, FuncCall):
        for a in expr.args:
            _scan_names_only(a, sink)


def recursion_members(defs: dict) -> list:
    """找出**递推成员**（定义顺序返回 ✓），并校验组内互引写法。

    `defs`: `{大写名: 表达式}`（含输出线 ✓）。
    """
    imm, delayed, ref_compound = {}, {}, {}
    for name, expr in defs.items():
        i, d, rc = set(), set(), set()
        _scan_deps(expr, i, d, rc)
        imm[name], delayed[name], ref_compound[name] = i, d, rc
    # 全图（同根 + 延迟）里能回到自己 ⇒ 递推成员 ✓
    edges = {n: set(imm[n]) | {m for (m, _k) in delayed[n]} for n in defs}
    members = []
    for n in defs:                                   # 保序（= 公式里的定义顺序 ✓）
        seen, stack = set(), [n]
        while stack:
            u = stack.pop()
            for v in edges.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        if n in seen:
            members.append(n)
    mset = set(members)
    # ---- ⓪ 自引用但 REF 写得**不合规**（`REF(A+1,1)` / `REF(MA(A,5),1)` / `REF(A,N)`）⇒
    #      必须在这里先说清楚 ✓：否则它会落到"普通变量内联"，最终报一个**指错地方**的
    #      "中间变量存在循环引用 ✗"（2026-10-15 单测抓到 ✓）。
    for n, rc in ref_compound.items():
        if n in rc:
            raise SemanticError(_ref_form_error(n))
    if not members:
        return []
    # ---- ① 同根成环 ⇒ 沿用**老错**（"中间变量存在循环引用"，如 `A:=A+1;` ✓ 保持既有行为 ✓）
    cyc = _find_cycle({n: {x for x in imm[n] if x in defs} for n in defs})
    if cyc:
        raise SemanticError("中间变量存在循环引用：%s" % " -> ".join(cyc))
    # ---- ② 组内"同根引用"只允许指向**先定义**的成员（= 每根按定义顺序推进 ✓，用户 2026-10-15 ✓）
    #       ⚠ 这一条是自检脚本用**真实趋势线**跑出来的 ✗→✓（第一版要求"组内互引一律走 REF"too strict ✗：
    #         趋势线的 `LINE` 在同根里要读 `UP`（UP 在它前面定义 ✓）——那不是环，是可排序的 ✓）。
    order = {n: i for i, n in enumerate(members)}
    for n in members:
        late = sorted(x for x in imm[n] if x in mset and order[x] > order[n])
        if late:
            raise SemanticError(
                "递推组里**同根**引用只能指向**先定义**的变量 ✗ —— `%s` 引用了后定义的 `%s`\n"
                "  · 每根按**定义顺序**推进 ✓（先定义的先算完 ⇒ 同根可以读它 ✓）；\n"
                "  · 要读后定义变量的**上一根** ⇒ 写 `REF(变量,1)` ✓（首根没有上一根 ⇒ 按 0 算 ✓）；\n"
                "  · 或者把它们俩的定义顺序换一下 ✓。"
                % (n, "`、`".join(late)))
        bad_ref = sorted(x for x in ref_compound[n] if x in mset)
        if bad_ref:
            raise SemanticError(_ref_form_error(n, bad_ref))
    return members


def _ref_form_error(name: str, others=None) -> str:
    """`REF` 写法不合规的**统一报错文案**（两处调用：自引用 / 组内互引 ✓）。"""
    who = "引用了 `%s`" % "`、`".join(others) if others else "引用了自己"
    return (
        "REF 里的递推变量写法不支持 ✗ —— `%s` 出现在 `REF(...)` 里，但不是"
        "`REF(变量, 常量)` 这种形式（%s）\n"
        "  · 支持：`REF(A,1)`、`REF(A,k)`（k 为常量整数 ≥1）✓；\n"
        "  · 不支持：`REF(A+1,1)`、`REF(MA(A,5),1)`、`REF(A,N)`（N 不是常量）✗；\n"
        "  · 需要位移的复合量 ⇒ 先 `T:=A+1;` 再 `REF(T,1)`（T 是普通变量 ✓）。"
        % (name, who))


def _find_cycle(edges: dict):
    """在**同根引用图**里找一个环（返回环上的名字序列；无环返回 None ✓）。"""
    state: Dict[str, int] = {}            # 0=未访问 1=在栈上 2=已完成
    for start in edges:
        if state.get(start):
            continue
        stack = [(start, iter(sorted(edges.get(start, ()))))]
        path = [start]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            for nxt in it:
                st = state.get(nxt)
                if st == 1:
                    return path[path.index(nxt):] + [nxt]
                if not st and nxt in edges:
                    state[nxt] = 1
                    path.append(nxt)
                    stack.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    break
            else:
                state[node] = 2
                stack.pop()
                path.pop()
    return None

# 公式可用的内置函数名（用于区分"函数调用"与"变量引用"；未在此表内的标识符视为变量）
BUILTIN_FUNCS = {
    "MA", "SMA", "EMA", "WMA", "MEMA", "REF", "HHV", "LLV", "SUM", "COUNT",
    "FILTER", "HHVALL", "LLVALL", "HHVBARS", "LLVBARS", "BARSLAST",
    "BARSSINCE", "BARSSINCEN", "BARSCOUNT", "DMA", "ALL", "ANY", "LAST",
    # 自定义外挂算子（app/factors/ops_ext.py），需同时在 codegen 中映射
    "DYN_MIN", "DYN_MAX", "DYN_COUNT", "DYN_REF", "DYN_SUM", "EMA_TDX", "SGN", "SIGN", "INT", "BETWEEN",
    "CROSS", "IF", "IFS", "RANGE", "LONGCROSS",
    # 筹码分布（v1.19.83）：通达信/益盟的 WINNER(P)/COST(q)，由面板级派生字段承载
    "COST", "WINNER",
    # 基本面（2026-10-09；2026-10-10 加第二参数"口径"）：益盟盘口那批基本面指标，
    # FINANCE(q[,口径]) 取第 q 个指标的历史序列（1 单季 / 2 年化 / 3 TTM；省略 = 1 单季）
    # （字段 bin 由 tools/dump_finance.py 从米筐 pit 财报表 + 总市值生成，见该脚本顶部口径表）
    "FINANCE",
    # 基本面（通达信源，2026-10-09）：★ 2026-10-10 **已下线** —— 这里**故意保留**名字，
    #   让 codegen 能给一条"请改用 FINANCE(q[,口径])"的**友好错** ✓（删掉它只会变成
    #   "不支持的函数：FINANCE_TDX"，用户看不出该换哪个 ✓）。
    "FINANCE_TDX",
    # 横向统计（2026-10-09，通达信语义）：板块当日股票数 / 板内某公式输出的横向聚合
    #   BLOCKSETNUM('全部A股')            → 该板块当日成分股数（日截面）
    #   INSUM('全部A股','IS_GOLD_PIT',1,0) → 板内各股该公式第 1 个输出按计算类型聚合
    # 二者都**物化成市场级字段**（同日所有股票同值），见 app/factors/market_stat.py
    "BLOCKSETNUM", "INSUM",
    # ★ 2026-10-15：DATE 既是字段（`DATE<=20070901`）也是函数（`DATE(20070901)`，
    #   把字面量折成日序号 ⇒ 减法=真实天数 ✓），故函数白名单里也要有它 ✓。
    "DATE", "日期",
    "DELTA", "MEAN", "MED",
    "ABS", "SQRT", "LOG", "LN", "EXP", "POW", "POWER", "MAX", "MIN", "MOD",
    "CEILING", "FLOOR",
    "STD", "STDP", "VAR", "VARP", "AVEDEV", "SLOPE", "FORCAST", "DEVSQ",
    # Level2（留接口：语法支持，计算层待数据）
    "BIGORDER", "ORDER", "ORDERAMT", "ORDERNUM", "ORDERNWP", "ORDERVOL",
    "TRANSACTNUM", "TRANSACTVOL", "ALLASKVOL", "ALLBIDVOL",
    # 资金流向（moneyflow）：L2_PCT(n)/L2_AMO(n)/L2_VOL(n)，n=0主力/1超大/2大/3中/4小
    "L2_PCT", "L2_AMO", "L2_VOL",
}


class SemanticError(Exception):
    pass


def resolve_vars(formula: Formula) -> Formula:
    """语义分析：校验单输出，并检查变量定义。返回原 Formula（变量内联在 codegen 阶段做）。"""
    if len(formula.outputs) == 0:
        raise SemanticError("公式没有输出线（需要至少一条 `因子名:表达式;`）")
    if len(formula.outputs) > 1:
        names = "、".join(o.name for o in formula.outputs)
        raise SemanticError(f"公式只允许 1 个输出因子，当前有 {len(formula.outputs)} 个：{names}")

    # 检查变量是否被重复定义（大小写不敏感）
    seen_assigns = {}
    for a in formula.assigns:
        key = a.name.upper()
        if key in seen_assigns:
            raise SemanticError(
                f"变量 `{a.name}` 被重复定义（前面已定义 `{seen_assigns[key]}`），请删除其中一行或改名")
        seen_assigns[key] = a.name

    # 检查中间变量是否被定义 / 是否有未定义引用
    assign_names = {a.name.upper(): a for a in formula.assigns}
    output_name = formula.outputs[0].name.upper()

    def check(expr: Expr, ctx: str):
        if isinstance(expr, Var):
            if expr.name.upper() not in assign_names and expr.name.upper() != output_name:
                raise SemanticError(f"引用了未定义的变量或函数：{expr.name}（在 {ctx}）")
        elif isinstance(expr, BinOp):
            check(expr.left, ctx)
            check(expr.right, ctx)
        elif isinstance(expr, UnaryOp):
            check(expr.operand, ctx)
        elif isinstance(expr, FuncCall):
            if expr.name not in BUILTIN_FUNCS:
                raise SemanticError(f"不支持的函数：{expr.name}（在 {ctx}）")
            for i, a in enumerate(expr.args):
                # L2_PCT/L2_AMO/L2_VOL 的第 2+ 参数是买卖方向关键字（b/s），
                # 是否合法统一由 codegen 校验（此处按变量检查会把 X 误报为未定义变量）
                if expr.name in ("L2_PCT", "L2_AMO", "L2_VOL") and i >= 1:
                    continue
                check(a, ctx)

    for a in formula.assigns:
        check(a.value, f"变量 {a.name}")
    for o in formula.outputs:
        check(o.value, f"输出 {o.name}")

    return formula


def inline_variables(formula: Formula) -> Expr:
    """变量内联 + **递推组**编译（v2.4.0 ✓），返回输出表达式的单棵树（无 Var 引用）。

    - 普通变量：与以前完全一致（递归展开、循环引用报错 ✓）；
    - **递推成员**（`A:=REF(A,1)+…` 或 `A:REF(A,1)+…`）：不再内联成普通子树，而是编译成
      `__RECUR_OUT(j, N, body_0…)` 取**当前值**；成员体内 `REF(成员,k)` 变成 `__RECUR_PREV(j,k)`
      （= 组内回看 ✓）。整组由面板/内核一次 O(N) 推进 ✓（见 `factors/recursion.py` ✓）。
    """
    assign_map: Dict[str, Expr] = {a.name.upper(): a.value for a in formula.assigns}
    output = formula.outputs[0]
    out_name = output.name.upper()

    # 定义表（含输出线 ✓ —— 输出名自引用 `A:REF(A,1)+1;` 也是一种递推写法 ✓）
    defs: Dict[str, Expr] = dict(assign_map)
    defs.setdefault(out_name, output.value)
    members = recursion_members(defs)
    m_index = {name: i for i, name in enumerate(members)}
    _bodies: Dict[str, Expr] = {}          # 成员体（展开后，含 __RECUR_PREV ✓）

    def _member_body(name: str) -> Expr:
        """成员体的展开（**只在组内视角**展开：成员引用只允许经 REF ✓，已由语义校验 ✓）。"""
        if name not in _bodies:
            _bodies[name] = expand(defs[name], [name], in_group=True)
        return _bodies[name]

    def _recur_out(j: int) -> Expr:
        """`__RECUR_OUT(j, N, body_0 … body_{N-1})`（每个使用点各写一份整组 ✓，面板按结构缓存 ✓）。"""
        return FuncCall(RECUR_OUT, [Num(float(j)), Num(float(len(members)))]
                        + [_member_body(m) for m in members])

    # 展开一个表达式（先展开其内部变量引用）
    def expand(expr: Expr, stack, in_group: bool = False) -> Expr:
        if isinstance(expr, Var):
            name = expr.name.upper()
            if name in m_index:
                if in_group:
                    # 组内**同根**引用：成员顺序已校验（只能引用先定义的 ✓）
                    # ⇒ 编译成"取本根已算出的值"（`RecurCur(j)` ✓），由内核按定义顺序推进 ✓。
                    return FuncCall(RECUR_CUR, [Num(float(m_index[name]))])
                return _recur_out(m_index[name])
            if name not in assign_map:
                # ⚠ 未定义引用在这里**保留**，由 codegen 报"未展开的变量引用"（保持旧行为 ✓）；
                #   ⚠ 注意 `out_name` 已在 `defs` 里 ⇒ 输出名自引用会走上面的递推分支 ✓
                #   （旧注释说"输出名自引用落到后面处理"是过时的 ✓，v2.4.0 起真的支持了 ✓）。
                return expr
            if name in stack:
                raise SemanticError(f"中间变量存在循环引用：{' -> '.join(stack + [name])}")
            return expand(assign_map[name], stack + [name], in_group)
        if isinstance(expr, BinOp):
            return BinOp(expr.op, expand(expr.left, stack, in_group),
                         expand(expr.right, stack, in_group))
        if isinstance(expr, UnaryOp):
            return UnaryOp(expr.op, expand(expr.operand, stack, in_group))
        if isinstance(expr, FuncCall):
            # 组内互引的唯一合法形态：`REF(成员, 常量k≥1)` ⇒ `__RECUR_PREV(j,k)` ✓
            if in_group and expr.name.upper() == "REF" and len(expr.args) == 2 \
                    and isinstance(expr.args[0], Var) \
                    and expr.args[0].name.upper() in m_index:
                k = _const_int(expr.args[1])
                if k is None or k < 1:                       # 语义校验已拦，这里兜底 ✓
                    raise SemanticError(
                        "递推组内的 REF 回看根数必须是**常量整数且 ≥1** ✗：REF(%s, %s)"
                        % (expr.args[0].name, _expr_repr(expr.args[1])))
                return FuncCall(RECUR_PREV, [
                    Num(float(m_index[expr.args[0].name.upper()])), Num(float(k))])
            return FuncCall(expr.name, [expand(a, stack, in_group) for a in expr.args])
        return expr

    if out_name in m_index:
        # 输出线本身是递推成员（`A:REF(A,1)+1;`）⇒ 取它的当前值 ✓
        return _recur_out(m_index[out_name])
    return expand(output.value, [])


def resolve(formula: Formula) -> Expr:
    """一步完成：校验单输出 + 变量内联，返回输出表达式的单棵树。"""
    formula = resolve_vars(formula)
    return inline_variables(formula)
