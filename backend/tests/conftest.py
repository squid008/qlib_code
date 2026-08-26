# -*- coding: utf-8 -*-
"""pytest 公共配置：确保能 import backend/app 下的模块。"""
import os
import sys

# 把 backend 目录加入 sys.path，使 `import app.xxx` 可用
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
