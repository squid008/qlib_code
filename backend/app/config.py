# -*- coding: utf-8 -*-
"""
全局配置。
"""
from __future__ import annotations

import os

# Qlib 数据路径（优先环境变量 ✓；否则读**用户选中的数据集** data/active_dataset.json ✓；
# 再否则用项目目录下 data/cn_data ✓；最后用当前用户主目录 .qlib ✓）
#
# ★ 为什么这里要读 active_dataset.json（用户 2026-10-09 要求"三套数据来回切换对照"）：
#   切换是在**运行期**改的（`app/services/datasets.py::activate` ✓），但后端是 uvicorn +
#   joblib 子进程（回测/单因子 spawn 出 worker ✓）⇒ 子进程会**重新 import 本模块** ✗。
#   靠"只改内存变量"传不过去 ✗ ⇒ 把选择落盘、在**导入时**读回来，子进程才能自动跟随 ✓。
_qlib_uri = os.environ.get("QLIB_PROVIDER_URI", None)
if not _qlib_uri:
    _backup_data = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cn_data")
    _active_fp = os.path.join(os.path.dirname(__file__), "..", "..", "data", "active_dataset.json")
    try:
        import json as _json

        with open(_active_fp, "r", encoding="utf-8-sig") as _fh:  # utf-8-sig：容忍 BOM ✓
            _picked = (_json.load(_fh) or {}).get("dir")
        _qlib_uri = _picked if (_picked and os.path.isdir(_picked)) else None
    except Exception:  # 文件不存在/损坏/路径失效 ⇒ 回落到默认 ✓（绝不因为选择文件而启动失败 ✗）
        _qlib_uri = None
    if not _qlib_uri and os.path.isdir(_backup_data):
        _qlib_uri = _backup_data
    if not _qlib_uri:
        _qlib_uri = os.path.join(os.path.expanduser("~"), ".qlib", "qlib_data", "cn_data")
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
