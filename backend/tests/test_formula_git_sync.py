# -*- coding: utf-8 -*-
"""★ v1.20.63：`services/formula_git_sync`（公式库自动提交+推送）单测。

⚠⚠ **最高原则**：本测试**绝不允许真的执行 git** ✗ —— 否则会真的 commit/push 仓库 ✗！
   全部用例都在 `formula_git_sync._run`（唯一出口 ✓）上打桩 ✓，并断言"没被真调用" ✓。

背景（2026-09-23 用户问「咱们的公式不会 push 上去吗？我家里 Pull 怎么公式没更新」）：
    `custom_formulas.json` 早已纳入版本控制 ✓，但**提交靠人记** ✗ ⇒ 本地 65 条 vs 仓库 35 条 ✗。
"""
from __future__ import annotations

import os

import pytest

from app.services import custom_formulas, formula_git_sync


class _FakeGit:
    """记录 git 调用的假实现 ✓（按 `rc` 表返回 returncode ✓）。"""

    def __init__(self, rc_map=None, default=0):
        self.calls = []
        self.rc_map = rc_map or {}
        self.default = default

    def __call__(self, args, timeout=60.0):
        self.calls.append(list(args))
        key = args[0] if args else ""
        rc = self.rc_map.get(key, self.default)
        if isinstance(rc, list):                 # 同一命令多次调用 ⇒ 依次取 ✓（如 push 直连失败→代理成功 ✓）
            rc = rc.pop(0) if rc else self.default
        return type("R", (), {"returncode": rc, "stdout": "", "stderr": "boom" if rc else ""})()

    def cmds(self):
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def _no_real_git(monkeypatch):
    """兜底（两道保险 ✓）：
    ① 把所有 git 调用换成"必然失败"的桩 ✓ —— 漏打桩也**绝不会**上真机 ✗；
    ② 强制 `ENABLED/PUSH=True` ✓ —— `tests/conftest.py` 为安全**全局关掉了**本机制 ✗，
       而本文件的用例测的正是"开启"路径 ✓（只关闭的那个用例自己再置 False ✓）。
    """
    monkeypatch.setattr(formula_git_sync, "_run", _FakeGit(default=1), raising=True)
    monkeypatch.setattr(formula_git_sync, "ENABLED", True)
    monkeypatch.setattr(formula_git_sync, "PUSH", True)


def test_repo_root_points_to_repo():
    """`_REPO_ROOT` 必须是**仓库根** ✓（含 .git ✓ / 含 `backend/workdir` ✓）—— 路径算错就全盘失效 ✗。"""
    assert os.path.isdir(os.path.join(formula_git_sync._REPO_ROOT, ".git"))
    assert os.path.isdir(os.path.join(formula_git_sync._REPO_ROOT, "backend", "workdir"))
    assert formula_git_sync._REL_PATH.replace("\\", "/") == "backend/workdir/custom_formulas.json"


def test_no_diff_skips_commit(monkeypatch):
    """**无差异 ⇒ 不造空提交** ✓（自动重编会频繁写盘 ✓ ⇒ 否则仓库被空提交刷屏 ✗）。"""
    fake = _FakeGit({"add": 0, "diff": 0})
    monkeypatch.setattr(formula_git_sync, "_run", fake)
    ok, msg = formula_git_sync.sync_now("t")
    assert ok and "无变化" in msg
    assert fake.cmds() == ["add", "diff"]                 # ★ 没有 commit / push ✓


def test_commit_then_push(monkeypatch):
    """有差异 ⇒ `add` + `commit -- <path>` + `push origin HEAD` ✓。"""
    fake = _FakeGit({"add": 0, "diff": 1, "commit": 0, "push": 0})
    monkeypatch.setattr(formula_git_sync, "_run", fake)
    ok, msg = formula_git_sync.sync_now("保存公式库")
    assert ok and msg == "已提交并推送"
    assert fake.cmds() == ["add", "diff", "commit", "push"]
    commit = [c for c in fake.calls if c[0] == "commit"][0]
    # ⚠ 只提交**公式库路径** ✗ 绝不夹带其它改动 ✓（★ v1.20.69：只为**存在**的目标 stage ⇒
    #   老单文件若已不在（v1.20.66 起就是常态 ✓）则不应出现在 add/commit 里 ✓）
    staged = [rel for rel in formula_git_sync._TARGETS
              if os.path.exists(os.path.join(formula_git_sync._REPO_ROOT, rel))]
    assert staged, "仓库里至少应存在按装机分文件的公式目录 ✓"
    assert commit[-len(staged):] == staged, commit
    assert "自动同步用户公式库" in " ".join(commit)


