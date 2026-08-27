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
        }
        with open(os.path.join(dir_path, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("保存回测参数快照(params/meta)失败: %s", e)
