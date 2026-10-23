# -*- coding: utf-8 -*-
"""**分层闸门**（★ v2.11.62）—— `app/` 各子包之间**不许"下层 import 上层"**。

## 为什么需要它（这是"架构债只减不增"的唯一保障）
`md/architecture.md` 里**实测**查出 **19 处分层违规** ✓（`services/datasets.py` 7 处、
`services/artifacts_service.py` 6 处 …，其中唯一一条 `services → routers` 最刺眼 ✓）。
但**当时没有任何闸门** ✗ —— 也就是说：
· 我修掉 3 处、再新写 5 处 ⇒ **总数还涨了**，没人会知道 ✗；
· 反过来，只在文档里写一句"现有 19 处" ⇒ **文档会过期** ✗（本项目反复踩这个 ✓）。

⇒ 沿用本项目成熟的套路：**冻结基线 + 只减不增** ✓（和 `test_code_health` 的行数棘轮、
`test_except_ratchet` 的静默吞异常棘轮同一套 ✓）。

## 分层约定（与 `md/architecture.md` §3 一致 ✓）
```
L1  services / models            ← 最底层（跨切面能力 + 纯数据契约）
L2  engine / datasource          ← 执行引擎、数据源实现
L3  factors / signals / exports  ← 业务垂域
L4  routers                      ← HTTP 层（最上层）
```
`app/*.py`（config / logger / metrics / main）算 **L0**：谁都可以用 ✓，它不许依赖任何业务包 ✗。

## 口径（重要，别改错方向）
- **允许**：上层 → 下层（L4 → L1 ✓）
- **允许**：同层互调（`factors ↔ signals` ✓ —— 它们本来就是并列垂域）
- **禁止**：下层 → 上层（`services → routers` ✗、`engine → factors` ✗）
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

import pytest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

LAYER = {
    "services": 1, "models": 1,
    "engine": 2, "datasource": 2,
    "factors": 3, "signals": 3, "exports": 3,
    "routers": 4,
}

#: 冻结基线：`"<文件相对 app/ 的路径> -> <被依赖的包>"` → **允许的最大出现次数** ✓
#: 规则：**只许减**（修掉了就把数字改小 ✓），**不许加新键** ✗。
#: 采集日期 **2026-10-23**（v2.11.62 ✓，共 **19** 处 / 8 个键 —— 由本文件的扫描器产出 ✓
#: 与 `md/architecture.md` §3 的表**逐条对得上** ✓）。
BASELINE: dict[str, int] = {
    "engine/qlib_engine.py -> factors": 1,            # ⚠ 引擎反向依赖因子层（最该先修的一条）
    "services/artifacts_service.py -> engine": 4,     # 产物层要知道引擎内部类型
    "services/artifacts_service.py -> factors": 2,
    "services/custom_formulas.py -> factors": 2,      # 公式持久化 ↔ 公式编译
    "services/datasets.py -> engine": 4,              # 切数据集要清引擎/因子缓存
    "services/datasets.py -> factors": 2,
    "services/datasets.py -> routers": 1,             # ⚠ 唯一一条 services → routers（L334）
    "services/qlib_runtime.py -> factors": 3,         # qlib.init 要读因子的缓存开关
}

_IMPORT_PATS = (
    re.compile(r"(?:from|import)\s+app\.(\w+)"),      # 绝对导入
    re.compile(r"^\s*from\s+\.{1,3}(\w+)", re.M),     # 相对导入（本项目大量使用 ✓）
)


def _scan() -> dict[str, int]:
    """扫出所有"下层 → 上层"的边，返回 `{"file -> pkg": 次数}` ✓。"""
    found: dict[str, int] = defaultdict(int)
    for pkg, layer in LAYER.items():
        base = os.path.join(APP, pkg)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                # ⚠⚠ 必须用 **`utf-8-sig`**（会吃掉 BOM）✗→✓ ——
                #   实测：用 `utf-8` 读**带 BOM 的文件**时，`^` 后面立刻是 `\ufeff` ✗
                #   ⇒ `^\s*from` 匹配不上 ⇒ **该文件被静默跳过** ✓（我拿探针文件验闸门时
                #   才发现：3 passed、探针明明写着违规 ✓）。PowerShell 5.1 的
                #   `Set-Content -Encoding utf8` 默认写 BOM ✓ ⇒ 这对本地脚本/工具生成的
                #   文件是**真实概率**，不是理论问题 ✓。
                with open(path, encoding="utf-8-sig") as f:
                    txt = f.read()
                rel = os.path.relpath(path, APP).replace("\\", "/")
                for pat in _IMPORT_PATS:
                    for m in pat.finditer(txt):
                        tgt = m.group(1)
                        if tgt in LAYER and LAYER[tgt] > layer:
                            found["%s -> %s" % (rel, tgt)] += 1
    return dict(found)


def test_layering_baseline_is_up_to_date():
    """⭐⭐ 既不许**新增**违规，也不许**变多** ✓（只减不增）。

    ⚠ 失败信息要把"怎么改"写清楚 ✗→✓：
      · 出现**新键** ⇒ 你写了一个下层→上层的 import ✗ ⇒ **改回去**（把依赖下沉/上移）；
      · 计数**变大** ⇒ 同一个文件又多了一处 ✗；
      · **成功修掉** 时这条不会红 ✓，但请**顺手把基线数字改小** ✓（否则闸门会慢慢变松 ✗）。
    """
    cur = _scan()
    new_keys = sorted(set(cur) - set(BASELINE))
    grew = sorted(k for k in cur if k in BASELINE and cur[k] > BASELINE[k])
    shrink = sorted(k for k in BASELINE if k not in cur or cur[k] < BASELINE[k])

    msg = []
    if new_keys:
        msg.append("新增了 %d 处分层违规 ✗（下层 import 上层）：\n" % len(new_keys)
                   + "\n".join("    + %s（%d 次）" % (k, cur[k]) for k in new_keys))
    if grew:
        msg.append("已有违规**变多** ✗：\n"
                   + "\n".join("    ↑ %s 基线 %d → 现 %d" % (k, BASELINE[k], cur[k]) for k in grew))
    if msg:
        msg.append("\n  修法：把该 import 下沉到更底层，或让它由上层注入 ✓（见 md/architecture.md §3）")
        pytest.fail("\n".join(msg))

    if shrink:
        # 不判红 ✓ —— 只是提醒"可以收窄基线了"✓（否则基线会永远比现实宽松 ✗）
        print("  ⭐ 有 %d 处已改善，可把基线数字改小：%s" % (len(shrink), ", ".join(shrink)))


def test_selfcheck_scanner_is_not_silently_empty():
    """**守卫自检** ✓ —— 扫描器写错（正则漏了相对导入 ✗）时会返回空集 ⇒ **恒绿** ✗。

    实测教训（本文件诞生时真踩过 ✓）：我第一版 `score.py` 里的依赖统计**只扫 `from app.X`**
    ✗ ⇒ 矩阵**全是 0** ✓，因为本项目大量用 `from ..engine.xxx import` ✓。
    ⇒ 这条钉住两件事：① 至少扫到 1 处；② 那处"最刺眼"的 `services/datasets.py -> routers`
      必须在名单里 ✓（它是 `md/architecture.md` 里点名的那条 ✓）。
    """
    cur = _scan()
    assert cur, "一处都没扫到 ⇒ 正则写错了（大概率漏了相对导入 ✗）"
    assert "services/datasets.py -> routers" in cur, (
        "没扫到已知的那条 `services → routers` ⇒ 扫描逻辑退化了 ✗\n  实际扫到：%s"
        % sorted(cur)
    )


def test_layering_total_does_not_grow():
    """总数棘轮 ✓（把"多条新违规互相抵消掉某条修复"的情况也堵住）。"""
    cur = _scan()
    assert sum(cur.values()) <= sum(BASELINE.values()), (
        "分层违规总数涨了 ✗：基线 %d → 现在 %d"
        % (sum(BASELINE.values()), sum(cur.values()))
    )
