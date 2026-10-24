#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**从真实行情抽取一个小夹具**（★ v2.11.70）—— 让 `datareq` 用例能在 CI 里真跑。

## 为什么需要它
夜间 job 里 `pytest -m datareq` 实测：**passed=13 / failed=10 / skipped=15** ✓ ——
那 15 条 skip 的原因是"**本机没真数据**"✗（守卫要求真实行情特有的性质：涨跌停、
停牌、真复权因子、长假日历 ✓）。它们的断言**恰恰是最有价值的**（净值起点、
不跌停才买得进、量纲、科创板剔除 ✓）—— 却因为 CI 没数据而**永远不跑** ✗。

⇒ 抽一份**小但真实**的夹具（默认 200 只 × 近 3 年 ✓，zip 后 ~几 MB ✓）：
· 进 **release asset**（不塞仓库、不脏历史 ✓），夜间 job 下载后解压 ✓；
· 断言用的就是**真数据**（真涨跌停/真停牌/真复权/真长假 ✓）⇒ 那 15 条从 skip 变 pass ✓。

## 为什么不是"再写一套合成数据"
合成数据是**随机游走** ✗ ⇒ 上面那些性质**天然不存在** ✓（`make_synth_qlib.py` 的注释里
已经记过：`factor=1.0` 让 3 条复权用例必须真数据 ✓）。

## 用法
```bash
# ① 本机抽（需要能读到真 cn_data ✓）
python scripts/make_real_fixture.py --n 200 --out data/_real_fixture

# ② 打包（发布成 release asset ✓）
python scripts/make_real_fixture.py --n 200 --zip data/real_fixture.zip
```

⚠ **夹具里写了什么**要看 `_real_fixture.json`（代码清单 / 日期区间 / 来源 ✓）——
以后有人怀疑"CI 用的是不是真数据"，看这个文件 ✓（不是看我的记忆 ✗）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile

import pandas as pd

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPTS)
sys.path.insert(0, _SCRIPTS)
# ⚠ 必须把 `backend/` 也加进来 ✗→✓ —— 本脚本要 `from app.config import QLIB_PROVIDER_URI`
#   （读的是**后端配置**，与运行时同源 ✓），而在仓库根跑时 `app` 是导不到的 ✗
#   （实测：`ModuleNotFoundError: No module named 'app'` ✓）。
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from make_synth_qlib import FIELDS, dump  # noqa: E402  —— 复用**同一个** bin 写入器 ✓

#: 断言里点名的代码 ⇒ **必须在夹具里** ✓（否则那些用例会 skip 或算错对象 ✗）
CORE_CODES = ["SH600000", "SH600519", "SZ000001", "SH601318", "SZ000002"]
#: 科创板（`exclude_stock_kcb` 那条真数据校验要用 ✓）
KCB_CODES = ["SH688111", "SH688981", "SH688036"]

#: ⭐ **用例里写死的标的**（★ v2.11.71）—— 不放进夹具 ⇒ 那些用例永远"没数据"✗：
#: · `test_signal_pool_wide` 的 10 只（它比较"快路径 vs 慢路径"逐位相同 ✓）
#: · `test_daily_bars_adjust` 的 `SH600000` ✓（已在 CORE_CODES 里 ✓）
#: ⇒ 造夹具时必须**按用例需求**挑，而不是"随便挑 200 只"✗ —— 上一版就吃了这个亏：
#:   两条 signal_pool_wide 因为返回 `None` 一直红 ✓（区间也不对：2021 ✗）。
POOL_WIDE_CODES = ["SH600000", "SH600519", "SZ000001", "SZ300750", "SH601318",
                   "SZ000651", "SH600036", "SZ002415", "SH688981", "SZ300059"]

#: ⚠ 起点必须 **2021-01-01** ✗→✓ —— `test_signal_pool_wide` 与 `test_daily_bars_adjust`
#: 都写死了 2021 年的区间 ✓，而第一版夹具从 2024 起 ⇒ 它们拿不到数据 ⇒ `None` ✗
#: （表象是 `assert None is not None` / `IndexError: list index out of range` ✓）。
_START = "2021-01-01"


def _pick_codes(n: int, provider: str) -> list:
    """挑代码：**核心 5 + 科创 3 + 用例写死的 10**（必须）＋ 从真实池子补足到 n ✓。"""
    picked = list(dict.fromkeys(CORE_CODES + KCB_CODES + POOL_WIDE_CODES))
    pool = os.path.join(provider, "instruments", "csi300.txt")
    if os.path.isfile(pool):
        with open(pool, encoding="utf-8") as f:
            for ln in f:
                code = ln.split("\t")[0].strip().upper()
                if code and code not in picked:
                    picked.append(code)
                if len(picked) >= n:
                    break
    if len(picked) < n:                       # 池子不够就扫 features 目录（按名字排序，可复现 ✓）
        feat = os.path.join(provider, "features")
        for d in sorted(os.listdir(feat)):
            code = d.upper()
            if code not in picked:
                picked.append(code)
            if len(picked) >= n:
                break
    must = len(set(CORE_CODES + KCB_CODES + POOL_WIDE_CODES))
    return picked[: max(n, must)]


