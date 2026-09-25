# -*- coding: utf-8 -*-
"""版本号一键递增：同步 `backend/app/__init__.py`（唯一来源 ✓）与 `README.md` 顶部。

为什么要有它：版本号**只在 `backend/app/__init__.py` 定义** ✓，但 README 顶部要跟着改 ✓；
手工改两处易漏（历史上就漏过一次：v1.19.28 时 README 还停在 v1.19.27 ✗）。

用法（仓库任意位置）：
    python scripts/bump_version.py            # 补丁号 +1（1.20.70 → 1.20.71）
    python scripts/bump_version.py 1.21.0     # 显式指定
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


def main(argv):
    src = io.open(INIT, encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    if not m:
        print("✗ 在 %s 里找不到 __version__" % INIT)
        return 1
    cur = m.group(1)
    if argv:
        new = argv[0]
    else:
        parts = cur.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            print("✗ 版本号不是 x.y.z 形式（当前 %r）⇒ 请显式指定新版本" % cur)
            return 1
        new = "%s.%s.%d" % (parts[0], parts[1], int(parts[2]) + 1)

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
