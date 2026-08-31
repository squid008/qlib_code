# -*- coding: utf-8 -*-
"""回测引擎的纯工具函数（从 qlib_engine.py 拆分而来）。

只包含无引擎内部依赖的日期/路径/基准/参数工具函数，保持无副作用、易测试。
"""
import os
from typing import List, Optional

from ..models.backtest import BacktestRequest


def _default_qlib_uri() -> str:
    from ..config import QLIB_PROVIDER_URI
    return QLIB_PROVIDER_URI


def _default_exp_uri(work_dir: Optional[str] = None) -> str:
    """返回 mlflow 实验追踪后端 uri（sqlite）。"""
    if work_dir is None:
        from ..config import WORK_DIR
        work_dir = WORK_DIR
    os.makedirs(work_dir, exist_ok=True)
    db_path = os.path.join(work_dir, "mlflow.db")
    # timeout=30：SQLite busy_timeout，写锁时最多等待 30 秒而不是立刻报 database is locked
    return f"sqlite:///{db_path}?timeout=30"


def _pick_benchmark(universe: str, instruments: List[str]) -> str:
    """根据股票池选一个基准。

    已知股票池映射到对应指数；其他（all / 未识别）默认用 SH000300（沪深300，最通用的指数），
    避免 fallback 到 instruments[0]（可能是无数据的小代码如北交所 BJ430017）。
    """
    bench_map = {
        "csi300": "SH000300",
        "csi500": "SH000905",
        "csi800": "SH000906",
        "csi1000": "SH000852",
    }
    if universe in bench_map:
        return bench_map[universe]
    # 其他股票池（如 all）：用沪深300作为通用基准
    return "SH000300"


def _fallback_benchmark(benchmark: str, start_time: str, end_time: str,
                        instruments: Optional[list] = None) -> str:
    """验证 benchmark 在指定时间段内是否有数据；若没有，回退到第一个成分股。

    若回退也失败，返回原 benchmark（会抛错让上层处理），确保不静默取消基准。
    """
    if not benchmark:
        return benchmark
    try:
        from qlib.data import D
        df = D.features([benchmark], ["$close"], start_time=start_time, end_time=end_time)
        if df is not None and len(df) > 0:
            return benchmark
    except Exception:
        pass
    # benchmark 无数据，回退到成分股
    if instruments:
        for code in instruments:
            try:
                from qlib.data import D
                df = D.features([code], ["$close"], start_time=start_time, end_time=end_time)
                if df is not None and len(df) > 0:
                    return str(code)
            except Exception:
                continue
    return benchmark


def _offset_date(date_str: str, days: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _add_period(date_str: str, amount: int, unit: str) -> str:
    """按 day/week/month 对日期加减，返回新日期字符串。amount 可为负。"""
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    d = datetime.strptime(date_str, "%Y-%m-%d")
    unit = (unit or "day").lower()
    if unit in ("day", "d"):
        delta = relativedelta(days=amount)
    elif unit in ("week", "w"):
        delta = relativedelta(days=amount * 7)
    elif unit in ("month", "mon", "m"):
        delta = relativedelta(months=amount)
    else:
        delta = relativedelta(days=amount)
    return (d + delta).strftime("%Y-%m-%d")


def _sanitize_name(s: str) -> str:
    """清理字符串，只保留安全字符，用于目录名。"""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in s)


def _make_artifact_dir(work_dir: str, task_id: str, req: BacktestRequest) -> str:
    """生成可读、唯一的回测产物目录名。

    格式：{日期}-{时间}_{模型}_{股票池}_{起始年}_{结束年}_{task_id前8位}
    例：20260823-154500_LightGBM_csi300_2022_2026_ab12cd34
    """
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    model = _sanitize_name(req.model or "unknown")
    universe = _sanitize_name(req.universe or "custom")
    start_y = (req.start_date or "")[:4]
    end_y = (req.end_date or "")[:4]
    # 目录名末尾带完整 task_id，保证唯一且可反查
    name = f"{ts}_{model}_{universe}_{start_y}_{end_y}_{task_id}"
    path = os.path.join(work_dir, "artifacts", name)
    os.makedirs(path, exist_ok=True)
    return path
