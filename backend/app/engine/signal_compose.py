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

import threading

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
def _codes_old(dataset):
    """老股（train 起点前已上市）代码列表：次新股请求超窗会触发 qlib 扩展窗口 bug。"""
    Xtr = _features(dataset, "train")
    inst = Xtr.index.get_level_values("instrument")
    dts = Xtr.index.get_level_values("datetime")
    mindt = pd.Series([str(x)[:10] for x in dts], index=inst).groupby(level=0).min()
    train_start = str(mindt.min())[:10]
    return [c for c in sorted(set(inst.astype(str))) if str(mindt[c]) <= train_start]


def _extra_cols(dataset, exprs, idx, buffer_days=120, universe=None, span=None) -> pd.DataFrame:
    """为 gate 附加的表达式特征（如若干 01 触发公式列）：D.features 计算并对齐到 idx。

    注意：D.features 的列名是表达式原文（含 ( ) , / 等特殊字符），LightGBM 不接受
    作为 feature name（报 "Do not support special JSON characters"），统一重命名为
    extra_feature_0/1/...（train/test 顺序一致，predict 按位置即可对齐）。

    `universe` + `span`（v1.19.39，**滚动回测必传**）：按**整个回测区间**求值一次、按段切片。
    为什么：本函数每段（train / test 各一次）都会被调用，而 `D.features` 的缓存键含区间 ⇒
    每段必然 miss，把同样几条表达式在**全A**上重算一遍（实测：用户那两条几千字符的 01 公式
    一次要 **48.5s/段**，占"信号合成 117s"的四成）。
    ⚠ 为什么等价：表达式全是**历史窗**运算（Ref/HHVBARS/DYN_* 等，无未来引用）⇒ 同一 (股票, 日期)
      的值**与加载区间无关**，加宽区间只是多给了历史。已用真实数据对拍验证（`ai_test/check_extra_cols_equiv.py`：
      3 条公式、NaN 掩码零差异、共同非 NaN 最大差 0）。
    ⚠ 股票子集仍按**本段的** `_codes_old(dataset)` 过滤 ⇒ 与旧实现完全同一批行（不多不少）。
    """
    from qlib.data import D
    codes = _codes_old(dataset)
    lo = min(str(x)[:10] for x in idx.get_level_values("datetime"))
    hi = max(str(x)[:10] for x in idx.get_level_values("datetime"))
    start = (pd.to_datetime(lo) - pd.Timedelta(days=buffer_days)).strftime("%Y-%m-%d")
    if span and universe:
        df = _span_features(universe, list(exprs), span[0], span[1])
        if df is not None:
            # 只保留本段那批股票（等价性关键：旧实现只加载 codes 这批）
            df = df[df.index.get_level_values("instrument").astype(str).isin(set(codes))]
        else:                                   # 整区间求值失败：退回旧路径（不阻塞回测）
            df = D.features(codes, list(exprs), start_time=start, end_time=hi)
    else:
        df = D.features(codes, list(exprs), start_time=start, end_time=hi)
    aligned = align_trig(df, idx).astype(float)
    aligned.columns = ["extra_feature_%d" % i for i in range(aligned.shape[1])]
    return aligned.fillna(0.0)


# 整区间额外特征缓存：key=(排序后的股票元组, 表达式元组, start, end) -> DataFrame
# （进程级；只留最近 2 份，避免多任务时内存膨胀。全A × 3 年 × 3 列 ≈ 90MB 量级。）
_SPAN_CACHE: dict = {}
_SPAN_LOCK = threading.Lock()
_SPAN_CACHE_MAX = 2


