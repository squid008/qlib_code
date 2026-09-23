# -*- coding: utf-8 -*-
"""★ v1.20.63：公式库 → git **自动同步**（每次写盘后去抖提交，并尽力推送）。

动机（2026-09-23 用户报：「咱们的公式不会 push 上去吗？我家里 Pull 怎么公式没更新」）：
    `backend/workdir/custom_formulas.json`（用户公式资产 ✓）**早就纳入版本控制** ✓
    （`.gitignore` 里专门开了 `!backend/workdir/custom_formulas.json` ✓），
    但**提交靠人记** ✗ —— 本地已有 **65 条**、仓库 HEAD 只有 **35 条** ✗（30 条从未推送 ✗），
    用户"以为会跟着 git 走" ✗ ⇒ 家里 pull 拿不到 ✓。
    ⇒ 本模块把"提交+推送"变成**保存公式的副作用** ✓，不再依赖任何人记得 ✓。

设计取舍（重要 ✓）：
· **挂载点**：`services/custom_formulas._save()` 末尾 ✓ —— 新建/更新/删除/陈旧重编**全部**经过它 ✓
  ⇒ 一处挂钩覆盖所有写盘路径 ✓（包括 v1.20.59 的自动重编 ✓）。
· **去抖**：`_DEBOUNCE` 秒内的多次保存**合并成一次** ✓（编辑时连续点保存不会刷屏 ✗）。
· **静默 + 不抛**：本模块**永远不会**把异常抛给调用方 ✓、也**不影响保存本身** ✓
  （同步是"副作用" ✗ 不是"前置条件" ✓）；失败只 `print` 一行 ✓。
· **只提交这一个文件** ✓（`git commit -- <path>` ⇒ 绝不夹带用户/我的其它改动 ✗）。
· **无差异不造空提交** ✓（`git diff --cached --quiet` 判空 ✓）。
· **推送尽力而为** ✓：先直连、失败再试常见本地代理 ✓；仍失败就**只留在本地** ✓
  —— 之后任何一次 `git push`（我发版时 / 用户自己 / 应用下次同步 ✓）都会把它一起带走 ✓✓。
· **`GIT_TERMINAL_PROMPT=0`** ✓：凭据缺失时**立刻失败**而不是**挂住线程** ✗（很关键 ✓）。

开关（环境变量，默认全开 ✓）：
· `FORMULA_GIT_SYNC=0`        ⇒ 整个机制关闭（不 add/commit/push ✓）
· `FORMULA_GIT_SYNC_PUSH=0`   ⇒ 只提交、不推送 ✓
· `FORMULA_GIT_SYNC_DEBOUNCE` ⇒ 去抖秒数（默认 5 ✓）
· `FORMULA_GIT_SYNC_PROXY`    ⇒ 直连失败后尝试的代理（默认 `http://127.0.0.1:7897` ✓）

⚠ 已知边界：公式库是**单文件 JSON** ✓ ⇒ 两台机器**同时**各自保存后互相 pull/push 可能
  产生**整文件冲突** ✗（git 无法自动合并 JSON ✗）⇒ 建议：家里编辑前先 `pull` ✓。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading

# d:\quant\qlib_code（本文件在 backend/app/services/ 下 ⇒ 上溯三级 ✓）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_REL_PATH = os.path.join("backend", "workdir", "custom_formulas.json")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


ENABLED = _env("FORMULA_GIT_SYNC", "1").strip().lower() not in ("0", "false", "no", "off")
PUSH = _env("FORMULA_GIT_SYNC_PUSH", "1").strip().lower() not in ("0", "false", "no", "off")
PROXY = _env("FORMULA_GIT_SYNC_PROXY", "http://127.0.0.1:7897").strip()
try:
    DEBOUNCE = max(0.0, float(_env("FORMULA_GIT_SYNC_DEBOUNCE", "5")))
except ValueError:
    DEBOUNCE = 5.0

# ⚠ 凭据缺失时**立刻失败**（否则 git 会等输入 ⇒ 后台线程永久挂住 ✗）
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GCM_INTERACTIVE": "never"}

_lock = threading.Lock()
_timer: "threading.Timer | None" = None
_reason = ""


def _run(args, timeout: float = 60.0):
    """跑一条 git 子命令（cwd = 仓库根 ✓）。⚠ 不抛，调用方看 `returncode` ✓。"""
    return subprocess.run(
        ["git", *args], cwd=_REPO_ROOT, env=_GIT_ENV, timeout=timeout,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _count() -> int:
    """当前公式条数（只用于提交信息 ✓；读不到就给 -1 ✓ 不影响流程 ✓）。"""
    try:
        with open(os.path.join(_REPO_ROOT, _REL_PATH), "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else -1
    except Exception:                                            # noqa: BLE001
        return -1


def sync_now(reason: str = "") -> "tuple[bool, str]":
    """**立即**同步一次（同步执行 ✓，供 `schedule` 与单测/手动调用 ✓）。

    返回 `(ok, msg)` ✓：`ok` 表示"流程走通"（**含"无变化跳过"** ✓）；
    **推送失败也算 ok=True** ✓（改动已**本地提交**、不会丢 ✓，下次任何 push 都会带走 ✓）。
    """
    if not ENABLED:
        return False, "FORMULA_GIT_SYNC=0 ⇒ 已关闭自动同步"
    try:
        add = _run(["add", "--force", "--", _REL_PATH])
        if add.returncode != 0:
            return False, "git add 失败: %s" % ((add.stderr or add.stdout).strip()[:200] or "未知")

        # 无差异 ⇒ 直接结束 ✓（避免每次启动/自动重编都造空提交 ✗）
        if _run(["diff", "--cached", "--quiet", "--", _REL_PATH]).returncode == 0:
            return True, "无变化，跳过"

        n = _count()
        msg = "chore(formulas): 自动同步用户公式库（%d 条）%s" % (n, ("　" + reason) if reason else "")
        com = _run(["commit", "-q", "-m", msg, "--", _REL_PATH])
        if com.returncode != 0:
            return False, "git commit 失败: %s" % ((com.stderr or com.stdout).strip()[:200] or "未知")

        if not PUSH:
            return True, "已本地提交（FORMULA_GIT_SYNC_PUSH=0 ⇒ 未推送）"

        # 尽力推送：先直连 ✓，失败再试本地代理 ✓（公司机器常见 ✓）
        err = ""
        for extra in ([], ["-c", "http.proxy=" + PROXY, "-c", "https.proxy=" + PROXY]):
            try:
                p = _run(["push", "origin", "HEAD", *extra], timeout=120.0)
            except subprocess.TimeoutExpired:
                err = "推送超时"
                continue
            if p.returncode == 0:
                return True, "已提交并推送"
            err = (p.stderr or p.stdout).strip().splitlines()[-1][:200] if (p.stderr or p.stdout).strip() else "未知"
        return True, "已本地提交（推送失败，留待下次 push 带走）: %s" % err
    except Exception as e:                                       # noqa: BLE001
        return False, "同步异常: %s: %s" % (type(e).__name__, e)


def _fire() -> None:
    global _timer, _reason
    with _lock:
        _timer = None
        reason = _reason
    ok, msg = sync_now(reason)
    print("[formula-git] %s %s" % ("✓" if ok else "✗", msg), flush=True)


def schedule(reason: str = "") -> None:
    """★ 写盘后调用：**去抖**起一个后台线程做 add/commit/push ✓（非阻塞、不抛 ✓）。

    ⚠ 本函数会被 `custom_formulas._save()` 在**持锁**状态下调用 ✓ ⇒ 必须**极轻量** ✓
      （只置标志 + 起线程 ✓，绝不在此做 git 调用 ✗）。
    """
    global _timer, _reason
    if not ENABLED:
        return
    try:
        with _lock:
            if reason:
                _reason = reason
            if _timer is not None and _timer.is_alive():
                return                       # 已有待触发的定时器 ⇒ 合并成一次 ✓
            t = threading.Timer(DEBOUNCE, _fire)
            t.daemon = True                  # 不阻塞进程退出 ✓
            _timer = t
            t.start()
    except Exception as e:                    # noqa: BLE001
        print("[formula-git] ✗ 调度失败（已忽略）: %s: %s" % (type(e).__name__, e), flush=True)
