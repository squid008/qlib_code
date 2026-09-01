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


def _extract_result(par, recorder, initial_account: Optional[float] = None) -> BacktestResult:
    """从 PortAnaRecord 的回测产物中提取风险指标与净值。

    initial_account: 段初账户总值（含初始持仓市值）。传入时用 report 的 account 列计算收益
    （`account / initial_account - 1`），**绕开 qlib 的 return 列**——qlib 的 return 列在
    账户带初始持仓时会把"初始持仓市值"误算成首日收益（如 372%），导致段收益虚高爆炸。
    不传时回退 qlib return 列（仅兼容无初始持仓的旧场景）。
    """
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
            bench = report["bench"] if "bench" in report.columns else None

            # 净值曲线来源：
            #   - initial_account 提供且 report 有 account 列：用 account/initial_account（相对段初账户），
            #     绕开 qlib return 列把"初始持仓市值误算为收益"的问题（带持仓跨段传递时首日 return 虚高）。
            #   - 否则回退 qlib 的 return 列累乘。
            use_account = initial_account is not None and "account" in report.columns and initial_account > 0
            if use_account:
                acct = report["account"].astype(float)
                acct = acct.fillna(acct.ffill().bfill())
                nav_values = acct / float(initial_account)
                # 日收益：相邻日净值之比 - 1；首日无前值，直接 = 首日净值 - 1（相对段初账户）
                ret = nav_values.pct_change(fill_method=None)
                ret = ret.copy()
                if len(ret) > 0:
                    ret.iloc[0] = float(nav_values.iloc[0]) - 1.0
            else:
                ret = report["return"] if "return" in report.columns else None
                ret = ret.astype(float) if ret is not None else None
                nav_values = (1 + ret).cumprod() if ret is not None else None

            if ret is not None and len(ret) > 0:
                result.total_return = float(nav_values.iloc[-1] - 1)
                years = len(ret) / 252.0
                if years > 0 and result.total_return is not None:
                    result.annualized_return = float((1 + result.total_return) ** (1 / years) - 1)
                running_max = nav_values.cummax()
                dd = (nav_values / running_max - 1).min()
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


def _extract_end_position(recorder) -> Optional[dict]:
    """从 recorder 提取该段回测的段末持仓，转为 qlib account dict 格式。

    返回 {"cash": 现金, 股票代码: {"amount": 股数, "price": 价格}, ...}；
    用于滚动回测"段间持仓跨段传递"：下一段以该持仓作为初始账户，
    段首调仓时能卖出不在新 topk 的旧持仓（修复"只买不卖/免费清仓"BUG）。
    提取失败（无 positions 记录）返回 None，调用方回退纯现金账户。
    """
    try:
        import pandas as pd
        positions = None
        for key in ["portfolio_analysis/positions_normal_1day.pkl", "positions_normal_1day.pkl"]:
            try:
                positions = recorder.load_object(key)
                if positions is not None:
                    break
            except Exception:
                continue
        if not positions:
            return None
        # positions: {pd.Timestamp: Position}，取最后一天
        last_ts = sorted(positions.keys())[-1]
        pos = positions[last_ts]
        p = getattr(pos, "position", None)
        if not isinstance(p, dict):
            return None
        account = {}
        for k, v in p.items():
            if k in ("cash", "now_account_value", "now_weight"):
                continue
            if isinstance(v, dict) and v.get("amount"):
                entry = {"amount": float(v["amount"])}
                if v.get("price") is not None:
                    entry["price"] = float(v["price"])
                account[str(k)] = entry
        account["cash"] = float(p.get("cash") or 0.0)
        return account
    except Exception:
        return None


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
