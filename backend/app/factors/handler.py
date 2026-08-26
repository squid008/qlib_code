# -*- coding: utf-8 -*-
"""自定义特征集 Handler：支持按特征名勾选子集。

基于 qlib 的 Alpha158 / Alpha360，重写 get_feature_config，
使 QlibDataLoader 只加载用户勾选的特征列，后续归一化处理自动只作用于这些列。

未来扩展因子库时，只需在 catalog 中新增 provider，并在此按需新增对应的 Handler。
"""
from typing import List, Optional

from qlib.contrib.data.handler import Alpha158, Alpha360, Alpha158DL, Alpha360DL
from qlib.data.dataset.handler import DataHandlerLP


class SelectedAlpha158(Alpha158):
    """Alpha158 的子集版：通过 fields 指定要保留的特征名列表（如 ["KMID", "ROC5"]）。

    留空/为 None 时行为与原生 Alpha158 完全一致（全量 158 特征）。
    特征名必须与 Alpha158DL.get_feature_config 生成的 names 一致。
    """

    def __init__(self, fields: Optional[List[str]] = None, label_horizon: Optional[int] = 2, **kwargs):
        self._selected = set(fields) if fields else None
        self._label_horizon = max(1, int(label_horizon or 2))
        super().__init__(**kwargs)

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
        n = self._label_horizon
        return [f"Ref($close, -{n + 1})/Ref($close, -1) - 1"], ["LABEL0"]


class SelectedAlpha360(Alpha360):
    """Alpha360 的子集版。Alpha360 的特征名为 CLOSE{i}/OPEN{i}/HIGH{i}/LOW{i}/
    VWAP{i}/VOLUME{i}（i=0..59）。通过 fields 指定要保留的特征名。"""

    def __init__(self, fields: Optional[List[str]] = None, label_horizon: Optional[int] = 2, **kwargs):
        self._selected = set(fields) if fields else None
        self._label_horizon = max(1, int(label_horizon or 2))
        super().__init__(**kwargs)

    def get_feature_config(self):
        fields, names = Alpha360DL.get_feature_config()
        if self._selected:
            keep = [(f, n) for f, n in zip(fields, names) if n in self._selected]
            fields, names = ([f for f, _ in keep], [n for _, n in keep])
        return fields, names

    def get_label_config(self):
        # 预测周期：未来 N 个交易日的收益。N=label_horizon
        n = self._label_horizon
        return [f"Ref($close, -{n + 1})/Ref($close, -1) - 1"], ["LABEL0"]
