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


def main() -> int:
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
    print("  逐条加载完毕 ⇒ 失败 %d 条" % len(bad))
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
