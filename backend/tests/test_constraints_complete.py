# -*- coding: utf-8 -*-
r"""`constraints.txt` 必须**完整锁住**所有直接依赖（★ v2.15.0）。

## 为什么加这条
第三方评审指出 `requirements.txt` 里 `==` 个数为 **0** ✓ —— 但那是**故意的**：
`requirements*.txt` 是**抽象声明**（区间），`constraints.txt` 才是**锁** ✓
（pip 官方推荐的组合，也让"升级"只需改一个文件 ✓）。

真正的问题不是"抽象声明没 `==`"，而是**锁不完整** ✗：
v2.15.0 之前 `pip-audit` 与 `python-multipart` **没有 pin** ⇒
同一个 commit 在不同时间装出两套环境 ✓（正是 `constraints.txt` 头部那段教训）。

⇒ 三条判据：
1. **每个** `requirements*.txt` 里的包，在 `constraints.txt` 里都有 `==` ✓；
2. `constraints.txt` 里**只有** `pkg==ver`（不许 `>=` / `~=` / 区间）✓；
3. 反向也查：`constraints.txt` 里的包**必须**是直接依赖之一 ✓
   （防"锁了但没人要"的僵尸行 ✗ —— 现在恰好 0 条 ✓）。
"""
from __future__ import annotations

import io
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ_FILES = ("requirements.txt", "requirements-dev.txt")
CON = os.path.join(BACKEND, "constraints.txt")

_NAME = re.compile(r"^([A-Za-z0-9_.\-]+)")
_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==(\S+)$")


def _norm(n: str) -> str:
    return n.lower().replace("_", "-")


def _req_names() -> list:
    names = []
    for f in REQ_FILES:
        for ln in io.open(os.path.join(BACKEND, f), encoding="utf-8").read().splitlines():
            ln = ln.split("#")[0].strip()
            if not ln:
                continue
            m = _NAME.match(ln)
            if m:
                names.append(_norm(m.group(1)))
    return names


def _con_lines() -> list:
    out = []
    for ln in io.open(CON, encoding="utf-8").read().splitlines():
        raw = ln.strip()
        if not raw or raw.startswith("#"):
            continue
        out.append(raw.split("#")[0].strip())
    return [x for x in out if x]


class TestConstraintsComplete:
    def test_every_direct_dep_is_pinned(self):
        """⭐⭐ 每个直接依赖都有 `==` 锁（漏一个 ⇒ 同一 commit 装出两套环境 ✗）。"""
        pins = {}
        for ln in _con_lines():
            m = _PIN.match(ln)
            if m:
                pins[_norm(m.group(1))] = m.group(2)
        missing = [p for p in sorted(set(_req_names())) if p not in pins]
        assert not missing, "这些直接依赖没有 pin ✗：%s" % missing

    def test_constraints_only_exact_pins(self):
        """⭐ `constraints.txt` 里只许 `pkg==ver`（区间会重新引入不可复现 ✗）。"""
        bad = [ln for ln in _con_lines() if not _PIN.match(ln)]
        assert not bad, "constraints 里有非精确版本行 ✗：%s" % bad[:5]

    def test_no_zombie_pins(self):
        """⭐ 反向：锁里不许有"没人要"的包（僵尸行会让人误以为有传递依赖 ✓）。"""
        direct = set(_req_names())
        pins = {_norm(_PIN.match(ln).group(1)) for ln in _con_lines() if _PIN.match(ln)}
        zombie = sorted(pins - direct)
        assert not zombie, "constraints 里有不在 requirements 中的包 ✗：%s" % zombie

    def test_versions_are_concrete(self):
        """⭐ 版本号必须像版本号（防手滑写成 `==latest` 之类 ✓）。"""
        bad = [ln for ln in _con_lines()
               if _PIN.match(ln) and not re.match(r"^[0-9][0-9A-Za-z.\-]*$", _PIN.match(ln).group(2))]
        assert not bad, "版本号可疑 ✗：%s" % bad[:5]
