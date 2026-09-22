# -*- coding: utf-8 -*-
"""回测产物/模型交付物模块（从 qlib_engine.py 拆分而来）。

包含：
  - 模型可复现交付物提取（公式/权重/超参数/特征/模型文件/特征重要性）
  - 模型对象与交付物持久化（mlflow artifact + 本地文件）
  - 复用模型的加载与特征顺序校验
  - 回测参数快照 / 结果 JSON 持久化
依赖 BacktestRequest / BacktestResult / context（产物目录），不依赖回测编排逻辑。
"""
import os
from typing import Any, Dict, List, Optional

from ..logger import get_logger
from ..models.backtest import BacktestRequest, BacktestResult
from .context import get_artifact_dir

logger = get_logger(__name__)


def _extract_model_artifacts(model, dataset, req: BacktestRequest, seg_label: str = "") -> dict:
    """提取模型的可复现交付物（公式/权重/超参数/特征/模型文件）。

    返回 dict，其中可能包含：
      - model_info: 模型类型、训练配置摘要
      - feature_names: 使用的特征列表
      - linear: 线性模型的系数与截距（coef_/intercept_）—— 即公式
      - params: 模型超参数
      - model_file: 树模型的序列化文本（LightGBM/XGBoost，可用于加载复现）
    """
    artifacts = {
        "model_info": {
            "model": req.model,
            "feature": req.feature,
            "topk": req.topk,
            "segment": seg_label,
        },
        "feature_names": [],
        "params": {},
        "linear": None,
        "model_file": None,
        "feature_importance": None,
        # ★★ v1.20.48：**训练口径戳** ✓（复用权重 / 断点续跑前比对 ✓ 见 `_verify_reuse_train_semantics`）
        #   为什么需要：`_verify_reuse_feature_order` 只比对**特征顺序** ✗ —— 训练代码改了
        #   （v1.20.46 把 `valid` 拆独立验证集 ✓、修分层前视 ✓、改标签口径 ✓）它照样放行 ✗
        #   ⇒ **静默吃旧口径模型** ✗（2026-09-22 实测事故：前 3 段是 `valid == train` 泄漏口径
        #   训的模型、后 60 段是新口径现场训练 ✓ ⇒ 同一张净值图混两套训练口径 ✗，而净值
        #   **累乘** ⇒ 整条曲线被抬高 ✗）。
        "train_semantics": _current_train_semantics(),
        "train_semantics_info": _train_semantics_info(req),
    }

    # 1) 特征列表（从 handler 取特征列名）
    try:
        handler = getattr(dataset, "handler", None)
        if handler is not None and hasattr(handler, "get_cols"):
            cols = handler.get_cols("feature")
            if cols:
                artifacts["feature_names"] = [str(c) for c in cols]
    except Exception:
        pass

    # 2) 线性模型：系数 + 截距 = 完整线性公式
    model_name = (req.model or "").lower()
    if "linear" in model_name:
        coef = getattr(model, "coef_", None)
        intercept = getattr(model, "intercept_", None)
        features = artifacts["feature_names"]
        if coef is not None:
            weights = [float(x) for x in coef]
            if features and len(weights) == len(features):
                artifacts["linear"] = {
                    "formula": "score = intercept + sum(w_i * feature_i)",
                    "intercept": float(intercept) if intercept is not None else 0.0,
                    "weights": weights,
                    "feature_weights": [{"feature": f, "weight": w} for f, w in zip(features, weights)],
                }
            else:
                artifacts["linear"] = {
                    "formula": "score = intercept + sum(w_i * feature_i)",
                    "intercept": float(intercept) if intercept is not None else 0.0,
                    "weights": weights,
                    "feature_weights": None,
                }

    # 3) 树模型（LightGBM/XGBoost）：保存模型文件 + 超参数
    elif "lightgbm" in model_name or "xgb" in model_name:
        try:
            # qlib LGBModel 用 self.model (Booster)，params 为 self.params
            booster = getattr(model, "model", None)
            params = getattr(model, "params", None)
            if booster is not None:
                # 优先用 model_to_string()（无需文件名，返回序列化文本）
                if hasattr(booster, "model_to_string"):
                    model_txt = booster.model_to_string(num_iteration=None)
                    artifacts["model_file"] = model_txt
                elif hasattr(booster, "save_model"):
                    import tempfile
                    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
                    tmp.close()
                    try:
                        # LightGBM 支持 num_iteration；XGBoost 的 save_model 只接受 fname
                        booster.save_model(tmp.name, num_iteration=None)
                    except TypeError:
                        booster.save_model(tmp.name)
                    with open(tmp.name, "r", encoding="utf-8", errors="ignore") as f:
                        artifacts["model_file"] = f.read()
                    os.unlink(tmp.name)
                # 树数量
                try:
                    if hasattr(booster, "num_trees"):
                        artifacts["model_info"]["num_trees"] = int(booster.num_trees())
                except Exception:
                    pass
            if params:
                safe = {}
                for k, v in dict(params).items():
                    if isinstance(v, (str, int, float, bool)) or v is None:
                        safe[k] = v
                artifacts["params"] = safe
            # 树模型的“权重”→ 特征重要性（gain）。XGBModel 有 get_feature_importance；
            # LGBModel 用 booster.feature_importance()。
            artifacts["feature_importance"] = _extract_feature_importance(
                model, booster, feature_names=artifacts.get("feature_names")
            )
        except Exception:
            pass

    return artifacts