def _span_features(universe, exprs, span_start, span_end, buffer_days=120):
    """按整个回测区间求值额外特征（带缓存）。失败返回 None（调用方退回逐段加载）。"""
    from qlib.data import D
    start = (pd.to_datetime(str(span_start)) - pd.Timedelta(days=buffer_days)).strftime("%Y-%m-%d")
    end = str(span_end)
    key = (tuple(sorted(str(c) for c in universe)), tuple(exprs), start, end)
    with _SPAN_LOCK:
        hit = _SPAN_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        df = D.features(list(universe), list(exprs), start_time=start, end_time=end)
    except Exception:
        return None
    with _SPAN_LOCK:
        _SPAN_CACHE[key] = df
        while len(_SPAN_CACHE) > _SPAN_CACHE_MAX:
            _SPAN_CACHE.pop(next(iter(_SPAN_CACHE)))
    try:
        from ..logger import get_logger
        get_logger(__name__).info(
            "额外特征整区间求值一次并缓存：%d 只 × %d 条 × %s~%s（此后各段只做切片）",
            len(universe), len(exprs), start, end)
    except Exception:
        pass
    return df


# gate 训练超参：train_gate 与归因 ablation 各变体**共用**（可比性前提）
_GATE_PARAMS = {"objective": "binary", "metric": "auc", "learning_rate": 0.05,
                "num_leaves": 64, "min_child_samples": 50, "num_threads": 0,
                "verbosity": -1, "feature_pre_filter": False}
_GATE_ROUNDS = 300
# 归因最多做多少个因子（勾很多公式时按 gain 取前 N，避免 ablation 训练时间失控）。
# 实测（本机，10 特征+3 因子、段1 训练 50 万行）：训 1 个 gate ≈ 2.7s ⇒ 每段 +11s、36 段约 +6 分钟；
# 若勾 20 个公式则会变成 +57s/段、约 +34 分钟 ⇒ 故设上限。
_ATTR_MAX_FACTORS = 12


def _fit_gate(X, y):
    """训一个 gate（LightGBM binary），返回 (bst, valid_auc)。

    ⚠ 归因 ablation（v1.19.72）**复用同一个 X 的索引、只换列** ⇒ 训练/验证切分完全一致，
      各变体的 valid_auc 才可比（否则"因子差异"会被"切分差异"污染）。
    """
    import lightgbm as lgb
    y = y.reindex(X.index)
    dates = X.index.get_level_values("datetime").unique()
    cut = dates[int(len(dates) * 0.8)]
    tr = X.index.get_level_values("datetime") < cut   # numpy bool 掩码（DatetimeIndex 比较即返回）
    dtr = lgb.Dataset(X[tr].values, label=y[tr].values, feature_name=list(X.columns))
    dva = lgb.Dataset(X[~tr].values, label=y[~tr].values, feature_name=list(X.columns),
                      reference=dtr)
    bst = lgb.train(dict(_GATE_PARAMS), dtr, num_boost_round=_GATE_ROUNDS, valid_sets=[dva],
                    callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)])
    return bst, float(bst.best_score["valid_0"]["auc"])


