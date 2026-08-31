# -*- coding: utf-8 -*-
"""自定义特征集 Handler：支持按特征名勾选子集。

基于 qlib 的 Alpha158 / Alpha360，重写 get_feature_config，
使 QlibDataLoader 只加载用户勾选的特征列，后续归一化处理自动只作用于这些列。

未来扩展因子库时，只需在 catalog 中新增 provider，并在此按需新增对应的 Handler。
"""
from typing import List, Optional

import numpy as np
from qlib.contrib.data.handler import Alpha158, Alpha360, Alpha158DL, Alpha360DL
from qlib.data.dataset.handler import DataHandlerLP

from .parser import translate_formula, CodeGenError
from .ops_ext import ensure_ops_registered


class CleanInf:
    """特征清洗：把 ±inf 替换为 NaN。

    自定义公式里可能出现除零（如停牌后复牌导致分母为 0），求值结果为 inf，
    LightGBM 对 NaN 友好（按缺失处理），但对 inf 敏感；CSZScoreNorm 计算截面
    均值/方差时也不会跳过 inf。本处理器放在 learn_processors 最前面，把 inf
    统一替换为 NaN，避免污染标准化与训练。
    """

    def __init__(self, **kwargs):
        pass

    def fit(self, df=None):
        return self

    def __call__(self, df):
        # 只处理数值列：±inf → NaN（NaN 保持原样）
        numeric = df.select_dtypes(include=[np.number])
        if numeric.empty:
            return df
        df = df.copy()
        df[numeric.columns] = numeric.mask(np.isinf(numeric), np.nan)
        return df

    def readonly(self):
        # 本处理器不修改输入数据（先 copy 再改），声明为只读，避免 Handler 多余拷贝
        return True


class SelectedAlpha158(DataHandlerLP):
    """Alpha158 的子集版：通过 fields 指定要保留的特征名列表（如 ["KMID", "ROC5"]）。

    留空/为 None 时行为与原生 Alpha158 完全一致（全量 158 特征）。
    特征名必须与 Alpha158DL.get_feature_config 生成的 names 一致。
    使用 CachedQlibDataLoader：特征计算走磁盘缓存，label 每次现算。
    """

    def __init__(
        self,
        fields: Optional[List[str]] = None,
        label_horizon: Optional[int] = 2,
        instruments="csi500",
        start_time=None,
        end_time=None,
        freq="day",
        infer_processors=None,
        learn_processors=None,
        fit_start_time=None,
        fit_end_time=None,
        process_type=DataHandlerLP.PTYPE_A,
        filter_pipe=None,
        inst_processors=None,
        **kwargs,
    ):
        from qlib.contrib.data.handler import check_transform_proc, _DEFAULT_LEARN_PROCESSORS

        if infer_processors is None:
            infer_processors = []
        if learn_processors is None:
            learn_processors = _DEFAULT_LEARN_PROCESSORS
        infer_processors = check_transform_proc(infer_processors, fit_start_time, fit_end_time)
        learn_processors = check_transform_proc(learn_processors, fit_start_time, fit_end_time)

        self._selected = set(fields) if fields else None
        self._label_horizon = max(1, int(label_horizon or 2))

        data_loader = {
            "class": "CachedQlibDataLoader",
            "module_path": "app.engine.feature_cache",
            "kwargs": {
                "config": {
                    "feature": self.get_feature_config(),
                    "label": kwargs.pop("label", self.get_label_config()),
                },
                "filter_pipe": filter_pipe,
                "freq": freq,
                "inst_processors": inst_processors,
            },
        }
        super().__init__(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            data_loader=data_loader,
            infer_processors=infer_processors,
            learn_processors=learn_processors,
            process_type=process_type,
            **kwargs,
        )

    def get_feature_config(self):
        # 复用 Alpha158 的全量配置生成逻辑（kbar+price+rolling）
        conf = {
            "kbar": {},
            "price": {"windows": [0], "feature": ["OPEN", "HIGH", "LOW", "VWAP"]},
            "rolling": {},
        }
        fields, names = Alpha158DL.get_feature_config(conf)
        if self._selected:
            keep = [(f, n) for f, n in zip(fields, names) if n in self._selected]
            fields, names = ([f for f, _ in keep], [n for _, n in keep])
        return fields, names

    def get_label_config(self):
        # 预测周期：未来 N 个交易日的收益。N=label_horizon
        # 口径：信号日（T）收盘价买入持有 N 个交易日（与回测 deal_price=close、单因子测试一致）
        n = self._label_horizon
        return [f"Ref($close, -{n + 1})/$close - 1"], ["LABEL0"]


