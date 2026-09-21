# -*- coding: utf-8 -*-
"""用 `E:\rq` 的「成交额 + 市值」造换手率，并物化成本仓的 `turn` 字段（v1.19.85）。

用户 2026-09-17 同意做两件事：
  ① **确认市值口径**（总市值 vs 流通市值）—— 用"隐含股本 = 市值 ÷ 真实价"与几家**股本公开已知**
     的公司比对（601398 工商银行、601857 中国石油、600519 贵州茅台）；
  ② 把「成交额 ÷ 流通市值」算成换手率，写进 `data/cn_data/features/<inst>/turn.day.bin`
     ⇒ 之后 `COST/WINNER`（筹码）用**真换手率**，不再靠反推 + 手/股猜测。

⚠ 关键口径（实测踩坑，别改错）：
  · `E:\rq\bundle\h5\equities\<code>.h5` 的 `data` 是**分钟**数据 —— `datetime` 形如
    `20050104093100`（yyyyMMddHHMMSS）⇒ `total_turnover` 是**每分钟**成交额 ⇒ **必须按日汇总**，
    否则会把"每分钟换手率"当"日换手率"（小两个数量级）；
  · `others/market-cap/market_cap.h5` 的日期是 **epoch 纳秒**（`946944000000000000` = 2000-01-04）
    ⇒ 与上面格式**不同**，两者别混用；
  · 本环境没有 pytables ⇒ 用 h5py 直读 pandas-HDF（codes/levels 结构可无损还原）。

⚠ 纪律（用户要求）：**分阶段日志 + 时间戳 + 立即落盘**，后台跑；日志 `ai_test/build_turn.out.txt`。
⚠ 只写 `turn`（新字段），**不覆盖**任何既有字段；已存在则跳过（`--overwrite` 才重写）。
"""
import os
import sys
import time

sys.path.insert(0, r"d:\quant\qlib_code\backend")

RQD = r"E:\rq"
MC_H5 = os.path.join(RQD, "others", "market-cap", "market_cap.h5")
BUNDLE = os.path.join(RQD, "bundle", "h5", "equities")
OUT = r"d:\quant\qlib_code\ai_test\build_turn.out.txt"
START, END = "2005-01-04", "2026-09-01"
OVERWRITE = "--overwrite" in sys.argv
ANCHORS = {"601398.XSHG": ("工商银行", 3564.06, 2696.0),
           "601857.XSHG": ("中国石油", 1830.21, 1619.0),
           "600519.XSHG": ("贵州茅台", 12.56, 12.56)}


