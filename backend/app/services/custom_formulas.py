# -*- coding: utf-8 -*-
"""自定义公式持久化：存到 workdir/custom_formulas.json。

公司/本地共享同一份文件（只要 workdir 在多端可见），刷新/重启不丢失。
结构：[{id, name, text, expression, created_at, updated_at}, ...]
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from typing import List, Optional

from ..config import WORK_DIR

_CUSTOM_FORMULAS_PATH = os.path.join(WORK_DIR, "custom_formulas.json")
_lock = threading.Lock()


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
    with _lock:
        return _load()


def create_custom_formula(name: str, text: str, expression: str) -> dict:
    with _lock:
        items = _load()
        item = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "text": text,
            "expression": expression,
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
