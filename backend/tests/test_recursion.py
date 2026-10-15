# -*- coding: utf-8 -*-
"""**递推组**（自引用/互引递归变量，v2.4.0）回归测试 —— 用户 2026-10-15 定稿 ✓。

用户原话（决定要这条特性）：「YMTREND() 这种加算子的方式是啥意思？以后每个递归都要加算子
那是不是不好？咱们可以直接搞成支持 `ema_c:ref(ema_c,1)+1;` 这种写法吧？首个 ema_c 没有值
一般通行做法是不是就是默认 0？然后性能上处理成 O(N) 不就好了？」

分工：
  · **编译期**（本文件 §1）：`REF(自己,k)` ⇒ `RecurOut/RecurPrev/RecurCur` 的产物与各类报错；
  · **内核**（本文件 §2，纯 numpy、不取数）：逐根推进 / 默认 0 / 同根读 / 停牌冻结 / float64 硬约束；
  · **面板接线**（本文件 §3，datareq）：真实数据上跑一条自引用公式，逐股核对 1,2,3… ✓；
  · **qlib 路径**（§3 末尾）：必须报**清楚的错**（不是 `is not registered` 天书 ✗）。
"""
import numpy as np
import pytest

from app.factors.parser import translate_formula
from app.factors.parser.semantic import SemanticError
from app.factors.recursion import float_accumulation_note, run_group

# ---------------------------------------------------------------------------
# 1) 编译期
# ---------------------------------------------------------------------------

def test_single_variable_self_reference():
    """`A:=REF(A,1)+1;` ⇒ 单成员递推组 ✓（用户点名的写法 ✓）。"""
    assert translate_formula("A:=REF(A,1)+1; 输出:A;").expression == \
        "RecurOut(0,1,Add(RecurPrev(0,1),1))"


def test_output_line_self_reference():
    """输出线自引用（`A:REF(A,1)+1;`）也支持 ✓ —— 以前只会报"未展开的变量引用" ✗。"""
    assert translate_formula("输出:REF(输出,1)+1;").expression == \
        "RecurOut(0,1,Add(RecurPrev(0,1),1))"


def test_group_with_three_members_and_same_bar_read():
    """递推组：3 个成员互引 ⇒ 一个 `RecurOut` 里带 3 个体 ✓；
    组内**同根**读先定义的成员 ⇒ `RecurCur` ✓（趋势线的 `LINE` 读 `UP` 就靠它 ✓）。"""
    src = ("UP:=IF(BARSCOUNT(C)<=3,0,REF(UP,1));"
           "LINE:=IF(REF(UP,1),REF(LINE,1),UP);"        # ← UP 同根引用（先定义 ✓）
           "STEP:=IF(UP,0.02,REF(STEP,1)+0.02);"
           "输出:UP+LINE+STEP;")
    e = translate_formula(src).expression
    assert e.count("RecurOut(") >= 1
    assert "RecurCur(0)" in e, "同根读先定义的成员应编译成 RecurCur ✓"
    assert "RecurPrev(1,1)" in e and "RecurPrev(2,1)" in e


def test_group_size_matches_members():
    src = "A:=REF(A,1)+1; B:=REF(B,1)+REF(A,1); 输出:B;"
    e = translate_formula(src).expression
    assert e.startswith("RecurOut(1,2,"), "成员数应为 2、取第 2 个（B）✓：%s" % e[:40]


@pytest.mark.parametrize("src,want", [
    # 同根成环（没有任何 REF）⇒ 沿用**老错**（保持既有行为 ✓）
    ("A:=A+1; 输出:A;", "循环引用"),
    ("A:=REF(A,1)+B; B:=A+1; 输出:B;", "循环引用"),
    # 组内同根引用**后定义**的成员 ⇒ 明确报错（每根按定义顺序推进 ✓）
    ("A:=IF(REF(A,1),B,0); B:=REF(B,1)+1; 输出:A;", "先定义"),
    # REF 里不是"裸变量 + 常量 k" ⇒ 明确报错
    ("A:=REF(A+1,1); 输出:A;", "REF"),
    ("A:=REF(MA(A,5),1); 输出:A;", "REF"),
])
def test_recursion_bad_forms(src, want):
    with pytest.raises(SemanticError) as ei:
        translate_formula(src)
    assert want in str(ei.value), str(ei.value)


def test_plain_variables_still_inline():
    """普通变量行为不变（仍内联，不会误进递推组 ✓）。"""
    assert translate_formula("A:=CLOSE*2; 输出:A+1;").expression == "Add(Mul($close,2),1)"


# ---------------------------------------------------------------------------
# 2) 内核（纯 numpy，不取数）
# ---------------------------------------------------------------------------

def test_kernel_accumulator_and_prev():
    """累加器 + 上一根：`A[i]=A[i-1]+1`（首根按 0 ⇒ 1,2,3…）；`B[i]=A[i-1]+10` ✓。"""
    skels = [("Add", [("p", 0, 1), ("c", 1.0)]),
             ("Add", [("p", 0, 1), ("c", 10.0)])]
    valid = np.ones((5, 1), dtype=bool)
    a, b = run_group(skels, 2, [], valid)
    assert a[:, 0].tolist() == [1, 2, 3, 4, 5]
    assert b[:, 0].tolist() == [10, 11, 12, 13, 14]


def test_kernel_same_bar_read_uses_definition_order():
    """同根读（`("m", j)`）取的是**本根刚算出的**值 ✓（不是上一根 ✗）。"""
    skels = [("c", 7.0),                      # 成员 0 = 常量 7
             ("Add", [("m", 0), ("c", 1.0)])]  # 成员 1 = 成员0(本根) + 1 ⇒ 恒 8
    valid = np.ones((3, 1), dtype=bool)
    _a, b = run_group(skels, 2, [], valid)
    assert b[:, 0].tolist() == [8.0, 8.0, 8.0]


