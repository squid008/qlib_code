# -*- coding: utf-8 -*-
"""公式库同步工具（**把"公式库怎么同步"这套约定固化在项目里** ✓，不靠任何人的记忆 ✓）。

【语义（用户 2026-09-25 定稿）】
  · 公式库按 **装机** 分文件：`backend/workdir/formulas/<install_id>.json`
    （`install_id` 见 `backend/workdir/.install_id`，与用户名无关 ⇒ 两台机器永不共用文件 ✓）。
  · 合并 = **只增不减 + 时间戳裁决**：同 id 取 `updated_at` 新者；删除写**带戳 tombstone**（只屏蔽）✓。
  · **保存/删除只在本机生效**：本地自动提交，但**默认不自动推送**（`FORMULA_GIT_SYNC_PUSH=1` 可开）✓。
  · **pull = 以"已推送的内容"为准，但只撤销"删除"、不撤销"新增"** ✓：
      - 本机**新增/编辑** ⇒ any pull 都**保留** ✓（"我这边的公式要保住" ✓）；
      - 本机**删除**（未推送的删标记）⇒ pull 时**撤销** ⇒ 被删的公式**恢复显示** ✓
        （"没 push 就不算数" ✓；同理：推送出去的删除才是真的删除 ✓）。
  · **push = 明确动作**：只有显式跑本工具的 `push`（或人工 `git push`）才会同步给另一台机器 ✓。

【用法（仓库任意位置）】
    python scripts/formula_sync.py status   # 现状：新增/删标记/未推送提交/两端可见条数
    python scripts/formula_sync.py pull     # 备份 → 撤销未推送删标记 → git pull --ff-only
    python scripts/formula_sync.py push     # 备份 → 打印将推送的提交 → git push（带代理）

【备份位置】`backend/workdir/formula_backups/<时间戳>/`（在 gitignore 里 ✓，不随库走 ✓）。
"""
import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")          # GBK 控制台也不炸 ✓

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)                                           # 复用同目录脚本 ✓
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import custom_formulas as cf                       # noqa: E402
# 代理口径与后端自动同步一致 ✓（`FORMULA_GIT_SYNC_PROXY` 可覆盖）
from app.services.formula_git_sync import PROXY                      # noqa: E402
import formula_state_check as state                                  # noqa: E402


def _git(args, timeout=180.0, proxy=False):
    """跑一条 git 命令 ✓。⚠ `proxy=True` 时把 `-c http.proxy=...` 放在 **`git` 与子命令之间** ✓ ——
    放到子命令后面 git 会当成子命令的参数 ⇒ 直接打印 usage 并失败 ✗（2026-09-25 实测踩到 ✓）。"""
    cmd = ["git"]
    if proxy:
        cmd += ["-c", "http.proxy=" + PROXY, "-c", "https.proxy=" + PROXY]
    cmd += list(args)
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _backup() -> str:
    """备份公式库目录（含别人的文件 ⇒ 回滚前留全量证据 ✓）。"""
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(ROOT, "backend", "workdir", "formula_backups", ts)
    os.makedirs(dst, exist_ok=True)
    src = os.path.dirname(cf.my_file())
    n = 0
    if os.path.isdir(src):
        for fn in os.listdir(src):
            if fn.endswith(".json"):
                shutil.copy2(os.path.join(src, fn), os.path.join(dst, fn))
                n += 1
    print("[备份] %d 个文件 → %s" % (n, os.path.relpath(dst, ROOT)))
    return dst


def _write_items(path: str, items):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)            # 与生产写盘格式一致 ✓
    os.replace(tmp, path)


def cmd_status(_args):
    st = state.snapshot()
    print("本机新增/编辑（★ pull 必须保留）= %d 条" % len(st["added"]))
    print("未推送删标记（pull 要撤销）        = %d 条" % len(st["tomb_unpushed"]))
    print("已推送删标记（保留）              = %d 条" % len(st["tomb_pushed"]))
    print("本机合并可见                      = %d 条" % len(st["visible"]))
    unpushed = _git(["log", "--oneline", "origin/main..HEAD"]).stdout.strip()
    print("未推送提交：")
    print("   " + (unpushed.replace("\n", "\n   ") if unpushed else "（无）"))
    return 0


def cmd_pull(_args):
    st = state.snapshot()
    _backup()
    dropped = st["tomb_unpushed"]
    if dropped:
        keep = [x for x in json.load(open(st["my_file"], encoding="utf-8"))
                if not (x.get("deleted") and x.get("id") in {y.get("id") for y in dropped})]
        _write_items(st["my_file"], keep)
        print("[回滚] 已撤销 %d 条**未推送的删标记**（被删公式恢复显示 ✓；文件其它条目一律不动 ✓）"
              % len(dropped))
        for x in dropped:
            print("       x %s（id=%s）" % (st["others"].get(x.get("id")), x.get("id")))
        if _git(["add", "--", st["rel"]]).returncode == 0:
            _git(["commit", "-q", "-m",
                  "chore(formulas): 撤销未推送的删标记（pull 约定：未推送的删除不算数）",
                  "--", st["rel"]])
            print("[回滚] 已本地提交该修正（未推送 ✓）")
    else:
        print("[回滚] 无需处理（没有未推送的删标记 ✓）")

    r = _git(["pull", "--ff-only", "origin", "main"], proxy=True)
    tail = (r.stdout or r.stderr).strip().splitlines()
    print("[pull] " + (tail[-1] if tail else "（无输出）"))
    if r.returncode != 0:
        print("[pull] ⚠ 失败（本地未做任何额外改动 ✓，备份见上 ✓）")
        return 1
    print("[pull] 之后：本机合并可见 = %d 条" % len(state.snapshot()["visible"]))
    return 0


def cmd_push(_args):
    print("[push] 将要推送的本地提交：")
    print("   " + (_git(["log", "--oneline", "origin/main..HEAD"]).stdout.strip().replace("\n", "\n   ")
                  or "（无）"))
    _backup()
    r = _git(["push", "origin", "HEAD"], timeout=300.0, proxy=True)
    out = (r.stdout or r.stderr).strip().splitlines()
    for ln in out[-3:]:
        print("[push] " + ln)
    if r.returncode != 0:
        return 1
    print("[push] 之后：")
    print(subprocess.run([sys.executable, os.path.join(_HERE, "formula_remote_view.py")],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout.strip())
    return 0


def main():
    ap = argparse.ArgumentParser(description="公式库同步（pull=只撤销未推送的删除；push=明确同步）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="现状（新增/删标记/未推送提交/可见条数）")
    sub.add_parser("pull", help="备份 → 撤销未推送删标记 → git pull --ff-only")
    sub.add_parser("push", help="备份 → 打印将推送的提交 → git push（带代理）")
    args = ap.parse_args()
    return {"status": cmd_status, "pull": cmd_pull, "push": cmd_push}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
