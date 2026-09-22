# -*- coding: utf-8 -*-
"""
回测产物（artifacts）服务层。

职责：处理与 artifacts 目录相关的文件扫描 / JSON 解析 / 图片定位 / 删除等业务逻辑。
从 routers/backtest.py 抽取而来，保持路由层薄、只负责 HTTP 映射。

所有返回数据为纯 dict/list，由路由层负责转为 HTTPException。
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
from typing import Optional

from .. import config
from ..logger import get_logger

logger = get_logger(__name__)


class ArtifactNotFoundError(Exception):
    """产物目录或文件不存在。"""


def artifacts_root() -> str:
    return os.path.join(config.WORK_DIR, "artifacts")


def _direct_artifact_dir(task_id: str) -> Optional[str]:
    """只按任务 id **直接**找目录（新命名 `*_<id>` ✓ / 旧命名 `<id>` ✓）。"""
    dirs = glob.glob(os.path.join(artifacts_root(), "*_" + task_id))
    if dirs:
        return dirs[0]
    old = os.path.join(artifacts_root(), task_id)
    return old if os.path.isdir(old) else None


def _resume_of(task_id: str) -> Optional[str]:
    """若该任务是"续测"⇒ 返回它复用的**源** task_id（读内存态 ✓，取不到返回 None ✓）。"""
    try:
        # ⚠ 惰性 import ✗：模块级 import task_manager 会形成循环依赖（task_manager → 本模块的调用链）
        from ..engine.task_manager import get_task_manager
        req = get_task_manager(config.WORK_DIR).get_req(task_id)
        rid = getattr(req, "resume_task_id", None) if req else None
        return str(rid) if rid else None
    except Exception:                                          # noqa: BLE001
        return None


def _resume_chain(task_id: str, depth: int = 3) -> list:
    """该任务的"续测源"链条（最多 depth 层 ✓，带环检测 ✓；续测可以套续测 ✓）。"""
    out, seen, cur = [], {str(task_id)}, str(task_id)
    for _ in range(max(1, int(depth))):
        nxt = _resume_of(cur)
        if not nxt or nxt in seen:
            break
        out.append(nxt)
        seen.add(nxt)
        cur = nxt
    return out


def _dir_with_resume_marker(task_id: str) -> Optional[str]:
    """扫 `artifacts/*/resume_tasks.json` ⇒ 找"记录过该续测任务"的目录 ✓（**跨重启有效** ✓）。"""
    try:
        for d in glob.glob(os.path.join(artifacts_root(), "*")):
            if not os.path.isdir(d):
                continue
            p = os.path.join(d, "resume_tasks.json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:                                  # noqa: BLE001
                continue
            ids = data.get("task_ids") if isinstance(data, dict) else data
            if isinstance(ids, list) and str(task_id) in [str(x) for x in ids]:
                return d
    except Exception:                                          # noqa: BLE001
        pass
    return None


def note_resume_task(src_dir: str, task_id: str) -> None:
    """记下"某续测任务复用了本目录" ⇒ 写 `<src_dir>/resume_tasks.json` ✓（失败不抛 ✓）。

    为什么需要落盘 ✗：内存态（`TaskManager._reqs`）**重启就没了** ✓ ⇒ 只靠内存回退的话，
    重启后再查续测任务的产物又会 404 ✗。落一个极小的标记文件即可跨重启 ✓（只写几个 id ✓）。
    """
    import time as _time
    if not src_dir or not task_id:
        return
    p = os.path.join(src_dir, "resume_tasks.json")
    try:
        data = {}
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:                                  # noqa: BLE001
                data = {}
        ids = data.get("task_ids") if isinstance(data, dict) else None
        ids = list(ids) if isinstance(ids, list) else []
        if str(task_id) not in [str(x) for x in ids]:
            ids.append(str(task_id))
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"task_ids": ids, "updated_at": _time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=1)
    except Exception:                                          # noqa: BLE001
        logger.warning("写 resume_tasks.json 失败: %s", p)


def find_artifact_dir(task_id: str) -> Optional[str]:
    """根据 task_id 找到产物目录（新目录名以 `*_task_id` 结尾；兼容旧版直接用 task_id 命名）。

    ★★ v1.20.49 **续测回退** ✗：**断点续跑（resume）复用源任务目录**（目录名后缀是**源** task_id ✓）
    ⇒ 用**续测任务自己的 id** 查目录必然落空 ✗ ⇒ 于是 `/artifacts`、`/features`、`/snapshot`、
    `/result`、`/image/*` 这些产物接口在**续测任务运行期间全部 404** ✗（2026-09-22 实测 ✓：
    `/backtest/{id}` 200 且带 partial ✓，其余全 404 ✗ —— 用户问"训练中看不了产物？"就是这个 ✓）。
    ⚠ `routers/backtest.py` 的 `/backtest/{id}` **早就单独做了 `resume_task_id` 回退** ✓
    ⇒ 两处不一致 ✗ ⇒ 在本函数补齐，**一次修好所有产物接口** ✓（它们都走这里 ✓）。
    回退顺序（均在"直接找不到"之后 ✓）：
      a. **内存**：`TaskManager` 里该任务 req 的 `resume_task_id` ⇒ 用源 id 再找 ✓；
      b. **磁盘**：`artifacts/*/resume_tasks.json`（运行期由 `qlib_engine` 写 ✓）⇒ **重启后也有效** ✓。
    """
    base = _direct_artifact_dir(task_id)
    if base:
        return base
    # ★ **先直接查磁盘标记** ✓ —— 它是"这个 id 的产物在哪个目录"的**权威答案** ✓
    #   ⚠ 不能只把它挂在内存链上 ✗：内存态一重启就没了 ✓（单测就是这么抓出来的 ✓
    #   2026-09-22：`test_resume_marker_fallback` 失败 ⇒ 标记根本没被查到 ✗）。
    hit = _dir_with_resume_marker(task_id)
    if hit:
        return hit
    for rid in _resume_chain(task_id):
        hit = _direct_artifact_dir(rid) or _dir_with_resume_marker(rid)
        if hit:
            return hit
    return None


def _seg_no_of_dir(path: str) -> int:
    """从 `.../segment_12` 解出**数字**段号 ✓（解不出 ⇒ 返回极大值 ⇒ 排到最后 ✓）。"""
    m = re.search(r"segment_(\d+)$", os.path.basename(str(path).rstrip("/\\")))
    return int(m.group(1)) if m else 10 ** 9


def _sorted_segment_dirs(base: str) -> list:
    """按**数字段号**排序段目录 ✓（★ v1.20.50）。

    ⚠ 原来用 `sorted(glob(...))` = **字符串排序** ✗ ⇒ `segment_1, segment_10, segment_11, …, segment_2,
    segment_20, …` ✗ ⇒ 前端段选择器按**数组下标**显示「段 N」时**编号全错** ✗（用户 2026-09-22 实测：
    "下拉里的『段 3』其实是 seg11" ✓）。数字段号也直接来自这里的顺序 ✓。
    """
    return sorted(glob.glob(os.path.join(base, "segment_*")), key=_seg_no_of_dir)


def load_model_artifacts(task_id: str) -> dict:
    """返回该回测任务训练得到的模型交付物。滚动训练返回 {segments:[...]}；single 返回单段。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有可用的模型交付物")

    seg_dirs = _sorted_segment_dirs(base)
    if seg_dirs:
        segments = []
        for sd in seg_dirs:
            af = os.path.join(sd, "model_artifacts.json")
            if not os.path.exists(af):
                continue
            try:
                with open(af, "r", encoding="utf-8") as f:
                    data = json.load(f)
                mt = os.path.join(sd, "model.txt")
                if os.path.exists(mt) and not data.get("model_file"):
                    with open(mt, "r", encoding="utf-8", errors="ignore") as f:
                        data["model_file"] = f.read()
                segments.append(data)
            except Exception as e:
                logger.warning("读取段模型交付物失败 %s: %s", sd, e)
                continue
        if not segments:
            raise ArtifactNotFoundError(f"任务 {task_id} 没有可用的模型交付物")
        return {"segments": segments}

    artifact_file = os.path.join(base, "model_artifacts.json")
    if not os.path.exists(artifact_file):
        raise ArtifactNotFoundError(f"任务 {task_id} 没有可用的模型交付物")
    try:
        with open(artifact_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        model_txt = os.path.join(base, "model.txt")
        if os.path.exists(model_txt) and not data.get("model_file"):
            with open(model_txt, "r", encoding="utf-8", errors="ignore") as f:
                data["model_file"] = f.read()
        return data
    except Exception as e:
        logger.error("读取交付物失败 %s: %s", task_id, e)
        raise ArtifactNotFoundError(f"读取交付物失败: {e}")


def build_feature_formulas(params: dict, feature_names: list) -> list:
    """把「训练时用的特征名」映射成「名称 + 公式」二元组（纯函数，便于单测）。

    第二列（`formula`）按来源给**用户要的那一个**：
      · `A158_x` / `A360_x`（混合模式的前缀名）⇒ 因子目录里该特征对应的 **qlib 表达式**；
      · 自定义公式（名字 = `translate_formula(原文).name`）⇒ **用户保存的公式原文**
        （⚠ 用户 2026-09-16 明确要求："如果是用户自定义的公式，第二列是用户前端保存的公式，
          **不是**转译后的公式"）；转译后的 qlib 表达式另放在 `qlib_expr` 里备查；
      · 非混合的单一特征集模式（特征名没有前缀）⇒ 先查自定义公式，再查 Alpha158 / Alpha360 目录；
      · 都查不到 ⇒ `kind="未知"`、`formula=""`（宁可留空，也不瞎猜）。
    用途：前端「特征列表」出表 + 下载 CSV（`特征名,公式`）。
    """
    from ..factors.catalog import get_catalog
    from ..factors.parser import translate_formula, build_library

    def _flat(dataset: str) -> dict:
        try:
            return {r["name"]: (r.get("expression") or "")
                    for r in (get_catalog(dataset).get("flat") or [])}
        except Exception:                         # 目录不可用 ⇒ 退化为"未知"，不影响其它列
            return {}

    a158, a360 = _flat("Alpha158"), _flat("Alpha360")

    custom = {}                                   # 翻译后的名字 -> (用户原文, qlib 表达式)
    _texts = params.get("custom_formulas") or []
    _lib = build_library(_texts)                  # 公式间调用（v1.19.87）：名字映射要与回测一致
    for text in _texts:
        try:
            t = translate_formula(text, library=_lib)
            custom[t.name] = (text, t.expression)
        except Exception:
            continue                              # 翻译失败的历史公式：跳过（不阻塞其它列）

    items = []
    for name in (feature_names or []):
        kind, key, formula, qexpr = "未知", name, "", ""
        if name.startswith("A158_"):
            key = name[len("A158_"):]
            if key in a158:
                kind, formula, qexpr = "Alpha158", a158[key], a158[key]
        elif name.startswith("A360_"):
            key = name[len("A360_"):]
            if key in a360:
                kind, formula, qexpr = "Alpha360", a360[key], a360[key]
        if kind == "未知" and name in custom:
            kind = "自定义公式"
            formula, qexpr = custom[name][0], custom[name][1]
        if kind == "未知" and name in a158:
            kind, formula, qexpr = "Alpha158", a158[name], a158[name]
        if kind == "未知" and name in a360:
            kind, formula, qexpr = "Alpha360", a360[name], a360[name]
        items.append({"name": name, "formula": formula, "kind": kind, "qlib_expr": qexpr})
    return items


def load_feature_formulas(task_id: str) -> dict:
    """读取某次回测的「特征名 ↔ 公式」对照（供前端特征表展示 + 下载 CSV）。

    特征名取自该次训练的交付物 `model_artifacts.json`（滚动回测取第一段；各段特征完全相同）；
    公式来源见 `build_feature_formulas`。失败即抛 ArtifactNotFoundError（前端已有兜底）。
    """
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有产物目录")
    params: dict = {}
    pfile = os.path.join(base, "params.json")
    if os.path.exists(pfile):
        try:
            with open(pfile, "r", encoding="utf-8") as f:
                params = json.load(f)
        except Exception as e:
            logger.warning("读取 params.json 失败 %s: %s", task_id, e)

    names: list = []
    candidates = [os.path.join(base, "model_artifacts.json")]
    candidates += [os.path.join(d, "model_artifacts.json")
                   for d in sorted(glob.glob(os.path.join(base, "segment_*")))]
    for af in candidates:
        if not os.path.exists(af):
            continue
        try:
            with open(af, "r", encoding="utf-8") as f:
                names = (json.load(f) or {}).get("feature_names") or []
        except Exception as e:
            logger.warning("读取交付物失败 %s: %s", af, e)
        if names:
            break

    return {
        "task_id": task_id,
        "dir_name": os.path.basename(base),
        "feature": params.get("feature"),
        "feature_mode": params.get("feature"),
        "price_adjust": params.get("price_adjust") or "none",
        "count": len(names),
        "items": build_feature_formulas(params, names),
    }


def load_snapshot(task_id: str) -> dict:
    """返回该回测任务的产物目录信息（含曲线/参数快照图、参数、meta、段目录）。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有产物目录")

    info = {
        "task_id": task_id,
        "dir_name": os.path.basename(base),
        "params": None,
        "meta": None,
        "images": {},
        "segments": [os.path.basename(d) for d in sorted(glob.glob(os.path.join(base, "segment_*")))],
        # 信号合成归因（v1.19.72）：Meta-Gate 的 gain 占比 + 单因子 ablation（"哪个风控因子更好"）
        "compose": None,
    }
    pfile = os.path.join(base, "params.json")
    if os.path.exists(pfile):
        try:
            with open(pfile, "r", encoding="utf-8") as f:
                info["params"] = json.load(f)
        except Exception as e:
            logger.warning("读取 params.json 失败 %s: %s", task_id, e)
    mfile = os.path.join(base, "meta.json")
    if os.path.exists(mfile):
        try:
            with open(mfile, "r", encoding="utf-8") as f:
                info["meta"] = json.load(f)
        except Exception as e:
            logger.warning("读取 meta.json 失败 %s: %s", task_id, e)
    for name in ["nav_curve.png", "params_snapshot.png"]:
        if os.path.exists(os.path.join(base, name)):
            info["images"][name] = name
    cfile = os.path.join(base, "compose.json")
    if os.path.exists(cfile):
        try:
            with open(cfile, "r", encoding="utf-8") as f:
                info["compose"] = json.load(f)
        except Exception as e:
            logger.warning("读取 compose.json 失败 %s: %s", task_id, e)
    return info


def load_result(task_id: str) -> dict:
    """读取持久化的回测完整结果（指标/净值/调仓记录），并清理 NaN/Infinity。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有产物目录")
    rfile = os.path.join(base, "result.json")
    if not os.path.exists(rfile):
        raise ArtifactNotFoundError(f"任务 {task_id} 没有持久化的结果")
    try:
        with open(rfile, "r", encoding="utf-8") as f:
            data = json.load(f)
        from ..engine.qlib_engine import _sanitize_json
        return _sanitize_json(data)
    except Exception as e:
        logger.error("读取结果失败 %s: %s", task_id, e)
        raise ArtifactNotFoundError(f"读取结果失败: {e}")


def resolve_image_path(task_id: str, name: str) -> Optional[str]:
    """定位产物图片文件绝对路径（防目录穿越）。不存在返回 None。"""
    base = find_artifact_dir(task_id)
    if base is None:
        raise ArtifactNotFoundError(f"任务 {task_id} 没有产物目录")
    safe = os.path.basename(name)
    fpath = os.path.join(base, safe)
    if not os.path.exists(fpath):
        raise ArtifactNotFoundError(f"图片 {safe} 不存在")
    return fpath


def scan_history() -> dict:
    """扫描 artifacts 目录下所有回测产物，作为历史回测列表返回（跨重启/跨版本）。

    同时附带 `is_task_running` 字段：从 task_manager 检查任务是否正在内存中运行，
    前端据此判断能否删除（未运行 → 可删除；运行中 → 禁用）。
    """
    from ..engine.task_manager import get_task_manager  # 避免循环 import
    from .. import config as _config

    root = artifacts_root()
    if not os.path.isdir(root):
        return {"items": [], "retention": None}

    _manager = get_task_manager(_config.WORK_DIR)

    # 正在运行/排队/取消中的任务所复用的源 task_id 集合。
    # 续测任务复用源 artifacts 目录（目录名后缀是源 task_id），此时源目录对应的历史行也应视为"运行中"，
    # 前端据此禁用删除（否则删除会破坏正在被续测写入的目录）。
    resume_sources = set()
    try:
        resume_sources = _manager.running_resume_sources()
    except Exception:
        pass

    items = []
    for name in sorted(os.listdir(root), reverse=True):
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue

        params_file = os.path.join(full, "params.json")
        result_file = os.path.join(full, "result.json")
        meta_file = os.path.join(full, "meta.json")

        params = None
        if os.path.exists(params_file):
            try:
                with open(params_file, "r", encoding="utf-8") as f:
                    params = json.load(f)
            except Exception as e:
                logger.warning("扫描历史时解析 params.json 失败 %s: %s", name, e)
                params = None
        meta = None
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                logger.warning("扫描历史时解析 meta.json 失败 %s: %s", name, e)
                meta = None
        # 年化收益（历史列表展示）：从 result.json 顶层读取；无结果则为 None（前端显示"-"）
        annual_return = None
        if os.path.exists(result_file):
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    annual_return = json.load(f).get("annualized_return")
            except Exception:
                annual_return = None

        parts = name.rsplit("_", 1)
        if len(parts) == 2 and re.match(r"^[0-9a-f]{12,}$", parts[1]):
            task_id = parts[1]
        else:
            task_id = name

        images = {}
        for img in ("nav_curve.png", "params_snapshot.png", "summary.png"):
            if os.path.exists(os.path.join(full, img)):
                images[img] = img
        segments = sorted(os.path.basename(d) for d in glob.glob(os.path.join(full, "segment_*")))

        meta_summary = {}
        segs = name.split("_")
        if len(segs) >= 6:
            meta_summary = {
                "model": segs[1],
                "universe": segs[2],
                "start_year": segs[3],
                "end_year": segs[4],
            }
        elif meta and isinstance(meta, dict):
            meta_summary = {
                "model": meta.get("模型"),
                "universe": meta.get("股票池"),
                "start_year": (meta.get("起始日期") or "")[:4],
                "end_year": (meta.get("结束日期") or "")[:4],
            }

        has_artifacts = os.path.exists(os.path.join(full, "model_artifacts.json")) or any(
            os.path.exists(os.path.join(full, sd, "model_artifacts.json"))
            for sd in segments
        )

        # 读取稳定序号（seq.json），没有则为 None（旧任务）
        seq = None
        seq_file = os.path.join(full, "seq.json")
        if os.path.exists(seq_file):
            try:
                with open(seq_file, "r", encoding="utf-8") as f:
                    seq = int(json.load(f).get("seq", 0))
            except Exception:
                seq = None

        items.append({
            "task_id": task_id,
            "dir_name": name,
            "seq": seq,
            "has_params": os.path.exists(params_file),
            "has_result": os.path.exists(result_file),
            "has_meta": os.path.exists(meta_file),
            "has_artifacts": has_artifacts,
            "images": images,
            "segments": segments,
            "meta_summary": meta_summary,
            "annual_return": annual_return,
            # 任务是否正在内存中运行（用于前端判断"能否删除"——运行中禁用删除）。
            # 额外检查是否正被某个运行中任务续测占用（续测复用源目录，源 task_id 不在内存但目录被占用）
            "is_task_running": _is_task_running(_manager, task_id) or (task_id in resume_sources),
        })
    # v1.19.13：附带**产物回收预测**（只读，带 30s 缓存）——前端在「历史回测」标题右侧提示
    # "哪些产物将被回收 / 预计几天后"，避免再出现"产物无声消失"（用户要求）。
    retention = None
    try:
        from ..engine.storage_cleanup import preview_artifacts_recycle

        # ⚠ 不传 root：走默认根目录才会命中 60s 缓存（`preview_artifacts_recycle` 的缓存只在
        #   root is None 时生效）。`artifacts_root()` 与 `storage_cleanup._work_dir()` 同为
        #   `config.WORK_DIR/artifacts` ⇒ 路径一致。实测：不传 0.0s（命中缓存）/ 传了每次 2.1s。
        retention = preview_artifacts_recycle()
    except Exception as e:  # 预测失败不影响列表
        logger.warning("产物回收预测失败: %s", e)
    return {"items": items, "retention": retention}


def _is_task_running(manager, task_id: str) -> bool:
    """判断任务是否正在内存里运行（running/pending/cancelling）。

    这些状态的任务不能删除产物目录。已结束（success/failed/cancelled）
    或不在内存的（重启后）则返回 False，可删除。
    """
    t = manager.get(task_id)
    if t is None:
        return False
    return t.status in ("running", "pending", "cancelling")


def delete_artifacts(task_id: str) -> Optional[str]:
    """删除某个回测的产物目录。返回被删除的目录名；目录不存在抛 ArtifactNotFoundError。"""
    base = find_artifact_dir(task_id)
    if base is None or not os.path.isdir(base):
        raise ArtifactNotFoundError(f"任务 {task_id} 产物目录不存在")
    # 防误删：只允许删除 artifacts 目录下的子目录
    artifacts_root_path = os.path.abspath(artifacts_root())
    target = os.path.abspath(base)
    if os.path.dirname(target) != artifacts_root_path:
        raise ArtifactNotFoundError("拒绝删除非产物目录")
    shutil.rmtree(target, ignore_errors=True)
    return os.path.basename(base)
