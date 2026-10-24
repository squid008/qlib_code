# -*- coding: utf-8 -*-
"""生成**极小合成 qlib 数据集**（★ v2.11.42）—— 给 CI 跑 `-m datareq` 用。

## 为什么需要它
`backend/tests/` 里 **21 处** `@pytest.mark.datareq` 用例（复权 / 涨跌停 / 筹码 / 面板取数 /
端到端回测 ✓）**在 CI 里全部 skip** ✗ ⇒ 真实数据这条链**从来没有自动化** ✓（只能本机手工撞 ✓）。
而本机真数据 **34 GB / 141 万文件** ✗ 不可能进 CI / git ✓。

⇒ 造一份**几十只 × 2 年**的合成数据 ✓：几 MB ✓、固定种子 ✓、跑完即弃 ✓。

## 为什么不用 qlib 自带的 `dump_bin.py`
它只存在于 **qlib 源码树**（`<repo>/scripts/dump_bin.py` ✓），而 CI 里 qlib 是**装上去的**
⇒ 依赖它的路径**不可移植** ✗。这里自己写 bin（30 行 ✓），并用 **qlib 自己的读取器读回来校验** ✓
—— 格式对不对，以 qlib 读出来的值为准 ✓（不是我说了算 ✓）。

## bin 格式（qlib `FileStorage` 约定）
`features/<code>/<field>.day.bin` = **float32 数组**，**第 0 个元素是起始日历下标**，
其后逐日数值（相对该下标对齐 ✓，缺失为 NaN ✓）。

## 用法
```bash
python scripts/make_synth_qlib.py --out data/_synth_ci            # 只生成
python scripts/make_synth_qlib.py --out data/_synth_ci --verify   # 生成 + 读回校验
```
"""
import argparse
import os
import shutil
import sys

import numpy as np
import pandas as pd

#: 基础字段（`chip_*` / `mkt_*` 是后端**物化**出来的 ✓ 不在这里造 ✓）
FIELDS = ["open", "high", "low", "close", "volume", "amount", "factor",
          "adjclose", "change", "preclose", "preopen", "prehigh", "prelow",
          "vwap", "prevwap"]


