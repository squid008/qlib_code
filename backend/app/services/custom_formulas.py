# -*- coding: utf-8 -*-
"""自定义公式持久化：★ v1.20.65 起改为**按人分文件 + 读取时合并**（用户 2026-09-24 的诉求 ✓）。

【为什么改】
  原先所有人共用 `workdir/custom_formulas.json` ✗（且该文件**纳入 git** ✓ —— 为了让公式跟着版本走 ✓）。
  ⇒ 两个人各自保存过公式后，**两边都改了同一个文件** ✗ ⇒ `git pull` 时 JSON **必然冲突** ✗
    （没有 `.gitattributes` ✓，逐行合并对 JSON 无效 ✓）⇒ 必须人工选一边 ✗ ⇒ 这一步**会真丢东西** ✗✓。
  ⚠ 而且 `refresh_stale_expressions` 会在**只读浏览**时按原文重编并写回 ✗ ⇒
    「只有一个人写公式」这种约定**守不住** ✗（实测：3 分钟内产生了两次自动提交 ✓）。

【方案（用户拍板 ✓）】
  · **每人一个文件**：`workdir/formulas/<user>.json` ✓ ⇒ git 层面**只新增文件** ✓ ⇒ **天然无冲突** ✓✓；
  · 读取时**合并所有文件** ✓（按 `id` 去重 ✓；同名/同 id 冲突时 **自己的文件 > 别人的 > 老的共享文件** ✓，
    别人的之间取 `updated_at` 较新者 ✓）⇒ 双方都能看到彼此的公式 ✓、谁也覆盖不了谁 ✓；
  · **写操作只写自己的文件** ✓：
      - 新建 ⇒ 追加到自己的文件 ✓；
      - 修改**别人的**条目 ⇒ 把自己文件里存一份**覆盖副本**（同 `id` ✓）⇒ 合并时优先 ✓；
      - 删除 ⇒ 自己文件里写一条 **tombstone（`{"id": ..., "deleted": true}` ✓）** ⇒ 合并时屏蔽 ✓；
  · **老的单文件继续可读** ✓（`workdir/custom_formulas.json` ✓，最低优先级 ✓，**不再写** ✗）⇒ 平滑迁移 ✓、
    现有 66 条一条不丢 ✓。
  ⚠ 用户名来源：`QUANT_FORMULA_USER` > `USERNAME`(Windows) > `USER` > `default` ✓ —— 两端机器上是不同的人 ✓
    （⚠ 同一台机器上不同人要用不同账号跑后端 ✗，或显式设环境变量 ✓）。

★ v1.20.59：`expression` 是**编译产物（缓存）** ✓ ⇒ 生成逻辑变了它就陈旧 ✗（**单因子测试**直接求值这个缓存 ✗，
  回测则按 `text` 重译 ✓，见 `parser/codegen.CODEGEN_SEMANTICS` 的长注释 ✓）⇒ `list_custom_formulas()`
  读到时**自动按原文重编** ✓✓，用户不必手动重存 ✓；重编结果**只回写自己那份文件**（作为覆盖副本 ✓）。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..config import WORK_DIR
from ..factors.parser.codegen import CODEGEN_SEMANTICS
from . import formula_git_sync as _formula_git_sync      # ★ v1.20.63：写盘后自动提交+推送 ✓

# 新方案：每人一个文件（本模块**只往这里写** ✓）
_FORMULAS_DIR = os.path.join(WORK_DIR, "formulas")
# 老的单文件：**只读**（兼容历史 ✓，不再写 ✗）
_CUSTOM_FORMULAS_PATH = os.path.join(WORK_DIR, "custom_formulas.json")


def _user_name() -> str:
    """当前"人"的标识（决定写哪个文件 ✓）。"""
    for k in ("QUANT_FORMULA_USER", "USERNAME", "USER", "LOGNAME"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in v)
    return "default"


_USER = _user_name()
# ★ 当前用户的文件（**唯一写入目标** ✓）。单测可 patch 本变量 ✓。
_MY_FILE = os.path.join(_FORMULAS_DIR, _USER + ".json")

_lock = threading.Lock()


def my_file() -> str:
    """当前用户的公式文件名（供日志/排查 ✓）。"""
    return _MY_FILE


def _read_items(path: str) -> List[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _sources() -> List[Tuple[str, List[dict], int]]:
    """所有来源，按**优先级从高到低**返回 `(路径, 条目, 优先级)` ✓。

    优先级：① 自己 → ② 别人的（按 `updated_at` 新→旧）→ ③ 老的共享单文件（最低 ✓）。
    ⚠ 别人的文件按"最后修改时间"排序只是为了**确定性** ✓（真正的取舍在合并规则里 ✓）。
    """
    mine = [(os.path.abspath(_MY_FILE), _read_items(_MY_FILE), 0)]
    others: List[Tuple[str, List[dict], int]] = []
    try:
        for fn in sorted(os.listdir(_FORMULAS_DIR)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(_FORMULAS_DIR, fn)
            if os.path.abspath(p) == os.path.abspath(_MY_FILE):
                continue
            others.append((p, _read_items(p), 1))
        others.sort(key=lambda t: os.path.getmtime(t[0]) if os.path.exists(t[0]) else 0, reverse=True)
    except OSError:
        pass
    legacy = [(os.path.abspath(_CUSTOM_FORMULAS_PATH), _read_items(_CUSTOM_FORMULAS_PATH), 2)]
    return mine + others + legacy


def _key(it: dict) -> str:
    """条目身份：优先 `id` ✓，老数据没有 id 时退化为 `name` ✓。"""
    return "id:" + it["id"] if it.get("id") else "name:" + (it.get("name") or "")


def load_merged() -> List[dict]:
    """★ 合并所有来源（**删标记生效** ✓、同 id 高优先级胜出 ✓）⇒ 去重后的条目列表 ✓。

    胜负规则（**确定性、可测** ✓）：
      · 优先级小的胜（自己 0 > 别人 1 > 老共享单文件 2 ✓）；
      · **同优先级**（= 不同人的文件之间 ✓）⇒ `updated_at` **较新**的胜 ✓
        （它比"文件 mtime"更语义化 ✓ —— mtime 会被 checkout / 编辑器触碰改变 ✗）。
    """
    best: Dict[str, Tuple[int, str, dict]] = {}
    tomb: Dict[str, Tuple[int, str]] = {}
    for _path, items, prio in _sources():
        for it in items:
            if not isinstance(it, dict):
                continue
            k = _key(it)
            ts = str(it.get("updated_at") or "")
            if it.get("deleted"):
                old = tomb.get(k)
                if old is None or (prio, ts) > old:      # ⚠ 元组比较：优先级小 → 需显式取小者 ✓
                    if old is None or prio < old[0] or (prio == old[0] and ts > old[1]):
                        tomb[k] = (prio, ts)
                continue
            cur = best.get(k)
            if cur is None or prio < cur[0] or (prio == cur[0] and ts > cur[1]):
                best[k] = (prio, ts, it)
    # 删除也按同规则裁决：只有"删除记录比保留记录更权威"时才隐藏 ✓
    out: List[dict] = []
    for k, (prio, ts, it) in best.items():
        t = tomb.get(k)
        if t is not None and (t[0] < prio or (t[0] == prio and t[1] >= ts)):
            continue
        out.append(it)
    return out


def _save_my(items: List[dict]) -> None:
    """写**自己的**文件（原子替换 ✓）并触发公式库的 git 自动同步 ✓。"""
    os.makedirs(os.path.dirname(_MY_FILE) or ".", exist_ok=True)
    tmp = _MY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _MY_FILE)
    # ★ v1.20.63：公式库是**用户资产且纳入版本控制** ✓ ⇒ 每次写盘后自动提交+推送 ✓
    #   ⚠ 非阻塞 + 不抛 ✓（同步失败绝不影响保存本身 ✓）
    _formula_git_sync.schedule("保存公式库")


def refresh_stale_expressions(items: List[dict]) -> bool:
    """★ v1.20.59：把 `codegen` 戳**过期的条目按 `text` 重新编译**（原地改 `items` ✓），返回是否有改动 ✓。

    ⓘ **纯函数式**：只动传入的列表、**不碰磁盘** ✓（便于单测 ✓）。
    ⚠ 编译失败 ⇒ **保留旧 expression** 并跳过（不推进戳 ✓ ⇒ 下次还会再试 ✓）——
      历史坏公式绝不该让整个列表接口 500 ✗，也不该被静默标记为已同步 ✗。
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


