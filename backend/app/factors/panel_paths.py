# -*- coding: utf-8 -*-
"""面板取数的**路径解析**（★ v2.11.47，从 `panel_expr` 抽出）。

## 为什么单独成模块
两个原因，都是硬的：
1. **`panel_expr.py` 有 2600 行的棘轮上限** ✓（`tests/test_code_health.py`，只减不增 ✗）
   ⇒ 修 bug 加 37 行会**顶破它** ✓ ⇒ 抽出来反而是净减 ✓；
2. 这个函数**值得单独测** ✓ —— 它是 P2-#23 的根因所在 ✓，而且测它**不需要真数据 / 不需要
   `qlib.init`** ✓（`tests/test_panel_inst_case.py` ✓）。

## 它解决的 bug（值得记住的那种）
`panel_features` 在 **Linux 上返回 0 行**、Windows 本地正常 ✗，同一份代码同一份数据：
· `features/<inst>` 的目录名是**全小写** ✓（qlib bin 的约定），而调用方传的是 `SH600000`（大写 ✓）；
· **Windows / NTFS 大小写不敏感** ⇒ 大写路径照样命中 ⇒ **本地永远看不出来** ✗✗；
· **Linux 大小写敏感** ⇒ 找不到 ⇒ `layout=[]` ⇒ 面板 0 行 ✗；
· 而 qlib 自己的 `D.features` 会 `lower()` ⇒ 它照样 129 行 ✓
  ⇒ 只看到"两边行数不一致"这种**看不出因果**的现象 ✗。
"""
import os
from typing import Dict, Set, Tuple

#: 已经告警过的目录（同一进程只喊一次 ✓ —— 每次求值都刷一行日志会淹掉真正的问题 ✗）
WARNED: Set[str] = set()


def warn_missing(features_dir: str) -> None:
    """`features` 目录不存在时**告警一次** ✓（★ v2.11.50 从 `panel_expr` 搬过来）。

    为什么需要它（P2-#23 的经验 ✓）：`_feature_dir()` 解析出的路径若不存在 ✓，
    面板侧会**读到 0 行、不报错** ✗ ⇒ 上层只看到空 DataFrame ✓（CI 上就是
    `(0, 13020)` 那种看不出因果的断言 ✗）。
    ⚠ **不抛异常** ✓：有些调用方确实允许"数据未就绪"✓，抛错会改既有行为 ✗。

    ⚠ 为什么不在 `panel_expr` 里就地写 ✗：那个文件有 **2600 行棘轮上限**
    （`test_code_health`，只减不增 ✗），而这段要 12 行 ✓ ⇒ 搬到这里是**净减 11 行** ✓。
    """
    if os.path.isdir(features_dir) or features_dir in WARNED:
        return
    WARNED.add(features_dir)
    try:
        from ..logger import get_logger

        get_logger("factors").warning(
            "面板取数的 feature 目录不存在：%s ⇒ 将返回空结果"
            "（检查 QLIB_PROVIDER_URI / 数据集是否已同步 ✓）", features_dir)
    except Exception as e:       # noqa: BLE001 —— 日志失败绝不能拦住取数 ✓
        # ⚠ 必须走 `_log_swallowed` **留痕** ✗→✓ —— 写裸 `pass` 会被
        #   `tests/test_except_ratchet.py` 判成"静默吞异常"+1 ✗
        #   （那个棘轮口径：except body 只有 pass/continue/break/... 且无日志痕迹 ⇒ 算静默 ✓）
        from ..logger import _log_swallowed

        _log_swallowed("panel_paths.py", e)

#: `(features_dir, inst)` → 解析出的目录（含**否定结果** ✓，避免每次 3 次 isdir ✗）
#: ⚠ 键里**必须带 features_dir** ✗→✓ —— 只按 inst 做键时，
#:   测试里"同一个 inst 名 + 不同临时目录"会**互相污染** ✓（实测 13 条既有用例因此变红 ✗）。
INST_DIR_CACHE: Dict[Tuple[str, str], str] = {}


def inst_dir(inst: str, features_dir: str) -> str:
    """→ `features/<inst>` 的**实际目录**，大小写归一 ✓。

    依次试 **原样 → 小写 → 大写**（原样优先 ✓，兼容不同 dump 工具的命名习惯 ✓）。

    ⚠⚠ **必须"原样优先"** ✗→✓ —— 我先写成"小写优先"，结果 **20 条既有测试红了** ✗：
    它们**故意**用"目录名与 inst 大小写不一致"来测「缺文件 ⇒ 跳过该股」✓，
    小写优先会把它们**变成"找到了"** ⇒ 语义被改 ✗。
    原样优先则：目录名与 inst 一致 ⇒ 行为与修复前**逐位相同** ✓；
    只有"原样找不到"时才归一小写/大写 ⇒ 恰好补上 Linux 上那个 bug ✓。

    都不存在时返回**原样**路径 ✓（调用方 `_dir_meta` 会照旧返回 None ✓ 语义不变 ✓）。
    """
    key = (features_dir, inst)
    hit = INST_DIR_CACHE.get(key)
    if hit is not None:
        return hit
    for cand in (inst, inst.lower(), inst.upper()):
        d = os.path.join(features_dir, cand)
        if os.path.isdir(d):
            INST_DIR_CACHE[key] = d
            return d
    d = os.path.join(features_dir, inst)
    INST_DIR_CACHE[key] = d           # 否定结果也缓存 ✓（目录后建时由 reset_caches 清 ✓）
    return d
