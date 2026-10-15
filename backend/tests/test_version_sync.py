# -*- coding: utf-8 -*-
"""**版本号三处同步**守卫（2026-10-15，用户点名要的第二层检查 ✓）。

用户原话：「你按照 trader_code 那样顶栏那个版本号『前后端版本不一致就标黄』，这样我就知道你有
  没有重启了，**还有一层"改版本号忘了同步"的自动检查**。」

三个地方必须一致 ✓：
  ① `backend/app/__init__.py::__version__`  ← **唯一版本来源** ✓（`/api/version` 与导出文件都用它）
  ② `frontend/package.json` 的 `version`
  ③ `frontend/src/version.ts` 的 `FE_VERSION`（顶栏显示的就是它 ✓）

为什么值得一条测试（✗→✓ 的真实场景）：
  · 只改后端 ⇒ 顶栏**永远标黄** ✗ ⇒ 用户会以为"AI 没重启"，实际是自己漏改前端 ✗；
  · 只改前端 ⇒ 用户以为"已发版" ✗，而后端跑的还是旧口径 ✗；
  · 发版时 tag/change_log 也跟着版本号走 ⇒ 漏改一处会让"这版到底发了啥"说不清 ✓。
（CI 在 Linux 上跑，能读到仓库里的前端文件 ✓。）
"""
import json
import re
from pathlib import Path

import pytest

from app import __version__

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "frontend" / "package.json"
FE_VERSION_TS = ROOT / "frontend" / "src" / "version.ts"


def _pkg_version() -> str:
    return str(json.loads(PKG.read_text(encoding="utf-8"))["version"])


def _fe_version() -> str:
    m = re.search(r"FE_VERSION\s*=\s*['\"]([^'\"]+)['\"]",
                  FE_VERSION_TS.read_text(encoding="utf-8"))
    assert m, "在 frontend/src/version.ts 里找不到 FE_VERSION = '…' ✗"
    return m.group(1)


def test_frontend_files_exist():
    assert PKG.exists(), "找不到 %s" % PKG
    assert FE_VERSION_TS.exists(), "找不到 %s" % FE_VERSION_TS


def test_backend_and_frontend_versions_match():
    """★ 核心守卫：三处版本号必须完全一致 ✓（漏改一处 ⇒ CI 直接红 ✓）。"""
    be, pkg, fe = __version__, _pkg_version(), _fe_version()
    assert be == pkg == fe, (
        "版本号不一致 ✗（改版本号时四处要一起改：backend/app/__init__.py 的 __version__ ✓、\n"
        "  frontend/package.json 的 version ✓、frontend/src/version.ts 的 FE_VERSION ✓、\n"
        "  md/change_log.md 追加对应小节 ✓）：\n"
        "  backend/app/__init__.py : %s\n"
        "  frontend/package.json   : %s\n"
        "  frontend/src/version.ts : %s" % (be, pkg, fe))


def test_version_looks_semver():
    """形状检查：`X.Y.Z`（发版 tag 与 change_log 小节都按它走 ✓）。"""
    for name, v in (("backend", __version__), ("frontend", _fe_version())):
        assert re.fullmatch(r"\d+\.\d+\.\d+", v), "%s 版本号不是 X.Y.Z 形状：%r" % (name, v)


def test_changelog_has_section_for_current_version():
    """`md/change_log.md` 必须有当前版本的小节 ✓（Keep a Changelog，用户规矩 ✓）。"""
    cl = ROOT / "md" / "change_log.md"
    if not cl.exists():
        pytest.skip("没有 change_log.md")
    body = cl.read_text(encoding="utf-8", errors="replace")
    assert ("## [%s]" % __version__) in body, (
        "md/change_log.md 里没有 `## [%s]` 小节 ✗ ⇒ 发版时补一条（改了什么/为什么/影响范围 ✓）"
        % __version__)