def list_custom_formulas() -> List[dict]:
    """列出全部公式（**合并所有来源** ✓）—— ★ v1.20.59 起顺带**刷新陈旧 expression** ✓。

    ⚠ 这一步是 GET 却会写盘 ✗ —— 但它是**缓存刷新**（`expression` 完全由 `text` 决定 ✓），不改用户语义 ✓；
      换来的是「**不必手动重存公式**」✓（否则单因子测试会一直吃到旧串 ✗）。
    ★ v1.20.65：重编结果**只写进自己那份文件**（作为覆盖副本 ✓）⇒ 不会去动别人的文件 ✓✓（避开冲突源 ✓）。
    """
    with _lock:
        items = load_merged()
        if refresh_stale_expressions(items):
            mine = _read_items(_MY_FILE)
            mine_keys = {_key(it) for it in mine if not it.get("deleted")}
            for it in items:
                if (it.get("codegen") or "") != CODEGEN_SEMANTICS:
                    continue
                # 只回写"过期重编过"的条目：自己文件里已有 ⇒ 原地替换 ✓；否则追加覆盖副本 ✓
                if _key(it) in mine_keys:
                    mine = [it if _key(x) == _key(it) else x for x in mine]
                else:
                    mine.append(it)
            _save_my(mine)
        return items


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_custom_formula(name: str, text: str, expression: str) -> dict:
    """新建：追加到**自己的**文件 ✓。"""
    with _lock:
        items = _read_items(_MY_FILE)
        item = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "text": text,
            "expression": expression,
            "codegen": CODEGEN_SEMANTICS,      # ★ 新存的必然是当前生成逻辑 ✓
            "created_at": _now(),
            "updated_at": _now(),
            "author": _USER,                   # ★ v1.20.65：标出作者（排查/展示用 ✓）
        }
        items.append(item)
        _save_my(items)
        return item


