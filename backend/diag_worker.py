# -*- coding: utf-8 -*-
"""诊断：真实 D.features（多进程 worker）下 DYN_COUNT 是否注册。"""
import json
import os

os.environ.setdefault("PYTHONPATH", "")
import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.data.ops import Operators

PROVIDER_URI = r"D:\quant\qlib_code\data\cn_data"


def worker_probe(config_c):
    from qlib.config import C as _C
    from qlib.data.ops import Operators as _Ops

    _C.register_from_C(config_c)
    keys = sorted(_Ops._ops.keys())
    return {
        "DYN_COUNT": "DYN_COUNT" in keys,
        "BARSLAST": "BARSLAST" in keys,
        "custom_ops_len": len(getattr(_C, "custom_ops", None) or []),
    }


def main():
    with open(r"D:\quant\qlib_code\backend\workdir\custom_formulas.json", encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else data.get("items", [])
    by_name = {it.get("name"): it for it in items}
    expr_gold = by_name["黄金回踩"]["expression"]
    expr_trend = by_name["趋势顶底"]["expression"]
    label_expr = "Ref($close, -3)/$close - 1"  # T 收盘买入持有 2 日，与 single_test/回测口径一致
    fields = [expr_gold, expr_trend, label_expr, "$close", "$change"]

    # 模拟后端 qlib_engine._ensure_qlib_init 的初始化
    from app.factors.ops_ext import _ALL_OPS as custom_ops
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN, custom_ops=custom_ops)

    print("主进程 DYN_COUNT registered:", "DYN_COUNT" in Operators._ops)
    print("主进程 C.custom_ops len:", len(getattr(qlib.config.C, "custom_ops", None) or []))

    from joblib import Parallel, delayed
    res = Parallel(n_jobs=2, backend=qlib.config.C.joblib_backend)(
        delayed(worker_probe)(qlib.config.C) for _ in range(1)
    )
    print("worker 探针结果:", res)

    # 真实 D.features：需要 instruments 列表
    try:
        insts = D.list_instruments(D.instruments(market="csi300"), start_time="2022-01-01", as_list=True)
        insts = [str(i).upper() for i in insts[:20]]
        print("instruments 数量:", len(insts))
        df = D.features(insts, fields, start_time="2022-01-01", end_time="2023-12-31")
        print("D.features OK, shape:", df.shape)
    except Exception as e:
        import traceback
        print("D.features FAILED:", type(e).__name__, e)
        traceback.print_exc()


if __name__ == "__main__":
    main()
