# -*- coding: utf-8 -*-
"""
全局配置。
"""
from __future__ import annotations

import os

# Qlib 数据路径（优先环境变量；否则用项目目录下 data/cn_data；否则用当前用户主目录 .qlib）
_qlib_uri = os.environ.get("QLIB_PROVIDER_URI", None)
if not _qlib_uri:
    _project_data = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cn_data")
    if os.path.isdir(_project_data):
        _qlib_uri = _project_data
    else:
        _qlib_uri = os.path.join(os.path.expanduser("~"), ".qlib", "qlib_data", "cn_data")
QLIB_PROVIDER_URI = _qlib_uri

# rqalpha h5 bundle 路径（预留，设置了则启用 rqalpha 数据源）
RQALPHA_BUNDLE_PATH = os.environ.get("RQALPHA_BUNDLE_PATH", None)

# 任务/回测临时目录
WORK_DIR = os.environ.get("QLIB_WORK_DIR", os.path.join(os.path.dirname(__file__), "..", "workdir"))

# 前端 CORS 允许来源
CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
