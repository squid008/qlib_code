# -*- coding: utf-8 -*-
"""回测指标/净值/调仓提取模块（从 qlib_engine.py 拆分而来）。

包含：净值汇总指标、PortAnaRecord 结果提取、调仓记录提取、段期末账户。
依赖 BacktestResult / pandas / recorder/par 对象，不依赖回测编排逻辑。
"""
import os
from typing import Optional

from ..models.backtest import BacktestResult


def _aggregate_from_nav(all_nav: list, seg_results: list) -> BacktestResult:
    """根据拼接的全局净值曲线与各段结果，汇总最终指标。"""
    import pandas as pd
    result = BacktestResult()
    if not all_nav:
        return result

    df = pd.DataFrame(all_nav)
    df["value"] = df["value"].astype(float)
    # 总收益
    result.total_return = float(df["value"].iloc[-1] - 1)
    # 年化
    n = len(df)
    years = n / 252.0
    if years > 0:
        result.annualized_return = float((1 + result.total_return) ** (1 / years) - 1)
    # 最大回撤
    running_max = df["value"].cummax()
    dd = (df["value"] / running_max - 1).min()
    result.max_drawdown = float(dd)
    # 日收益（用于夏普/胜率）
    ret = df["value"].pct_change(fill_method=None).dropna()
    if len(ret) > 0 and ret.std() and ret.std() > 0:
        result.sharpe = float(ret.mean() / ret.std() * (252 ** 0.5))
    result.win_rate = float((ret > 0).mean()) if len(ret) else None

    # 基准收益（benchmark 已在拼接时按段累乘，直接取最后一个有效值）
    bench_values = [pt.get("benchmark") for pt in all_nav if pt.get("benchmark") is not None]
    if bench_values:
        result.benchmark_return = float(bench_values[-1] - 1)
    else:
        result.benchmark_return = None
    # 年化超额收益
    if result.annualized_return is not None and result.benchmark_return is not None and years > 0:
        bench_years = n / 252.0
        if bench_years > 0:
            result.annualized_excess_return = (
                result.annualized_return
                - (float((1 + result.benchmark_return) ** (1 / bench_years) - 1))
            )

    result.nav = all_nav[:2000]
    # 备注滚动段数（用 report_df 附带，避免改动模型字段）
    try:
        result.report_df = {"rolling_segments": len(seg_results)}
    except Exception:
        pass
    return result