def test_push_default_is_off():
    """★★ v1.20.70：**默认不自动推送** ✓（用户 2026-09-25 定：公式改动**只在本机生效**，
    要同步给另一台机器必须由**人明确发起** ✓ ⇒ 未推送期间远端原样 ⇒ 另一端 pull 仍是旧条数 ✓）。

    ⚠ 这里直接断言**源码里的默认值** ✗ 而非模块常量：`tests/conftest.py` 出于安全把
    `ENABLED/PUSH` **全局置 False** ✓ ⇒ 运行时的 `PUSH` 无法区分"默认关闭"与"被测试关掉" ✗。
    """
    import inspect
    import re

    src = inspect.getsource(formula_git_sync)
    m = re.search(r'PUSH\s*=\s*_env\(\s*"FORMULA_GIT_SYNC_PUSH"\s*,\s*"([^"]*)"', src)
    assert m, "找不到 PUSH 默认值定义 ✗"
    assert m.group(1) == "0", "默认必须为 0（不自动推送）⇒ 实际 %r" % m.group(1)


def test_no_push_path_commits_locally_only(monkeypatch):
    """`PUSH=False` ⇒ **本地提交 + 不推送** ✓（`commit` 之后**不得**出现 `push` ✓）。

    这正是用户要的语义：改动落到本机（不会丢 ✓），远端保持原样 ⇒ 另一端 pull 仍是旧条数 ✓。
    """
    fake = _FakeGit({"add": 0, "diff": 1, "commit": 0, "push": 0})
    monkeypatch.setattr(formula_git_sync, "_run", fake)
    monkeypatch.setattr(formula_git_sync, "PUSH", False)
    ok, msg = formula_git_sync.sync_now("保存公式库")
    assert ok is True and "未推送" in msg
    assert fake.cmds() == ["add", "diff", "commit"]          # ★ 没有 push ✓


def test_missing_legacy_path_does_not_kill_sync(monkeypatch):
    """★★ v1.20.69 修的关键回归：**老单文件不存在时绝不能整条 add 失败** ✓。

    ⚠ 旧实现 `git add --force -- <老文件> <目录>` 在 `<老文件>` 不存在时会
      `fatal: pathspec ... did not match any files`（exit 128）并**中止整条 add**
      ⇒ 什么都没 stage ⇒ **公式库再也不被自动提交推送** ✗（而且静默不报错 ✗✗）。
      2026-09-24 实测确认（v1.20.66 移除老文件后本机制整体失效 ✓）。
    ⇒ 本用例把老路径换成**确定不存在**的路径 ✓：同步**必须**照常只 add 存在的目录 ✓。
    """
    fake = _FakeGit({"add": 0, "diff": 1, "commit": 0, "push": 0})
    monkeypatch.setattr(formula_git_sync, "_run", fake)
    monkeypatch.setattr(formula_git_sync, "_TARGETS",
                        ("backend/workdir/__this_path_is_gone__.json",
                         formula_git_sync._REL_DIR))
    ok, msg = formula_git_sync.sync_now("t")
    assert ok and msg == "已提交并推送"
    adds = [c for c in fake.calls if c[0] == "add"]
    assert len(adds) == 1 and adds[0][-1] == formula_git_sync._REL_DIR       # 只为存在的目标 add ✓
    assert fake.cmds() == ["add", "diff", "commit", "push"]                 # 流程没被打断 ✓


def test_add_uses_force_and_prompt_disabled():
    """`add --force` ✓（文件在忽略目录里 ✓）+ `GIT_TERMINAL_PROMPT=0` ✓（凭据缺失立刻失败 ✗ 不挂线程 ✓）。"""
    assert formula_git_sync._GIT_ENV.get("GIT_TERMINAL_PROMPT") == "0"
    assert formula_git_sync._GIT_ENV.get("GIT_ASKPASS") == ""
    assert formula_git_sync.PROXY


def test_push_falls_back_to_proxy(monkeypatch):
    """直连 push 失败 ⇒ 自动换本地代理再试 ✓（公司机器常见 ✓）。"""
    fake = _FakeGit({"add": 0, "diff": 1, "commit": 0, "push": [1, 0]})
    monkeypatch.setattr(formula_git_sync, "_run", fake)
    ok, msg = formula_git_sync.sync_now()
    assert ok and msg == "已提交并推送"
    pushes = [c for c in fake.calls if c[0] == "push"]
    assert len(pushes) == 2
    assert any("http.proxy=" in a for a in pushes[1])     # 第二次带代理 ✓


def test_push_failure_is_ok_and_never_raises(monkeypatch):
    """**推送全失败也不抛、且已本地提交** ✓（改动不丢 ✓，下次任何 push 会带走 ✓）。"""
    fake = _FakeGit({"add": 0, "diff": 1, "commit": 0, "push": 1})
    monkeypatch.setattr(formula_git_sync, "_run", fake)
    ok, msg = formula_git_sync.sync_now()
    assert ok is True and "推送失败" in msg


def test_git_failure_returns_false_not_raise(monkeypatch):
    """git 本身报错 ⇒ 返回 `(False, ...)` ✓ 而**不是抛异常** ✓（保存公式必须照常成功 ✓）。"""
    monkeypatch.setattr(formula_git_sync, "_run", _FakeGit(default=1))
    ok, msg = formula_git_sync.sync_now()
    assert ok is False and "失败" in msg


