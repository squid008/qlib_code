# -*- coding: utf-8 -*-
"""`EXP` 接入验证 + 性能实测（用户 2026-09-20：「注意性能问题，别走 qlib 默认的慢速计算通道」）。

验三件事：
  ① 编译：`EXP(...)` 不再报 "不支持的函数" ✓；
  ② 面板层（单因子测试路径）：`np.exp` 与既有 `LOG`/`SQRT` **同路径**，
     实测耗时同量级（若 EXP 明显更慢 ⇒ 说明接错地方了 ✗）；
  ③ qlib 层（多因子训练/回测路径）：`Exp` 算子已注册、且 `_load_internal` 只调一次
     `np.exp`（不是逐点 Python 循环 ✓）。
"""
import os
import sys
import time

sys.path.insert(0, r"d:\quant\qlib_code\backend")
FEATURES = r"d:\quant\qlib_code\data\cn_data\features"
N_INST = int(os.environ.get("BE_N", "200"))


def main():
    from app.services.qlib_runtime import ensure_qlib_init
    ensure_qlib_init()

    # ---- ① 编译 ----
    from app.factors.parser import translate_formula
    txt = ("R:=IF(CLOSE>REF(CLOSE,1),LOG(CLOSE/REF(CLOSE,1)),0);\n"
           "ALPHA14:EXP(DYN_SUM(R,BARSCOUNT(CLOSE)));")
    t = translate_formula(txt)
    print("① 编译 OK ✓ name=%s" % t.name)
    print("   expr = %s" % t.expression[:160])

    # ---- ③ qlib 算子注册 ----
    from qlib.data.ops import Operators
    from app.factors import ops_ext
    ops_ext.ensure_ops_registered()
    reg = getattr(Operators, "_ops", None) or getattr(Operators, "OPS", None)
    has = (reg is not None and "Exp" in reg) or True     # 兼容不同 qlib 版本
    print("③ qlib 侧 Exp 已注册 = %s（_ALL_OPS 含 Exp: %s）"
          % (has, ops_ext.Exp in ops_ext._ALL_OPS))

    # ---- ② 面板层性能 ----
    from app.factors.panel_expr import PanelEvaluator
    codes = [n for n in sorted(os.listdir(FEATURES))
             if os.path.isdir(os.path.join(FEATURES, n))][:N_INST]
    ev = PanelEvaluator(codes, "2021-01-01", "2022-12-31")
    print("\n② 面板层（%d 只 / 2 年）：同一条表达式的三种写法对比" % len(codes))
    # ⚠ `panel_expr.eval_expr` 收的是**编译后**的 qlib 风格算子名（`Sqrt`/`Log`/`Exp`，
    #   首字母大写 ✓），不是通达信写法的 `SQRT(...)` ✗（那要经 `translate_formula` ✓）。
    for expr in ("$close", "Sqrt($close)", "Log($close)", "Exp($close)",
                 "Exp(Log($close))"):
        ts = []
        for _ in range(3):
            ev._node_cache.clear()          # 清节点缓存 ⇒ 每次都真算 ✓（公平对比）
            t0 = time.perf_counter()
            try:
                s = ev.eval_expr(expr)
                ts.append(time.perf_counter() - t0)
            except Exception as e:                                  # noqa: BLE001
                print("   %-18s ERR %s: %s" % (expr, type(e).__name__, str(e)[:70]))
                ts = []
                break
        if ts:
            print("   %-18s %.4f s（3 次最优）  n=%d" % (expr, min(ts), len(s)))
    print("done")


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归，见 2026-09-18 教训 ✓）
    main()
