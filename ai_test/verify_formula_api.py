# -*- coding: utf-8 -*-
"""端到端验证「公式 → 编译」（走**服务端接口** ✓ —— 库、互相调用、CPX 等都由服务端处理 ✓）。

用途（v1.20.55 起 ✓）：
  ① 把公式库里某条公式**按当前代码重新编译**，并扫出"窗口不是整数常量"的算子（会崩的那些 ✗）；
  ② 直接编译一段**临时公式**（验证常量折叠/守卫等 ✓）。

用法：
    python ai_test/verify_formula_api.py 强龙起势            # 按名字编译库里的公式并扫描
    python ai_test/verify_formula_api.py --expr "OUT:SQRT(2)*CLOSE;"
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8001"
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend",
                     "workdir", "custom_formulas.json")

ROLLING_OPS = {"Ref", "Mean", "Std", "Var", "Sum", "Max", "Min", "Med", "Slope", "Rsquare",
               "Resi", "Quantile", "IdxMax", "IdxMin", "Delta", "WMA", "EMA", "Corr", "Cov",
               "Mad", "Skew", "Kurt", "Rank"}


def post(formula: str):
    body = json.dumps({"formula": formula}).encode("utf-8")
    req = urllib.request.Request(API + "/api/factors/translate", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        return 200, json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def _split_args(inner):
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


def scan_bad_windows(expr: str):
    """列出"窗口位不是整数常量"的滚动算子（= 会触发 `window must be an integer` 的那些 ✓）。"""
    bad = []
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr):
        name = m.group(1)
        if name not in ROLLING_OPS:
            continue
        i, depth = m.end(), 1
        while i < len(expr) and depth:
            depth += 1 if expr[i] == "(" else (-1 if expr[i] == ")" else 0)
            i += 1
        args = _split_args(expr[m.end():i - 1])
        if args and not re.fullmatch(r"-?\d+", args[-1]):
            bad.append((name, args[-1]))
    return bad


def main() -> int:
    argv = sys.argv[1:]
    if "--expr" in argv:
        formula = argv[argv.index("--expr") + 1]
        label = "<临时公式>"
    else:
        name = argv[0] if argv else "强龙起势"
        raw = json.load(open(STORE, encoding="utf-8"))
        items = raw.get("items") if isinstance(raw, dict) else raw
        hit = [x for x in items if (x.get("name") or "") == name]
        if not hit:
            print("  ✗ 公式库里没有名为 %r 的公式" % name)
            return 2
        formula, label = (hit[0].get("text") or ""), name

    print("  公式 = %s（%d 字符）" % (label, len(formula)))
    code, r = post(formula)
    print("  HTTP = %s" % code)
    if code != 200:
        print("  ✗ 编译失败（服务端返回）：%s" % str(r)[:400])
        return 1
    expr = (r.get("expression") or "")
    print("  name = %s | expression 长度 = %d" % (r.get("name"), len(expr)))
    print("  DYN_MEAN( 出现 %d 次 | Mean( 出现 %d 次"
          % (expr.count("DYN_MEAN("), len(re.findall(r"(?<!DYN_)Mean\(", expr))))
    bad = scan_bad_windows(expr)
    if bad:
        print("  ✗ 仍有 %d 处非法窗口：" % len(bad))
        for n, w in bad[:8]:
            print("      %s(..., %s)" % (n, w[:80]))
        return 1
    print("  ✅ 没有任何「窗口非常量」的算子 ✓")
    print("  expression 片段 = %s" % expr[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
