# -*- coding: utf-8 -*-
"""物化筹码派生字段（`chip_cost_{5,30,75,95}` / `chip_win_{close,high,low}`）—— **分批版**。

> 归档说明（2026-09-19）：本脚本原为 `ai_test/` 下本地脚本（换机器拿不到）⇒ 现移入 `tools/`（入库），
> 步骤见 `md/deploy.md`「物化数据」一节。

## 为什么必须物化（见 `backend/app/factors/chip_store.py` 模块说明）
`$chip_*` 原本只在**面板求值器**按需计算；但回测/训练的特征加载走 **qlib** ⇒ 它把
`$chip_cost_95` 当普通字段去读 `features/<inst>/chip_cost_95.day.bin`，文件不存在 ⇒ 返回空序列 ⇒
报 `np.greater ... (6337, 0)` / `Can only identify-labeled Series`（v1.19.85 起物化解决）。

⚠ **缺失时不报错、静默全 NaN**：`panel_expr._chip_or_bin` 优先读 bin（清单 `chip_store.DEFAULT_FIELDS`），
**不检查文件是否存在** ⇒ 用 COST/WINNER 的公式（过顶 / 黏合强突破 / 蹦极新生 …）会**悄悄失效**。

## 为什么分批（2026-09-19 实测教训）
`chip_store.materialize` 一次传入**全部 ~6141 只**时会构造"全池一次性面板"
（4 个基础字段 × 6141 只 × 6455 日）⇒ 实测 **20 分钟 0 个文件写出、CPU 仅占 ~22% 单核、内存 5GB**，
`progress_cb` 又只在**某个字段全池写完后**才回调 ⇒ **进度不可见、ETA 不可估**。
改成 400 只/批后：单批 20~45s、**16 批共 ~9 分钟**跑完 6141 只 × 7 字段，且每批都落盘。
`overwrite=False` ⇒ **断点续跑安全**（中断后重跑只补缺失）。

用法（cwd 任意）：
    python backend/tools/materialize_chip.py            # 默认 400 只/批
    python backend/tools/materialize_chip.py 1000        # 自定义批大小
之后跑 `python backend/tools/verify_materialized.py` 核对。
"""
import os
import sys
import time

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = backend/（本脚本在 backend/tools/ 下）
sys.path.insert(0, _BACKEND)


def main() -> int:
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 400

    from app.services.qlib_runtime import ensure_qlib_init   # ⚠ 必须走统一入口（带 custom_ops）

    ensure_qlib_init()
    from app.config import QLIB_PROVIDER_URI
    from app.factors.chip_store import DEFAULT_FIELDS, materialize

    fdir = str(QLIB_PROVIDER_URI).rstrip("/\\") + "/features"
    if not os.path.isdir(fdir):
        print("✗ 数据目录不存在: %s" % fdir, flush=True)
        return 2

    allinst = sorted(n for n in os.listdir(fdir) if os.path.isdir(os.path.join(fdir, n)))
    todo = [n for n in allinst
            if os.path.exists(os.path.join(fdir, n, "close.day.bin"))
            and not os.path.exists(os.path.join(fdir, n, "chip_cost_95.day.bin"))]
    print("数据目录 %s" % fdir, flush=True)
    print("股票 %d 只 | 待物化 %d 只 | 批大小 %d" % (len(allinst), len(todo), batch), flush=True)
    if not todo:
        print("无需物化（都已有）", flush=True)
        return 0

    t_all = time.time()
    n_batch = (len(todo) + batch - 1) // batch
    for b in range(0, len(todo), batch):
        chunk = todo[b:b + batch]
        t0 = time.time()
        # start_time 取日历起点：避免"2010 之前静默 NaN"；落盘前会对齐到各股 `$close` 的轴。
        r = materialize(chunk, start_time="2000-01-04", end_time="2026-12-31",
                        fields=DEFAULT_FIELDS, read_start="1999-01-01", overwrite=False,
                        progress_cb=None)
        n_files = sum(1 for n in chunk
                      if os.path.exists(os.path.join(fdir, n, "chip_cost_95.day.bin")))
        print("[批 %d/%d] %d 只 | 本批 %.1fs | 累计 %.1fs | 写出 %d 只 | %s"
              % (b // batch + 1, n_batch, len(chunk), time.time() - t0,
                 time.time() - t_all, n_files, r), flush=True)

    done = sum(1 for n in allinst
               if os.path.exists(os.path.join(fdir, n, "chip_cost_95.day.bin")))
    print("全部完成：chip_cost_95 覆盖 %d / %d 只 | 总耗时 %.1fs"
          % (done, len(allinst), time.time() - t_all), flush=True)
    print("done", flush=True)
    return 0


if __name__ == "__main__":        # ⚠ 必须有（Windows spawn 递归）
    sys.exit(main())
