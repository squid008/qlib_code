# -*- coding: utf-8 -*-
"""版本号一键递增：同步 `backend/app/__init__.py`（唯一来源 ✓）与 `README.md` 顶部。

【进位规则（用户 2026-09-25 定稿，必须照办 ✓）】
    · 补丁号到 **99** 就向次版本号进 1、自己归 0：`1.20.99 → 1.21.0` ✓
    · 次版本号到 **99** 就向主版本号进 1：`1.99.99 → 2.0.0` ✓
    · **除非人明确指定版本号，不许无规则跳版本** ✗（例如别从 1.20.71 直接跳 1.21.0 ✗）

为什么要有这个脚本：版本号**只在 `backend/app/__init__.py` 定义** ✓，但 README 顶部要跟着改 ✓；
两处手工改易漏（历史上漏过一次：README 还停在 v1.19.27 而代码已 1.19.28 ✗）。

用法（仓库任意位置）：
    python scripts/bump_version.py              # 按进位规则递增（1.20.71 → 1.20.72）
    python scripts/bump_version.py --dry-run    # 只看会变成什么，不写文件
    python scripts/bump_version.py 1.21.0       # 人明确指定（跳版本也允许 ✓）
⚠ 之后请手动补 `md/change_log.md`（新增 `## [<新版>] - <日期>` 段落 ✓）与 `md/开发记录.md` ✓。
"""
import datetime as _dt
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "backend", "app", "__init__.py")
README = os.path.join(ROOT, "README.md")

_MAX = 99          # ★ 每个字段的上限：到 99 就进位（用户 2026-09-25 定的规则）


def next_version(cur: str) -> str:
    """按进位规则算下一个版本号 ✓（纯函数，便于自检/单测 ✓）。"""
    parts = cur.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError("版本号不是 x.y.z 形式（当前 %r）" % cur)
    major, minor, patch = (int(p) for p in parts)

    patch += 1
    if patch > _MAX:                 # 1.20.99 → 1.21.0 ✓
        patch = 0
        minor += 1
    if minor > _MAX:                 # 1.99.99 → 2.0.0 ✓
        minor = 0
        major += 1
    return "%d.%d.%d" % (major, minor, patch)


def main(argv):
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]

    src = io.open(INIT, encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    if not m:
        print("✗ 在 %s 里找不到 __version__" % INIT)
        return 1
    cur = m.group(1)
    try:
        new = argv[0] if argv else next_version(cur)
    except ValueError as e:
        print("✗ %s ⇒ 请显式指定新版本（如 1.21.0）" % e)
        return 1

    if dry:
        print("[dry-run] 按进位规则：%s → %s（未写文件 ✓）" % (cur, new))
        return 0

    src2, n1 = re.subn(r'__version__\s*=\s*"%s"' % re.escape(cur),
                       '__version__ = "%s"' % new, src)
    io.open(INIT, "w", encoding="utf-8").write(src2)

    rd = io.open(README, encoding="utf-8").read()
    rd2, n2 = re.subn(r"当前版本：v%s" % re.escape(cur), "当前版本：v%s" % new, rd)
    io.open(README, "w", encoding="utf-8").write(rd2)

    print("版本 %s → %s" % (cur, new))
    print("  %s : %d 处" % (os.path.relpath(INIT, ROOT), n1))
    print("  README.md : %d 处%s" % (n2, "" if n2 else "  ⚠ 顶部那行没匹配到，请手工核对"))
    print("下一步：在 md/change_log.md 顶部加 `## [%s] - %s` 段落；"
          "并按需在 md/开发记录.md 记要点。" % (new, _dt.date.today().isoformat()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
