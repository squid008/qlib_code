# -*- coding: utf-8 -*-
"""按某个**回测任务**的 params.json 逐条真机加载公式，定位 `operands could not be broadcast`。

背景（2026-09-23 任务 `07bf2c5ba41e`）：
    100% - 失败: operands could not be broadcast together with shapes (4992,) (5110,)
    栈：ops_ext.py:657 `_LogicalAndOr._load_internal` → :681 `And._op` 的 `(a!=0)&(b!=0)`
⇒ `And/Or` **只处理了"常量 vs 序列"**（`ndim==0` 广播 ✓），**没处理"两侧长度不同"** ✗。
⚠ 同一文件里 `Corr` 的注释写着「容忍 **SR 删行导致的左右不等长**」✗ ⇒ 同一类问题，`And/Or` 漏了 ✓。

用法：
    python ai_test/dbg_broadcast.py 07bf2c5ba41e            # 逐条加载，报出坏公式
    python ai_test/dbg_broadcast.py 07bf2c5ba41e SH600000   # 指定标的
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "backend")))

from app.services.qlib_runtime import ensure_qlib_init    # noqa: E402

ensure_qlib_init()

from qlib.data import D                                    # noqa: E402

from app.factors.parser import build_library, translate_formula   # noqa: E402

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend",
                   "workdir", "artifacts")


def _params_path(task_id: str) -> str:
    import glob
    hits = glob.glob(os.path.join(ART, "*_" + task_id, "params.json"))
    if not hits:
        raise SystemExit("✗ 找不到该任务的 params.json：%s" % task_id)
    return hits[-1]


def _sweep(task: str, code: str) -> int:
    """★ 扫若干候选区间做**整组加载** —— 单条/单区间常常复现不出 ✗（见上 ✓）。
    用来给"修好了吗"找一个**能复现的区间** ✓（先让它失败、再验证修复 ✓）。"""
    p = json.load(open(_params_path(task), encoding="utf-8"))
    texts = p.get("custom_formulas") or []
    lib = build_library(texts)
    exprs = []
    for text in texts:
        try:
            exprs.append(translate_formula(text, library=lib).expression)
        except Exception:                                       # noqa: BLE001
            pass
    cands = [
        ("2021-01-01", "2026-08-10"),
        ("2020-07-01", "2021-01-31"),
        ("2020-01-01", "2021-03-31"),
        ("2019-01-01", "2021-03-31"),
        ("2016-01-01", "2021-03-31"),
        ("2010-01-01", "2021-03-31"),
        ("2005-01-01", "2021-03-31"),
    ]
    bad = []
    for s, e in cands:
        try:
            D.features([code], exprs, start_time=s, end_time=e)
            print("  OK   %s ~ %s" % (s, e))
        except Exception as ex:                                 # noqa: BLE001
            print("  FAIL %s ~ %s ⇒ %s: %s" % (s, e, type(ex).__name__, str(ex)[:90]))
            bad.append((s, e))
    print("  可复现区间数 = %d" % len(bad))
    return 0


def main() -> int:
    if "--sweep" in sys.argv:
        rest = [a for a in sys.argv[1:] if not a.startswith("--")]
        return _sweep(rest[0] if rest else "4e8e5fe991d3",
                      rest[1] if len(rest) > 1 else "SH600000")
    task = sys.argv[1] if len(sys.argv) > 1 else "07bf2c5ba41e"
    code = sys.argv[2] if len(sys.argv) > 2 else "SH600000"
    p = json.load(open(_params_path(task), encoding="utf-8"))
    texts = p.get("custom_formulas") or []
    start = p.get("start_date") or "2021-01-01"
    end = p.get("end_date") or "2026-08-10"
    lib = build_library(texts)
    print("  任务 %s | feature=%s | 公式 %d 条 | %s ~ %s | 标的 %s"
          % (task, p.get("feature"), len(texts), start, end, code))

    bad = []
    for i, text in enumerate(texts):
        try:
            t = translate_formula(text, library=lib)
        except Exception as e:                                  # noqa: BLE001
            print("  [%2d] ✗ 翻译失败：%s: %s" % (i, type(e).__name__, str(e)[:90]))
            continue
        try:
            D.features([code], [t.expression], start_time=start, end_time=end)
        except Exception as e:                                  # noqa: BLE001
            msg = str(e)
            tag = "★广播" if "broadcast" in msg else "✗"
            print("  [%2d] %s %s：%s" % (i, tag, type(e).__name__, msg[:110]))
            print("       公式: %s" % text.replace("\n", " | ")[:180])
            print("       表达: %s" % t.expression[:220])
            bad.append((i, text, t.expression))
    print("")
    print("  【逐条】加载完毕 ⇒ 失败 %d 条" % len(bad))

    # ★★ 还要做**整组一次加载** ✓ —— 单条加载常常**复现不出** ✗：qlib 会用整组的
    #   `get_extended_window_size()` **最大值**去扩窗口 ⇒ 同一棵表达式在"单条"和"整组"
    #   下拿到的历史长度不同 ⇒ 只有在整组里才会暴露长短不一 ✓（2026-09-23 实测：
    #   任务 `4e8e5fe991d3` 逐条 0 失败 ✗，而真实回测崩 ✓）。
    exprs, names = [], []
    for text in texts:
        try:
            t = translate_formula(text, library=lib)
            exprs.append(t.expression)
            names.append(t.name)
        except Exception:                                       # noqa: BLE001
            pass
    print("  【整组】一次加载 %d 条表达式 …" % len(exprs))
    try:
        D.features([code], exprs, start_time=start, end_time=end)
        print("  【整组】OK ✓")
    except Exception as e:                                      # noqa: BLE001
        print("  【整组】✗ %s：%s" % (type(e).__name__, str(e)[:110]))
        # 二分定位：逐步加入表达式，找到第一个触发的那条
        lo, hi = 1, len(exprs)
        while lo < hi:
            mid = (lo + hi) // 2
            try:
                D.features([code], exprs[:mid], start_time=start, end_time=end)
                lo = mid + 1
            except Exception:                                   # noqa: BLE001
                hi = mid
        i = lo - 1
        print("  ⇒ 二分定位到第 %d 条（累计加入时首次崩）：%s" % (i, names[i]))
        print("     表达: %s" % exprs[i][:200])
    if bad:
        i, text, expr = bad[0]
        # 对第一条坏公式做**左右分侧**诊断：分别加载含 `And(`/`Or(` 的子表达式，比长度
        print("  ── 首条坏公式的分侧诊断 ──")
        import re
        for m in re.finditer(r"(And|Or)\(([^()]*(?:\([^()]*\)[^()]*)*)\)", expr):
            whole, inner = m.group(0), m.group(2)
            args = []
            depth, cur = 0, []
            for ch in inner:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    args.append("".join(cur))
                    cur = []
                else:
                    cur.append(ch)
            if len(args) != 2:
                continue
            lens = []
            for a in args:
                try:
                    df = D.features([code], [a.strip()], start_time=start, end_time=end)
                    lens.append(int(df.iloc[:, 0].notna().sum()))
                except Exception as e:                          # noqa: BLE001
                    lens.append("ERR:%s" % str(e)[:40])
            print("     %s\n        左=%s ｜ 右=%s" % (whole[:110], lens[0], lens[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