def _gate_attribution(X, y, bst, extra_cols, exprs) -> dict:
    """gate 因子归因：① gain 重要性 ② **单因子 ablation**（回答"哪个附加因子更好"）。

    背景（用户 2026-09-16）：「我勾了 Meta-Gate 风控，训练产物里没有这块结果？不知道机器最后
    觉得用哪个风控因子更好？」—— 此前 gate 只被用来产出 z 做拒尾，**importance 算了没提取、
    gate 模型也没落盘** ⇒ 前端明明写着"机器用 importance 自动挑选"，产物里却查不到挑了谁。
    本函数补上这个输出（结果随 `compose.json` 落进产物目录 + 前端出表）。

    ⚠ 为什么两个指标都给：**gain 是"树在它上面花了多少分裂增益"**，会被特征个数与共线性影响
      （两条相似公式互相分走 gain，排名会失真）；**ablation 是"单独把它给 gate 能不能提升
      valid_auc"** —— 后者才是可比的"哪个因子更好"。基线 = 不含任何附加因子（只有主特征 + 主分）。
    ⚠ 成本：ablation 要额外训 **k + 1** 个 gate（k=附加因子数）⇒ 由 `attribution` 开关控制。
    """
    cols = list(X.columns)
    gains = pd.Series(bst.feature_importance(importance_type="gain"),
                      index=cols, dtype=float).fillna(0.0)
    ex_set = set(extra_cols)
    base_cols = [c for c in cols if c not in ex_set]
    g_base, g_ex = float(gains[base_cols].sum()), float(gains[extra_cols].sum())
    tot = g_base + g_ex
    out = {
        "primary_p_gain_share": (float(gains.get("primary_p", 0.0)) / tot) if tot else 0.0,
        "extras_gain_share": (g_ex / tot) if tot else 0.0,
        "note": "gain_share=该因子在这批附加因子内分到的 gain 占比；auc/delta_auc=单独把它加进"
                "基线后 gate 的 valid_auc 及相对基线的提升（更可比，建议看 delta_auc）",
    }
    try:
        _, auc0 = _fit_gate(X[base_cols], y)          # 基线：主特征 + 主分
    except Exception as e:                            # noqa: BLE001 —— 归因失败不影响 gate 主流程
        out["error"] = "基线 gate 训练失败：%r" % (e,)
        return out
    out["primary_only_auc"] = auc0
    out["full_auc"] = float(bst.best_score["valid_0"]["auc"])
    out["full_delta_auc"] = out["full_auc"] - auc0     # 这批因子整体值不值（相对不用它们）
    rows = []
    order = sorted(range(len(extra_cols)), key=lambda i: -float(gains[extra_cols[i]]))
    picked = order[:_ATTR_MAX_FACTORS]
    out["ablation_factors"] = len(picked)
    out["ablation_capped"] = len(picked) < len(extra_cols)
    if out["ablation_capped"]:
        out["note"] += ("；⚠ 附加因子 %d 个、超过上限 %d ⇒ 只对 gain 最高的前 %d 个做了单因子 ablation"
                        % (len(extra_cols), _ATTR_MAX_FACTORS, len(picked)))
    for i in picked:
        c = extra_cols[i]
        row = {"i": i, "gain": float(gains[c]),
               "gain_share": (float(gains[c]) / g_ex) if g_ex else 0.0,
               "expr": (str(exprs[i])[:300] if i < len(exprs) else ""),
               "auc": None, "delta_auc": None}
        try:
            _, auc1 = _fit_gate(X[base_cols + [c]], y)
            row["auc"] = auc1
            row["delta_auc"] = auc1 - auc0
        except Exception as e:                        # noqa: BLE001
            row["error"] = repr(e)
        rows.append(row)
    out["extras"] = rows
    return out


def train_gate(dataset, model, opts=None, universe=None, span=None):
    """训练 gate，返回 (bst, info)。

    info = {reject_ratio, n, valid_auc} + 有附加因子时的 `attribution`（gain 占比 + 单因子
    ablation，见 `_gate_attribution`）；`opts["attribution"]=False` 可关（省 k+1 次 gate 训练）。
    """
    o = dict(scope="all", ydef="abs", reject_ratio=0.25, extra_features=[], attribution=True)
    if opts:
        o.update({k: v for k, v in opts.items() if v is not None})
    X = _features(dataset, "train").dropna()
    L = _labels(dataset, "train").reindex(X.index)
    p = model.predict(dataset, segment="train").reindex(X.index)
    X = X.join(p.rename("primary_p"), how="inner")
    extra = o.get("extra_features") or []
    extra_cols = []
    if extra:
        Xe = _extra_cols(dataset, extra, X.index, universe=universe, span=span)
        extra_cols = list(Xe.columns)
        X = X.join(Xe, how="left").fillna(0.0)
    L = L.reindex(X.index)
    y = (L > 0).astype(int) if o["ydef"] == "abs" else \
        (L > L.groupby(level="datetime").transform("median")).astype(int)
    if o["scope"] == "positive":
        keep = X["primary_p"] > 0
        X, y = X[keep], y[keep]
    if len(X) < 5000 or y.nunique() < 2:
        raise ValueError("gate 训练样本不足 %d" % len(X))
    bst, auc = _fit_gate(X, y)
    gi = {"reject_ratio": o["reject_ratio"], "n": len(X), "valid_auc": auc,
          "extras": len(extra_cols)}
    if extra_cols and o.get("attribution", True):
        gi["attribution"] = _gate_attribution(X, y, bst, extra_cols, extra)
    return bst, gi