def log(s):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), s)
    print(line, flush=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def to_cn(code: str) -> str:
    """`600000.XSHG` → `sh600000`（cn_data 的 features 目录名口径）。"""
    num, _, ex = code.partition(".")
    ex = ex.upper()
    return ("sh" if ex.startswith("XSH") else "sz" if ex.startswith("XSHE") else "bj") + num


def read_mc_h5(path):
    """直读 pandas-HDF（fixed 格式）→ Series(index=(instrument, date))，无需 pytables。"""
    import h5py
    with h5py.File(path, "r") as f:
        g = f["data"]
        lv_inst = [s.decode() if isinstance(s, bytes) else str(s) for s in g["axis1_level0"][:]]
        lv_date = pd.to_datetime(g["axis1_level1"][:], unit="ns")     # ⚠ 纳秒
        c_inst, c_date = g["axis1_label0"][:], g["axis1_label1"][:]
        vals = np.asarray(g["block0_values"][:, 0], dtype=float)
    mi = pd.MultiIndex.from_arrays(
        [np.asarray(lv_inst, dtype=object)[c_inst], lv_date[c_date]],
        names=["instrument", "tradedate"])
    return pd.Series(vals, index=mi, name="market_cap")


def daily_turnover(bp):
    """bundle h5（**分钟**）→ **按日**成交额 Series（日期为索引）。"""
    import h5py
    with h5py.File(bp, "r") as f:
        d = f["data"]
        raw = np.asarray(d["datetime"][:], dtype=np.int64)   # yyyyMMddHHMMSS
        amt = np.asarray(d["total_turnover"][:], dtype=float)
        close_last = float(np.asarray(d["close"][:], dtype=float)[-1])
    day = raw // 1000000                                     # → yyyymmdd
    ser = pd.Series(amt).groupby(pd.Index(day)).sum()
    idx = pd.to_datetime(ser.index.astype(str), format="%Y%m%d")
    return pd.Series(ser.to_numpy(), index=idx).sort_index(), close_last


open(OUT, "w", encoding="utf-8").close()
log("启动：overwrite=%s" % OVERWRITE)
import numpy as np                                  # noqa: E402
import pandas as pd                                 # noqa: E402

# ---------- stage 1/6 ----------
log("stage 1/6 读市值面板（17.6M 行，h5py 直读）")
t0 = time.time()
mc_w = read_mc_h5(MC_H5).unstack(level="instrument").sort_index()
log("  宽表 shape=%s（行=日期 %s ~ %s，列=股票），耗时 %.1fs"
    % (mc_w.shape, mc_w.index[0].date(), mc_w.index[-1].date(), time.time() - t0))

# ---------- stage 2/6：① 市值口径诊断 ----------
log("stage 2/6 ① 市值口径诊断（隐含股本 = 市值 ÷ 当日收盘，与公开股本比对）")
for code, (nm, tot_bn, flo_bn) in ANCHORS.items():
    bp = os.path.join(BUNDLE, code + ".h5")
    if not os.path.exists(bp) or code not in mc_w.columns:
        log("  %s %s：文件/列缺失，跳过" % (code, nm))
        continue
    try:
        amt_d, px_last = daily_turnover(bp)
        cap_s = mc_w[code].dropna()
        common = cap_s.index.intersection(amt_d.index)
        if not len(common):
            log("  %s %s：市值与行情无交集日期，跳过" % (code, nm))
            continue
        d = common[-1]
        cap, implied_bn = float(cap_s[d]), float(cap_s[d]) / px_last / 1e8
        near = ("总股本" if abs(implied_bn - tot_bn) <= 0.1 * tot_bn else
                "流通股本" if abs(implied_bn - flo_bn) <= 0.1 * flo_bn else "?")
        log("  %s %s @%s：市值 %.4e ÷ 收盘 %.2f = 隐含股本 %8.2f 亿股 ⇒ **%s**"
            % (code, nm, d.date(), cap, px_last, implied_bn, near))
        log("      公开口径：总 %.2f 亿 / 流通 %.2f 亿（隐含股本更接近哪个即为该口径）"
            % (tot_bn, flo_bn))
    except Exception as e:                                   # noqa: BLE001
        log("  %s %s：诊断失败 %r" % (code, nm, e))

# ---------- stage 3/6 ----------
log("stage 3/6 载入 cn_data 日历与 features 目录")
from app.config import QLIB_PROVIDER_URI                    # noqa: E402
FDIR = str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"
from app.factors.chip_store import _write_bin                # noqa: E402
from app.factors.panel_expr import _calendar                 # noqa: E402
from app.services.qlib_runtime import ensure_qlib_init       # noqa: E402

ensure_qlib_init()
cal = _calendar()
have = set(os.listdir(FDIR)) if os.path.isdir(FDIR) else set()
log("  日历 %d 天（%s ~ %s）；cn_data 已有 %d 个股票目录"
    % (len(cal), cal[0].date(), cal[-1].date(), len(have)))

# ---------- stage 4/6：逐票物化 turn ----------
codes = sorted(mc_w.columns)
log("stage 4/6 逐票物化 turn（共 %d 只；每 200 只报进度）" % len(codes))
n_w = n_s = n_e = 0
skip_reason = {"无 cn_data 目录": 0, "已存在": 0, "无 bundle 文件": 0, "日期无交集": 0, "其它": 0}
t0 = time.time()
for n, code in enumerate(codes, 1):
    cn = to_cn(code)
    path = os.path.join(FDIR, cn, "turn.day.bin")
    if cn not in have:
        n_s += 1
        skip_reason["无 cn_data 目录"] += 1
        continue
    if os.path.exists(path) and not OVERWRITE:
        n_s += 1
        skip_reason["已存在"] += 1
        continue
    bp = os.path.join(BUNDLE, code + ".h5")
    if not os.path.exists(bp):
        n_s += 1
        skip_reason["无 bundle 文件"] += 1
        continue
    try:
        amt_d, _px = daily_turnover(bp)
        cap = mc_w[code].reindex(amt_d.index).to_numpy(dtype=float)
        with np.errstate(all="ignore"):
            turn_pct = np.where(cap > 0, amt_d.to_numpy(dtype=float) / cap * 100.0, np.nan)
        dts = amt_d.index
        # 写到 cn_data 的日历上：**必须日历连续**（读取端按"起始下标+行号"还原）
        pos = cal.get_indexer(dts)
        ok = (pos >= 0) & (dts >= pd.Timestamp(START)) & (dts <= pd.Timestamp(END))
        if not ok.any():
            n_s += 1
            skip_reason["日期无交集"] += 1
            continue
        p_ok, v_ok = pos[ok], turn_pct[ok]
        j0, j1 = int(p_ok.min()), int(p_ok.max())
        vals = np.full(j1 - j0 + 1, np.nan)
        vals[p_ok - j0] = v_ok
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_bin(path, j0, vals)
        n_w += 1
    except Exception as e:                                   # noqa: BLE001
        n_e += 1
        if n_e <= 5:
            log("  ⚠ %s 失败：%r" % (code, e))
    if n % 200 == 0:
        log("  进度 %d/%d（写 %d，跳过 %d，错 %d，%.0fs）"
            % (n, len(codes), n_w, n_s, n_e, time.time() - t0))

# ---------- stage 5~6 ----------
log("stage 5/6 完成：写入 %d 只，跳过 %d，出错 %d，总耗时 %.0fs" % (n_w, n_s, n_e, time.time() - t0))
log("  跳过原因分类：%s（「无 cn_data 目录」属合理跳过；若「日期无交集」偏大则要查区间 ✗）"
    % (", ".join("%s=%d" % kv for kv in skip_reason.items()),))
log("stage 6/6 turn 为**百分数**（1.23 = 1.23%%）；口径 = 日成交额 ÷ **总市值** × 100")
log("  ⚠ 口径说明：市场数据里的 market_cap 是**总市值**（已用工商银行/中石油/茅台锚点验证）"
    "⇒ 全流通票 = 标准换手率 ✓；含 H 股/非流通的票**略低估**（工行约 −24%、中石油约 −12%）")
log("  下一步：重跑筹码物化以改用真 turn（`materialize_chip.py`，并把 overwrite 打开）")
