# -*- coding: utf-8 -*-
"""公式库「本机 vs 远端」状态核对（**pull 前/后**用；与 App 用同一套合并逻辑 ✓）。

打印三件事（正是 pull 约定需要的判据 ✓）：
  ① 本机**新增/编辑**（★ 任何 pull 都必须**保留** ✓）
  ② 本机**删标记**：逐条标出「已推送（保留）／未推送（pull 要撤销）」
  ③ **需要且只允许回滚的未推送删标记条数** + 合并后的可见条数

用法（仓库任意位置）：
    python scripts/formula_state_check.py
⚠ 控制台输出按 UTF-8 走 ✓（Windows GBK 控制台下中文不会炸 ✓）。
"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")          # GBK 控制台也不炸 ✓

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.services import custom_formulas as cf                       # noqa: E402


def _repo_root() -> str:
    return subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip() or ROOT


def _remote_items(rel_path: str, repo: str):
    """取 `origin/main` 上该文件的条目（文件不存在 ⇒ None ✓）。"""
    r = subprocess.run(["git", "show", "origin/main:" + rel_path], cwd=repo, capture_output=True)
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout.decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:                                                # noqa: BLE001
        return None


def snapshot():
    """一次性算出状态（供本脚本与 `formula_sync.py` 复用 ✓）。"""
    repo = _repo_root()
    my = cf.my_file()
    rel = os.path.relpath(my, repo).replace(os.sep, "/")
    loc = json.load(open(my, encoding="utf-8")) if os.path.isfile(my) else []
    rem = _remote_items(rel, repo) or []
    rem_ids = {x.get("id") for x in rem}
    rem_tomb = {x.get("id") for x in rem if x.get("deleted")}
    others = {}
    d = os.path.dirname(my)
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json") and os.path.join(d, fn) != my:
                try:
                    for x in json.load(open(os.path.join(d, fn), encoding="utf-8")):
                        others[x.get("id")] = x.get("name")
                except Exception:                                    # noqa: BLE001
                    pass
    return {
        "repo": repo, "my_file": my, "rel": rel,
        "added": [x for x in loc if not x.get("deleted") and x.get("id") not in rem_ids],
        "tomb_pushed": [x for x in loc if x.get("deleted") and x.get("id") in rem_tomb],
        "tomb_unpushed": [x for x in loc if x.get("deleted") and x.get("id") not in rem_tomb],
        "others": others,
        "visible": [x.get("name") for x in cf.list_custom_formulas()],
    }


def main():
    st = snapshot()
    print("=" * 92)
    print("我的装机文件 : %s" % st["rel"])
    print("")
    print("① 本机新增/编辑（★ pull **必须保留**）：%d 条" % len(st["added"]))
    for x in st["added"]:
        print("     + %-16s id=%s updated_at=%s" % (x.get("name"), x.get("id"), x.get("updated_at")))
    print("")
    print("② 本机删标记：%d 条（已推送 %d / 未推送 %d）"
          % (len(st["tomb_pushed"]) + len(st["tomb_unpushed"]), len(st["tomb_pushed"]), len(st["tomb_unpushed"])))
    for tag, arr in (("已推送（保留 ✓ 删除已生效）", st["tomb_pushed"]),
                     ("⚠ 未推送（pull 时撤销 ⇒ 条目恢复）", st["tomb_unpushed"])):
        for x in arr:
            print("     x %-16s id=%s at=%s ⇒ %s"
                  % (st["others"].get(x.get("id")), x.get("id"), x.get("updated_at"), tag))
    print("")
    print("⇒ **需要（且只允许）回滚的未推送删标记 = %d 条**" % len(st["tomb_unpushed"]))
    print("")
    names = st["visible"]
    print("【合并可见】%d 条 | 含 ALPHA143_S4 = %s" % (len(names), "ALPHA143_S4" in names))
    print("=" * 92)


if __name__ == "__main__":
    main()
