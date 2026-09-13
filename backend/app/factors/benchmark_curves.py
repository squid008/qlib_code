# -*- coding: utf-8 -*-
"""指数基准曲线（单因子测试页「持仓期收益曲线」的可切换基准，v1.18.45）。

口径（**必须与组合曲线同口径**，否则"超额"没有意义）：
  · 组合每期收益 = 调仓日 T 建仓、**持有 h 个交易日**的组合收益
    （面板 `LABEL` = `Ref($close, -(h+1))/Ref($close, -1) - 1` ⇒ T+1 收盘买入、T+h+1 收盘卖出）；
  · 基准同口径 = **指数在同一个调仓日的 T+1 → T+h+1 收益**，各期**算术累加**（与组合曲线同）；
  · 基准用**价格指数**（不含分红），只作同口径对照，**不是全收益指数**。

候选只列 `data/cn_data` 里**确有行情**的指数（上证综指 `SH000001`、创业板指 `SZ399006`
在该数据集里没有 ⇒ 别加）；默认基准复用回测页的映射 `engine.utils._pick_benchmark`
（csi300→沪深300、csi500→中证500、csi800→中证800、csi1000→中证1000，其余→沪深300）。
"""
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from app.engine.perf_metrics import compute_perf

# (代码, 展示名)。⚠ 只放确有行情的；新增前先确认 `data/cn_data/features/<code 小写>` 存在
BENCHMARKS = (
    ("SH000300", "沪深300"),
    ("SH000852", "中证1000"),
    ("SH000905", "中证500"),
    ("SH000906", "中证800"),
    ("SH000985", "中证全指"),
)
BENCH_CODES = tuple(c for c, _ in BENCHMARKS)
BENCH_NAMES = dict(BENCHMARKS)


def default_benchmark(universe: str) -> str:
    """与回测页同源的默认基准（股票池 → 指数）；映射不到时落沪深300。"""
    try:
        from app.engine.utils import _pick_benchmark

        code = str(_pick_benchmark(universe or "", []))
    except Exception:
        code = "SH000300"
    return code if code in BENCH_NAMES else "SH000300"


def load_bench_close(codes: Iterable[str] = BENCH_CODES, start=None, end=None,
                     cancelled=None) -> Optional[pd.DataFrame]:
    """取多个指数收盘 → 宽表（index = 交易日历升序、columns = 有数据的代码）。

    ⚠⚠ **走项目的面板求值器 `panel_features`，不要换回 qlib `D.features`** —— 实测
    （`ai_test/bench_bench_close.py`，5 个指数 / 2021-01-01~2026-12-31）：

    | 路径 | 冷 | 热 |
    |---|---|---|
    | `panel_features`（本实现） | **48ms** | **4ms** |
    | `D.features` | 11.33s | **11.12s** |

    即 `D.features` 的 ~2.2s/只 是**每次调用的固定开销**（不是一次性冷启动），接进单因子测试
    会让每次请求 +11s（item_s 才 ~7s）；面板求值器正是本项目为绕开它而写的（同一条读数据路径，
    口径也更一致）。

    多取几个候选是为了**前端纯切换、零重算**（定稿口径）。取数区间与特征面板一致
    （含尾部 h+1 个交易日的延展）⇒ 最后一期基准也能算出来。失败/无数据返回 `None`
    —— 基准是可选项，**不阻塞**单因子测试主流程（前端显示"未取到基准行情"）。
    """
    want = [str(c) for c in codes]
    if not want:
        return None
    try:
        from app.factors.panel_expr import panel_features

        pdf = panel_features(want, [("$close", "CLOSE")], start, end,
                             cancel_cb=cancelled, warmup_days=0)
    except Exception:
        return None
    if pdf is None or len(pdf) == 0 or "CLOSE" not in pdf.columns:
        return None
    try:
        s = pdf["CLOSE"]
        wide = s.unstack(level=s.index.names.index("instrument")).sort_index()
    except Exception:
        return None
    wide = wide.reindex(columns=[c for c in want if c in wide.columns])
    if not wide.shape[1] or len(wide) < 2:
        return None
    # ⚠ 指数是**价格水平**，缺日（数据洞/临时无行情）按**前收盘延续** —— 只改 NaN 那两个点，
    #   非 NaN 的值一个不动。
    #   动机（2026-09-12 用户反馈「中证1000 只到 2025-04」）：面板里 `SH000852` 在
    #   **2025-04-16 恰好有一个 NaN**，而 `period_return_cums` 原本「一期取不到价 ⇒ 其后全
    #   None」⇒ 整条基准线从那天断掉（`ai_test/diag_bench_range.py` 定位）。
    #   开盘那段（序列头部）的 NaN 仍保留 ⇒ 对应期仍是 None（不往前编数据）。
    wide = wide.ffill()
    return wide


