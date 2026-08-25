# -*- coding: utf-8 -*-
"""因子库目录（Factor Catalog）。

对外提供"特征名 + 表达式 + 分类 + 描述"的目录，供前端勾选特征、悬停查看公式。

== 扩展因子库（未来维护成千上万因子）的方法 ==
本文件以"Provider + Registry"方式组织，方便将来接入自研/第三方因子库：

1. 新增一个 Provider 函数，返回一组因子记录（每条含 name/expression/category/description）：
       def my_factor_provider():
           return [
               {"name": "MYF1", "expression": "...", "category": "自研", "description": "..."},
           ]
2. 在 FACTOR_PROVIDERS 列表中注册它（dataset 名 + provider 函数）：
       FACTOR_PROVIDERS = [
           {"dataset": "Alpha158", "provider": alpha158_provider},
           {"dataset": "MyFactors", "provider": my_factor_provider},
       ]
3. 前端会自动通过 /api/factors/catalog?dataset=MyFactors 拉取目录。
4. 若新因子需要配套的 Handler，在 handler.py 中新增对应类并支持 fields 参数即可。

注意：因子表达式使用 qlib 的 Field/表达式语法（Ref/Mean/Std/Corr 等），
必须能被 qlib 的 loader 正确解析。可参考 qlib/data/ops.py。
"""
from typing import Dict, List


def _alpha158_raw_config():
    """返回 Alpha158 全量的 (expressions, names)。"""
    from qlib.contrib.data.loader import Alpha158DL
    conf = {
        "kbar": {},
        "price": {"windows": [0], "feature": ["OPEN", "HIGH", "LOW", "VWAP"]},
        "rolling": {},
    }
    return Alpha158DL.get_feature_config(conf)


# ---- 各分类的描述与表达式模板 ----

KBAR = [
    ("KMID", "($close-$open)/$open", "K线中段：收盘相对开盘涨跌幅"),
    ("KLEN", "($high-$low)/$open", "K线长度：当日振幅"),
    ("KMID2", "($close-$open)/($high-$low+1e-12)", "K线中段(归一)：实体相对振幅"),
    ("KUP", "($high-Greater($open, $close))/$open", "上影线长度"),
    ("KUP2", "($high-Greater($open, $close))/($high-$low+1e-12)", "上影线(归一)"),
    ("KLOW", "(Less($open, $close)-$low)/$open", "下影线长度"),
    ("KLOW2", "(Less($open, $close)-$low)/($high-$low+1e-12)", "下影线(归一)"),
    ("KSFT", "(2*$close-$high-$low)/$open", "收盘位置：2收盘-最高-最低"),
    ("KSFT2", "(2*$close-$high-$low)/($high-$low+1e-12)", "收盘位置(归一)"),
]

PRICE_FIELDS = [("OPEN0", "$open/$close", "开盘价相对收盘"), ("HIGH0", "$high/$close", "最高价相对收盘"),
                ("LOW0", "$low/$close", "最低价相对收盘"), ("VWAP0", "$vwap/$close", "均价相对收盘")]

