# -*- coding: utf-8 -*-
"""★ v1.20.63：公式库 → git **自动同步**（每次写盘后去抖提交，并尽力推送）。

动机（2026-09-23 用户报：「咱们的公式不会 push 上去吗？我家里 Pull 怎么公式没更新」）：
    公式库（用户资产 ✓）**早就纳入版本控制** ✓，但**提交靠人记** ✗ —— 当时本地已有 **65 条**、
    仓库 HEAD 只有 **35 条** ✗（30 条从未推送 ✗），用户"以为会跟着 git 走" ✗ ⇒ 家里 pull 拿不到 ✓。
    ⇒ 本模块把"提交+推送"变成**保存公式的副作用** ✓，不再依赖任何人记得 ✓。
⚠ v1.20.69（2026-09-24）：修本机制的**整体失效** —— v1.20.66 把老单文件
    `workdir/custom_formulas.json` 从 git 移除后，`sync_now` 里那条
    `git add --force -- <老文件> <公式目录>` 因**路径不存在**而 `fatal`（exit 128）
    并**中止整条 add** ⇒ 什么都没 stage ⇒ **公式再也不会被自动提交/推送** ✗（静默不同步 ✗✗）。
    现在改为**只 stage 存在的目标、且逐个 add** ✓（并在 `.gitignore` 里修好被 `workdir/`
    盖掉的 `!formulas/*.json` 例外 ✓）。

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

开关（环境变量）：
· `FORMULA_GIT_SYNC=0`        ⇒ 整个机制关闭（不 add/commit/push ✓）
· `FORMULA_GIT_SYNC_PUSH=1`   ⇒ ★ **恢复"保存即推送"** ✓（**默认关闭** ✗ —— 见下）
· `FORMULA_GIT_SYNC_DEBOUNCE` ⇒ 去抖秒数（默认 5 ✓）
· `FORMULA_GIT_SYNC_PROXY`    ⇒ 直连失败后尝试的代理（默认 `http://127.0.0.1:7897` ✓）

★★ v1.20.70：**推送默认关闭**（只本地提交 ✓）—— 用户 2026-09-25 定：
   公式的保存/删除**只在本机生效** ✓；"要不要给另一台机器"必须由人明确发起 ✓（手动 `git push`）
   ⇒ **未推送期间远端保持原样** ⇒ 另一端 `pull` 仍是**改动前**的条数 ✓（"没 push 就不该影响别人" ✓）。
   ⚠ 保留本地自动提交的原因：工作区保持干净 ⇒ `git pull --ff-only` 不会被未提交的公式改动挡住 ✓。

⚠ 已知边界（v1.20.69 起已大幅缓解 ✓）：公式库现按**装机**分文件（`formulas/<install_id>.json` ✓）
  ⇒ 两台机器**各写各的文件** ✓，`pull` 不再产生"整文件 JSON 冲突"✗；合并按 `updated_at` 裁决 ✓。
  老的单文件 `custom_formulas.json` 只在"老环境"里存在 ✓（存在才 stage ✓，见 `sync_now` ✓）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from ..logger import _Utf8SafeStream      # ★ v1.20.63：安全打印（GBK 下不抛 ✓）

# ⚠ 本模块的日志**必须**走安全打印：后端 stdout 在 Windows 下常是 **GBK** ✗ ⇒
#   `print("✓/✗ ...")` 会抛 `UnicodeEncodeError`（这两三个字符**不在 GBK 字符集**里 ✗✓）
#   ⇒ 后台线程里异常 ⇒ **同步结果那行日志整条丢掉** ✗（2026-09-25 一并修 ✓）。
_SAFE_OUT = _Utf8SafeStream(sys.stdout)


def _log(msg: str) -> None:
    """安全打印 ✓（按 UTF-8 写底层 buffer、永不抛 ✓ —— 日志绝不参与主流程 ✓）。"""
    try:
        _SAFE_OUT.write("[formula-git] " + msg + "\n")
        _SAFE_OUT.flush()
    except Exception:                                        # noqa: BLE001
        pass

# d:\quant\qlib_code（本文件在 backend/app/services/ 下 ⇒ 上溯三级 ✓）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
# 老的单文件：v1.20.66 起**已停止 git 跟踪**、新环境里**大概率不存在** ⇒ 只在存在时才 stage ✓
# （⚠ 它的"存在与否"曾让整条 add 失败 ⇒ 见 `sync_now` 里 v1.20.69 的逐个 add 修复 ✓）
_REL_PATH = os.path.join("backend", "workdir", "custom_formulas.json")
# ★ v1.20.65 按人分文件 → ★ v1.20.69 起按**装机**分文件（`<install_id>.json` ✓，
#   与用户名无关 ⇒ 两台机器永不共用文件 ⇒ git 层面只新增/改自己的文件 ⇒ 永不冲突 ✓✓）
_REL_DIR = os.path.join("backend", "workdir", "formulas")
# 每次同步都一起 stage 的目标（顺序无关 ✓；**逐个 add** ✓ —— 见 `sync_now` ✓）
_TARGETS = (_REL_PATH, _REL_DIR)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


ENABLED = _env("FORMULA_GIT_SYNC", "1").strip().lower() not in ("0", "false", "no", "off")
# ★★ v1.20.70：**默认不自动推送** ✗（`FORMULA_GIT_SYNC_PUSH=1` 可恢复"保存即推送" ✓）。
#   用户 2026-09-25 的要求（原话："我删了之后没有 PUSH，PULL 的话要能重新显示 67 个公式才对；
#   我删了之后，手动让你 PUSH，然后再 PULL，这样显示 66 个才没问题"）：
#   ⇒ **公式的改动只在本机生效** ✓（本地照常提交，改动不会丢 ✓）；**要不要同步给另一台机器，
#     必须由人明确发起** ✓（我/用户手动 `git push`）⇒ 远端在"未推送"期间保持原样 ✓
#     ⇒ 另一端 `pull` 仍看到**删除前**的条数 ✓（这正是"没 push 就不该影响别人"的直觉 ✓）。
#   ⚠ 为什么**保留本地自动提交**（而不是连提交也关掉 ✗）：本地提交让工作区**保持干净** ✓
#     ⇒ `git pull --ff-only` 不会被"未提交的公式改动"挡住/搅乱 ✓（公式文件是 git 跟踪的 ✓）。
PUSH = _env("FORMULA_GIT_SYNC_PUSH", "0").strip().lower() not in ("0", "false", "no", "off")
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
    """当前公式条数（只用于提交信息 ✓；读不到就给 -1 ✓ 不影响流程 ✓）。

    ★ v1.20.65：把 `formulas/*.json`（按人分文件 ✓）与老单文件**一起**数 ✓
      （取最大者作为条目数近似 ✓ —— 只影响提交信息文案 ✓）。
    """
    best = -1
    for rel in (_REL_PATH,):
        try:
            with open(os.path.join(_REPO_ROOT, rel), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                best = max(best, len([x for x in data if not (isinstance(x, dict) and x.get("deleted"))]))
        except Exception:                                        # noqa: BLE001
            pass
    try:
        for fn in os.listdir(os.path.join(_REPO_ROOT, _REL_DIR)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(_REPO_ROOT, _REL_DIR, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                best = max(best, len([x for x in data if not (isinstance(x, dict) and x.get("deleted"))]))
    except Exception:                                            # noqa: BLE001
        pass
    return best


def sync_now(reason: str = "") -> "tuple[bool, str]":
    """**立即**同步一次（同步执行 ✓，供 `schedule` 与单测/手动调用 ✓）。

    返回 `(ok, msg)` ✓：`ok` 表示"流程走通"（**含"无变化跳过"** ✓）；
    **推送失败也算 ok=True** ✓（改动已**本地提交**、不会丢 ✓，下次任何 push 都会带走 ✓）。
    """
    if not ENABLED:
        return False, "FORMULA_GIT_SYNC=0 ⇒ 已关闭自动同步"
    try:
        # ⚠⚠ v1.20.69 修：**必须逐个 add** —— `git add --force -- A B` 在 A **不存在**时会
        #   `fatal: pathspec 'A' did not match any files`（exit 128）并**中止整条 add**
        #   （其余路径被静默丢弃 ✗）⇒ v1.20.66 停跟踪老单文件之后，本机制就**整个失效**了 ✗：
        #   2026-09-24 实测（老文件已不在）⇒ add 失败、**什么都没 stage**、公式库再也不会被
        #   自动提交推送（而且**不报错给用户**，只是静默不同步 ✗✗）。
        #   ⇒ 只 stage **实际存在**的目标 ✓（老单文件不在就跳过 ✓），并逐个 add 以便定位失败项 ✓。
        staged = [rel for rel in _TARGETS if os.path.exists(os.path.join(_REPO_ROOT, rel))]
        if not staged:
            return True, "公式库路径都不存在，跳过"
        for rel in staged:
            add = _run(["add", "--force", "--", rel])
            if add.returncode != 0:
                return False, "git add %s 失败: %s" % (
                    rel, (add.stderr or add.stdout).strip()[:200] or "未知")

        # 无差异 ⇒ 直接结束 ✓（避免每次启动/自动重编都造空提交 ✗）
        if _run(["diff", "--cached", "--quiet", "--", *staged]).returncode == 0:
            return True, "无变化，跳过"

        n = _count()
        msg = "chore(formulas): 自动同步用户公式库（%d 条）%s" % (n, ("　" + reason) if reason else "")
        com = _run(["commit", "-q", "-m", msg, "--", *staged])
        if com.returncode != 0:
            return False, "git commit 失败: %s" % ((com.stderr or com.stdout).strip()[:200] or "未知")

        if not PUSH:
            # ★ v1.20.70：默认路径（推送要人明确发起 ✓）—— 说清"改动没丢、只是没同步" ✓
            return True, "已本地提交（未推送：默认不自动同步；需要时手动 git push）"

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
    _log("%s %s" % ("✓" if ok else "✗", msg))


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
