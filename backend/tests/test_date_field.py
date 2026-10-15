# -*- coding: utf-8 -*-
"""`DATE` 字段（公式语言里的"当前 K 线日期"）—— 用户 2026-10-15 点名要的 ✓。

守三件事：

1. **换算口径**：`DATE(20071008)-DATE(20070901)` = **37**（真实天数 ✓，而 8 位整数直接相减是 107 ✗）；
2. **编译期折字面量**：`DATE <= 20070901` ⇒ `Le($date,13757)` ✓；写成带横杠的 `2007-09-01`（= 1997 ✗）
   或越界值 ⇒ **友好错**（绝不能静默按整数比 ✗）；
3. **两条求值路径同值**（datareq）：面板由 index **现合成**（`panel_expr.field("$date")` ✓）
   vs **物化 bin**（`tools/dump_date.py` ✓）—— 都走 `app/factors/calc_date.py` ⇒ 必须逐位相同 ✓。

⚠ 值 = **日序号**（距 1970-01-01 的天数），**不是 YYYYMMDD** ✗：
qlib bin 是 float32，只能精确表示 ≤ 2**24 的整数 ⇒ 实测 `np.float32(20260305)=20260304`、
`np.float32(20070901)=20070900` ✗（详见 `calc_date` 顶部 ✓）。
"""
import numpy as np
import pytest

from app.factors.calc_date import from_serial, is_date_literal, to_serial
from app.factors.parser import translate_formula
from app.factors.parser.codegen import CodeGenError


# ---------------------------------------------------------------------------
# 1) 换算口径（不取数）
# ---------------------------------------------------------------------------
def test_serial_roundtrip_and_day_diff():
    a, b = to_serial(20071008), to_serial(20070901)
    assert a - b == 37, "跨月日期相减必须等于真实天数 ✗"
    assert from_serial(a) == 20071008 and from_serial(b) == 20070901
    assert to_serial(20260305) == 20517, "个例锚点（2026-03-05 → 20517）变了 ⇒ 基准改过 ✗"


def test_date_literal_shape():
    for v in (20070901, 19000101, 21001231):
        assert is_date_literal(v), v
    # ⚠ `2007-09-01` 语法上是 1997 ✗、8 位以外的一律不算日期 ⇒ 必须为 False（否则静默比错 ✗）
    for v in (1997, 0, 123456789, 2007090, 21001232, None, float("nan")):
        assert not is_date_literal(v), v


# ---------------------------------------------------------------------------
# 2) 编译期：折字面量 + 友好错（不取数）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("src,want", [
    ("老规则:DATE<=20070901;", "Le($date,13757)"),
    ("X:CLOSE>0 AND DATE<=20070901;", "And(Gt($close,0),Le($date,13757))"),
    ("X:日期<=20070901 AND CLOSE>0;", "And(Le($date,13757),Gt($close,0))"),
    # 日期减法 ⇒ 折成常量（37 ✓），再由常量折叠并进表达式 ✓
    ("X:DATE(20071008)-DATE(20070901)+CLOSE;", "Add(37,$close)"),
    ("X:DATE+0*CLOSE;", "Add($date,Mul(0,$close))"),
])
def test_date_literals_are_folded(src, want):
    assert translate_formula(src).expression == want


@pytest.mark.parametrize("src", [
    "X:DATE<=2007-09-01;",        # 带横杠 ⇒ 语法上是 1997 ✗（比较语境必须报错）
    "X:DATE(2007-09-01)+CLOSE;",  # 同上，函数形态
    "X:DATE(1997)>0;",            # 越界
    "X:DATE()>0;",                # 参数个数
    "X:DATE(CLOSE)>0;",           # 必须常量
])
def test_date_bad_forms_raise_friendly_error(src):
    with pytest.raises(CodeGenError) as ei:
        translate_formula(src)
    msg = str(ei.value)
    assert "DATE" in msg, msg


def test_date_only_formula_is_rejected():
    """整条公式不含任何行情字段 ⇒ 仍按既有规矩报错（不生成常量列 ✓）。"""
    with pytest.raises(CodeGenError):
        translate_formula("X:DATE(20071008)-DATE(20070901);")


def test_date_inputs_reported():
    tf = translate_formula("X:CLOSE>0 AND DATE<=20070901;")
    assert tf.inputs == ["$close", "$date"]


# ---------------------------------------------------------------------------
# 3) 停牌删行：DATE 必须与 $close 一起删（用户 2026-10-15 定稿 ✓）
# ---------------------------------------------------------------------------
def test_date_is_sr_masked():
    from app.engine.feature_cache import _SR_FIELDS_BY_CLOSE_MASK, _sr_wrap_expr
    assert "$date" in _SR_FIELDS_BY_CLOSE_MASK, "DATE 每根都有值 ⇒ 不按 close 掩码删行会错位 ✗"
    assert _sr_wrap_expr("$date") == "SR($date,$close)"


# ---------------------------------------------------------------------------
# 4) datareq：面板现合成 vs 物化 bin（同值 ✓）
# ---------------------------------------------------------------------------
@pytest.mark.datareq
def test_date_panel_synthesis_matches_materialized_bin():
    """面板合成值逐位 == 物化 bin 值 == 该行日期本身 ✓（两条路径同源 ⇒ 必须全中 ✓）。"""
    import os

    from app.factors.panel_expr import PanelEvaluator
    from app.services.datasets import active_dir
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()          # ⚠ 必须走统一入口（裸调 qlib.init 会清空 custom_ops）
    ddir = active_dir()
    cal_path = os.path.join(ddir, "calendars", "day.txt")
    if not os.path.exists(cal_path):
        pytest.skip("数据集没有日历文件：%s" % cal_path)
    with open(cal_path, "r", encoding="utf-8") as f:
        cal = [ln.strip() for ln in f if ln.strip()]
    pos = {d: i for i, d in enumerate(cal)}

    ev = PanelEvaluator(["SH600000", "SZ000001"], "2024-01-02", "2024-03-29",
                        union_fields=["close", "date"])
    s = ev.field("$date")
    assert len(s), "取不到面板行"
    bins = {}
    n_ok = 0
    for (inst, dt), v in s.items():
        if not np.isfinite(v):
            continue
        # ① 面板合成值 == 该行日期本身 ✓
        assert from_serial(v) == int(dt.strftime("%Y%m%d")), (
            "面板合成的 DATE 与该行日期对不上：%s %s ⇒ %s" % (inst, dt.date(), from_serial(v)))
        # ② 同一个 (股票, 日期) 的物化 bin 值逐位相同 ✓
        code = str(inst).lower()
        if code not in bins:
            path = os.path.join(ddir, "features", code, "date.day.bin")
            if not os.path.exists(path):
                pytest.skip("没有物化的 date.day.bin ⇒ 先跑 backend/tools/dump_date.py")
            raw = np.fromfile(path, dtype="<f4")
            bins[code] = (int(raw[0]), raw[1:])
        first, vals = bins[code]
        k = pos[str(dt.date())] - first
        assert 0 <= k < len(vals), "bin 与日历对不上（k=%d, len=%d）" % (k, len(vals))
        assert float(vals[k]) == float(v), (
            "bin 值与面板合成值不一致：%s %s（%s vs %s）" % (code, dt.date(), vals[k], v))
        n_ok += 1
    assert n_ok > 100, "有效行太少（%d）⇒ 断言没意义" % n_ok