ROLLING_OPS = {
    "ROC": ("Ref($close, {d})/$close", "近{d}日动量（当前收盘 / {d}日前收盘）"),
    "MA": ("Mean($close, {d})/$close", "近{d}日均价相对当前收盘"),
    "STD": ("Std($close, {d})/$close", "近{d}日价格标准差（波动）"),
    "BETA": ("Slope($close, {d})/$close", "近{d}日收盘线性回归斜率（Beta）"),
    "RSQR": ("Rsquare($close, {d})", "近{d}日线性回归 R²（趋势拟合度）"),
    "RESI": ("Resi($close, {d})/$close", "近{d}日线性回归残差（偏离趋势程度）"),
    "MAX": ("Max($high, {d})/$close", "近{d}日最高价相对当前收盘"),
    "MIN": ("Min($low, {d})/$close", "近{d}日最低价相对当前收盘"),
    "QTLU": ("Quantile($close, {d}, 0.8)/$close", "近{d}日收盘 80% 分位数"),
    "QTLD": ("Quantile($close, {d}, 0.2)/$close", "近{d}日收盘 20% 分位数"),
    "RANK": ("Rank($close, {d})", "近{d}日收盘在序列中的排名（0~1）"),
    "RSV": ("($close-Min($low, {d}))/(Max($high, {d})-Min($low, {d})+1e-12)", "RSV 指标（KDJ 前身）"),
    "IMAX": ("IdxMax($high, {d})/{d}", "近{d}日最高价所在位置/天数"),
    "IMIN": ("IdxMin($low, {d})/{d}", "近{d}日最低价所在位置/天数"),
    "IMXD": ("(IdxMax($high, {d})-IdxMin($low, {d}))/{d}", "最高最低位置差/天数"),
    "CORR": ("Corr($close, Log($volume+1), {d})", "近{d}日价格与成交量的相关性"),
    "CORD": ("Corr($close/Ref($close,1), Log($volume/Ref($volume,1)+1), {d})", "近{d}日涨跌幅与量比的相关性"),
    "CNTP": ("Mean($close>Ref($close,1), {d})", "近{d}日上涨天数占比"),
    "CNTN": ("Mean($close<Ref($close,1), {d})", "近{d}日下跌天数占比"),
    "CNTD": ("Mean($close>Ref($close,1), {d})-Mean($close<Ref($close,1), {d})", "近{d}日上涨占比-下跌占比"),
    "SUMP": ("Sum(Greater($close-Ref($close,1),0), {d})/(Sum(Abs($close-Ref($close,1)), {d})+1e-12)", "近{d}日正涨跌幅之和/总波动"),
    "SUMN": ("Sum(Greater(Ref($close,1)-$close,0), {d})/(Sum(Abs($close-Ref($close,1)), {d})+1e-12)", "近{d}日负涨跌幅之和/总波动"),
    "SUMD": ("(Sum(Greater($close-Ref($close,1),0), {d})-Sum(Greater(Ref($close,1)-$close,0), {d}))/(Sum(Abs($close-Ref($close,1)), {d})+1e-12)", "近{d}日涨跌动能差/总波动"),
    "VMA": ("Mean($volume, {d})/($volume+1e-12)", "近{d}日均量相对当日量"),
    "VSTD": ("Std($volume, {d})/($volume+1e-12)", "近{d}日成交量波动"),
    "WVMA": ("Std(Abs($close/Ref($close,1)-1)*$volume, {d})/(Mean(Abs($close/Ref($close,1)-1)*$volume, {d})+1e-12)", "成交量加权波动均值比"),
    "VSUMP": ("Sum(Greater($volume-Ref($volume,1),0), {d})/(Sum(Abs($volume-Ref($volume,1)), {d})+1e-12)", "近{d}日放量占比"),
    "VSUMN": ("Sum(Greater(Ref($volume,1)-$volume,0), {d})/(Sum(Abs($volume-Ref($volume,1)), {d})+1e-12)", "近{d}日缩量占比"),
    "VSUMD": ("(Sum(Greater($volume-Ref($volume,1),0), {d})-Sum(Greater(Ref($volume,1)-$volume,0), {d}))/(Sum(Abs($volume-Ref($volume,1)), {d})+1e-12)", "近{d}日量能差占比"),
}

ROLLING_WINDOWS = [5, 10, 20, 30, 60]


def _alpha158_provider() -> List[Dict[str, str]]:
    """生成 Alpha158 全量因子目录（与 qlib 默认 158 特征保持一致）。"""
    records = []
    # K线形态
    for name, expr, desc in KBAR:
        records.append({"name": name, "expression": expr, "category": "K线形态", "description": desc})
    # 原始价格
    for name, expr, desc in PRICE_FIELDS:
        records.append({"name": name, "expression": expr, "category": "原始价格", "description": desc})
    # 滚动算子（5/10/20/30/60）
    for op, (template, tmpl_desc) in ROLLING_OPS.items():
        for d in ROLLING_WINDOWS:
            name = "%s%d" % (op, d)
            records.append({
                "name": name,
                "expression": template.format(d=d),
                "category": "滚动算子",
                "description": tmpl_desc.format(d=d),
            })
    return records


# ---- 因子库注册表：将来新增因子集在这里注册 ----
FACTOR_PROVIDERS = [
    {"dataset": "Alpha158", "provider": _alpha158_provider, "grouping": "category"},
]


def get_catalog(dataset: str = "Alpha158") -> Dict[str, object]:
    """返回某个特征集的因子目录（按分类分组）。"""
    for reg in FACTOR_PROVIDERS:
        if reg["dataset"].lower() == dataset.lower():
            records = reg["provider"]()
            break
    else:
        # 未知数据集：尝试用 qlib 的 Alpha360 全量目录兜底
        records = _alpha360_provider()

    groups: Dict[str, List[Dict[str, str]]] = {}
    for r in records:
        cat = r.get("category") or "其他"
        groups.setdefault(cat, []).append(r)
    return {
        "dataset": dataset,
        "total": len(records),
        "groups": [{"group": k, "fields": v} for k, v in groups.items()],
        "flat": records,
    }


def _alpha360_provider() -> List[Dict[str, str]]:
    """Alpha360：6 字段 × 60 历史，Ref($X,i)/$close。"""
    from qlib.contrib.data.loader import Alpha360DL
    fields, names = Alpha360DL.get_feature_config()
    records = []
    for expr, name in zip(fields, names):
        base = name.rstrip("0123456789")
        records.append({
            "name": name,
            "expression": expr,
            "category": "Alpha360",
            "description": "Alpha360: %s 相对最新收盘的归一化历史值" % base,
        })
    return records