class SelectedAlpha360(DataHandlerLP):
    """Alpha360 的子集版。Alpha360 的特征名为 CLOSE{i}/OPEN{i}/HIGH{i}/LOW{i}/
    VWAP{i}/VOLUME{i}（i=0..59）。通过 fields 指定要保留的特征名。
    使用 CachedQlibDataLoader：特征计算走磁盘缓存，label 每次现算。"""

    def __init__(
        self,
        fields: Optional[List[str]] = None,
        label_horizon: Optional[int] = 2,
        instruments="csi500",
        start_time=None,
        end_time=None,
        freq="day",
        infer_processors=None,
        learn_processors=None,
        fit_start_time=None,
        fit_end_time=None,
        process_type=DataHandlerLP.PTYPE_A,
        filter_pipe=None,
        inst_processors=None,
        **kwargs,
    ):
        from qlib.contrib.data.handler import check_transform_proc, _DEFAULT_LEARN_PROCESSORS

        if infer_processors is None:
            infer_processors = []
        if learn_processors is None:
            learn_processors = _DEFAULT_LEARN_PROCESSORS
        infer_processors = check_transform_proc(infer_processors, fit_start_time, fit_end_time)
        learn_processors = check_transform_proc(learn_processors, fit_start_time, fit_end_time)

        self._selected = set(fields) if fields else None
        self._label_horizon = max(1, int(label_horizon or 2))

        data_loader = {
            "class": "CachedQlibDataLoader",
            "module_path": "app.engine.feature_cache",
            "kwargs": {
                "config": {
                    "feature": self.get_feature_config(),
                    "label": kwargs.pop("label", self.get_label_config()),
                },
                "filter_pipe": filter_pipe,
                "freq": freq,
                "inst_processors": inst_processors,
            },
        }
        super().__init__(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            data_loader=data_loader,
            infer_processors=infer_processors,
            learn_processors=learn_processors,
            process_type=process_type,
            **kwargs,
        )

    def get_feature_config(self):
        fields, names = Alpha360DL.get_feature_config()
        if self._selected:
            keep = [(f, n) for f, n in zip(fields, names) if n in self._selected]
            fields, names = ([f for f, _ in keep], [n for _, n in keep])
        return fields, names

    def get_label_config(self):
        # 预测周期：未来 N 个交易日的收益。N=label_horizon
        # 口径：信号日（T）收盘价买入持有 N 个交易日（与回测 deal_price=close、单因子测试一致）
        n = self._label_horizon
        return [f"Ref($close, -{n + 1})/$close - 1"], ["LABEL0"]


class FormulaHandler(DataHandlerLP):
    """自定义公式因子 Handler（M2）。

    把用户粘贴的益盟/通达信公式翻译成 qlib 表达式，作为回测的特征喂给模型。
    formulas 里的每条公式翻译成一个特征列。

    用法（dataset 配置里）：
        handler_cls = "FormulaHandler"
        handler_module = "app.factors.handler"
        kwargs = { ..., "formulas": ["A:=MA(CLOSE,5); 输出:A+100;", ...] }
    """

    def __init__(
        self,
        instruments="csi500",
        start_time=None,
        end_time=None,
        freq="day",
        infer_processors=[],
        learn_processors=None,
        fit_start_time=None,
        fit_end_time=None,
        process_type=DataHandlerLP.PTYPE_A,
        filter_pipe=None,
        inst_processors=None,
        formulas: Optional[List[str]] = None,
        label_horizon: Optional[int] = 2,
        **kwargs,
    ):
        # 注册自定义算子（BARSCOUNT/BARSSINCEN），幂等
        ensure_ops_registered()
        # 翻译公式 → (expressions, names)
        self._formulas = formulas or []
        self._label_horizon = max(1, int(label_horizon or 2))
        self._expressions, self._names = self._translate_all(self._formulas)

        # 默认学习处理器：先清洗 ±inf（自定义公式除零保护），
        # 再沿用 qlib 的 DropnaLabel + CSZScoreNorm（标签标准化）
        from qlib.contrib.data.handler import _DEFAULT_LEARN_PROCESSORS

        if learn_processors is None:
            learn_processors = [
                {"class": "CleanInf", "module_path": "app.factors.handler"},
                *_DEFAULT_LEARN_PROCESSORS,
            ]

        data_loader = {
            "class": "CachedQlibDataLoader",
            "module_path": "app.engine.feature_cache",
            "kwargs": {
                "config": {
                    "feature": self.get_feature_config(),
                    "label": kwargs.pop("label", self.get_label_config()),
                },
                "filter_pipe": filter_pipe,
                "freq": freq,
                "inst_processors": inst_processors,
            },
        }
        super().__init__(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            data_loader=data_loader,
            infer_processors=infer_processors,
            learn_processors=learn_processors,
            process_type=process_type,
            **kwargs,
        )

    def _translate_all(self, formulas: List[str]):
        """翻译所有公式，返回 (expressions, names)。"""
        expressions, names = [], []
        for text in formulas:
            t = translate_formula(text)
            if t.has_patch:
                raise CodeGenError(
                    f"公式[{t.name}]含尚未实现的有状态算子（{t.expression}），"
                    f"请先实现外挂算子或改用其他函数")
            expressions.append(t.expression)
            names.append(t.name)
        return expressions, names

    def get_feature_config(self):
        return self._expressions, self._names

    def get_label_config(self):
        # 预测周期：未来 N 个交易日的收益。N=label_horizon
        # 口径：信号日（T）收盘价买入持有 N 个交易日（与回测 deal_price=close、单因子测试一致）
        n = self._label_horizon
        return [f"Ref($close, -{n + 1})/$close - 1"], ["LABEL0"]
