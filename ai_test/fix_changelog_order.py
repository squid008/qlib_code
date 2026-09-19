# -*- coding: utf-8 -*-
"""修 `change_log.md` 的版本条目顺序：把 `[1.19.96]` 搬回 `[1.19.95]` 之前（用户 2026-09-19 发现）。

背景：条目应为**降序**（新在上）。实测 `[1.19.96]` 被写在了 `[1.19.93]` 之后 ✗
（`1.19.97 → 1.19.95 → 1.19.94 → 1.19.93 → 1.19.96 → 1.19.92` ✗）。
顺带报告：条目间的**版本缺号**（如 `1.19.98`~`1.19.103`、`1.20.3`、`1.20.6` 是否缺失 ✓）。

用法：
    python fix_changelog_order.py            # dry-run（只报告）
    python fix_changelog_order.py --apply    # 真搬
"""
import io
import re
import sys

P = r"d:\quant\qlib_code\md\change_log.md"
TITLE = re.compile(r"^## \[([^\]]+)\]")


def blocks(lines):
    """→ [(版本, 起行idx, 止行idx), …]（止 = 下一个标题前 ✓ 或 EOF ✓）。"""
    idx = [i for i, l in enumerate(lines) if TITLE.match(l)]
    out = []
    for j, i in enumerate(idx):
        end = idx[j + 1] if j + 1 < len(idx) else len(lines)
        out.append((TITLE.match(lines[i]).group(1), i, end))
    return out


def _key(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0, 0, 0)


def main():
    apply = "--apply" in sys.argv
    lines = io.open(P, encoding="utf-8").read().splitlines(keepends=True)
    bs = blocks(lines)
    print("版本条目 %d 个" % len(bs))

    # ---- ① 顺序体检（只对 1.19.9x / 1.20.x 段，历史更早的不动 ✓）----
    v19 = [(n, s, e) for n, s, e in bs if n.startswith("1.19.") or n.startswith("1.20.")]
    bad = []
    for (n1, s1, _e1), (n2, s2, _e2) in zip(v19, v19[1:]):
        if _key(n1) < _key(n2):
            bad.append((n1, s1 + 1, n2, s2 + 1))
    for n1, l1, n2, l2 in bad:
        print("  ✗ 逆序: [%s](L%d) 在 [%s](L%d) 之上" % (n1, l1, n2, l2))
    if not bad:
        print("  ✓ 顺序正确")

    # ---- ② 定位 [1.19.96] 并搬到 [1.19.95] 之前 ----
    by_v = {n: (s, e) for n, s, e in bs}
    if "1.19.96" not in by_v or "1.19.95" not in by_v:
        print("  ⚠ 找不到 1.19.96 / 1.19.95，跳过搬移")
    else:
        s96, e96 = by_v["1.19.96"]
        s95, _ = by_v["1.19.95"]
        if s96 < s95:
            print("  ✓ [1.19.96] 已在 [1.19.95] 之前，无需搬移")
        else:
            print("  → 搬移 [1.19.96]（L%d-L%d）到 [1.19.95]（L%d）之前"
                  % (s96 + 1, e96, s95 + 1))
            if apply:
                block = lines[s96:e96]
                rest = lines[:s96] + lines[e96:]
                # 重新定位 1.19.95（删掉 block 后行号会变 ✓）
                s95b = next(i for i, l in enumerate(rest)
                            if TITLE.match(l) and TITLE.match(l).group(1) == "1.19.95")
                new = rest[:s95b] + block + rest[s95b:]
                io.open(P, "w", encoding="utf-8", newline="").write("".join(new))
                print("  已写入 ✓")

    # ---- ③ 缺号报告（1.19.90 之后 + 1.20.x ✓）----
    have = {n for n, _s, _e in bs}
    miss = []
    for minor, lo, hi in (("19", 90, 103), ("20", 0, 30)):
        for p in range(lo, hi + 1):
            v = "1.%s.%d" % (minor, p)
            if v not in have:
                miss.append(v)
    print("  缺号（可能合并/漏写）: %s" % (", ".join(miss) if miss else "无"))


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归，见 2026-09-18 教训 ✓）
    main()
