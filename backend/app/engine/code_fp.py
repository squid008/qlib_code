# -*- coding: utf-8 -*-
"""**源码指纹**（零副作用轻量模块）—— 给磁盘缓存用，避免"手工版本号忘了改"✗。

为什么需要它（用户 2026-10-12 提的正是这一条 ✓："信号测试是不是有缓存功能？没有加版本的
哈希校验？"）：
  · 单因子面板缓存（`single_test.py` ✓）早就用**本模块源码的 mtime+size** 当指纹 ✓ ——
    v1.19.97 的注释写得很清楚："任何改动 ⇒ key 变 ⇒ 自动失效 ✓（比手工改版本号可靠 ✓，
    不会忘 ✓）"；
  · 而信号测试这条链上的其余缓存（`signals/event.py` 的事件结果 ✓、`signals/pricing.py`
    的池宽表 / 行情面板 ✓、`factors/event_study_cache.py` ✓）**只靠手工版本号** ✗
    ⇒ 2026-10-12 这轮改了成交时点/剔除/价格表范围/池口径（v2.1.52~60 ✓），
      若某次改动**恰好没改变表的形状**（形状/日期/列名相同 ✗），旧结果就会被当成新结果返回 ✗✗
      —— 那正是本项目最忌讳的"口径漂移 + 看不见" ✓。

用法（`key` 里拼一行即可 ✓）：
    from ..engine.code_fp import code_fingerprint
    h.update(code_fingerprint("signals/event.py", "app/signals/event.py"))
  返回形如 `"m1750000000-s12345"`；取不到文件时返回 `"na"`（**绝不抛异常** ✗ —— 缓存键不能因它失败 ✓）。
"""
from __future__ import annotations

import os
from typing import Iterable

from ..logger import _log_swallowed  # noqa: E402


_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def code_fingerprint(*paths: str) -> str:
    """`m<修改时间>-s<大小>` 串（多文件用 `|` 连接 ✓）；缺文件记 `na` ✓（不抛 ✗）。"""
    parts: list[str] = []
    for p in paths:
        try:
            fp = p if os.path.isabs(p) else os.path.join(_BACKEND_DIR, p)
            st = os.stat(fp)
            parts.append("m%d-s%d" % (int(st.st_mtime), int(st.st_size)))
        except Exception as e:  # noqa: BLE001
            _log_swallowed("code_fp.py", e)
            parts.append("na")
    return "|".join(parts) if parts else "na"


def modules_fingerprint(names: Iterable[str]) -> str:
    """按**模块名**取指纹（相对 backend 根 ✓），如 `modules_fingerprint(["app.signals.event"])` ✓。"""
    return code_fingerprint(*["%s.py" % n.replace(".", "/") for n in names])
