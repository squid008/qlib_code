# -*- coding: utf-8 -*-
"""`DYN_MEAN`（`MA/MEAN` 的**变量周期**）+ 函数常量折叠 + 常量窗口守卫（v1.20.55）。

背景（用户 2026-09-23）：「强龙起势（周期 60 天）：特征计算失败: window must be an integer 0 or greater」
—— 公式里 `MA5:=MA(C,MIN(BARNUM,5))`（窗口不超过已上市天数 ✓，通达信里完全合法 ✓）被翻成
`Mean($close,Less(BARSCOUNT($close),5))` ✗ ⇒ qlib `Rolling` 拿**序列**当窗口 ⇒ `pandas.rolling(序列)` ⇒ 崩 ✗。
同一次还要求：「`SQRT(2)*CLOSE` 这种**常量参数的函数**也修掉」✓（原先生成 `Mul(Sqrt(2),$close)`
⇒ 触发 qlib 对"没有任何 `$字段` 的子树"的敏感 ✗）。
"""
import numpy as np
import pandas as pd
import pytest
from qlib.data.base import Expression

from app.factors.parser import translate_formula
from app.factors.parser.codegen import CodeGenError


class _S(Expression):
    """最小序列桩（继承 `Expression` ⇒ 会被算子当"表达式"而非裸数字 ✓，见 v1.20.54 的教训 ✓）。"""

    def __init__(self, arr):
        self.arr = np.asarray(arr, dtype=float)
        super().__init__()

    def load(self, *_a, **_k):
        return pd.Series(self.arr)

    def _load_internal(self, *_a, **_k):
        return pd.Series(self.arr)

    def get_longest_back_rolling(self):
        return 0

    def get_extended_window_size(self):
        return (0, 0)

    def __str__(self):
        return "S"


# ---------------- ① 翻译：变量窗口必须走 DYN_MEAN ----------------

class TestDynMeanTranslate:
    def test_variable_window_uses_dyn_mean(self):
        r = translate_formula("OUT:MA(CLOSE,MIN(BARSCOUNT(CLOSE),5));")
        assert r.expression == "DYN_MEAN($close,Less(BARSCOUNT($close),5))"
        assert "Mean(" not in r.expression, "变量窗口绝不能再生成 qlib 内建 Mean ✗"

    def test_mean_alias_also_dynamic(self):
        r = translate_formula("OUT:MEAN(CLOSE,MIN(BARSCOUNT(CLOSE),20));")
        assert r.expression.startswith("DYN_MEAN(")

    def test_constant_window_still_builtin(self):
        """常量窗口必须仍走 qlib 内建 `Mean` ✓（性能更好 ✓，行为不变 ✓）。"""
        assert translate_formula("OUT:MA(CLOSE,20);").expression == "Mean($close,20)"
        assert translate_formula("OUT:MEAN(CLOSE,20);").expression == "Mean($close,20)"

    def test_user_formula_shape(self):
        """用户真实写法（`BARNUM` 变量 + `MIN`）⇒ 必须不再产出非法窗口 ✓。"""
        text = "BARNUM:=BARSCOUNT(C); MA5:=MA(C,MIN(BARNUM,5)); OUT:MA5;"
        assert "DYN_MEAN($close,Less(BARSCOUNT($close),5))" in translate_formula(text).expression


# ---------------- ② 求值：与 pandas rolling(N,min_periods=1).mean() 逐位一致 ----------------

class TestDynMeanEval:
    def test_matches_pandas(self):
        from app.factors.ops_ext import DYN_MEAN

        vals = [1.0, 2.0, np.nan, 4.0, 5.0, 6.0]
        ns = [3.0, 3.0, 3.0, 3.0, 2.0, 1.0]
        got = DYN_MEAN(_S(vals), _S(ns))._load_internal("x", 0, len(vals))
        exp = []
        for i in range(len(vals)):
            w = max(1, int(ns[i]))
            seg = pd.Series(vals[max(0, i - w + 1):i + 1]).dropna()
            exp.append(seg.mean() if len(seg) else np.nan)
        np.testing.assert_allclose(got.to_numpy(), np.asarray(exp), equal_nan=True)

    def test_all_nan_window_gives_nan(self):
        from app.factors.ops_ext import DYN_MEAN

        got = DYN_MEAN(_S([np.nan, np.nan]), _S([2.0, 2.0]))._load_internal("x", 0, 2)
        assert np.isnan(got.to_numpy()).all(), "窗口内全 NaN ⇒ NaN（不是 0 ✗、也不报警 ✓）"

    def test_nan_window_len_is_one(self):
        """窗口序列为 NaN ⇒ 按 1 处理（`_win_lens_vec` 既有口径 ✓）。"""
        from app.factors.ops_ext import DYN_MEAN

        got = DYN_MEAN(_S([3.0, 4.0]), _S([np.nan, np.nan]))._load_internal("x", 0, 2)
        np.testing.assert_allclose(got.to_numpy(), [3.0, 4.0])


# ---------------- ③ 常量折叠：纯数学函数（用户本次要求 ✓） ----------------

class TestConstFoldFuncCall:
    def test_sqrt_of_const_folds(self):
        r = translate_formula("OUT:SQRT(2)*CLOSE;")
        assert "Sqrt(" not in r.expression, "常量参数不该留下 Sqrt(2) 这种纯常量子树 ✗"
        assert r.expression.startswith("Mul(1.4142")
        assert "$close" in r.expression

    def test_mod_of_const_folds_with_fmod_semantics(self):
        """`MOD(-7,3)` 必须折成 **-1** ✓（符号随被除数 ✓ = `math.fmod` ✓；Python `%` 会给 2 ✗）。"""
        assert translate_formula("OUT:CLOSE+MOD(-7,3);").expression == "Add($close,-1)"

    def test_abs_and_max(self):
        assert translate_formula("OUT:CLOSE+ABS(-3);").expression == "Add($close,3)"
        assert translate_formula("OUT:CLOSE+MAX(2,5);").expression == "Add($close,5)"

    def test_negative_sqrt_not_folded(self):
        """`SQRT(-1)` 无实数值 ⇒ **不折** ✓（否则会写出 `nan` 字面量，qlib 解析不了 ✗）。"""
        assert "Sqrt(-1)" in translate_formula("OUT:CLOSE*SQRT(-1);").expression

    def test_stateful_func_never_folded(self):
        """⚠ 依赖历史序列的函数**绝不能**折叠 ✓（`REF/MA/BARSLAST` 没有常量答案 ✓）。"""
        assert translate_formula("OUT:REF(CLOSE,5);").expression == "Ref($close,5)"
        assert "Mean(" in translate_formula("OUT:MA(CLOSE,5);").expression


# ---------------- ④ 常量窗口守卫：变量周期要报**清楚**（别再让 pandas 报 ✗） ----------------

class TestConstWindowGuard:
    @pytest.mark.parametrize("name", ["EMA", "WMA", "STD", "VAR", "SLOPE", "MED", "DELTA"])
    def test_variable_window_raises_friendly(self, name):
        with pytest.raises(CodeGenError) as ei:
            translate_formula("OUT:%s(CLOSE,MIN(BARSCOUNT(CLOSE),5));" % name)
        msg = str(ei.value)
        assert "常量整数" in msg
        assert "MA" in msg, "错误提示里要给出可操作的改法 ✓"

    def test_constant_window_ok(self):
        assert translate_formula("OUT:EMA(CLOSE,12);").expression == "EMA($close,12)"