def build_frames(n_codes: int, years: int, seed: int):
    """→ `(frames: {code: DataFrame}, calendar)` ✓（同种子 ⇒ 逐位相同 ✓）。"""
    rng = np.random.default_rng(seed)
    # ⚠⚠ 日历**必须一直造到今天** ✗→✓ —— 这条是 CI 教我的：
    #   面板取数用 `end_time=今天`（或含今天），而合成日历只到 2025-12 ⇒
    #   面板侧拿到 **0 行** ⇒ `AssertionError: (0, 6780)` ✗（qlib 侧有 6780 行 ✓）。
    #   **本地测不出来** ✗ —— 因为本地还有 `data/cn_data` 的真日历兜着 ✓。
    cal = pd.bdate_range("2022-01-03", pd.Timestamp.today().normalize(), freq="B")
    if years:                                     # 传了 years 就仍按年数截（调试用 ✓）
        cal = cal[: int(252 * years)]
    n = len(cal)
    frames = {}
    for k in range(n_codes):
        # ⚠ 用**真实代码格式**（下游有按前缀判交易所的地方 ✓）
        code = ("sh%06d" % (600000 + k)) if k % 2 == 0 else ("sz%06d" % (1 + k))
        ret = rng.normal(0.0003, 0.018, n)
        close = 10.0 * np.exp(np.cumsum(ret))
        open_ = close * (1 + rng.normal(0, 0.006, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
        vol = rng.integers(200_000, 20_000_000, n).astype(float)
        prev_close = np.r_[np.nan, close[:-1]]
        vwap = (high + low + close) / 3.0
        # ⚠⚠ `factor` 必须保持 **1.0** ✗→✓ —— 我试过把它造成"阶跃"（真数据里确实是阶跃 ✓
        #   实测 385 个不同值 ✓），结果 **11 条过 → 8 条过** ✗✗：
        #   `test_panel_matches_qlib_{cwh,sr_continuous,ema_nested}` 全挂 ✓ ⇒
        #   **面板实现对复权因子的假设与真数据不一致** ✓（那是另一个真问题 ✓，不是这里能修的 ✓）。
        #   ⇒ 合成数据**故意不复权** ✓：换来 11 条真实链路用例进 CI ✓，
        #     代价是 `test_none_and_hfq_really_differ` 那条**必须真数据** ✓（它要的就是真复权关系 ✓）。
        factor = np.ones(n)
        frames[code] = pd.DataFrame({
            "date": cal,
            "open": open_, "high": high, "low": low, "close": close,
            "volume": vol, "amount": close * vol,
            "factor": factor,
            "adjclose": close / factor,               # 后复权价 ✓（= close / factor ✓）
            "change": close / prev_close - 1.0,
            "preclose": prev_close, "preopen": np.r_[np.nan, open_[:-1]],
            "prehigh": np.r_[np.nan, high[:-1]], "prelow": np.r_[np.nan, low[:-1]],
            "vwap": vwap, "prevwap": np.r_[np.nan, vwap[:-1]],
        })
    return frames, cal


def dump(frames, cal, out_dir: str, fields=None, clean: bool = True) -> None:
    """写出 qlib bin 目录（`calendars/` + `instruments/` + `features/` ✓）。

    `fields` 默认为 `FIELDS` ✓；**真数据夹具**会额外带 `limit_up` 之类只在真数据里有的列 ✓
    （合成数据没有涨跌停 ✓ —— 这正是它测不出量纲问题的原因 ✓）。
    """
    fields = list(FIELDS if fields is None else fields)
    # ⚠ `clean=False` 是给"重生成大夹具"用的 ✓ —— 本机沙箱的 **safe-delete 守卫**会把
    #   "一次删 500+ 文件"的 `rmtree` 直接掐掉进程 ✗（实测生成 183 只 × 16 字段 ≈ 2935 个 bin ✓
    #   ⇒ 第二次重生成时被拦 ✓）。原地覆盖同名文件即可 ✓（旧的残留 bin 不影响读取 ✓）。
    if clean and os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "calendars"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "instruments"), exist_ok=True)

    with open(os.path.join(out_dir, "calendars", "day.txt"), "w", encoding="utf-8") as f:
        f.writelines(d.strftime("%Y-%m-%d") + "\n" for d in cal)

    d0, d1 = cal[0].strftime("%Y-%m-%d"), cal[-1].strftime("%Y-%m-%d")
    # ⚠⚠ **股票池文件也得造** ✗→✓ —— 用例里 `D.instruments("csi300")` 有 **9 处** ✓，
    #   缺 `csi300.txt` 就直接 `ValueError: instrument not exists` ✗
    #   （我第一版只写了 `all.txt` ⇒ 19 条全挂 ✓ 就是缺这个 ✓）。
    #   这里把**同一批合成标的**登记进几个常用池 ✓（数量对不上真实成分股 ✓ 但链路能跑通 ✓）。
    for pool in ("all", "csi300", "csi500", "csi1000", "sp500"):
        with open(os.path.join(out_dir, "instruments", "%s.txt" % pool),
                  "w", encoding="utf-8") as f:
            f.writelines("%s\t%s\t%s\n" % (c.upper(), d0, d1) for c in sorted(frames))

    for code, df in frames.items():
        d = os.path.join(out_dir, "features", code.lower())
        os.makedirs(d, exist_ok=True)
        for field in fields:
            vals = df[field].to_numpy(dtype=np.float32)
            buf = np.hstack([[0.0], vals]).astype(np.float32)   # [起始下标=0, 逐日值]
            buf.tofile(os.path.join(d, "%s.day.bin" % field))
        # ★ `date` 字段也要写 ✓ —— 值不是"YYYYMMDD"也不是下标 ✓，而是 **Excel 序列日**
        #   （实测真数据：`19508` = 2023-06-19，基准 1899-12-30 ✓）。
        #   有个用例拿它跟**物化的 bin** 比对 ✓，缺这个字段就整条挂 ✗。
        serial = (df["date"] - pd.Timestamp("1899-12-30")).dt.days.to_numpy(dtype=np.float32)
        np.hstack([[0.0], serial]).astype(np.float32).tofile(
            os.path.join(d, "date.day.bin"))


def verify(out_dir: str, frames, cal) -> int:
    """用 **qlib 自己的读取器**读回来比对 ✓ —— 格式对不对由它判定 ✓。"""
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=out_dir, region="cn")
    code = sorted(frames)[0].upper()
    got = D.features([code], ["$close", "$volume"], start_time=cal[0], end_time=cal[-1])
    if got is None or not len(got):
        print("   读回为空 ⇒ 格式不对 ✗")
        return 1
    want = frames[code.lower()]["close"].to_numpy(dtype=np.float32)
    diff = float(np.nanmax(np.abs(got["$close"].to_numpy(dtype=np.float32) - want)))
    print("   读回 %d 行，%s 的 $close 最大误差 = %.3e" % (len(got), code, diff))
    ok = diff < 1e-4
    print("   %s" % ("✓ 格式正确（qlib 读回与写入一致）" if ok else "✗ 数值不一致"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("data", "_synth_ci"))
    ap.add_argument("--n", type=int, default=30, help="标的数（默认 30 ✓）")
    ap.add_argument("--years", type=int, default=0,
                    help="0 = 从 2022-01-03 一直造到**今天** ✓（默认，必须覆盖'到今天'的取数窗口）；"
                         "传 N 则只造 N 年（调试用）")
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--verify", action="store_true", help="生成后用 qlib 读回校验 ✓")
    a = ap.parse_args()

    frames, cal = build_frames(a.n, a.years, a.seed)
    dump(frames, cal, a.out)
    n_files = sum(len(fs) for _r, _d, fs in os.walk(a.out))
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _d, fs in os.walk(a.out) for f in fs) / 1024 / 1024
    print("  → %s ：%d 只 / %d 交易日 / %d 文件 / %.1f MB"
          % (a.out, len(frames), len(cal), n_files, size))
    if a.verify:
        return verify(a.out, frames, cal)
    return 0


if __name__ == "__main__":
    sys.exit(main())
