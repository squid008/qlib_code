# -*- coding: utf-8 -*-
"""诊断「window must be an integer 0 or greater」：找出生成表达式里**窗口参数不是整数常量**的算子。

背景（2026-09-23 用户报「强龙起势（周期 60 天）：特征计算失败: window must be an integer 0 or greater」）：
栈指向 qlib `data/ops.py:752` 的 `Rolling._load_internal` ⇒ `series.rolling(self.N, ...)` ✗
⇒ pandas 要求 `N` 是**整数** ✗；只要公式把"变量/表达式"当周期传进去（如 `MA(C,MIN(BARNUM,5))`），
生成的就是 `Mean($close,Less(BARSCOUNT($close),5))` ✗ ⇒ 求值即崩。

本脚本：① 读公式库里的原文（可按名字过滤 ✓）；② 用**同一套翻译器**生成表达式 ✓；
③ 递归扫描表达式，列出**所有"周期位不是纯整数常量"的算子** ✓（也就是会崩的那些 ✓）。

用法：
    python ai_test/dbg_bad_window.py                # 扫全部公式
    python ai_test/dbg_bad_window.py 强龙起势        # 只扫名字含该串的公式
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "backend")))

from app.factors.parser import build_library, translate_formula   # noqa: E402

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend",
                     "workdir", "custom_formulas.json")

# 这些算子的**最后一个参数**是滚动窗口 ⇒ 必须是整数常量（qlib `Rolling` 的 N ✓）
ROLLING_OPS = {
    "Ref", "Mean", "Std", "Var", "Sum", "Max", "Min", "Med", "Slope", "Rsquare",
    "Resi", "Quantile", "IdxMax", "IdxMin", "Delta", "WMA", "EMA", "Corr", "Cov",
    "Mad", "Skew", "Kurt", "Rank",
}


def _split_args(inner: str):
    """按**顶层**逗号切分参数（忽略括号内的逗号 ✓）。"""
    out, depth, cur = [], 0, []
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _iter_calls(expr: str):
    """遍历表达式里所有 `Name(...)` 调用（返回 name 与参数列表 ✓）。"""
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr):
        name = m.group(1)
        # 找到配对的右括号
        i = m.end()
        depth = 1
        while i < len(expr) and depth:
            if expr[i] == "(":
                depth += 1
            elif expr[i] == ")":
                depth -= 1
            i += 1
        yield name, _split_args(expr[m.end():i - 1]), m.start()


def _is_int_const(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", s.strip()))


def scan(expr: str):
    """返回问题列表 [(位置, 算子, 参数, 问题描述)] ✓。"""
    bad = []
    for name, args, pos in _iter_calls(expr):
        if name not in ROLLING_OPS or not args:
            continue
        w = args[-1]
        if _is_int_const(w):
            continue
        why = "负数" if re.fullmatch(r"-\d+", w) else ("浮点常量" if re.fullmatch(r"-?\d+\.\d+", w)
                                                     else "非常量表达式")
        bad.append((pos, name, args, why))
    return bad


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    fresh = "--fresh" in sys.argv            # ★ 用当前代码**重新翻译**（验证修复 ✓，而非扫旧存盘 ✓）
    filt = argv[0] if argv else ""
    raw = json.load(open(STORE, encoding="utf-8"))
    items = raw.get("items") if isinstance(raw, dict) else raw
    lib = build_library({(it.get("name") or ""): (it.get("text") or "") for it in items
                         if it.get("name")})
    print("  公式库条目数 = %d | 过滤 = %r" % (len(items), filt))
    total_bad = 0
    for it in items:
        name = it.get("name") or ""
        if filt and filt not in name:
            continue
        text = it.get("text") or ""
        if not text.strip():
            continue
        # ★ 优先扫**存盘的 expression** ✓ —— 那才是引擎真正加载的串 ✓
        #   （重新翻译需要完整库 ✓，而库里有互相调用如 `CPX` ✗ ⇒ 容易因库不全而误报 ✓）
        expr = "" if fresh else (it.get("expression") or "").strip()
        if not expr:
            try:
                r = translate_formula(text, library=lib)
                expr = r.expression
            except Exception as e:                              # noqa: BLE001
                print("  ✗ %s 翻译失败：%s: %s" % (name, type(e).__name__, e))
                continue
        bad = scan(expr)
        if not bad:
            continue
        total_bad += 1
        print("")
        print("  ★★ 有问题：%s（id=%s）—— %d 处非法窗口" % (name, it.get("id"), len(bad)))
        # 打印原文里的周期相关行（便于用户对照 ✓）
        for ln in text.splitlines():
            if re.search(r"\b(MA|EMA|WMA|STD|HHV|LLV|SUM|COUNT|REF|SLOPE)\s*\(", ln):
                print("      原文: %s" % ln.strip()[:150])
        seen = set()
        for pos, op, args, why in bad[:8]:
            key = (op, tuple(args))
            if key in seen:
                continue
            seen.add(key)
            print("      → %s( %s )   周期位=「%s」 ⇒ %s"
                  % (op, ", ".join(a[:70] for a in args), args[-1][:70], why))
        print("      生成表达式片段: %s" % expr[max(0, bad[0][0] - 60):bad[0][0] + 90])
    print("")
    print("  合计有问题的公式 = %d" % total_bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
