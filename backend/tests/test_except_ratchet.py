# -*- coding: utf-8 -*-
"""**静默吞异常棘轮**（★ P2-A，2026-10-18 ✓）—— 只许减少，不许增加 ✓。

## 什么叫"静默"（AST 判 ✓，不靠 grep ✗）

`except Exception` / 裸 `except` 的 body **只有** `pass` / `continue` / `break` / `...`，
并且**没有任何日志调用**（`log*` / `print` / `warn*` / `error*` …）✓
⇒ 出事时**零痕迹** ✗ —— 本项目最贵的 bug 类（"数字对不上""结果丢了"查不到原因 ✓）。

## 规矩

- 全仓静默总数 **只许 ≤ 基线** ✓（改动时把基线一起调小 ✓）；
- 超了就让测试红 ✓，并打印**哪个文件**在涨 ✓（不写"某处"✗）。
"""
import ast
import os

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

#: 基线（2026-10-18 实测 ✓）。**只能调小** ✓ —— 每收一批就改这个数字 ✓。
#: 163 → 149（`expr_cache` 13 ✓ + `task_store` 8 ✓ + `error_policy` 3 ✓）
#: → 130（`engine/artifacts.py` 22 ✓、`engine/resource.py` 9 ✓）
#: → **107**（`engine/metrics.py` 14 ✓ + `factors/single_test.py` 32 ✓）
#: ⚠ 这一批把 helper 从 `_dbg` 改名成 `_log_swallowed` ✗→✓ —— 因为 `single_test.py` 里
#:   **已有一个局部变量叫 `_dbg`** ✓（`QLIB_SFT_TOPK_DEBUG` 开关 ✓）⇒ ruff `F823` ✓。
SILENT_BASELINE = 2

_SILENT_NODES = (ast.Pass, ast.Continue, ast.Break, ast.Expr)
#: ⚠ `swallow` 必须算"有痕迹" ✓ —— 它**就是**本项目的留痕通路 ✓
#:   （第一版漏了 ⇒ 把 `tolerate` 里 `swallow(...)` 误判成静默 ✗，实测抓出来的 ✓）。
#: ⚠ `dbg` 也要算 ✓ —— `engine/` 那两个文件用的是**受保护的** `_dbg(where, exc)` ✓
#:   （它内部兜住"logger 为 None"✓，因为 `resource.py` 的 logger 在 Linux 上就是 None ✗）。
_LOG_HINTS = ("log", "logger", "print", "warn", "error", "info", "debug", "dbg", "swallow")


def _is_log_call(node) -> bool:
    for x in ast.walk(node):
        if isinstance(x, ast.Attribute) and any(h in x.attr.lower() for h in _LOG_HINTS):
            return True
        if isinstance(x, ast.Name) and any(h in x.id.lower() for h in _LOG_HINTS):
            return True
        if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "print":
            return True
    return False


def _scan():
    """返回 (总数, {文件: 静默数}) ✓。"""
    total = 0
    per = {}
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, APP).replace(os.sep, "/")
            try:
                tree = ast.parse(open(p, encoding="utf-8").read())
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if not isinstance(n, ast.ExceptHandler):
                    continue
                tp = n.type
                broad = tp is None or (isinstance(tp, ast.Name) and tp.id == "Exception")
                if not broad:
                    continue
                only_silent = True
                logged = False
                for st in n.body:
                    if not isinstance(st, _SILENT_NODES):
                        only_silent = False
                    if _is_log_call(st):
                        logged = True
                if only_silent and not logged:
                    total += 1
                    per[rel] = per.get(rel, 0) + 1
    return total, per


def test_silent_swallow_only_decreases():
    """静默吞异常总数**不许超过基线** ✓。"""
    total, per = _scan()
    top = "、".join("%s=%d" % (k, v) for k, v in
                    sorted(per.items(), key=lambda kv: -kv[1])[:6])
    assert total <= SILENT_BASELINE, (
        "静默吞异常从 %d 涨到 %d ✗（基线 %d）\n"
        "  最集中的地方：%s\n"
        "  ⇒ 要么补一行日志（用 `services.error_policy.tolerate / swallow` ✓），"
        "要么把它归类为「必报」并 raise ✓；"
        "确实收掉了一批 ⇒ 把 SILENT_BASELINE 调小 ✓"
        % (SILENT_BASELINE, total, SILENT_BASELINE, top))


def test_ratchet_baseline_is_not_stale():
    """基线**贴着现实** ✓（比现实高太多 ⇒ 会掩盖回退 ✗）。"""
    total, _per = _scan()
    assert total >= SILENT_BASELINE - 60, (
        "基线 %d 明显高于实测 %d ⇒ 把它调小（棘轮必须咬住 ✓）" % (SILENT_BASELINE, total))


def test_modern_modules_have_zero_silent_swallow():
    """**今天新写/改造过的模块必须 0 静默** ✓（存量是债 ✓，新账不许记 ✗）。

    ⚠ 第一版我写成 `legacy = set(per)` ⇒ `bad` 恒为空 ⇒ **恒真测试** ✗（等于没测 ✓）。
      现在改成**点名**这几个模块 ✓ —— 它们都是 2.9.18~2.9.25 新写的 ✓，
      没有历史包袱 ⇒ 就不该有静默吞 ✓。
    """
    per = _scan()[1]
    must_be_clean = [
        "services/error_policy.py",     # 本模块自己 ✓
        "services/expr_cache.py",       # 2.9.22 新写，11 处已改造 ✓
        "services/task_store.py",       # 2.9.18 新写 ✓
    ]
    bad = ["%s=%d" % (f, per.get(f, 0)) for f in must_be_clean if per.get(f, 0)]
    assert not bad, "这些新模块出现了静默吞异常 ✗：%s" % bad
