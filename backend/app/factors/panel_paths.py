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
from typing import Dict

#: `inst` → 解析出的目录（含**否定结果** ✓，避免每次 3 次 isdir ✗）
INST_DIR_CACHE: Dict[str, str] = {}


def inst_dir(inst: str, features_dir: str) -> str:
    """→ `features/<inst>` 的**实际目录**，大小写归一 ✓。

    依次试 **小写 → 原样 → 大写**（兼容不同 dump 工具的命名习惯 ✓）：
    ⚠ **小写排第一是为了两平台行为一致** ✓ —— 若先试原样，Windows 会返回
    `features/SH600000`、Linux 返回 `features/sh600000` ⇒ 同一函数两平台返回不同字符串 ✗，
    回归测试也就没法只写一份 ✓。

    都不存在时返回**小写**路径 ✓（调用方 `_dir_meta` 会照旧返回 None ✓ 语义不变 ✓）。
    """
    hit = INST_DIR_CACHE.get(inst)
    if hit is not None:
        return hit
    for cand in (inst.lower(), inst, inst.upper()):
        d = os.path.join(features_dir, cand)
        if os.path.isdir(d):
            INST_DIR_CACHE[inst] = d
            return d
    d = os.path.join(features_dir, inst.lower())
    INST_DIR_CACHE[inst] = d          # 否定结果也缓存 ✓（目录后建时由 reset_caches 清 ✓）
    return d
