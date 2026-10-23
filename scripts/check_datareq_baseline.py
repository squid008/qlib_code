# -*- coding: utf-8 -*-
"""**真实数据链路的看门狗**（★ v2.11.54）—— 允许"已知会失败"，但**不许悄悄多失败**。

## 为什么需要它（而不是直接 `pytest -m datareq`）
`datareq` 有 21 条：其中 **6 条**能在合成数据上跑通 ✓（已进 PR CI 的 `synth-data` job），
另外 **15 条**要真实数据**特有的性质**（物化 `chip_*` bin / 真复权因子 / 万科那种真标的 ✗）
⇒ 在 CI 上**必然失败** ✗。

如果夜间 job 直接跑 `pytest -m datareq` ⇒ **永远红** ✗ ⇒ 几天后大家就不看了 ✓（这比不跑更糟 ✗）。
⇒ 所以：把"**当前已知会失败的清单**"存成文件 ✓，夜间跑完比对：
· 出现**清单外**的失败 ⇒ **退出码 1**（这是真回归 ✓）；
· 失败**比清单少** ⇒ 通过，并提示"可以更新清单了"（说明有人修好了 ✓）。

## 用法
```bash
# 夜间 job（或本地用合成数据验证）
python scripts/check_datareq_baseline.py --pytest-args "-m datareq"

# 修好了一批之后，重新采基线
python scripts/check_datareq_baseline.py --rebaseline --pytest-args "-m datareq"
```
⚠ 必须在**指定环境**下采基线（CI 是"合成数据"环境 ✓，本机真数据下这些用例是**全过**的 ✗）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
BASELINE = os.path.join(ROOT, "scripts", "datareq_known_failures.txt")


def run_pytest(pytest_args: str) -> tuple[list[str], list[str], str]:
    """→ (failed, passed, 摘要)。用 junit 解析（不依赖 stdout 汇总行 ✓）。"""
    xml = os.path.join(BACKEND, "_dr_junit.xml")
    if os.path.exists(xml):
        os.remove(xml)
    cmd = [sys.executable, "-m", "pytest", "tests", "-q", "--tb=no",
           "--junit-xml=%s" % xml] + (pytest_args.split() if pytest_args else [])
    p = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    failed, passed = [], []
    if os.path.exists(xml):
        root = ET.parse(xml).getroot()
        suites = [root] if root.tag == "testsuite" else list(root)
        for s in suites:
            for tc in s.iter("testcase"):
                name = "%s::%s" % (tc.get("classname", ""), tc.get("name", ""))
                name = re.sub(r"^tests\.", "", name)
                bad = any(c.tag in ("failure", "error") for c in tc)
                (failed if bad else passed).append(name)
        os.remove(xml)
    return failed, passed, (p.stdout or "")[-600:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytest-args", default="-m datareq", help="传给 pytest 的筛选参数")
    ap.add_argument("--rebaseline", action="store_true", help="重新采基线（覆盖清单文件）")
    a = ap.parse_args()

    failed, passed, tail = run_pytest(a.pytest_args)
    print("  本轮：failed=%d  passed=%d" % (len(failed), len(passed)))
    if not failed and not passed:
        print("✗ 一条都没收集到 —— 参数或数据环境有问题 ✗\n" + tail)
        return 1

    known = set()
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            known = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}

    if a.rebaseline:
        with open(BASELINE, "w", encoding="utf-8") as f:
            f.write("# 夜间 job 的**已知失败清单**（★ v2.11.54）\n")
            f.write("# 这些用例需要真实行情特有的性质 ⇒ 在合成数据环境里必然失败 ✓\n")
            f.write("# 规则：出现**清单外**的失败 ⇒ 夜间 job 红（真回归 ✓）；\n")
            f.write("#       失败**比清单少** ⇒ 通过 + 提示更新（说明有人修好了 ✓）。\n")
            f.write("# 更新：python scripts/check_datareq_baseline.py --rebaseline\n")
            for n in sorted(failed):
                f.write(n + "\n")
        print("  已写入基线 %d 条 → %s" % (len(failed), BASELINE))
        return 0

    new_fail = sorted(set(failed) - known)
    fixed = sorted(known - set(failed))
    if fixed:
        print("  ⭐ 有 %d 条已不再失败（可以 --rebaseline 收窄清单）：" % len(fixed))
        for n in fixed[:10]:
            print("     - " + n)
    if new_fail:
        print("  ✗✗ **清单外的失败 %d 条** ⇒ 真回归：" % len(new_fail))
        for n in new_fail[:20]:
            print("     + " + n)
        return 1
    print("  ✓ 与已知清单一致（无新增失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