def _extract_feature_importance(model, booster, feature_names=None):
    """提取树模型的特征重要性（按值降序），返回 [{feature, importance}] 或 None。

    兼容 XGBoost（booster.get_score()，key 可能为 f0/f1 索引）与 LightGBM
    （booster.feature_importance()，返回 f0/f1 索引数组）。若 key 是 f<i> 形式，
    尝试映射到真实特征名（feature_names），否则保留 f<i>。
    """
    try:
        import numpy as np
        fi = None
        # 1) XGBModel 自带接口
        if hasattr(model, "get_feature_importance"):
            try:
                fi = model.get_feature_importance()
            except Exception:
                fi = None
        # 2) XGBoost Booster：get_score()
        if fi is None and booster is not None and hasattr(booster, "get_score"):
            try:
                fi = booster.get_score()
            except Exception:
                fi = None
        # 3) LightGBM Booster：feature_importance()
        if fi is None and booster is not None and hasattr(booster, "feature_importance"):
            try:
                fi = booster.feature_importance(importance_type="gain")
            except Exception:
                fi = None
        if fi is None:
            return None

        # 归一化为 [(key, value)]
        if hasattr(fi, "items"):
            items = [(str(k), float(v)) for k, v in fi.items()]
        elif isinstance(fi, (list, np.ndarray)):
            items = [("f%d" % i, float(v)) for i, v in enumerate(fi)]
        else:
            return None
        if not items:
            return None

        # 特征索引 key → 真实特征名映射。
        # 兼容多种格式：f<i>（旧 xgboost）、Column_N / column_N（新 xgboost / lightgbm 默认列名）、
        # 纯数字。索引 N 即特征在 DataFrame 中的位置下标。
        def _real_name(k):
            idx = None
            if k.startswith("f") and k[1:].isdigit():
                idx = int(k[1:])
            else:
                # Column_9 / column_9 / f_9 等，取最后一个下划线后的数字
                lower = k.lower()
                if ("column_" in lower or lower.startswith("f_")) and "_" in k:
                    tail = k.rsplit("_", 1)[-1]
                    if tail.isdigit():
                        idx = int(tail)
                elif k.isdigit():
                    idx = int(k)
            if idx is not None and feature_names and 0 <= idx < len(feature_names):
                return feature_names[idx]
            return k

        items = sorted(items, key=lambda t: t[1], reverse=True)
        return [{"feature": _real_name(k), "importance": round(v, 4)} for k, v in items]
    except Exception:
        return None