def test_exception_is_swallowed(monkeypatch):
    """**任何异常都被吞掉** ✓（含超时/编码等 ✓）—— 绝不冒泡到保存接口 ✗。"""
    def _boom(args, timeout=60.0):
        raise OSError("git 不见了")

    monkeypatch.setattr(formula_git_sync, "_run", _boom)
    ok, msg = formula_git_sync.sync_now()
    assert ok is False and "同步异常" in msg


def test_disabled_does_nothing(monkeypatch):
    """`FORMULA_GIT_SYNC=0` ⇒ 完全停手 ✓（不 add/commit/push ✓），连 schedule 都是空操作 ✓。"""
    fake = _FakeGit()
    monkeypatch.setattr(formula_git_sync, "_run", fake)
    monkeypatch.setattr(formula_git_sync, "ENABLED", False)
    ok, msg = formula_git_sync.sync_now()
    assert ok is False and "已关闭" in msg
    formula_git_sync.schedule("x")                        # 不得起线程 ✓
    assert formula_git_sync._timer is None
    assert fake.calls == []


def test_schedule_debounces(monkeypatch):
    """★ 去抖：窗口内连续多次 `schedule` ⇒ 只起**一个**定时器 ✓（编辑时连点保存不刷屏 ✓）。"""
    started = []

    class _T:
        def __init__(self, *a, **k):
            started.append(a[0])
            self.daemon = False

        def start(self):
            pass

        def is_alive(self):
            return True                                   # 假装"仍在等" ⇒ 后续调用被合并 ✓

    monkeypatch.setattr(formula_git_sync, "ENABLED", True)
    monkeypatch.setattr(formula_git_sync, "DEBOUNCE", 30.0)
    monkeypatch.setattr(formula_git_sync.threading, "Timer", _T)
    monkeypatch.setattr(formula_git_sync, "_timer", None)
    for _ in range(5):
        formula_git_sync.schedule("保存公式库")
    assert len(started) == 1                              # ★ 5 次 ⇒ 1 个定时器 ✓


def test_save_hook_triggers_schedule(tmp_path, monkeypatch):
    """★ 挂钩验证：`custom_formulas._save()`（新建/更新/删除**共用** ✓）必须触发同步调度 ✓。"""
    calls = []
    monkeypatch.setattr(custom_formulas, "_CUSTOM_FORMULAS_PATH", str(tmp_path / "cf.json"))
    # ★ v1.20.65：写操作改为**只写自己的文件**（`formulas/<user>.json` ✓）⇒ 单测一并指过去 ✓
    monkeypatch.setattr(custom_formulas, "_MY_FILE", str(tmp_path / "cf.json"))
    monkeypatch.setattr(custom_formulas, "_formula_git_sync",
                        type("F", (), {"schedule": staticmethod(lambda reason="": calls.append(reason))})())
    item = custom_formulas.create_custom_formula("测试", "OUT:CLOSE;", "Close($close)")
    assert item["name"] == "测试"
    assert calls, "写盘后没有触发 git 同步调度 ✗"
    assert custom_formulas.delete_custom_formula(item["id"]) is True
    assert len(calls) >= 2                                # 删除也要触发 ✓


def test_schedule_swallows_exceptions(monkeypatch):
    """★ `schedule` 必须**自己吞掉一切异常** ✓ —— 它跑在 `_save()` 的**持锁路径**上 ✗，
    一旦冒泡就会让"保存公式"直接失败 ✗（副作用绝不许污染主流程 ✓）。"""
    monkeypatch.setattr(formula_git_sync, "ENABLED", True)
    monkeypatch.setattr(formula_git_sync, "_timer", None)

    def _boom(*a, **k):
        raise RuntimeError("Timer 炸了")

    monkeypatch.setattr(formula_git_sync.threading, "Timer", _boom)
    formula_git_sync.schedule("x")                        # ⇒ 不得抛 ✓


def test_real_save_path_writes_file_even_if_sync_fails(tmp_path, monkeypatch):
    """★ 端到端（真实 `_save` + 真实 `schedule`，git 被打桩成**必失败** ✓）：
    文件照常落盘 ✓、接口不抛 ✓ —— 这正是"同步是副作用"的定义 ✓。"""
    monkeypatch.setattr(custom_formulas, "_CUSTOM_FORMULAS_PATH", str(tmp_path / "cf.json"))
    # ★ v1.20.65：写操作改为**只写自己的文件**（`formulas/<user>.json` ✓）⇒ 单测一并指过去 ✓
    monkeypatch.setattr(custom_formulas, "_MY_FILE", str(tmp_path / "cf.json"))
    monkeypatch.setattr(formula_git_sync, "DEBOUNCE", 3600.0)     # 别让定时器真触发 ✓
    monkeypatch.setattr(formula_git_sync, "_timer", None)
    item = custom_formulas.create_custom_formula("测试3", "OUT:CLOSE;", "Close($close)")
    assert item["id"] and (tmp_path / "cf.json").exists()
    assert custom_formulas.list_custom_formulas()[0]["name"] == "测试3"