def _find_report_fallback():
    """兜底：全局搜索 mlruns 目录下的 report_normal 文件并读取最新的。"""
    import glob
    import pickle
    try:
        from ..config import WORK_DIR
        base = WORK_DIR
    except Exception:
        base = "."
    patterns = [
        os.path.join(base, "mlruns", "*", "*", "artifacts", "portfolio_analysis", "report_normal*.pkl"),
        os.path.join(base, "..", "mlruns", "*", "*", "artifacts", "portfolio_analysis", "report_normal*.pkl"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    if not files:
        return None
    files.sort(key=lambda f: os.path.getmtime(f))
    try:
        with open(files[-1], "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _extract_result(par, recorder) -> BacktestResult:
    """从 PortAnaRecord 的回测产物中提取风险指标与净值。"""
    result = BacktestResult()
    report = None

    # 方式一：通过 recorder.load_object 按 artifact 路径读取
    for key in ["portfolio_analysis/report_normal_1day.pkl", "report_normal_1day.pkl",
                "portfolio_analysis/report_normal_day.pkl"]:
        try:
            report = recorder.load_object(key)
            if report is not None:
                break
        except Exception:
            continue

    # 方式二：par.load
    if report is None:
        for key in ["report_normal_1day.pkl", "report_normal_day.pkl"]:
            try:
                report = par.load(key)
                if report is not None:
                    break
            except Exception:
                continue

    # 方式三：全局搜索 mlruns 下的报告文件
    if report is None:
        report = _find_report_fallback()

    if report is not None and hasattr(report, "columns"):
        import pandas as pd

        try:
            ret = report["return"] if "return" in report.columns else None
            bench = report["bench"] if "bench" in report.columns else None

            if ret is not None:
                ret = ret.astype(float)
                cum = (1 + ret).cumprod()
                result.total_return = float(cum.iloc[-1] - 1) if len(cum) else None
                years = len(ret) / 252.0
                if years > 0 and result.total_return is not None:
                    result.annualized_return = float((1 + result.total_return) ** (1 / years) - 1)
                running_max = cum.cummax()
                dd = (cum / running_max - 1).min()
                result.max_drawdown = float(dd) if not pd.isna(dd) else None
                std = ret.std()
                if std and std > 0:
                    result.sharpe = float(ret.mean() / std * (252 ** 0.5))
                result.win_rate = float((ret > 0).mean()) if len(ret) else None

                if bench is not None:
                    bench = bench.astype(float)
                    bench_cum = (1 + bench).cumprod()
                    result.benchmark_return = float(bench_cum.iloc[-1] - 1) if len(bench_cum) else None
                    if result.benchmark_return is not None and result.total_return is not None \
                            and result.annualized_return is not None:
                        years_b = len(bench) / 252.0
                        if years_b > 0:
                            result.annualized_excess_return = (
                                result.annualized_return
                                - (float((1 + result.benchmark_return) ** (1 / years_b) - 1))
                            )

                # 净值曲线（index 可能为单层 datetime 或 MultiIndex）
                nav = []
                if isinstance(ret.index, pd.MultiIndex):
                    dates = [ts.strftime("%Y-%m-%d") for ts in ret.index.get_level_values("datetime")]
                else:
                    dates = [pd.Timestamp(d).strftime("%Y-%m-%d") if not isinstance(d, str) else str(d)[:10]
                             for d in ret.index]
                nav_values = (1 + ret).cumprod()
                bench_nav = (1 + bench).cumprod() if bench is not None else None
                for i, d in enumerate(dates):
                    pt = {"date": d, "value": round(float(nav_values.iloc[i]), 6)}
                    if bench_nav is not None:
                        pt["benchmark"] = round(float(bench_nav.iloc[i]), 6)
                    nav.append(pt)
                result.nav = nav[:800]
        except Exception as e:
            result.message = f"指标提取异常: {e}"
    else:
        result.message = "未找到回测报告(report_normal)"
    return result


def _get_segment_end_account(par, recorder) -> Optional[float]:
    """获取该段回测的期末账户总值。优先从 report_normal 的 account 列取最后一天。"""
    report = None
    for key in ["portfolio_analysis/report_normal_1day.pkl", "report_normal_1day.pkl"]:
        try:
            report = recorder.load_object(key)
            if report is not None:
                break
        except Exception:
            continue
    if report is None or not hasattr(report, "columns") or "account" not in report.columns:
        return None
    try:
        acct = report["account"].dropna()
        if len(acct) > 0:
            return float(acct.iloc[-1])
    except Exception:
        pass
    return None


def _extract_trades(par, recorder) -> list:
    """从回测的 Indicator 对象中提取逐日逐笔调仓记录（含成交价、成本、滑点）。"""
    import glob
    import pickle
    import pandas as pd

    def _load_from_file() -> object:
        """从 mlruns 目录加载最新 indicators 对象；失败返回 None。"""
        try:
            from ..config import WORK_DIR
            # WORK_DIR 形如 backend/app/../workdir，需 normpath 后取 dirname 得到 backend 目录
            base = os.path.normpath(os.path.dirname(WORK_DIR))
        except Exception:
            base = os.path.abspath(".")
        patterns = [
            os.path.join(base, "mlruns", "*", "*", "artifacts", "portfolio_analysis",
                         "indicators_normal*_obj.pkl"),
            os.path.join(base, "..", "mlruns", "*", "*", "artifacts", "portfolio_analysis",
                         "indicators_normal*_obj.pkl"),
        ]
        files = []
        for p in patterns:
            files.extend(glob.glob(p))
        if not files:
            return None
        files.sort(key=lambda f: os.path.getmtime(f))
        try:
            with open(files[-1], "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    indicator = None
    # 方式一：从 recorder 加载 indicators 对象
    for key in ["portfolio_analysis/indicators_normal_1day_obj.pkl",
                "indicators_normal_1day_obj.pkl",
                "portfolio_analysis/indicators_normal_day_obj.pkl"]:
        try:
            indicator = recorder.load_object(key)
            if indicator is not None:
                break
        except Exception:
            continue

    # 校验方式一的 indicator 是否包含订单历史；否则回退到文件
    def _get_his(ind) -> dict:
        if ind is None:
            return {}
        h = getattr(ind, "order_indicator_his", None)
        return h if h else {}

    his = _get_his(indicator)
    if not his:
        indicator = _load_from_file()
        his = _get_his(indicator)

    if not his:
        return []

    # 从 Indicator 的 order_indicator_his 提取每笔订单
    trades = []
    try:
        for trade_date, oi in his.items():
            td = oi.get_index_data("trade_dir")
            if td is None or len(td.index) == 0:
                continue
            deal_amount = oi.get_index_data("deal_amount")
            trade_price = oi.get_index_data("trade_price")
            trade_cost = oi.get_index_data("trade_cost")
            trade_value = oi.get_index_data("trade_value")
            amount = oi.get_index_data("amount")
            ffr = oi.get_index_data("ffr")

            date_str = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
            for i in range(len(td.index)):
                sid = td.index[i]
                # qlib OrderDir: SELL=0, BUY=1；这里统一转成 1=买入, -1=卖出
                raw_dir = td.data[i] if td.data[i] is not None else None
                direction = None
                if raw_dir is not None:
                    try:
                        direction = 1 if float(raw_dir) > 0 else -1
                    except Exception:
                        direction = None
                trades.append({
                    "date": date_str,
                    "instrument": str(sid),
                    "direction": direction,          # 1=买入, -1=卖出
                    "amount": float(amount.data[i]) if amount is not None and i < len(amount.data) else None,
                    "deal_price": float(trade_price.data[i]) if trade_price is not None and i < len(trade_price.data) else None,
                    "trade_value": float(trade_value.data[i]) if trade_value is not None and i < len(trade_value.data) else None,
                    "trade_cost": float(trade_cost.data[i]) if trade_cost is not None and i < len(trade_cost.data) else None,
                    "ffr": float(ffr.data[i]) if ffr is not None and i < len(ffr.data) else None,
                })
    except Exception:
        pass
    return trades