def update_custom_formula(formula_id: str, name: str, text: str, expression: str) -> Optional[dict]:
    """修改：**写进自己的文件** ✓（若是别人的条目 ⇒ 存一份覆盖副本 ✓，不去动别人的文件 ✓）。"""
    with _lock:
        merged = {_key(it): it for it in load_merged()}
        cur = merged.get("id:" + formula_id)
        if cur is None:
            return None
        item = dict(cur)
        item["name"] = name
        item["text"] = text
        item["expression"] = expression
        item["codegen"] = CODEGEN_SEMANTICS     # ★ 手动保存 ⇒ 必然是最新的生成逻辑 ✓
        item["updated_at"] = _now()
        item["author_modified_by"] = _USER
        mine = _read_items(_MY_FILE)
        if any(_key(x) == "id:" + formula_id for x in mine):
            mine = [item if _key(x) == "id:" + formula_id else x for x in mine]
        else:
            mine.append(item)
        _save_my(mine)
        return item


def delete_custom_formula(formula_id: str) -> bool:
    """删除：自己的条目 ⇒ 直接从自己文件移除 ✓；别人的条目 ⇒ 写 **tombstone（删除标记）** ✓。"""
    with _lock:
        merged = {_key(it): it for it in load_merged()}
        if ("id:" + formula_id) not in merged:
            return False
        mine = _read_items(_MY_FILE)
        if any(_key(x) == "id:" + formula_id for x in mine):
            mine = [x for x in mine if _key(x) != "id:" + formula_id]
        else:
            mine.append({"id": formula_id, "deleted": True, "updated_at": _now(), "author": _USER})
        _save_my(mine)
        return True


def save_as_my(items: List[dict]) -> None:
    """整体覆盖写入自己的文件（**仅供迁移/运维脚本**用它把老单文件搬过来 ✓）。"""
    with _lock:
        _save_my(items)