def period_return_cums(close: pd.DataFrame, reb_dates, horizon: int) -> dict:
    """`{code: {"name": …, "cum": [float|None, …], …}}`：各指数**逐调仓期收益的算术累加**。

    `close` —— `load_bench_close()` 的宽表（index 覆盖调仓日**及之后 h+1 个交易日**）。
    每期 = `close[T+1] → close[T+h+1]`（与组合 `LABEL` **完全同口径**），各期**算术累加**。

    ⚠⚠ **`T+h+1` 越界（数据尾部不足）时必须用「该指数最后一个收盘价」兜底 —— 不许给 None**
    （2026-09-13 用户报「负市值对数的基准曲线怎么 2026-08-14 就没了？几个分组的曲线倒是有的」）：
    组合侧的 `freeze_suspended_price=True`（默认）会重算 label ——
    `single_test.py` 的「冻结价 label 兜底」：
        exit_px = Ref($close, -(h+1))；**越界处 `where(notna, last_c)` 兜底成该股最后一个收盘价**
        （`last_c = CLOSE.ffill().groupby(inst).transform("last")`），entry_px = Ref($close, -1)
    ⇒ **最后一期是「不完全持有窗口」**（例：调仓日 2026-08-14、数据尾 2026-08-21，实际只持有 4 个交易日），
    组合/分组曲线照常有值。基准若坚持"越界即 None"，就会**比组合少最后一段**（图上表现为基准线提前断掉、
    最后一期算不出超额）。故这里逐字对齐：卖点越界 ⇒ `p2 = 该列最后一个有限价`（部分持有期），
    并在 `n_partial` 里回传**用了兜底的期数**（前端据此提示"末期持有窗口不足 h 日"）。

    缺失语义（三档，**不要混**）：
      · **买点 `T+1` 越界/无价** ⇒ 该期 `None`（组合侧 entry_px 也是 NaN ⇒ label 亦为 NaN，同口径）；
      · **卖点 `T+h+1` 越界** ⇒ **用最后价算部分持有期**（上面那条，**不是** None）；
      · **中间某期取不到价**（单个空洞/停牌）⇒ **只有该期** `None`，**后续照算**。
        ⚠ 曾把"中间空洞"与"尾部越界"都按"其后全 None"处理 ⇒ 面板里 `SH000852` 2025-04-16 的
        一个空洞把整条基准线从那天截断（用户当场发现）；`load_bench_close` 的 `ffill` +
        这里的"只断当期"是两道独立防线。
    前端 Recharts 遇 null 会断开该点，比"猜一个数接着画"诚实。
    """
    h = max(0, int(horizon or 0))
    idx = close.index
    n = len(idx)
    out = {}
    for code in close.columns:
        s = close[code]
        sarr = s.to_numpy(dtype=np.float64)
        # ⚠ 尾部兜底价 = 该列**最后一个有限值**（= 面板最后一日的收盘价）。与 label 侧的
        #   `last_c = CLOSE.ffill().groupby(inst).transform("last")` 同义 ⇒ 两侧同口径。
        _fin = np.flatnonzero(np.isfinite(sarr))
        last_px = float(sarr[_fin[-1]]) if _fin.size else float("nan")
        # v1.18.47：同时给**算术累加**与**复利累乘**两条（前端默认显示复利，与组合曲线同口径）；
        # 缺失语义两者完全一致。
        cums, cums_cmp = [], []
        cum, navc = 0.0, 1.0
        n_partial = 0            # 用「最后价兜底」的期数（= 末期持有窗口不足 h 个交易日）
        # v1.18.48：逐日盯市净值（与组合同构：期内 [T+1, T+h] 走指数逐日、期末由期收益推进）
        # → 供「年化/最大回撤/夏普/索提诺/卡玛」使用（基准与组合同口径才有比较意义）。
        nav_arr = np.ones(n, dtype=np.float64)
        gd, cur = 1.0, 0
        _begb = 0                        # 第一个有效期的 T+1 位置（裁掉预热段的平坦点）
        for d in reb_dates:
            t = pd.Timestamp(d)
            pos = int(idx.searchsorted(t))
            if pos >= n or idx[pos] != t:
                # 调仓日不在行情日历内（超出尾部/缺口）⇒ 该期 None（**不**牵连后续期）
                cums.append(None)
                cums_cmp.append(None)
                continue
            i1 = pos + 1
            p1 = float(sarr[i1]) if i1 < n else float("nan")
            if not np.isfinite(p1) or p1 <= 0:
                # 买点（T+1）越界/无价：组合侧 entry_px 同是 NaN ⇒ label 亦 NaN ⇒ 同为 None
                cums.append(None)
                cums_cmp.append(None)
                continue
            # ⚠⚠ 卖点 T+h+1 越界 ⇒ **最后价兜底（不完全持有期）**，与组合 label 的冻结价兜底逐字对齐
            i2 = pos + 1 + h
            partial = i2 >= n
            p2 = last_px if partial else float(sarr[i2])
            if not np.isfinite(p2) or p2 <= 0:
                cums.append(None)                # 仅当期缺（后续照算）
                cums_cmp.append(None)
                continue
            if partial:
                n_partial += 1
            rr = p2 / p1
            cum += (rr - 1.0)
            navc *= rr
            cums.append(round(cum, 6))
            cums_cmp.append(round(navc - 1.0, 6))
            # 逐日路径（相对本期期初 p1）；卖点取 min(T+h+1, 数据尾下一格) —— 部分持有期时净值
            # 只推进到数据尾（与 `p2 = 最后价` 的期收益一致）
            if _begb == 0:
                _begb = i1               # 首个调仓期的 T+1（此前是预热/建仓前的平坦段）
            if i1 > cur:
                nav_arr[cur:i1] = gd
            i2c = min(i2, n)
            seg = sarr[i1:i2c] / p1               # [T+1, T+h]
            if seg.size and np.all(np.isfinite(seg)):
                nav_arr[i1:i2c] = gd * seg
            gd = gd * rr
            nav_arr[i2c - 1] = gd
            cur = i2c
        if cur < n:
            nav_arr[cur:] = gd
        # ⚠ 两边都要截：`close` 宽表带 warmup 前段（起点比组合面板早约 250 交易日）与尾部
        #   h+1+3 天延展；不截会把这两段平坦期计入样本数、摊薄年化/波动（实测 1471 → 1212）。
        _endb = cur if 0 < cur < n else n
        _b = _begb if 0 <= _begb < _endb else 0
        net = np.empty(max(_endb - _b, 0), dtype=np.float64)
        if net.size:
            net[0] = 0.0                     # 起点当天无收益（相对起点）
            if net.size > 1:
                net[1:] = nav_arr[_b + 1:_endb] / nav_arr[_b:_endb - 1] - 1.0
        out[str(code)] = {"name": BENCH_NAMES.get(str(code), str(code)),
                          "cum": cums, "cum_compound": cums_cmp,
                          # 用「最后价兜底」的期数（>0 ⇒ 末期持有窗口不足 h 日，与组合同口径；
                          # 前端据此提示用户，避免把部分持有期的收益当完整一期读）
                          "n_partial": n_partial,
                          "perf": compute_perf(net)}
    return out
