# -*- coding: utf-8 -*-
"""构建特征**合并层** `features_merged/`（T2 §7.7-E #7）。

把「每股每字段一个小 .bin」合并成「**每字段一个大文件**」，把全池面板的读盘次数
从 `股票数 × 字段数` 降到 `字段数`。

实测依据（`ai_test/bench_io_format.py` / `bench_io_h5.py` / `bench_io_parallel.py`）：
  · `features/` = 6142 目录 / 321249 文件 / 3.2GB，单文件仅 1.9~2.3KB
  · 单次 `stat + fromfile` ≈ **205us**（只 stat 就 71us）⇒ 有效 9.5MB/s，比 NVMe 慢 20~50×
  · **并发救不了**（thread-4/8/16 均 ~1.8×；同一文件重复读也一样慢）⇒ 只能减少 open 次数
  · 字段级合并 ⇒ 3213 次读 → 7 次，**65×**（换成单文件反而无进一步收益；HDF5 无优势）

产物（放在 `features/` **同级**，qlib 原生目录一个字节都不动）：
    features_merged/
        <field>.bin         该字段全部股票按代码升序**顺序拼接**，逐股**原样字节**
                            （仍 `<f4`、首元素 = start_idx）—— 任一切片与源文件逐字节相同
        <field>.idx.json    列式索引 {n, inst[], off[], cnt[], start[]}
                            （off/cnt 以 **float32 字（4 字节）** 为单位；start 为源首元素）
        manifest.json       {version, built_at, fields, src_files, src_bytes, words}

⚠ 语义必须与 `panel_expr._read_field_bin` 对齐（否则数值会变）：
    · 用 `np.fromfile(dtype="<f4")` 读 → 元素数 = `size // 4`（`size % 4` 的尾部字节被忽略）
    · `arr.size < 2` 的股票**不收录**（读者对它们返回 None）
    · `start_idx = int(arr[0])`、`n_rows = arr.size - 1`

⚠ 这是**派生数据**：源数据重 dump 后必须重建本层；服务端可用 `QLIB_PANEL_MERGED=0` 停用它。

用法：
    python backend/tools/build_merged_features.py                    # 全量
    python backend/tools/build_merged_features.py --fields close,open
    python backend/tools/build_merged_features.py --verify 3000      # 抽样逐字节校验
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

SUF = ".day.bin"
VERSION = 1


def default_features_dir() -> str:
    """与 `panel_expr._feature_dir()` 同规则。"""
    try:
        from app.config import QLIB_PROVIDER_URI

        if QLIB_PROVIDER_URI:
            return os.path.join(QLIB_PROVIDER_URI, "features")
    except Exception:
        pass
    return os.path.join(_BACKEND, "..", "data", "cn_data", "features")


def scan(fd: str):
    """一次 scandir 收集 {field: [(inst, size)]}（Windows 上 DirEntry.stat 复用 scandir 结果）。"""
    insts = sorted(d for d in os.listdir(fd) if os.path.isdir(os.path.join(fd, d)))
    per_field: dict = {}
    nfiles = 0
    nbytes = 0
    t0 = time.perf_counter()
    for k, inst in enumerate(insts, 1):
        try:
            with os.scandir(os.path.join(fd, inst)) as it:
                for e in it:
                    if not e.name.endswith(SUF):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    if st.st_size < 8:        # size//4 < 2 → 读者返回 None，不收录
                        continue
                    f = e.name[: -len(SUF)]
                    per_field.setdefault(f, []).append((inst, st.st_size))
                    nfiles += 1
                    nbytes += st.st_size
        except OSError:
            continue
        if k % 2000 == 0:
            print("    scandir %d/%d 股票  %.1fs" % (k, len(insts), time.perf_counter() - t0),
                  flush=True)
    return insts, per_field, nfiles, nbytes


def build_field(fd: str, out: str, field: str, recs, force: bool) -> dict:
    binp = os.path.join(out, field + ".bin")
    idxp = os.path.join(out, field + ".idx.json")
    off, cnt, start, inst_names = [], [], [], []
    pos = 0
    skipped = 0
    t0 = time.perf_counter()
    with open(binp, "wb", buffering=1 << 22) as fh:
        for inst, _size in recs:
            try:
                arr = np.fromfile(os.path.join(fd, inst, field + SUF), dtype="<f4")
            except OSError:
                skipped += 1
                continue
            if arr.size < 2:
                skipped += 1
                continue
            fh.write(arr.tobytes())
            inst_names.append(inst)
            off.append(pos)
            cnt.append(int(arr.size))
            start.append(int(arr[0]))
            pos += int(arr.size)
    with open(idxp, "w", encoding="utf-8") as fh:
        json.dump({"field": field, "n": len(inst_names), "words": pos,
                   "inst": inst_names, "off": off, "cnt": cnt, "start": start},
                  fh, separators=(",", ":"), ensure_ascii=False)
    return {"field": field, "n": len(inst_names), "words": pos, "skipped": skipped,
            "bytes": pos * 4, "sec": time.perf_counter() - t0}


def verify(fd: str, out: str, fields, per_field, sample: int) -> int:
    """抽样**逐字节**校验：合并文件里的切片必须与源文件字节完全相同。"""
    bad = 0
    rng = random.Random(20260912)
    checked = 0
    for field in fields:
        idxp = os.path.join(out, field + ".idx.json")
        idx = json.load(open(idxp, encoding="utf-8"))
        mm = np.memmap(os.path.join(out, field + ".bin"), dtype="<f4", mode="r")
        n = idx["n"]
        pick = set(rng.sample(range(n), min(sample // len(fields) + 1, n)))
        for k in pick:
            inst = idx["inst"][k]
            o, c = idx["off"][k], idx["cnt"][k]
            seg = np.asarray(mm[o:o + c])
            src = np.fromfile(os.path.join(fd, inst, field + SUF), dtype="<f4")
            checked += 1
            # ⚠ 必须 equal_nan=True：close 等字段含 NaN，默认判 NaN≠NaN 会整片误报
            # （今天第二次踩这个坑，见 ai_test/bench_io_format.py 的同类修正）
            if src.size != c or not np.array_equal(seg, src[:c], equal_nan=True):
                bad += 1
                if bad <= 5:
                    print("    ❌ %s/%s 不一致: merged(c=%d,start=%d) vs src(size=%d,start=%s)"
                          % (field, inst, c, int(seg[0]) if seg.size else -1,
                             src.size, int(src[0]) if src.size else None), flush=True)
            if int(seg[0]) != idx["start"][k]:
                bad += 1
                print("    ❌ %s/%s start 与索引不符" % (field, inst), flush=True)
        del mm
    print("  抽样逐字节校验：%d 对，失败 %d" % (checked, bad), flush=True)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fields", default=None, help="逗号分隔；默认全部字段")
    ap.add_argument("--verify", type=int, default=2000, help="抽样逐字节校验的对数（0=跳过）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的合并层")
    a = ap.parse_args()

    fd = os.path.abspath(a.features_dir or default_features_dir())
    out = os.path.abspath(a.out or os.path.join(os.path.dirname(fd.rstrip("\\/")),
                                                "features_merged"))
    print("源目录 = %s" % fd)
    print("输出   = %s" % out)
    if not os.path.isdir(fd):
        raise SystemExit("源目录不存在")
    if os.path.exists(out) and not a.force:
        raise SystemExit("输出目录已存在（--force 覆盖）")
    os.makedirs(out, exist_ok=True)

    print("\n[1/4] scandir 收集字段与文件规模 ...", flush=True)
    insts, per_field, nfiles, nbytes = scan(fd)
    fields = sorted(per_field) if not a.fields else [x.strip() for x in a.fields.split(",")]
    print("  股票目录 %d，文件 %d，源字节 %.2f GB，字段 %d 个"
          % (len(insts), nfiles, nbytes / 1e9, len(per_field)))

    print("\n[2/4] 构建每字段大文件 ...", flush=True)
    t0 = time.perf_counter()
    stats = []
    for field in fields:
        recs = sorted(per_field.get(field, []))
        st = build_field(fd, out, field, recs, a.force)
        stats.append(st)
        print("  %-24s %6d 只  %8.1f MB  %7.1fs  跳过 %d"
              % (field, st["n"], st["bytes"] / 1e6, st["sec"], st["skipped"]), flush=True)
    total = time.perf_counter() - t0
    words = sum(s["words"] for s in stats)
    print("  合计 %d 字段 / %.2f GB / %.1fs" % (len(stats), words * 4 / 1e9, total))

    print("\n[3/4] 写 manifest ...", flush=True)
    man = {"version": VERSION, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "features_dir": fd, "fields": fields,
           "n_fields": len(fields), "words": words, "bytes": words * 4,
           "src_files": nfiles, "src_bytes": nbytes}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)
    print("  %s" % json.dumps({k: man[k] for k in ("version", "built_at", "n_fields",
                                                   "bytes", "src_files")},
                              ensure_ascii=False))

    rc = 0
    if a.verify:
        print("\n[4/4] 抽样逐字节校验 ...", flush=True)
        rc = verify(fd, out, fields, per_field, a.verify)
    else:
        print("\n[4/4] 跳过校验")
    print("\n完成：%s" % out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
