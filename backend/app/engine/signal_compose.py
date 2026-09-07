# -*- coding: utf-8 -*-
"""信号合成（S1 Meta-Gate + S2 触发叠加）统一模块。

设计（原型 ai_test/meta_proto.py / meta_rc.py / meta_sim.py 验证，2026-09-07）：
  主模型 score 负责排序（topk）。可选两档增强，都只改"进策略的预测"，不动策略内核：
    S1 meta_gate   : 主 topK 候选内按日拒 gate 概率 z 最低 reject_ratio（score 置 -inf）
    S2 触发叠加     : 触发专用模型（触发行子集上 binary 学"未来收益>0"）对每日触发行打分，
                     取当日触发池 z 前 M 只，作为组合外 20% 权重叠加（target_w 合成）
  触发公式列由 D.features 计算（引擎统一 provider）；对齐按 level 名 (datetime/instrument)；
  次新股（train 起点前未上市）不参与触发计算（qlib 扩展窗口边界 bug），其触发标记=0。

对外唯一入口： compose_final_signal(dataset, model, req) -> (frame, info)
  frame 列：score（主模型最终分；gate 拒尾的被剔除行不在 frame 中）、
           target_w（S2 叠加权重，>0 表示进入目标组合；无 S2 时该列不存在 → 策略走等权 topk 旧路径）
  info：   dict（gate/overlay 训练样本数、validAUC 等，供日志）
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _norm_midx(idx) -> pd.MultiIndex:
    inst = idx.get_level_values("instrument").astype(str)
    dt = pd.to_datetime(idx.get_level_values("datetime")).strftime("%Y-%m-%d")
    return pd.MultiIndex.from_arrays([inst, dt])


def align_trig(trig: pd.DataFrame, idx) -> pd.DataFrame:
    """把 D.features 结果（index=instrument,datetime）对齐到目标 idx。未命中=NaN。"""
    cols = {}
    n = _norm_midx(trig.index)
    t = _norm_midx(idx)
    for c in trig.columns:
        cols[c] = pd.Series(trig[c].values, index=n).reindex(t).values
    return pd.DataFrame(cols, index=idx)


# ---------------------------------------------------------------------------
# 硬规则闸门（确定性过滤：市值/股价等；财务类规则将来接入同款通道）
# ---------------------------------------------------------------------------
def _daily_series(score_idx, fields):
    """按 score 的 codes/日期范围取日频字段并逐行对齐（未命中=NaN）。"""
    from qlib.data import D
    codes = sorted(set(score_idx.get_level_values("instrument").astype(str)))
    dt0 = min(str(x)[:10] for x in score_idx.get_level_values("datetime"))
    dt1 = max(str(x)[:10] for x in score_idx.get_level_values("datetime"))
    df = D.features(codes, fields, start_time=dt0, end_time=dt1)
    return align_trig(df, score_idx)


def apply_hard_filters(score: pd.Series, req) -> pd.Series:
    """确定性硬规则过滤：不满足任何启用条件的候选行置 -inf（不进回测候选）。

    支持（单位已在字段说明标注）：
      min_mktcap_bn / max_mktcap_bn : 总市值 亿元（数据 $market_cap 单位元）
      min_price                    : 真实股价下限（真实价 = $close/$factor，元）
    NaN（如停牌无市值/价格）在启用对应规则时视为不满足。
    """
    hf = getattr(req, "hard_filters", None) or {}
    if not hf:
        return score
    lo_bn = hf.get("min_mktcap_bn")
    hi_bn = hf.get("max_mktcap_bn")
    min_px = hf.get("min_price")
    if all(v is None for v in (lo_bn, hi_bn, min_px)):
        return score
    out = score.copy().astype(float)
    daily = _daily_series(None, score.index, ["$market_cap", "$close", "$factor"])
    keep = pd.Series(True, index=score.index)
    if lo_bn is not None or hi_bn is not None:
        mcap = daily["$market_cap"].astype(float)
        bad = mcap.isna()
        if lo_bn is not None:
            bad |= (mcap < float(lo_bn) * 1e8)
        if hi_bn is not None:
            bad |= (mcap > float(hi_bn) * 1e8)
        keep &= ~bad
    if min_px is not None:
        rp = daily["$close"].astype(float) / daily["$factor"].astype(float)
        keep &= rp.notna() & (rp >= float(min_px))
    out[~keep] = -np.inf
    return out


def _features(dataset, seg):
    from qlib.data.dataset import DataHandlerLP
    return dataset.prepare(seg, col_set="feature", data_key=DataHandlerLP.DK_I)


def _labels(dataset, seg):
    from qlib.data.dataset import DataHandlerLP
    L = dataset.prepare(seg, col_set="label", data_key=DataHandlerLP.DK_I)
    return L.iloc[:, 0] if isinstance(L, pd.DataFrame) else L


# ---------------------------------------------------------------------------
# S1 gate（从 signal_gate 平移，独立可复现）
# ---------------------------------------------------------------------------
def train_gate(dataset, model, opts=None):
    import lightgbm as lgb
    o = dict(scope="all", ydef="abs", reject_ratio=0.25)
    if opts:
        o.update({k: v for k, v in opts.items() if v is not None})
    X = _features(dataset, "train").dropna()
    L = _labels(dataset, "train").reindex(X.index)
    p = model.predict(dataset, segment="train").reindex(X.index)
    X = X.join(p.rename("primary_p"), how="inner")
    L = L.reindex(X.index)
    y = (L > 0).astype(int) if o["ydef"] == "abs" else \
        (L > L.groupby(level="datetime").transform("median")).astype(int)
    if o["scope"] == "positive":
        keep = X["primary_p"] > 0
        X, y = X[keep], y[keep]
    if len(X) < 5000 or y.nunique() < 2:
        raise ValueError("gate 训练样本不足 %d" % len(X))
    dates = X.index.get_level_values("datetime").unique()
    cut = dates[int(len(dates) * 0.8)]
    tr = X.index.get_level_values("datetime") < cut
    params = {"objective": "binary", "metric": "auc", "learning_rate": 0.05,
              "num_leaves": 64, "min_child_samples": 50, "num_threads": 0,
              "verbosity": -1, "feature_pre_filter": False}
    dtr = lgb.Dataset(X[tr].values, label=y[tr].values, feature_name=list(X.columns))
    dva = lgb.Dataset(X[~tr].values, label=y[~tr].values, feature_name=list(X.columns), reference=dtr)
    bst = lgb.train(params, dtr, num_boost_round=300, valid_sets=[dva],
                    callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)])
    return bst, {"reject_ratio": o["reject_ratio"], "n": len(X),
                 "valid_auc": float(bst.best_score["valid_0"]["auc"])}


def gate_z(dataset, bst, p_test: pd.Series) -> pd.Series:
    X = _features(dataset, "test").dropna()
    X = X.join(p_test.rename("primary_p"), how="inner")
    return pd.Series(bst.predict(X.values, num_iteration=bst.best_iteration), index=X.index)


def compose_gate_tail(score: pd.Series, z: pd.Series, topk: int, reject_ratio: float) -> pd.Series:
    out = score.copy().astype(float)
    df = pd.DataFrame({"score": score, "z": z.reindex(score.index)}).dropna()
    if not len(df):
        return out
    rk = df["score"].groupby(level="datetime").rank(ascending=False, method="first")
    sub = df[rk <= topk]
    if not len(sub):
        return out
    zr = sub["z"].groupby(level="datetime").rank(ascending=False, method="first")
    cnt = sub["z"].groupby(level="datetime").transform("size")
    keep_n = (cnt * (1.0 - reject_ratio)).round().clip(lower=1)
    out.loc[sub[zr > keep_n].index] = -np.inf
    return out


# ---------------------------------------------------------------------------
# S2 触发叠加
# ---------------------------------------------------------------------------
def compute_trigger_col(dataset, trig_expr: str) -> pd.DataFrame:
    """触发公式在回测 instrument/区间上算出列 trig（对齐到 dataset index 全量）。"""
    from qlib.data import D
    # 老股过滤（train 起点前后已上市）：trigger 只算这些，其余行=NaN（调用方 fill 0）
    Xtr = _features(dataset, "train")
    inst = Xtr.index.get_level_values("instrument")
    dtm = pd.Series([str(x)[:10] for x in Xtr.index.get_level_values("datetime")], index=inst)
    mindt = dtm.groupby(level=0).min()
    train_start = dtm.min()
    codes = [c for c in sorted(set(inst.astype(str))) if str(mindt[c]) <= train_start]
    # 计算区间：train 起点 - 缓冲（公式最大窗口约 55 天）~ test 终点
    from qlib.data.dataset import DataHandlerLP
    Xte = dataset.prepare("test", col_set="feature", data_key=DataHandlerLP.DK_I)
    dates_tr = Xtr.index.get_level_values("datetime")
    dates_te = Xte.index.get_level_values("datetime")
    lo = pd.to_datetime(min(str(x)[:10] for x in dates_tr)) - pd.Timedelta(days=90)
    hi = max(str(x)[:10] for x in dates_te)
    df = D.features(codes, [trig_expr], start_time=str(lo.date()), end_time=hi)
    trig = df.iloc[:, 0].astype(float)
    trig.name = "trig"
    # 对齐目标（dataset 全量行顺序保持）；这里只返回原始 df，由 fit/predict 时 align
    return pd.DataFrame({"trig": trig})


def fit_overlay(dataset, trig_tr: pd.DataFrame, p_tr: pd.Series, label_thr=0.0):
    """触发专用模型：触发行子集 binary。返回 (booster, info)。"""
    import lightgbm as lgb
    X = _features(dataset, "train").dropna()
    tg = trig_tr["trig"].reindex(X.index).fillna(0.0)
    rows = tg > 0.5
    Xs = X[rows].join(p_tr.reindex(X[rows].index).rename("primary_p"), how="inner")
    L = _labels(dataset, "train").reindex(Xs.index)
    y = (L > label_thr).astype(int)
    if len(Xs) < 150 or y.nunique() < 2:
        raise ValueError("触发样本不足 train=%d" % len(Xs))
    dates = Xs.index.get_level_values("datetime").unique()
    cut = dates[int(len(dates) * 0.8)]
    tr = Xs.index.get_level_values("datetime") < cut
    if int(tr.sum()) < 80 or int((~tr).sum()) < 20:
        raise ValueError("触发样本划分不足")
    params = {"objective": "binary", "metric": "auc", "learning_rate": 0.03,
              "num_leaves": 31, "min_child_samples": 40, "num_threads": 0,
              "verbosity": -1, "feature_pre_filter": False}
    dtr = lgb.Dataset(Xs[tr].values, label=y[tr].values, feature_name=list(Xs.columns))
    dva = lgb.Dataset(Xs[~tr].values, label=y[~tr].values, feature_name=list(Xs.columns), reference=dtr)
    bst = lgb.train(params, dtr, num_boost_round=250, valid_sets=[dva],
                    callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)])
    return bst, {"n": len(Xs), "valid_auc": float(bst.best_score["valid_0"]["auc"])}


def overlay_z(bst, X_te, p_test: pd.Series, trig_te: pd.DataFrame) -> pd.Series:
    """对测试集所有行给 overlay 概率：非触发行返回 NaN。"""
    X = X_te.dropna()
    tg = trig_te["trig"].reindex(X.index).fillna(0.0)
    X = X.join(p_test.reindex(X.index).rename("primary_p"), how="inner")
    z = pd.Series(np.nan, index=X.index)
    rows = tg.reindex(X.index) > 0.5
    if rows.any():
        z.loc[rows] = bst.predict(X[rows].values, num_iteration=bst.best_iteration)
    return z


# ---------------------------------------------------------------------------
# 组合层权重合成
# ---------------------------------------------------------------------------
def build_target_w(score_final: pd.Series, z_overlay: pd.Series,
                   topk_main: int, ovl_topk: int, ovl_weight: float) -> pd.Series:
    """逐日合成目标权重列 target_w。

    - 主层：score_final 每日前 topk_main 且非 -inf → (1-ovl_weight)/计数
    - 触发层：z_overlay 非空（=当日触发且有分）前 ovl_topk → 加 ovl_weight/实际只数
    - 逐日归一至总和≈1
    """
    out = pd.Series(0.0, index=score_final.index)
    df = pd.DataFrame({"s": score_final, "z": z_overlay.reindex(score_final.index)})
    df = df.replace([np.inf], np.nan)
    grp = df.groupby(level="datetime")
    rk = grp["s"].rank(ascending=False, method="first")
    n_main = grp["s"].transform("size")
    main_mask = (df["s"].notna()) & (rk <= topk_main)
    # -inf 保持不可选（notna 检查不生效于 -inf）显式处理：
    main_mask &= (df["s"] > -1e18)
    n_main_sel = main_mask.groupby(level="datetime").transform("sum")
    w_main = (1.0 - ovl_weight) / n_main_sel.replace(0, np.nan)
    out[main_mask] = w_main[main_mask]
    if ovl_weight > 0:
        zmask = df["z"].notna()
        zrk = df.loc[zmask, "z"].groupby(level="datetime").rank(ascending=False, method="first")
        zcand = zmask & (pd.Series(zrk.reindex(df.index), index=df.index) <= ovl_topk) \
            if zrk is not None else zmask
        # 触发层只数逐日
        n_ovl = zcand.groupby(level="datetime").transform("sum") if zcand.any() else pd.Series(0, index=df.index)
        w_ovl = ovl_weight / n_ovl.replace(0, np.nan)
        out[zcand] = out[zcand] + w_ovl[zcand]
    # 归一
    tot = out.groupby(level="datetime").transform("sum").replace(0, np.nan)
    out = out / tot
    return out.fillna(0.0)


def compose_final_signal(dataset, model, req) -> pd.DataFrame:
    """引擎总入口：返回覆盖 pred.pkl 的 DataFrame。

    列：
      score: 最终主信号（S1 gate 开启时被拒 -inf；否则原主分）
      target_w: None/无叠加时为 None（策略保持等权 topk 旧路径）；S2 开启时给权重
    """
    p_test = model.predict(dataset, segment="test")
    score = p_test.astype(float)
    info = {}
    # 硬规则闸门：确定性过滤先于一切（市值/股价等）
    rule_on = bool(getattr(req, "hard_filters", None))
    if rule_on:
        score = apply_hard_filters(score, req)
        info["hard"] = "applied"
    gate_on = bool(getattr(req, "meta_gate", False))
    ovl_cfg = getattr(req, "trigger_overlay_opts", None) or {}
    ovl_on = bool(ovl_cfg.get("enabled", False)) if isinstance(ovl_cfg, dict) else False

    if gate_on:
        bst_g, gi = train_gate(dataset, model, getattr(req, "meta_gate_opts", None))
        z = gate_z(dataset, bst_g, p_test)
        score = compose_gate_tail(score, z, req.topk, float(gi["reject_ratio"]))
        info["gate"] = gi

    trig_col = None
    bst_o = None
    if ovl_on:
        expr = ovl_cfg.get("formula") or ovl_cfg.get("expression")
        if not expr:
            raise ValueError("trigger_overlay_opts 需提供 formula/expression")
        trig_df = compute_trigger_col(dataset, expr)          # index=instrument,datetime
        Xtr = _features(dataset, "train")
        trig_tr = align_trig(trig_df, Xtr.index)
        p_tr = model.predict(dataset, segment="train").reindex(Xtr.index)
        bst_o, oi = fit_overlay(dataset, trig_tr, p_tr)
        info["overlay"] = oi
        Xte = _features(dataset, "test")
        trig_te = align_trig(trig_df, Xte.index)
        z_ovl = overlay_z(bst_o, Xte, p_test, trig_te)
        ovl_topk = int(ovl_cfg.get("topk", 5) or 5)
        ovl_w = float(ovl_cfg.get("weight", 0.2) or 0.2)
        # 触发行列保留用于调试/可解释
        trig_col = trig_te["trig"].reindex(score.index).fillna(0.0)

    frame = pd.DataFrame({"score": score})
    if ovl_on:
        w = build_target_w(score, z_ovl, req.topk, ovl_topk, ovl_w)
        frame["target_w"] = w.reindex(score.index)
        if trig_col is not None:
            frame["trig"] = trig_col.reindex(score.index)
    # gate 拒尾 / 硬规则过滤：被拒(-inf)行直接从候选剔除，策略对余下行按 score topk（旧等权路径）
    if gate_on or rule_on:
        frame = frame.loc[frame["score"] != -np.inf]
    return frame, info
