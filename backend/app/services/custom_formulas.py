# -*- coding: utf-8 -*-
"""自定义公式持久化：存到 workdir/custom_formulas.json。

公司/本地共享同一份文件（只要 workdir 在多端可见），刷新/重启不丢失。
结构：[{id, name, text, expression, codegen, created_at, updated_at}, ...]

★ v1.20.59：`expression` 是**编译产物（缓存）** ✓ ⇒ 生成逻辑变了它就陈旧 ✗
（**单因子测试**直接求值这个缓存 ✗，回测则按 `text` 重译 ✓，见 `parser/codegen.CODEGEN_SEMANTICS` 的
长注释 ✓）⇒ `list_custom_formulas()` 读到时**自动按原文重编**并写回 ✓✓，用户不必手动重存 ✓。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from typing import List, Optional

from ..config import WORK_DIR
from ..factors.parser.codegen import CODEGEN_SEMANTICS

_CUSTOM_FORMULAS_PATH = os.path.join(WORK_DIR, "custom_formulas.json")
_lock = threading.Lock()


def refresh_stale_expressions(items: List[dict]) -> bool:
    """★ v1.20.59：把 `codegen` 戳**过期的条目按 `text` 重新编译**（原地改 `items` ✓）。

    返回"是否有改动"✓。ⓘ **纯函数式**：只动传入的列表、**不碰磁盘** ✓（便于单测 ✓）。

    ⚠ 编译失败 ⇒ **保留旧 expression** 并跳过（不推进戳 ✓ ⇒ 下次还会再试 ✓）——
      历史坏公式绝不该让整个列表接口 500 ✗，也不该被静默"标记为已同步" ✗。
    """
    stale = [it for it in items if (it.get("codegen") or "") != CODEGEN_SEMANTICS]
    if not stale:
        return False
    # 库按**全部**条目原文构建 ✓（公式间调用要能互相看见 ✓，与 `handler._translate_all` 同口径 ✓）
    from ..factors.parser import build_library, translate_formula

    lib = build_library([(it.get("text") or "") for it in items])
    changed = False
    for it in stale:
        text = it.get("text") or ""
        if not text.strip():
            continue
        try:
            t = translate_formula(text, library=lib)
        except Exception as e:                                   # noqa: BLE001
            print("[formulas] ⚠ %s 重新编译失败，保留旧 expression：%s: %s"
                  % (it.get("name"), type(e).__name__, e), flush=True)
            continue
        if t.expression != it.get("expression"):
            print("[formulas] ↻ %s 的 expression 已按原文重编（codegen %s）"
                  % (it.get("name"), CODEGEN_SEMANTICS), flush=True)
        it["expression"] = t.expression
        it["codegen"] = CODEGEN_SEMANTICS
        changed = True
    return changed


def _load() -> List[dict]:
    if not os.path.exists(_CUSTOM_FORMULAS_PATH):
        return []
    try:
        with open(_CUSTOM_FORMULAS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: List[dict]) -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    tmp = _CUSTOM_FORMULAS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CUSTOM_FORMULAS_PATH)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_custom_formulas() -> List[dict]:
    """列出全部公式 ✓ —— ★ v1.20.59 起顺带**刷新陈旧 expression**（生成逻辑变过 ⇒ 按原文重编 ✓）。

    ⚠ 这一步是 GET 却会写盘 ✗ —— 但它是**缓存刷新**（`expression` 完全由 `text` 决定 ✓），
      不改任何用户语义 ✓；换来的是"**不必手动重存公式**" ✓（否则单因子测试会一直吃到旧串 ✗，
      见 `parser/codegen.CODEGEN_SEMANTICS` 的说明 ✓）。
    """
    with _lock:
        items = _load()
        if refresh_stale_expressions(items):
            _save(items)
        return items


def create_custom_formula(name: str, text: str, expression: str) -> dict:
    with _lock:
        items = _load()
        item = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "text": text,
            "expression": expression,
            "codegen": CODEGEN_SEMANTICS,      # ★ 新存的必然是当前生成逻辑 ✓
            "created_at": _now(),
            "updated_at": _now(),
        }
        items.append(item)
        _save(items)
        return item


def update_custom_formula(formula_id: str, name: str, text: str, expression: str) -> Optional[dict]:
    with _lock:
        items = _load()
        for item in items:
            if item.get("id") == formula_id:
                item["name"] = name
                item["text"] = text
                item["expression"] = expression
                item["codegen"] = CODEGEN_SEMANTICS     # ★ 手动保存 ⇒ 必然是最新的生成逻辑 ✓
                item["updated_at"] = _now()
                _save(items)
                return item
        return None


def delete_custom_formula(formula_id: str) -> bool:
    with _lock:
        items = _load()
        next_items = [i for i in items if i.get("id") != formula_id]
        if len(next_items) == len(items):
            return False
        _save(next_items)
        return True