def gate_z(dataset, bst, p_test: pd.Series, opts=None, universe=None, span=None) -> pd.Series:
    o = dict(extra_features=[])
    if opts:
        o.update({k: v for k, v in opts.items() if v is not None})
    X = _features(dataset, "test").dropna()
    X = X.join(p_test.rename("primary_p"), how="inner")
    extra = o.get("extra_features") or []
    if extra:
        X = X.join(_extra_cols(dataset, extra, X.index, universe=universe, span=span),
                   how="left").fillna(0.0)
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


def _attr_enabled(setting, seg_label) -> bool:
    """把 `meta_gate_opts.attribution` 解析成"**本段**是否做单因子 ablation"。

    取值（兼容 bool 与字符串）：
      · False / 'off' / 'none'        ⇒ 关（只算 gain 占比，不额外训练；免费）
      · 'seg1'                        ⇒ 只第一段做（`seg_label` 为 None=一次性训练，或 "段1"）
      · True / 'all' / None（未设置） ⇒ **每段都做**（默认；用户 2026-09-16 选的就是这个）

    ⚠ 代价（**端到端实测**，见开发记录 §9.15）：一个 gate 训练约 **4.5~7.6s**（真实全A、段训 50 万行）
      ⇒ ablation 要 k+1 个 ⇒ **+20~30s/段** ⇒ 36 段约 **+12~18 分钟**（相对一次滚动回测的总时长是十几个百分点）。
      ⚠ 我曾被"gate 训练 24~93s"误导：那段时间里还包含 `_extra_cols` 的 `D.features` **额外特征求值**
      （真公式在 全A 上一次 ~48.5s），并不是 gate 本身 ⇒ **看耗时一定要看它到底包了哪几步**。
      ⚠ 也别用合成数据估真实训练耗时（早停太快，会低估到 1/3 量级）。
    """
    if setting is None:
        return True                        # 未设置 ⇒ 每段（默认，与用户选择一致）
    if isinstance(setting, str):
        s = setting.strip().lower()
        if s in ("", "off", "false", "none", "0", "no"):
            return False
        if s == "seg1":
            return seg_label in (None, "段1", "seg1", "1")
        return True                       # all/on/true/1/yes 及其它未知值 ⇒ 每段（显式要就给）
    return bool(setting)


def compose_final_signal(dataset, model, req, universe=None, span=None,
                        seg_label=None) -> pd.DataFrame:
    """引擎总入口：返回覆盖 pred.pkl 的 DataFrame。

    列：
      score: 最终主信号（S1 gate 开启时被拒 -inf；否则原主分）
      target_w: None/无叠加时为 None（策略保持等权 topk 旧路径）；S2 开启时给权重
    """
    # 分阶段计时（v1.19.39）：回答"信号合成到底慢在哪"（预测 / gate 训练 / gate 打分+额外特征加载）
    import time as _time
    _spans, _tick = [], _time.perf_counter()

    def _mark(label):
        nonlocal _tick
        _now = _time.perf_counter()
        _spans.append((label, _now - _tick))
        _tick = _now

    p_test = model.predict(dataset, segment="test")
    _mark("预测 test")
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
        gopts = dict(getattr(req, "meta_gate_opts", None) or {})
        # 归因范围（v1.19.72）：默认只第一段（见 _attr_enabled 的实测说明）
        gopts["attribution"] = _attr_enabled(gopts.get("attribution"), seg_label)
        bst_g, gi = train_gate(dataset, model, gopts, universe=universe, span=span)
        _mark("gate 训练")
        z = gate_z(dataset, bst_g, p_test, gopts, universe=universe, span=span)
        _mark("gate 打分（含额外特征 D.features）")
        score = compose_gate_tail(score, z, req.topk, float(gi["reject_ratio"]))
        _mark("gate 拒尾合成")
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

    _mark("收尾")
    from ..logger import get_logger as _get_logger
    _get_logger(__name__).info("信号合成分阶段：%s | 合计 %.1fs",
                              " | ".join("%s %.1fs" % (k, v) for k, v in _spans),
                              sum(v for _, v in _spans))

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