def _frames(codes: list, end: str, start: str = _START) -> tuple:
    """真数据 → `{code: DataFrame}` + calendar（走 qlib 读 ✓，与后端同一条链路 ✓）。"""
    import qlib
    from qlib.data import D

    from app.config import QLIB_PROVIDER_URI
    from app.services.qlib_runtime import ensure_qlib_init

    ensure_qlib_init()
    cal = pd.DatetimeIndex(D.calendar(start_time=start, end_time=end))
    if not len(cal):
        raise SystemExit("真日历为空 ⇒ 检查 QLIB_PROVIDER_URI（%s）" % QLIB_PROVIDER_URI)

    # ⚠ **不许写重复列** ✗ —— 我第一版把 `$close` 写了三次（想算 adjclose/raw_close）✓
    #   qlib 会因重名列直接抛 ✗。⇒ 只读**真实存在**的 8 列，其余按同口径自己算 ✓。
    # ⚠ `$limit_up` 也要读 ✗→✓ —— "量纲不变量"那条（`close_raw` 与 `limit_up` 同量纲 ✓）
    #   必须有它才跑得起来 ✓（第一版没加 ⇒ 实测 skip「三列无交集」✗）。
    exprs = ["$open", "$high", "$low", "$close", "$volume", "$amount", "$factor",
             "$change", "$limit_up"]
    df = D.features(codes, exprs, start_time=start, end_time=end, freq="day")
    if not len(df):
        raise SystemExit("真数据读回 0 行 ⇒ 代码/字段不匹配（检查 $factor / $amount 是否存在 ✓）")
    df.columns = ["open", "high", "low", "close", "volume", "amount", "factor", "change",
                  "limit_up"]

    frames = {}
    for code, g in df.groupby(level="instrument"):
        g = g.droplevel("instrument").reindex(cal).copy()
        g["date"] = cal
        # 与合成数据同口径（下游按这个约定读 ✓）：后复权价 = close / factor ✓
        g["adjclose"] = g["close"] / g["factor"]
        for src, dst in (("close", "preclose"), ("open", "preopen"),
                         ("high", "prehigh"), ("low", "prelow")):
            g[dst] = g[src].shift(1)
        g["vwap"] = (g["high"] + g["low"] + g["close"]) / 3.0
        g["prevwap"] = g["vwap"].shift(1)
        frames[str(code)] = g[["date"] + FIELDS + ["limit_up"]]
    return frames, cal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="标的数（默认 200 ✓ 够触发各种性质 ✓）")
    ap.add_argument("--start", default=_START, help="起点（默认 2021-01-01 ✓ 用例要）")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--out", default=os.path.join("data", "_real_fixture"))
    ap.add_argument("--zip", dest="zip_path", default="")
    a = ap.parse_args()

    from app.config import QLIB_PROVIDER_URI

    codes = _pick_codes(a.n, QLIB_PROVIDER_URI)
    print("  标的 %d 只（含核心 %d + 科创 %d）｜ 区间 %s ~ %s"
          % (len(codes), len(CORE_CODES), len(KCB_CODES), a.start, a.end))
    frames, cal = _frames(codes, a.end, a.start)
    print("  日历 %d 天 ｜ 实际读到 %d 只" % (len(cal), len(frames)))

    out = os.path.abspath(a.out)
    # clean=False ⇒ **不批量删**（safe-delete 守卫会掐掉 rmtree ✗，见 dump 的注释 ✓）
    dump(frames, cal, out, fields=FIELDS + ["limit_up"], clean=False)
    meta = {
        "_说明": "**真实行情**夹具（★ v2.11.70）—— 给 CI 跑 datareq 用；合成数据天然缺这些性质 ✗",
        "source": QLIB_PROVIDER_URI,
        "start": a.start, "end": a.end, "n_codes": len(frames),
        "core_codes": CORE_CODES, "kcb_codes": KCB_CODES,
        "fields": FIELDS + ["limit_up"],
        "生成命令": "python scripts/make_real_fixture.py --n %d" % a.n,
    }
    with open(os.path.join(out, "_real_fixture.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("  已写出 %s（含 _real_fixture.json 指纹 ✓）" % out)

    if a.zip_path:
        zp = os.path.abspath(a.zip_path)
        if os.path.exists(zp):
            os.remove(zp)
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for root, _d, fs in os.walk(out):
                for fn in fs:
                    p = os.path.join(root, fn)
                    z.write(p, os.path.relpath(p, out))
        print("  已打包 %s（%.1f MB）" % (zp, os.path.getsize(zp) / 1024 / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