def _save_model_artifacts(recorder, artifacts: dict):
    """把模型交付物保存到 mlflow artifact 与本地文件，方便后续查看/复现。

    滚动训练时，各段写入主目录下的 segment_XX 子目录；single 模式写入主目录。
    """
    import json
    # 保存到 mlflow artifact
    try:
        recorder.save_objects(**{"model_artifacts.json": artifacts}, artifact_path="model_artifacts")
    except Exception as e:
        logger.warning("保存模型交付物到 mlflow 失败: %s", e)

    art_dir = get_artifact_dir()
    if not art_dir:
        return
    try:
        # 确定保存子目录：滚动段(seg1/seg2/...)写入独立子目录，single 写入主目录
        seg = (artifacts.get("model_info") or {}).get("segment") or ""
        if seg:
            sub = os.path.join(art_dir, "segment_%s" % str(seg).replace("seg", ""))
        else:
            sub = art_dir
        os.makedirs(sub, exist_ok=True)

        # 交付物 JSON
        meta = {k: v for k, v in artifacts.items() if k != "model_file"}
        with open(os.path.join(sub, "model_artifacts.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
        # 树模型文件单独保存
        if artifacts.get("model_file"):
            with open(os.path.join(sub, "model.txt"), "w", encoding="utf-8") as f:
                f.write(artifacts["model_file"])
    except Exception as e:
        logger.warning("保存模型交付物到本地失败: %s", e)


def _save_model_object(model, dir_path):
    """把训练好的 qlib 模型对象保存为 pickle，供复用模式直接加载（跳过训练）。"""
    import pickle
    if not dir_path:
        return
    try:
        with open(os.path.join(dir_path, "model.pkl"), "wb") as f:
            pickle.dump(model, f)
    except Exception as e:
        logger.warning("保存模型对象(pkl)失败: %s", e)


def _read_train_feature_names(task_id: str, seg_no=None) -> Optional[list]:
    """读取某次回测训练时的特征名顺序（从 model_artifacts.json 的 feature_names 字段）。

    这是模型训练时实际喂给模型的特征顺序（位置对应 Column_N）。
    用于复用模型时校验当前回测的特征顺序是否一致，防止静默错位。
    """
    import glob
    import json
    try:
        from ..config import WORK_DIR
        artifacts_root = os.path.join(WORK_DIR, "artifacts")
    except Exception:
        artifacts_root = os.path.join(os.path.abspath("."), "artifacts")
    dirs = glob.glob(os.path.join(artifacts_root, "*_" + task_id))
    if not dirs:
        dirs = glob.glob(os.path.join(artifacts_root, task_id))
    if not dirs:
        return None
    base = dirs[-1]
    candidates = []
    if seg_no is not None:
        candidates.append(os.path.join(base, "segment_%s" % seg_no, "model_artifacts.json"))
    candidates.append(os.path.join(base, "model_artifacts.json"))
    for cp in candidates:
        if os.path.exists(cp):
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("feature_names"):
                    return d["feature_names"]
            except Exception:
                continue
    return None


def _dataset_feature_names(dataset) -> Optional[list]:
    """从 dataset 的 handler 拿当前实际喂给模型的特征名顺序。"""
    try:
        handler = getattr(dataset, "handler", None)
        if handler is not None and hasattr(handler, "get_cols"):
            cols = handler.get_cols("feature")
            if cols:
                return [str(c) for c in cols]
    except Exception:
        pass
    return None


def _find_artifact_base(task_id: str) -> Optional[str]:
    """定位某次回测的 artifacts 目录（`*_<task_id>` 优先；不存在 ⇒ None ✓）。"""
    import glob
    try:
        from ..config import WORK_DIR
        artifacts_root = os.path.join(WORK_DIR, "artifacts")
    except Exception:                                      # noqa: BLE001
        artifacts_root = os.path.join(os.path.abspath("."), "artifacts")
    dirs = glob.glob(os.path.join(artifacts_root, "*_" + task_id))
    if not dirs:
        dirs = glob.glob(os.path.join(artifacts_root, task_id))
    return dirs[-1] if dirs else None


def _current_train_semantics() -> str:
    """当前**训练口径**语义版本（★ v1.20.48 ✓）。

    ⚠ 惰性导入 ✓：`qlib_engine` 在模块级 import 本模块 ✓ ⇒ 这里不能在模块级反向 import
    （会形成循环依赖 ✗）。真值定义在 `qlib_engine.TRAIN_SEMANTICS` ✓（改训练口径时在那里递增 ✓）。
    """
    try:
        from .qlib_engine import TRAIN_SEMANTICS
        return str(TRAIN_SEMANTICS)
    except Exception:                                      # noqa: BLE001
        return "unknown"


def _train_semantics_info(req) -> dict:
    """训练口径的**可读明细**（写进 `model_artifacts.json` ✓，便于事后比对"到底哪里变了" ✓）。"""
    def _g(name):
        v = getattr(req, name, None)
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        return str(v)

    return {
        "semantics": _current_train_semantics(),
        "model": _g("model"),
        "feature": _g("feature"),
        "split_mode": _g("split_mode"),
        "label_horizon": _g("label_horizon"),
        "price_adjust": _g("price_adjust"),
        "universe": _g("universe"),
        "n_selected_features": len(getattr(req, "selected_features", None) or []),
        "n_custom_formulas": len(getattr(req, "custom_formulas", None) or []),
    }


def _read_train_semantics(task_id: str, seg_no=None) -> Optional[str]:
    """读某次回测训练时的**训练口径戳**（`model_artifacts.json` 的 `train_semantics` ✓）。

    v1.20.48 之前的产物没有这个字段 ⇒ 返回 None ✓（= "无法证明口径一致" ✓ 见调用方）。
    """
    import json
    base = _find_artifact_base(task_id)
    if not base:
        return None
    candidates = []
    if seg_no is not None:
        candidates.append(os.path.join(base, "segment_%s" % seg_no, "model_artifacts.json"))
    candidates.append(os.path.join(base, "model_artifacts.json"))
    for cp in candidates:
        if os.path.exists(cp):
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    d = json.load(f)
                v = d.get("train_semantics")
                if v:
                    return str(v)
            except Exception:                              # noqa: BLE001
                continue
    return None


def _verify_reuse_train_semantics(load_from: str, seg_no=None) -> None:
    """复用模型前校验**训练口径**一致性（★ v1.20.48 ✓）。

    和 `chip_store.chip_meta_state` 同一套思路 ✓：**宁可挡住，也别静默复用旧口径** ✗。
      · 戳在且与当前一致 ⇒ 放行 ✓；
      · 戳不一致 ⇒ 报错 ✗；
      · **没有戳**（v1.20.48 之前的产物 ✓）⇒ 也报错 ✗（无法证明一致 ✓）。
    ⚠ 源任务目录都找不到时**不报错** ✓ —— 交给 `_load_model_object` 走"未找到可复用权重" ✓
      （避免把"任务被删了"误报成"口径不一致" ✗）。
    """
    if _find_artifact_base(load_from) is None:
        return
    got = _read_train_semantics(load_from, seg_no)
    want = _current_train_semantics()
    if got and got == want:
        return
    where = ("task %s" % load_from) if seg_no is None else ("task %s·段%s" % (load_from, seg_no))
    if not got:
        raise ValueError(
            "复用模型失败：源模型（%s）**没有训练口径戳** ✗\n\n"
            "它是 v1.20.48 之前训练的产物 ⇒ 无法确认与当前训练口径（%s）一致。\n"
            "为什么必须挡住：v1.20.46 已把 `valid` 改成**独立验证集**（原先 `valid == train` ✗\n"
            "⇒ early_stopping 在训练集上做、模型选择失真），并修了分层 1 日前视。\n"
            "旧口径模型混进新回测 ⇒ 同一张净值图混两套训练口径，而净值是**累乘**的\n"
            "⇒ 整条曲线被抬高 ✗（2026-09-22 就是这么出事的）。\n\n"
            "处理：把【复用模型权重】关掉（刷新页面后重填参数，或走 API 提交）⇒ 重新训练 ✓。"
            % (where, want)
        )
    raise ValueError(
        "复用模型失败：训练口径不一致 ✗\n"
        "  源模型（%s）训练口径 = %s\n"
        "  当前代码训练口径   = %s\n\n"
        "口径包含：训练/验证拆分、标签（label_horizon/对齐）、特征集/自定义公式/复权方式、\n"
        "股票池过滤口径、以及筹码等物化字段的口径。\n"
        "口径变过就不能复用（会混口径，且净值累乘 ⇒ 结果失真 ✗）。\n\n"
        "处理：关掉【复用模型权重】重新训练 ✓（确要复用，就必须与源口径一致）。"
        % (where, got, want)
    )


def _verify_reuse_feature_order(current_names: Optional[list], load_from: str, seg_no=None) -> None:
    """复用模型前校验特征顺序一致性。

    若被复用回测能读到训练时的 feature_names，且与当前特征顺序不一致，
    则抛出明确错误（而不是静默错位预测）。读不到训练特征名时跳过（不阻断）。
    """
    train_names = _read_train_feature_names(load_from, seg_no)
    if not train_names or not current_names:
        return  # 无训练特征名或当前无特征名，无法校验，跳过
    if train_names != current_names:
        # 展示前若干特征（避免超长刷屏），并给出"新训练 vs 复用权重"的引导
        def _fmt(names, n=8):
            return ", ".join(map(str, names[:n])) + ("..." if len(names) > n else "")

        raise ValueError(
            "复用模型失败：当前回测的特征与模型训练时不匹配（不能复用该模型权重做预测）。\n\n"
            "模型训练时特征（%d 个）：%s\n"
            "当前回测特征（%d 个）：%s\n\n"
            "原因与处理：\n"
            "• 若你只是想用【新参数】做新回测（改了股票池/特征/区间等），应【取消复用模型权重】，\n"
            "  让它重新训练，就不会报此错（请把表单里的\"复用模型权重\"关掉再开始回测）。\n"
            "• 若你确实要复用该模型权重，请把股票池、特征集、特征选择改回与训练时一致。"
            % (len(train_names), _fmt(train_names), len(current_names), _fmt(current_names))
        )


def _task_has_segment_models(task_id: str) -> bool:
    """判断某任务是否为滚动训练（模型按 segment_N 分段保存）。

    滚动训练任务只在 segment_N/model.pkl 保存模型，主目录没有 model.pkl；
    single 任务只在主目录保存。用于区分复用失败时的错误提示。
    """
    import glob
    try:
        from ..config import WORK_DIR
        artifacts_root = os.path.join(WORK_DIR, "artifacts")
    except Exception:
        artifacts_root = os.path.join(os.path.abspath("."), "artifacts")
    dirs = glob.glob(os.path.join(artifacts_root, "*_" + task_id))
    if not dirs:
        dirs = glob.glob(os.path.join(artifacts_root, task_id))
    if not dirs:
        return False
    base = dirs[-1]
    return os.path.isdir(os.path.join(base, "segment_1"))


def _load_model_object(task_id: str, model_name: str, seg_no=None):
    """从某次回测的 artifacts 加载模型对象（model.pkl）。
    seg_no 指定时，加载滚动训练的对应段模型（segment_{seg_no}/model.pkl）；
    否则加载主目录模型（single 模式）。
    """
    import pickle
    import glob
    try:
        from ..config import WORK_DIR
        artifacts_root = os.path.join(WORK_DIR, "artifacts")
    except Exception:
        artifacts_root = os.path.join(os.path.abspath("."), "artifacts")

    # 定位该任务的 artifacts 目录
    dirs = glob.glob(os.path.join(artifacts_root, "*_" + task_id))
    if not dirs:
        dirs = glob.glob(os.path.join(artifacts_root, task_id))
    if not dirs:
        return None
    base = dirs[-1]

    # 若指定段号，优先加载段模型；否则加载主目录模型
    candidates = []
    if seg_no is not None:
        candidates.append(os.path.join(base, "segment_%s" % seg_no, "model.pkl"))
    candidates.append(os.path.join(base, "model.pkl"))
    for cp in candidates:
        if os.path.exists(cp):
            try:
                with open(cp, "rb") as f:
                    return pickle.load(f)
            except Exception:
                continue
    return None


def _sanitize_json(o):
    """递归把 NaN/Infinity 等非有限浮点转成 None，避免写入 JSON 后前端/接口序列化失败。"""
    import math
    if isinstance(o, float):
        return None if not math.isfinite(o) else o
    if isinstance(o, dict):
        return {k: _sanitize_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize_json(v) for v in o]
    return o


def _save_result_json(dir_path: str, result: BacktestResult):
    """把回测完整结果（指标/净值/调仓记录）持久化到 artifacts，供历史查看。

    保存前会把 NaN/Infinity 清理为 null，确保 result.json 是标准 JSON，
    /result 接口与前端能正常读取。
    """
    import json
    try:
        data = result.model_dump() if hasattr(result, "model_dump") else result.dict()
        data = _sanitize_json(data)
        with open(os.path.join(dir_path, "result.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("保存回测结果 result.json 失败: %s", e)


_SEQ_LOCK = __import__("threading").Lock()


def _assign_backtest_seq(dir_path: str):
    """给回测目录分配稳定序号（写入 dir_path/seq.json）。

    规则：扫描 artifacts 下所有已有回测目录的 seq，取 max+1 作为新序号；
    删除某回测后该序号不回收（因为序号是持久化在各目录的 seq.json 里）；
    当 artifacts 目录为空（无任何 seq）时，新回测序号 = 1，重新开始。
    """
    import json
    import threading

    seq_file = os.path.join(dir_path, "seq.json")
    if os.path.exists(seq_file):
        return  # 已分配过

    root = os.path.dirname(dir_path)  # artifacts/ 目录
    with _SEQ_LOCK:
        max_seq = 0
        if os.path.isdir(root):
            for d in os.listdir(root):
                sf = os.path.join(root, d, "seq.json")
                if os.path.exists(sf):
                    try:
                        with open(sf, "r", encoding="utf-8") as f:
                            v = int(json.load(f).get("seq", 0))
                        if v > max_seq:
                            max_seq = v
                    except Exception:
                        pass
        new_seq = max_seq + 1
        try:
            with open(seq_file, "w", encoding="utf-8") as f:
                json.dump({"seq": new_seq}, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("分配回测序号失败 %s: %s", dir_path, e)


def _save_compose_attr(dir_path: str, seg_label, info: dict) -> None:
    """把信号合成（Meta-Gate / 触发叠加 / 硬规则）的归因结果**按段累积**写入 `compose.json`。

    v1.19.72（用户 2026-09-16 报）：「我勾了 Meta-Gate 风控，训练产物里没有这块结果？不知道机器
    最后觉得用哪个风控因子更好？」—— 此前 `compose_final_signal` 返回的 info 只被拼进进度消息
    就丢了（gate 模型也不落盘）⇒ 产物里查不到任何归因（`result.json` 里 gate 出现 0 次）。
    现在落盘，前端在结果页出「因子归因」表。失败不阻塞回测（与其它旁路记录一致）。

    结构：`{"segments": {"段1": <info>, ...}, "updated_at": "..."}`（滚动回测按段累积写）。
    """
    import json
    import time
    if not dir_path:
        return
    try:
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, "compose.json")
        data: Dict[str, Any] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:                     # 旧文件损坏 ⇒ 重建（旁路记录，不值得失败）
                data = {}
        segs = dict(data.get("segments") or {})
        segs[str(seg_label or "段1")] = info
        data = {"segments": segs, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_sanitize_json(data), f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("保存信号合成归因(compose.json)失败: %s", e)


def _save_backtest_params(dir_path: str, req: BacktestRequest):
    """保存回测参数快照（完整可复现参数 + 人工可读 meta）。

    同时分配"稳定序号"（seq.json）：
      - 序号按创建顺序递增，最早的回测序号=1；
      - 删除某个回测后序号不回收（被删的序号永久空着）；
      - 仅当 artifacts 目录被全部清空后，序号才重新从 1 开始。
    """
    import json
    try:
        _assign_backtest_seq(dir_path)
        # 完整参数（前端复现模式直接用）
        params = req.model_dump()
        with open(os.path.join(dir_path, "params.json"), "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False, indent=2, default=str)

        # 人工可读参数快照
        meta = {
            "回测目录": os.path.basename(dir_path),
            "股票池": req.universe,
            "起始日期": req.start_date,
            "结束日期": req.end_date,
            "起始资金(元)": req.initial_capital,
            "模型": req.model,
            "特征": req.feature,
            "TopK": req.topk,
            "持仓周期(天)": req.n_days_hold,
            "划分方式": "滚动训练" if (req.split_mode or "").lower() == "custom" else "一次性训练",
            "成交价基准": req.deal_price,
            "买入手续费": req.open_cost,
            "卖出手续费": req.close_cost,
            "滑点": req.impact_cost,
            "最低手续费(元)": req.min_cost,
            "成交量限制": req.volume_threshold,
            "涨跌停限制": req.limit_threshold,
            "每手股数": req.trade_unit,
            "训练窗口": f"{req.train_win} {req.train_unit}",
            "测试窗口": f"{req.test_win} {req.test_unit}",
            "复权方式": {"none": "不复权", "forward": "前复权", "backward": "后复权"}.get(
                getattr(req, "price_adjust", "none") or "none", "不复权"
            ),
        }
        with open(os.path.join(dir_path, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("保存回测参数快照(params/meta)失败: %s", e)


def _save_train_signature(dir_path: str, req) -> None:
    """保存"训练签名快照"（train_signature.json），为历史模型提供可追溯性。

    记录决定训练结果的全部关键信息（代码版本 / 数据版本 / 依赖版本 / 参数 / 特征指纹），
    用于将来精确复现：训练数据或环境一旦漂移，即可据此定位差异（见开发记录 #12 的教训）。
    不参与回测逻辑，只做旁路记录；失败不影响任务。
    """
    import hashlib
    import json
    import subprocess
    import time

    def _git_head() -> str:
        try:
            repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            out = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, cwd=repo
            )
            if out.returncode == 0:
                return out.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def _data_version() -> str:
        try:
            from ..config import QLIB_PROVIDER_URI
            feat_dir = os.path.join(QLIB_PROVIDER_URI or "", "features")
            mtimes = []
            if os.path.isdir(feat_dir):
                for name in os.listdir(feat_dir)[:500]:
                    p = os.path.join(feat_dir, name)
                    if os.path.isdir(p):
                        mtimes.append(os.path.getmtime(p))
            if mtimes:
                return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(max(mtimes)))
        except Exception:
            pass
        return "unknown"

    def _dep_versions() -> dict:
        deps = {}
        for mod in ("xgboost", "pandas", "numpy", "scipy", "lightgbm"):
            try:
                m = __import__(mod)
                deps[mod] = getattr(m, "__version__", "unknown")
            except Exception:
                pass
        try:
            import qlib
            deps["qlib"] = getattr(qlib, "__version__", getattr(qlib, "__file__", "unknown"))
        except Exception:
            pass
        return deps

    def _feature_fingerprint() -> str:
        parts = [
            "feature=" + str(getattr(req, "feature", "") or "Alpha158"),
            "selected=" + ",".join(sorted(getattr(req, "selected_features", None) or [])),
            "formulas=" + "|".join(getattr(req, "custom_formulas", None) or []),
            "label_horizon=" + str(getattr(req, "label_horizon", "") or ""),
            "model=" + str(getattr(req, "model", "") or ""),
            "price_adjust=" + str(getattr(req, "price_adjust", "none") or "none"),
        ]
        raw = "||".join(parts)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    try:
        sig = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "git_commit": _git_head(),
            "data_version": _data_version(),
            "deps": _dep_versions(),
            "price_adjust": getattr(req, "price_adjust", "none") or "none",
            "feature_fingerprint": _feature_fingerprint(),
            "params_md5": hashlib.md5(
                json.dumps(req.model_dump(), sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:16],
        }
        with open(os.path.join(dir_path, "train_signature.json"), "w", encoding="utf-8") as f:
            json.dump(sig, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("保存训练签名快照失败: %s", e)