def test_kernel_freeze_and_nan_on_invalid_rows():
    """停牌/无 K 线的行：状态**冻结**（不推进）、输出 NaN ✓（= 益盟"无停牌行"口径 ✓）。"""
    skels = [("Add", [("p", 0, 1), ("c", 1.0)])]
    valid = np.ones((5, 1), dtype=bool)
    valid[2, 0] = False
    a, = run_group(skels, 1, [], valid)
    assert np.isnan(a[2, 0]), "无效行应输出 NaN ✓"
    # 有效行的计数不受无效行影响（状态没被推进 ✓）：1,2,3,4
    assert a[[0, 1, 3, 4], 0].tolist() == [1, 2, 3, 4]


def test_kernel_prev_k_step_and_default():
    """`("p", j, k)` 的 k 根前 + 越界取默认 0 ✓（用户："首个没有值一般默认 0" ✓）。"""
    skels = [("p", 0, 2), ("p", 1, 1)]
    valid = np.ones((4, 1), dtype=bool)
    a, b = run_group(skels, 2, [], valid)
    assert a[:, 0].tolist() == [0, 0, 0, 0], "成员0 = 自己2根前 ⇒ 恒 0 ✓"
    assert b[:, 0].tolist() == [0, 0, 0, 0], "成员1 = 成员0的1根前 ⇒ 恒 0 ✓"


def test_kernel_pre_arrays():
    """预计算子树（`("a", k)`）逐根取用 ✓（= 面板里"与递归无关的部分一次算完"✓）。"""
    pre = [np.array([[10.0], [20.0], [30.0]])]
    skels = [("Add", [("a", 0), ("p", 0, 1)])]
    valid = np.ones((3, 1), dtype=bool)
    a, = run_group(skels, 1, pre, valid)
    assert a[:, 0].tolist() == [10.0, 30.0, 60.0]         # 10+0, 20+10, 30+30 ✓（累加 ✓）


def test_kernel_rejects_sequence_ops():
    """组内用窗口/序列算子（如 MA）⇒ **能自助的错** ✓（不是莫名 KeyError ✗）。"""
    from app.factors.recursion import RecursionError_
    with pytest.raises(RecursionError_) as ei:
        run_group([("Mean", [("p", 0, 1), ("c", 5.0)])], 1, [],
                  np.ones((3, 1), dtype=bool))
    assert "逐元素" in str(ei.value)


def test_float64_accumulation_is_load_bearing():
    """★ 用户脚本 `STEP` 的上限**恰好落在浮点误差上** ⇒ 中途绝不能降精度 ✗。

    `0.02` 累加 10 次 = `0.19999999999999998`（仍 `< 0.2` ⇒ 会再加一次到 ≈0.22 ✓）；
    若中途降到 **float32**（= 我们 bin 的精度 ✗）它就变成 `0.20000000298023224` ⇒ `< 0.2` 判据
    **翻转** ⇒ 趋势线翻向时机改变 ✗✗ —— 用数值钉死这条（改精度这里立刻红 ✓）。
    """
    v10, v11 = float_accumulation_note()
    assert v10 == 0.19999999999999998
    assert v10 < 0.2, "第 10 次累加必须仍 < 0.2 ✓"
    assert abs(v11 - 0.22) < 1e-15, "再加一次应到 ≈0.22 ✓"
    assert not (float(np.float32(v10)) < 0.2), "float32 会翻转该判据（所以必须 float64 ✓）"


# ---------------------------------------------------------------------------
# 3) 面板接线 + qlib 路径（datareq）
# ---------------------------------------------------------------------------

@pytest.mark.datareq
def test_panel_recursion_runs_and_counts_bars():
    """真实数据：`A:=REF(A,1)+1;` ⇒ 每股在**有效 K 线**上正好是 1,2,3… ✓
    （顺带验证：默认 0 起算 ✓、停牌行 NaN ✓、多股各自独立 ✓）。"""
    from app.factors.panel_expr import PanelEvaluator
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()
    expr = translate_formula("A:=REF(A,1)+1; 输出:A;").expression
    ev = PanelEvaluator(["SH600000", "SZ000001"], "2024-01-02", "2024-03-29",
                        union_fields=["close"])
    s = ev.eval_expr(expr)
    assert len(s) > 0
    inst = s.index.get_level_values("instrument")
    for code in ("SH600000", "SZ000001"):
        vals = s[inst == code].dropna().to_numpy(dtype=float)
        assert len(vals) > 30, "%s 有效行太少（%d）" % (code, len(vals))
        assert vals.tolist() == list(np.arange(1.0, len(vals) + 1.0)), \
            "%s 的递推序列不是 1,2,3…（前 5 个：%s）" % (code, vals[:5].tolist())


@pytest.mark.datareq
def test_qlib_path_reports_clear_error():
    """qlib 逐股路径（模型特征）暂不支持递推 ⇒ 必须报**清楚的中文错** ✓（不是注册表天书 ✗）。"""
    from app.services.qlib_runtime import ensure_qlib_init
    import qlib.data as _d

    ensure_qlib_init()
    expr = translate_formula("A:=REF(A,1)+1; 输出:A;").expression
    with pytest.raises(Exception) as ei:                          # noqa: BLE001
        _d.D.features(["SH600000"], [expr], start_time="2024-01-02", end_time="2024-03-29")
    assert "递推变量" in str(ei.value), "报错要能自助（拿到的是：%s）" % str(ei.value)[:200]
