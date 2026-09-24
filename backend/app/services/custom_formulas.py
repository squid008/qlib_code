# -*- coding: utf-8 -*-
"""自定义公式持久化：★ v1.20.69 起 = **每装机一个文件 + 只增不减（时间戳裁决）** 的合并模型。

【演化史（目标始终是同一件事：两人各自改公式后 `git pull` 别冲突 ✗、也别互相遮蔽 ✗）】
  · v1.20.65：原先所有人共用 `workdir/custom_formulas.json` ✗（且该文件纳入 git ✓）⇒
    两人各存一次就**必然** JSON 整文件冲突 ✗（没有 `.gitattributes` ✓、逐行合并对 JSON 无效 ✗）。
    改法：**每人一个文件** `formulas/<用户名>.json` ✓ + 读取时合并 ✓。**但仍有两个洞**
    （2026-09-24 用户追问 + 审计实锤 ✓）：
      ① 隔离**依赖"两端用户名不同"**这个不可靠假设 ✗ —— 两台机器都叫 `admin`/`Administrator`
        （或同一账号在两台登录）时，两个后端写**同一个文件** ⇒ pull 又回到整文件冲突 ✗；
      ② 旧合并规则是**身份优先**（自己 0 > 别人 1 > 老文件 2，且"自己"**恒胜**、连 `updated_at`
        都不看 ✗），而 `list_custom_formulas` 重编时会把**别人的条目也抄进自己文件** ✗
        ⇒ 只要 `CODEGEN_SEMANTICS` 升一次（这种升级很频繁 ✓）就能把对方**全部条目**抄成
        "我的高优先副本" ⇒ 此后对方再改这些公式，我这边**永远看不到** ✗✗。
  · **v1.20.69（本版）**：
      · **写入单位 = 装机**：`formulas/<install_id>.json`，`install_id` 首次运行随机生成并落盘
        `workdir/.install_id`（**与用户名无关** ✓）⇒ 两台机器**永不共用文件** ⇒ git 层面只会
        新增/修改**自己的**文件 ⇒ **pull 不可能覆盖** ✓✓；
      · **裁决 = 时间戳**：同 `id` ⇒ `updated_at` 新者胜 ✓；平局 ⇒ 非老单文件者优先 ✓、再按**文件名**
        字典序（纯为确定性 ✓）—— **不再看"是不是自己"** ✗；
      · **删除 = 带戳 tombstone**：只**屏蔽**、不物理删 ✓ ⇒ 数据"只增不减"，之后被再编辑
        （时间戳更新 ✓）还能"复活" ✓；
      · **别人的条目只读、不回写** ✓（掐掉遮蔽源 ✓，`GET` 的副作用只落在自己文件里 ✓）。
  · 读取始终**合并所有来源** ✓（本装机文件 > 其它装机文件 > 老单文件；后者只在"时间戳相同时"让步 ✓）。

【写操作只动自己的文件 ✓】
  新建 ⇒ 追加 ✓；改**别人的**条目 ⇒ 自己文件里存一份**同 id 覆盖副本**（`updated_at` 更新 ⇒ 按时间戳胜出 ✓）；
  删（无论谁的）⇒ 自己文件里写一条 **tombstone `{"id":..., "deleted": true, "updated_at": ...}`** ✓。
  ⚠ 老的单文件 `workdir/custom_formulas.json` **只读、不再写** ✓（平滑迁移，见 `ensure_migrated` ✓）。

★ v1.20.59：`expression` 是**编译产物（缓存）** ✓ ⇒ 生成逻辑变了它就陈旧 ✗（**单因子测试**直接求值这个缓存 ✗，
  回测则按 `text` 重译 ✓，见 `parser/codegen.CODEGEN_SEMANTICS` 的长注释 ✓）⇒ `list_custom_formulas()`
  读到时**自动按原文重编** ✓✓，用户不必手动重存 ✓；但重编结果**只回写"自己文件里已有的"条目** ✓
  （别人的只返回、不落盘 ✓ —— 旧实现 `mine.append(it)` 会把别人的条目抄进来 ⇒ 永久遮蔽 ✗）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..config import WORK_DIR
from ..factors.parser.codegen import CODEGEN_SEMANTICS
from ..logger import _Utf8SafeStream                    # ★ v1.20.63：安全打印（GBK 下不抛 ✓）
from . import formula_git_sync as _formula_git_sync      # ★ v1.20.63：写盘后自动提交+推送 ✓

# ⚠⚠ 本模块的消息**必须**走 `_log()`：Windows 下后端 stdout 常是 **GBK** ✗ ⇒ 直接
#   `print()` 带 `⚠ ⇢ ✓ ↻` 会抛 `UnicodeEncodeError` ✗✓ —— 2026-09-24 实测：
#   **迁移时的那条消息**（含 `⇢`/`✓`）把 `GET /api/factors/custom-formulas` 直接打成
#   **500** ✗✗；更糟的是**错误分支那句还带 `⚠`** ⇒ 兜底打印**同样炸** ✗ ⇒ 异常逃出
#   `ensure_migrated` 的 `except` ⇒ 整个接口挂掉 ✗。
_SAFE_OUT = _Utf8SafeStream(sys.stdout)


def _log(msg: str) -> None:
    """安全打印 ✓（按 UTF-8 写底层 buffer、永不抛 ✓ —— 打印绝不参与主流程 ✓）。"""
    try:
        _SAFE_OUT.write("[formulas] " + msg + "\n")
        _SAFE_OUT.flush()
    except Exception:                                        # noqa: BLE001
        pass

# 公式目录（本模块**只往这里写** ✓；每个装机一个 `.json` ✓）
_FORMULAS_DIR = os.path.join(WORK_DIR, "formulas")
# 老的单文件：**只读**（兼容历史 ✓，不再写 ✗）
_CUSTOM_FORMULAS_PATH = os.path.join(WORK_DIR, "custom_formulas.json")
# ★ v1.20.69：本装机 ID 的落盘文件（放在 `workdir/` 下 ⇒ 被 `.gitignore` 的 `workdir/` 排除 ✓，
#   **绝不能**随 git 传播 ✗ —— 传过去两台机器就会写同一个文件，又回到冲突 ✗）
_INSTALL_ID_FILE = ".install_id"


def _sanitize(s: str) -> str:
    """只留文件名/JSON 里安全的字符 ✓（用户可能把 `QUANT_FORMULA_USER` 设成任意串 ✓）。"""
    return "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in s)


def _user_name() -> str:
    """登录用户名 —— **仅用于展示/兼容** ✓（v1.20.69 起**不再**决定写哪个文件 ✗）。"""
    for k in ("USERNAME", "USER", "LOGNAME"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return _sanitize(v)
    return "default"


def _install_id_path() -> str:
    """装机 ID 文件路径（与 `_FORMULAS_DIR` **同级**，便于单测随 `_FORMULAS_DIR` 一起隔离 ✓）。"""
    return os.path.join(os.path.dirname(_FORMULAS_DIR) or ".", _INSTALL_ID_FILE)


def _install_id() -> str:
    """★ v1.20.69：本**装机**的稳定标识（决定写哪个文件 ✓）—— 与"是谁"无关 ✓。

    ⚠ 为什么不再用用户名（v1.20.65 的做法 ✗）：`<用户名>.json` 的隔离**依赖"两端用户名不同"**
      这个不可靠假设 ✗ —— 两台机器都叫 `admin`/`Administrator`（或同账号在两台登录）时，
      两个后端会写**同一个文件** ⇒ `git pull` 又回到"整文件 JSON 冲突" ✗。
    ⇒ 改为**每装机一个随机 ID**（首次运行生成并落盘 ✓、此后**永不变** ✓）⇒ 两台机器**永不共用
      文件** ⇒ git 层只新增/改自己的文件 ⇒ **pull 不可能覆盖** ✓✓。
    优先级：`QUANT_FORMULA_USER`（显式指定 ✓ 便于迁移/同机多开 ✓）> 落盘的 `.install_id`。
    """
    env = (os.environ.get("QUANT_FORMULA_USER") or "").strip()
    if env:
        return _sanitize(env)
    p = _install_id_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            v = (f.read() or "").strip()
        if v:
            return _sanitize(v)
    except OSError:
        pass
    v = "u-" + uuid.uuid4().hex[:8]
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(v + "\n")
        os.replace(tmp, p)          # 原子替换 ✓（并发首启时双方各写一个随机值，后写者胜，无害 ✓）
    except OSError:
        pass
    return v


_USER = _user_name()                      # 展示/兼容（老 `<用户名>.json` 的迁移判定用 ✓）
_INSTALL_ID = _install_id()               # ★ 本装机 ID（稳定 ✓）
_AUTHOR = _INSTALL_ID                     # 写进条目的作者标识（稳定且唯一 ✓）
# ★ 当前装机的文件（**唯一写入目标** ✓）。单测可 patch 本变量 ✓。
_MY_FILE = os.path.join(_FORMULAS_DIR, _INSTALL_ID + ".json")

_lock = threading.Lock()


def my_file() -> str:
    """当前装机的公式文件名（供日志/排查 ✓）。"""
    return _MY_FILE


def install_id() -> str:
    """当前装机 ID（供日志/接口展示 ✓）。"""
    return _INSTALL_ID


def _read_items(path: str) -> List[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _sources() -> List[Tuple[str, List[dict], int]]:
    """所有来源 `(路径, 条目, prio)` ✓；`prio` 只在**时间戳相同时**当决胜项（0 = 普通装机文件 ✓，1 = 老单文件 ✓）。"""
    mine = [(os.path.abspath(_MY_FILE), _read_items(_MY_FILE), 0)]
    others: List[Tuple[str, List[dict], int]] = []
    try:
        for fn in sorted(os.listdir(_FORMULAS_DIR)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(_FORMULAS_DIR, fn)
            if os.path.abspath(p) == os.path.abspath(_MY_FILE):
                continue
            others.append((p, _read_items(p), 0))
    except OSError:
        pass
    legacy = [(os.path.abspath(_CUSTOM_FORMULAS_PATH), _read_items(_CUSTOM_FORMULAS_PATH), 1)]
    return mine + others + legacy


def _key(it: dict) -> str:
    """条目身份：优先 `id` ✓，老数据没有 id 时退化为 `name` ✓。"""
    return "id:" + it["id"] if it.get("id") else "name:" + (it.get("name") or "")


def ensure_migrated() -> None:
    """★ **平滑迁移**（幂等 ✓）：把自己"历史上属于我"的公式搬进**本装机**的文件。

    ① `formulas/<登录用户名>.json`（v1.20.65 的旧方案里"我的"那份 ✓）⇒ **改名**成本装机文件 ✓
       （改名失败则复制 ✓）—— 让它继续"属于我"，而不是退化成"别人的文件" ✓；
    ② 老共享单文件 `workdir/custom_formulas.json`（v1.20.65 之前 ✓）⇒ 搬一份进本装机文件 ✓。

    ⚠ **绝不搬别人的** ✗：`<别人的用户名>.json` 与其它装机文件都靠**读时合并**天然可见 ✓，
      抄进自己文件只会制造"高时间戳副本"⇒ 反过来遮蔽别人 ✗（这正是 v1.20.69 要消灭的行为 ✓）。
    """
    try:
        if os.path.exists(_MY_FILE):
            return
        # ① v1.20.65 的"按人文件"里，文件名等于**登录用户名**的那份曾是我的 ✓
        legacy_mine = os.path.join(_FORMULAS_DIR, _USER + ".json")
        if _USER and os.path.abspath(legacy_mine) != os.path.abspath(_MY_FILE) \
                and os.path.isfile(legacy_mine):
            try:
                os.makedirs(_FORMULAS_DIR, exist_ok=True)
                os.replace(legacy_mine, _MY_FILE)
                _log("已把 v1.20.65 的按人文件 %s 改名为本装机文件 %s"
                     % (os.path.basename(legacy_mine), os.path.basename(_MY_FILE)))
                return
            except OSError:
                items = _read_items(legacy_mine)
                if items:
                    _save_my(items)
                    _log("改名失败 ⇒ 已复制旧按人文件（%d 条）到本装机文件"
                         % len(items))
                    return
        # ② 老共享单文件 ⇒ 搬一份进本装机文件 ✓
        legacy = _read_items(_CUSTOM_FORMULAS_PATH)
        if not legacy:
            return
        _save_my([x for x in legacy if isinstance(x, dict) and not x.get("deleted")])
        _log("已把老公式库（%d 条）迁入 %s"
             % (len(legacy), os.path.basename(_MY_FILE)))
    except Exception as e:                                       # noqa: BLE001
        _log("迁移公式库失败（已忽略）：%r" % (e,))


def load_merged() -> List[dict]:
    """★ 合并所有来源（**只增不减 + 时间戳裁决** ✓）⇒ 去重后的条目列表 ✓。

    ⚠ 首次调用会先做一次**平滑迁移**（见 `ensure_migrated` ✓）。

    胜负规则（v1.20.69 起；**确定性、可测** ✓）：
      · 同 `id` ⇒ `updated_at` **较新**者胜 ✓（`"YYYY-MM-DD HH:MM:SS"` 定长 ⇒ 直接字符串比较 ✓；
        这比"文件 mtime"语义化得多 ✓ —— mtime 会被 checkout / 编辑器触碰改变 ✗）；
      · 时间戳相同 ⇒ `prio` 小者胜（装机文件 0 < 老单文件 1 ✓），再相同 ⇒ **文件名**字典序小者胜
        （纯粹为了确定性 ✓）；
      · ⚠ **不再看"是不是自己"** ✗ —— 旧规则「自己恒胜」+ "把别人的条目抄进自己文件"会让
        覆盖副本**永久遮蔽**对方的后续更新 ✗✗（详见模块头）；
      · **删除**用同一套裁决 ✓：tombstone 比条目"更权威"才隐藏 ⇒ 数据**只屏蔽不物理删** ⇒
        之后被再编辑（时间戳更新 ✓）还能**复活** ✓ —— 这就是"只增不减"的必然语义 ✓。
    """
    ensure_migrated()
    best: Dict[str, Tuple[str, int, str, dict]] = {}
    tomb: Dict[str, Tuple[str, int, str]] = {}
    for path, items, prio in _sources():
        for it in items:
            if not isinstance(it, dict):
                continue
            k = _key(it)
            ts = str(it.get("updated_at") or "")
            rank = (ts, -prio, path)                 # 大者胜 ✓
            if it.get("deleted"):
                cur = tomb.get(k)
                if cur is None or rank > cur:
                    tomb[k] = rank
                continue
            cur = best.get(k)
            if cur is None or rank > cur[:3]:
                best[k] = (ts, -prio, path, it)
    out: List[dict] = []
    for k, (ts, nprio, path, it) in best.items():
        t = tomb.get(k)
        if t is not None and t > (ts, nprio, path):
            continue                                 # 删除记录更权威 ⇒ 屏蔽（不物理删 ✓）
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
            _log("%s 重新编译失败，保留旧 expression：%s: %s"
                 % (it.get("name"), type(e).__name__, e))
            continue
        if t.expression != it.get("expression"):
            _log("%s 的 expression 已按原文重编（codegen %s）"
                 % (it.get("name"), CODEGEN_SEMANTICS))
        it["expression"] = t.expression
        it["codegen"] = CODEGEN_SEMANTICS
        changed = True
    return changed


def list_custom_formulas() -> List[dict]:
    """列出全部公式（**合并所有来源** ✓）—— ★ v1.20.59 起顺带**刷新陈旧 expression** ✓。

    ⚠ 这一步是 GET 却会写盘 ✗ —— 但它是**缓存刷新**（`expression` 完全由 `text` 决定 ✓），不改用户语义 ✓；
      换来的是「**不必手动重存公式**」✓（否则单因子测试会一直吃到旧串 ✗）。
    ★ v1.20.69：重编结果**只回写"自己文件里已有的"条目** ✓ —— 别人的条目**只返回、不落盘** ✓
      （旧实现会把别人的条目 `append` 进自己文件 ⇒ 变成"高时间戳副本"⇒ 永久遮蔽对方后续更新 ✗✗；
       现在别人的条目本次照样以**重编后的**内容返回 ✓，只是不写盘 ⇒ 下次再重编一次，幂等 ✓）。
    """
    with _lock:
        items = load_merged()
        if refresh_stale_expressions(items):
            mine = _read_items(_MY_FILE)
            mine_keys = {_key(it) for it in mine}
            for it in items:
                if (it.get("codegen") or "") != CODEGEN_SEMANTICS:
                    continue
                # ⚠ 只回写**自己文件里已有的**条目：原地替换 ✓（缺了就不动 —— 绝不 append 别人的 ✗）
                if _key(it) in mine_keys:
                    mine = [it if _key(x) == _key(it) else x for x in mine]
            _save_my(mine)
        return items


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_custom_formula(name: str, text: str, expression: str) -> dict:
    """新建：追加到**本装机**的文件 ✓。"""
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
            "author": _AUTHOR,                 # ★ v1.20.69：装机 ID（稳定唯一 ✓）
            "author_user": _USER,              # 登录用户名（仅展示 ✓）
        }
        items.append(item)
        _save_my(items)
        return item


def update_custom_formula(formula_id: str, name: str, text: str, expression: str) -> Optional[dict]:
    """修改：**写进本装机的文件** ✓（若是别人的条目 ⇒ 存一份**同 id 覆盖副本** ✓ + `updated_at` 更新 ⇒
    按时间戳裁决我这份更新 ⇒ 生效 ✓；**绝不去动别人的文件** ✓）。"""
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
        item["updated_at"] = _now()             # ★ 时间戳前进 ⇒ 按 LWW 胜出 ✓
        item["author_modified_by"] = _AUTHOR
        mine = _read_items(_MY_FILE)
        if any(_key(x) == "id:" + formula_id for x in mine):
            mine = [item if _key(x) == "id:" + formula_id else x for x in mine]
        else:
            mine.append(item)
        _save_my(mine)
        return item


def delete_custom_formula(formula_id: str) -> bool:
    """删除：**一律写带时间戳的 tombstone** ✓（只屏蔽、不物理删 ✓ —— "只增不减"的必然语义 ✓）。

    ⚠ 旧实现「自己的条目就直接从文件里移除」✗ 有个洞：若**别的装机**也持有同 id 的副本
      （比如对方曾改过这条 ✓），我删掉自己那份后，对方那份会**重新浮现** ✗。
      带戳 tombstone 能把它压住 ✓；而对方**之后**再编辑它（时间戳更新 ✓）又能"复活" ✓
      —— 这正是"谁最后改谁赢" ✓。
    """
    with _lock:
        merged = {_key(it): it for it in load_merged()}
        if ("id:" + formula_id) not in merged:
            return False
        mine = [x for x in _read_items(_MY_FILE) if _key(x) != "id:" + formula_id]
        mine.append({"id": formula_id, "deleted": True, "updated_at": _now(),
                     "author": _AUTHOR, "author_user": _USER})
        _save_my(mine)
        return True


def save_as_my(items: List[dict]) -> None:
    """整体覆盖写入本装机的文件（**仅供迁移/运维脚本**用它把老单文件搬过来 ✓）。"""
    with _lock:
        _save_my(items)
