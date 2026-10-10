# -*- coding: utf-8 -*-
"""
全局配置。
"""
from __future__ import annotations

import os

# Qlib 数据路径（★ 2026-10-10 改优先级：**落盘的"当前数据集"优先**，其次环境变量，
# 再其次项目目录下 data/cn_data，最后当前用户主目录 .qlib）
#
# ★ 为什么这里要读 active_dataset.json（用户 2026-10-09 要求"三套数据来回切换对照"）：
#   切换是在**运行期**改的（`app/services/datasets.py::activate` ✓），但后端是 uvicorn +
#   joblib 子进程（回测/单因子 spawn 出 worker ✓）⇒ 子进程会**重新 import 本模块** ✗。
#   靠"只改内存变量"传不过去 ✗ ⇒ 把选择落盘、在**导入时**读回来，子进程才能自动跟随 ✓。
#
# ★★ 为什么**不能**让环境变量优先（2026-10-10 实测踩到，代价很大 ✗✗）：
#   本机存在**用户级**环境变量 `QLIB_PROVIDER_URI=data\cn_data`（历史遗留）⇒ 旧逻辑"环境变量优先" ✗
#   ⇒ 界面里切到 `cn_data2` 之后，**新起的进程/子进程又回到 cn_data** ⇒ 切换"看起来生效、实际没生效"✗
#   ⇒ 用户的"两个数据源对照实验"得出**结果一模一样**的假象（实测：面板链读的始终是 cn_data ✓）。
#   ⇒ 新逻辑：**active_dataset.json 优先**（UI 切换会原子写它 ✓，且 `data/` 不入库 ⇒ 不会污染 CI ✓）；
#     文件不存在/路径无效时才用环境变量；要"环境变量强制覆盖"（容器/CI 显式指定数据根 ✓）
#     请设 `QLIB_PROVIDER_URI_FORCE=1` ✓。
_env_uri = os.environ.get("QLIB_PROVIDER_URI") or None
_force_env = os.environ.get("QLIB_PROVIDER_URI_FORCE", "") not in ("", "0")
_backup_data = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cn_data")
_active_fp = os.path.join(os.path.dirname(__file__), "..", "..", "data", "active_dataset.json")
_file_uri = None
try:
    import json as _json

    with open(_active_fp, "r", encoding="utf-8-sig") as _fh:  # utf-8-sig：容忍 BOM ✓
        _picked = (_json.load(_fh) or {}).get("dir")
    _file_uri = _picked if (_picked and os.path.isdir(_picked)) else None
except Exception:  # 文件不存在/损坏/路径失效 ⇒ 回落 ✓（绝不因为选择文件而启动失败 ✗）
    _file_uri = None

if _file_uri and not (_force_env and _env_uri):
    _qlib_uri = _file_uri
    QLIB_PROVIDER_URI_SOURCE = "active_dataset.json"
elif _env_uri:
    _qlib_uri = _env_uri
    QLIB_PROVIDER_URI_SOURCE = "env" + ("(force)" if _force_env else "")
elif os.path.isdir(_backup_data):
    _qlib_uri = _backup_data
    QLIB_PROVIDER_URI_SOURCE = "default"
else:
    _qlib_uri = os.path.join(os.path.expanduser("~"), ".qlib", "qlib_data", "cn_data")
    QLIB_PROVIDER_URI_SOURCE = "home"

# ⚠ 把解析结果**回写环境变量**：本仓库不少 CLI 工具直接读 `os.environ["QLIB_PROVIDER_URI"]`
#   （如 build_preclose.py 的旧版本、若干 dump 脚本的 --qlib-dir 默认值）⇒ 不回写的话它们会
#   继续用那个**过期的用户级环境变量**、写错数据集 ✗（2026-10-10：cn_data 的 pre* 就是这么
#   变成"另一套数据的副本"的 ✗）。回写后整个进程树（含 spawn 子进程）统一 ✓。
if os.environ.get("QLIB_PROVIDER_URI") != _qlib_uri:
    os.environ["QLIB_PROVIDER_URI"] = _qlib_uri

# 两者都在且不一致 ⇒ 明确告警一次（否则这种"看着切了、其实没切"极难发现 ✓）
if _env_uri and _file_uri and os.path.normcase(os.path.normpath(_env_uri)) != os.path.normcase(
        os.path.normpath(_file_uri)):
    import sys as _sys

    print("[config] ⚠ 环境变量 QLIB_PROVIDER_URI=%s 与落盘的当前数据集 %s 不一致 ⇒ "
          "按【%s】生效（要环境变量优先请设 QLIB_PROVIDER_URI_FORCE=1）"
          % (_env_uri, _file_uri, QLIB_PROVIDER_URI_SOURCE), file=_sys.stderr, flush=True)

QLIB_PROVIDER_URI = _qlib_uri

# rqalpha h5 bundle 路径（预留，设置了则启用 rqalpha 数据源）
RQALPHA_BUNDLE_PATH = os.environ.get(
    "RQALPHA_BUNDLE_PATH",
    r"E:\rq\bundle" if os.path.isdir(r"E:\rq\bundle") else None,
)
# rqalpha 财报(pit)与指数成分目录（默认在 bundle 同级 finance/constituents 下）
RQALPHA_FINANCE_DIR = os.environ.get("RQALPHA_FINANCE_DIR", None)
RQALPHA_CONSTITUENTS_DIR = os.environ.get("RQALPHA_CONSTITUENTS_DIR", None)

# 任务/回测临时目录
WORK_DIR = os.environ.get("QLIB_WORK_DIR", os.path.join(os.path.dirname(__file__), "..", "workdir"))

# 前端 CORS 允许来源
CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
