# -*- coding: utf-8 -*-
"""物化前复权价格字段（`preclose / preopen / prehigh / prelow / prevwap`）。

> 归档说明（2026-09-19）：本脚本原为个人 `ai_test/` 下的本地脚本 ⇒ **换机器拿不到**，
> 而它生成的数据**不在 git 里** ⇒ 新机器上 `forward`（前复权）会失效。现移入 `tools/`（入库），
> 步骤见 `md/deploy.md`「物化数据」一节。

## 为什么需要（见 README / `md/change_log.md` [1.19.97] + [1.19.103]）
本机 qlib 的 `$close/$open/$high/$low` 原生是**后复权价**（= 真实价 × `$factor`）。
`adjust_expr(..., "forward")` 会引用 `$pre*` 这几个**物化字段**（写成 `Add($pre*,0)`）：
- 缺失时 `forward` **必然失效** —— v1.19.96 之前 `forward` 曾"原样返回" ⇒ 价格量纲因子实际按
  **后复权价**排序，与米筐对不上（LLT K=20 年化 +9.03% ↔ 米筐 −5.24%）。

## 口径（严格照文档，勿自行发挥）
- `pre<field> = <field> / factor_last`，**`factor_last` = 该股 `factor.day.bin` 的最后一个值**；
  等价于"除以常数"，因此**排序与真实价（`$close/$factor`）在同一天截面上一致**，
  且五个字段**共用同一个 `factor_last`** ⇒ 同尺度、不会出现 `prehigh < prelow`；
- **各字段用自己 bin 的 `first_idx`**（输出轴 = 源字段轴，逐位不变）；
- 值序列原样除以常数 ⇒ NaN 保持 NaN；只写缺失文件（`--overwrite` 可全量重写）；
- ⚠ **数据更新后必须重跑**（`factor_last` 变 ⇒ 整条历史价缩放）。

用法（cwd 任意）：
    python tools/build_preclose.py               # 只补缺失
    python tools/build_preclose.py --overwrite    # 全量重写
之后跑 `python tools/verify_materialized.py` 核对。
"""
import os
import sys

_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, _BACKEND)

# 源字段 → 目标字段（与 backend/app/engine/adjust.py 的 _PRE_OF 对应）
PAIRS = (("close", "preclose"), ("open", "preopen"), ("high", "prehigh"),
         ("low", "prelow"), ("vwap", "prevwap"))


def features_dir() -> str:
    """数据目录（由后端 config 推导 ⇒ 与运行中的后端看到的是同一份）。"""
    from app.config import QLIB_PROVIDER_URI

    return str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"


def read_bin(path):
    """读字段文件 → (起始日历下标, 值数组)；缺失/空返回 (None, None)。"""
    import numpy as np

    if not os.path.exists(path):
        return None, None
    raw = np.fromfile(path, dtype="<f4")
    if raw.size == 0:
        return None, None
    return int(raw[0]), raw[1:]


def write_bin(path, first_idx, vals) -> None:
    import numpy as np

    with open(path, "wb") as f:
        np.asarray([float(first_idx)], dtype="<f4").tofile(f)
        np.asarray(vals, dtype="<f4").tofile(f)


def main() -> int:
    overwrite = "--overwrite" in sys.argv
    fdir = features_dir()
    print("数据目录: %s" % fdir, flush=True)
    if not os.path.isdir(fdir):
        print("✗ 数据目录不存在", flush=True)
        return 2

    insts = sorted(n for n in os.listdir(fdir) if os.path.isdir(os.path.join(fdir, n)))
    print("股票目录 %d 个 | overwrite=%s" % (len(insts), overwrite), flush=True)

    n_ok = n_skip = n_no_factor = 0
    miss_src: dict = {}
    for i, inst in enumerate(insts, 1):
        d = os.path.join(fdir, inst)
        if (not overwrite) and all(os.path.exists(os.path.join(d, dst + ".day.bin"))
                                   for _s, dst in PAIRS):
            n_skip += 1
            continue
        _fi, fvals = read_bin(os.path.join(d, "factor.day.bin"))
        if fvals is None or fvals.size == 0 or not (float(fvals[-1]) > 0):
            n_no_factor += 1
            continue
        factor_last = float(fvals[-1])
        wrote = 0
        for src, dst in PAIRS:
            out = os.path.join(d, dst + ".day.bin")
            if os.path.exists(out) and not overwrite:
                continue
            fi, vals = read_bin(os.path.join(d, src + ".day.bin"))
            if vals is None:
                miss_src[src] = miss_src.get(src, 0) + 1
                continue
            write_bin(out, fi, vals / factor_last)
            wrote += 1
        if wrote:
            n_ok += 1
        if i % 1000 == 0:
            print("   进度 %d/%d …" % (i, len(insts)), flush=True)

    print("写出股票数=%d | 已存在跳过=%d | 无 factor 跳过=%d" % (n_ok, n_skip, n_no_factor), flush=True)
    if miss_src:
        print("⚠ 源字段缺失计数: %s" % (miss_src,), flush=True)
    print("done", flush=True)
    return 0


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归，见 md/开发记录.md 2026-09-18 教训）
    sys.exit(main())
