# -*- coding: utf-8 -*-
"""回测曲线/参数快照绘图模块（从 qlib_engine.py 拆分而来）。

回测完成后，用 matplotlib 生成「曲线 + 参数横排」快照图存入 artifacts。
纯绘图逻辑，依赖 BacktestRequest / BacktestResult / utils._add_period。
"""
import os

from ..models.backtest import BacktestRequest, BacktestResult
from .utils import _add_period


def _save_curve_snapshot(dir_path: str, req: BacktestRequest, result: BacktestResult):
    """回测完成后，用 matplotlib 生成一张「曲线 + 参数横排」快照图，存入 artifacts。

    - summary.png: 上半部策略净值（红粗） vs 基准（蓝）曲线；下半部参数横排（多列网格）
    - 兼容旧命名：也生成 nav_curve.png（仅曲线）与 params_snapshot.png（仅参数）
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 无界面后端
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec

        # 尝试使用中文字体（找不到就退化为英文标签）
        plt.rcParams["axes.unicode_minus"] = False
        try:
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
        except Exception:
            pass

        if not dir_path:
            return
        nav = (result.nav or []) if result else []
        if not nav:
            return

        dates = [p.get("date") for p in nav]
        values = [float(p.get("value", 1.0)) for p in nav]
        bench = [p.get("benchmark") for p in nav]

        # 收集结果指标与参数
        metrics = []
        if result.annualized_return is not None:
            metrics.append(("年化收益", "%.2f%%" % (result.annualized_return * 100)))
        if result.annualized_excess_return is not None:
            metrics.append(("年化超额", "%.2f%%" % (result.annualized_excess_return * 100)))
        if result.sharpe is not None:
            metrics.append(("夏普比率", "%.2f" % result.sharpe))
        if result.max_drawdown is not None:
            metrics.append(("最大回撤", "%.2f%%" % (result.max_drawdown * 100)))
        if result.win_rate is not None:
            metrics.append(("胜率", "%.2f%%" % (result.win_rate * 100)))
        if result.total_return is not None:
            metrics.append(("累计收益", "%.2f%%" % (result.total_return * 100)))
        if result.benchmark_return is not None:
            metrics.append(("基准收益", "%.2f%%" % (result.benchmark_return * 100)))

        # 格式化参数值（None → "不限"），便于图片展示
        def _fmt_val(v, digits=None):
            if v is None:
                return "不限"
            if digits is not None and isinstance(v, (int, float)):
                return ("%." + str(digits) + "f") % v
            return str(v)

        # 训练窗 / 测试窗：算出对应的具体日期区间（基于回测 start_date 反推训练起点）
        try:
            train_start_date = _add_period(req.start_date, -req.train_win, req.train_unit)
            train_label = f"{req.train_win}{req.train_unit[0]} ({train_start_date} ~ {req.start_date})"
        except Exception:
            train_label = f"{req.train_win}{req.train_unit[0]}"
        try:
            test_end_date = _add_period(req.start_date, req.test_win, req.test_unit)
            # 测试窗结束超过回测结束则截断
            if test_end_date > req.end_date:
                test_end_date = req.end_date
            test_label = f"{req.test_win}{req.test_unit[0]} ({req.start_date} ~ {test_end_date})"
        except Exception:
            test_label = f"{req.test_win}{req.test_unit[0]}"

        params_text = {
            "股票池": req.universe,
            "资金": "%.0f万" % ((req.initial_capital or 0) / 10000),
            "模型": req.model,
            "特征": req.feature,
            "TopK": str(req.topk),
            "持仓": f"{req.n_days_hold}天",
            "划分": "滚动" if (req.split_mode or "").lower() == "custom" else "一次性",
            "成交价": req.deal_price,
            "买费": "%.4f" % req.open_cost,
            "卖费": "%.4f" % req.close_cost,
            "滑点": "%.4f" % req.impact_cost,
            "量限制": _fmt_val(req.volume_threshold, 2),
            "涨跌停": _fmt_val(req.limit_threshold, 3),
            "每手": _fmt_val(req.trade_unit),
            # 把训练窗/测试窗放最后一行（最长），避免覆盖其他参数
            "训练窗": train_label,
            "测试窗": test_label,
        }

        # ---------- 主图：曲线(上) + 参数横排(下) ----------
        try:
            fig = plt.figure(figsize=(13, 8))
            # 上曲线：高度 2.2，下参数：高度 1.5，给参数区更多空间；增加 hspace 让参数与曲线分开
            gs = GridSpec(2, 1, height_ratios=[2.2, 1.5], hspace=0.45)

            # 上：曲线
            ax = fig.add_subplot(gs[0])
            ax.plot(dates, values, color="red", linewidth=3.0, label="策略净值")
            if any(b is not None for b in bench):
                bench_vals = [b if b is not None else float("nan") for b in bench]
                ax.plot(dates, bench_vals, color="blue", linewidth=1.5, label="基准")
            ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.8)
            ax.set_title("回测净值曲线  %s → %s  (模型: %s, 股票池: %s, 资金: %.0f万元)" % (
                req.start_date, req.end_date, req.model, req.universe,
                (req.initial_capital or 0) / 10000))
            # 不再显示 "日期" x 轴标签
            ax.set_xlabel("")
            ax.set_ylabel("净值")
            ax.legend(loc="upper left")
            if len(dates) > 20:
                step = max(1, len(dates) // 10)
                ax.set_xticks(dates[::step])
            fig.autofmt_xdate(rotation=45)

            # 下：参数横排（网格）
            ax2 = fig.add_subplot(gs[1])
            ax2.axis("off")
            # 把训练窗/测试窗（长字符串）放在主网格的最后两个位置，单独成行不被覆盖
            metrics_list = list(metrics)
            base_items = list(params_text.items())
            train_test_items = [(k, v) for k, v in base_items if k in ("训练窗", "测试窗")]
            other_items = [(k, v) for k, v in base_items if k not in ("训练窗", "测试窗")]

            ncols = 6
            # 第一区域：结果指标 + 主要参数
            main_items = [(k, v, "#0f766e") for k, v in metrics_list] + \
                         [(k, v, "black") for k, v in other_items]
            x_positions = [0.03 + 0.165 * c for c in range(ncols)]
            # 行高收紧（0.18），保证 20 项（4 行）能放下，且训练窗行在主区域之后仍有位置
            row_height = 0.18
            for idx, (k, v, color) in enumerate(main_items):
                r = idx // ncols
                c = idx % ncols
                y = 0.95 - r * row_height
                ax2.text(x_positions[c], y, f"{k}: {v}", ha="left", va="top", fontsize=8,
                         color=color)

            # 第二区域：训练窗/测试窗 单独一行（放在量限制/涨跌停/每手之后）
            n_main_rows = (len(main_items) + ncols - 1) // ncols
            y_train = 0.95 - n_main_rows * row_height - 0.04
            if y_train > 0.0:
                ax2.text(0.03, y_train, f"{train_test_items[0][0]}: {train_test_items[0][1]}",
                         ha="left", va="top", fontsize=7.5, color="black")
                ax2.text(0.55, y_train, f"{train_test_items[1][0]}: {train_test_items[1][1]}",
                         ha="left", va="top", fontsize=7.5, color="black")

            fig.savefig(os.path.join(dir_path, "summary.png"), dpi=100, bbox_inches="tight")
            plt.close(fig)
        except Exception:
            pass

        # ---------- 兼容：单独曲线图 ----------
        try:
            fig, ax = plt.subplots(figsize=(12, 6))
            ax.plot(dates, values, color="red", linewidth=3.0, label="策略净值")
            if any(b is not None for b in bench):
                bench_vals = [b if b is not None else float("nan") for b in bench]
                ax.plot(dates, bench_vals, color="blue", linewidth=1.5, label="基准")
            ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.8)
            ax.set_title("回测净值曲线  %s → %s" % (req.start_date, req.end_date))
            ax.set_xlabel("日期")
            ax.set_ylabel("净值")
            ax.legend()
            if len(dates) > 20:
                step = max(1, len(dates) // 12)
                ax.set_xticks(dates[::step])
            fig.autofmt_xdate(rotation=45)
            fig.tight_layout()
            fig.savefig(os.path.join(dir_path, "nav_curve.png"), dpi=100)
            plt.close(fig)
        except Exception:
            pass

        # ---------- 兼容：单独参数图 ----------
        try:
            fig2, ax2 = plt.subplots(figsize=(10, 9))
            ax2.axis("off")
            ax2.text(0.5, 0.96, "回测参数快照", ha="center", va="top", fontsize=16, fontweight="bold")
            y = 0.90
            ax2.text(0.03, y, "回测结果指标：", ha="left", va="top", fontsize=12, fontweight="bold")
            y -= 0.045
            for k, v in metrics:
                ax2.text(0.05, y, f"{k}:  {v}", ha="left", va="top", fontsize=11)
                y -= 0.04
            y -= 0.02
            ax2.text(0.03, y, "回测参数：", ha="left", va="top", fontsize=12, fontweight="bold")
            y -= 0.045
            for k, v in params_text.items():
                ax2.text(0.05, y, f"{k}:  {v}", ha="left", va="top", fontsize=11)
                y -= 0.038
            fig2.tight_layout()
            fig2.savefig(os.path.join(dir_path, "params_snapshot.png"), dpi=100)
            plt.close(fig2)
        except Exception:
            pass
    except Exception:
        pass
