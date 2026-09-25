# -*- coding: utf-8 -*-
"""从 **`origin/main`** 的公式库文件，算出"另一台机器 pull 后会看到几条"（远端视角）。

为什么要专门算：公式库是**多文件合并**（`formulas/<装机ID>.json` × N + 老单文件）
⇒ **一条删标记就能隐藏对面的一条** ⇒ 只看单文件条数会误判 ✗。
本脚本把 `origin/main:backend/workdir/formulas/*.json` 导出到临时目录，再用**生产合并逻辑**
（`custom_formulas.load_merged` —— 与 App 完全同一套规则）算一遍 ✓。

用法（仓库任意位置）：
    python scripts/formula_remote_view.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")          # GBK 控制台也不炸 ✓

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import custom_formulas as cf                       # noqa: E402

_REL_DIR = "backend/workdir/formulas"


def main():
    repo = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip() or ROOT
    files = subprocess.run(["git", "ls-tree", "--name-only", "origin/main", _REL_DIR + "/"],
                           cwd=repo, capture_output=True, text=True).stdout.split()
    tmp = tempfile.mkdtemp(prefix="remote_formulas_")
    n_raw = 0
    try:
        for rel in files:
            blob = subprocess.run(["git", "show", "origin/main:" + rel], cwd=repo, capture_output=True).stdout
            with open(os.path.join(tmp, os.path.basename(rel)), "wb") as f:
                f.write(blob)
            arr = json.loads(blob.decode("utf-8"))
            n_raw += len(arr)
            print("   %-24s %3d 条" % (os.path.basename(rel), len(arr)))

        # 用生产合并逻辑算"远端视角"（临时目录 + 不存在的"我的文件"/老文件 ⇒ 只读 ✓）
        # ⚠ 同时换掉 `_USER` ⇒ 确保 `ensure_migrated()` 不会给临时文件改名 ✓
        old = (cf._FORMULAS_DIR, cf._MY_FILE, cf._CUSTOM_FORMULAS_PATH, cf._USER)
        try:
            cf._FORMULAS_DIR = tmp
            cf._MY_FILE = os.path.join(tmp, "__none__.json")
            cf._CUSTOM_FORMULAS_PATH = os.path.join(tmp, "__no_legacy__.json")
            cf._USER = "__none__"
            names = [x.get("name") for x in cf.load_merged()]
        finally:
            (cf._FORMULAS_DIR, cf._MY_FILE, cf._CUSTOM_FORMULAS_PATH, cf._USER) = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    print("远端文件共 %d 条（原始计数，含删标记）" % n_raw)
    print("★ 另一台机器 pull 后可见 = **%d 条**" % len(names))
    print("   含 ALPHA143_S4 = %s" % ("ALPHA143_S4" in names))


if __name__ == "__main__":
    main()
