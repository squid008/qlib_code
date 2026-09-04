# -*- coding: utf-8 -*-
"""pytest 公共配置：确保能 import backend/app 下的模块。"""
import os
import sys

import pytest

# 把 backend 目录加入 sys.path，使 `import app.xxx` 可用
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "datareq: 需要本机真实行情(cn_data)/资金流(moneyflow)数据；无数据环境(CI)自动跳过",
    )


@pytest.fixture(autouse=True)
def _datareq_guard(request):
    """对打上 datareq 标记的用例：cn_data 不可用时自动 skip（CI/新机器不报红）。"""
    if request.node.get_closest_marker("datareq"):
        try:
            from app.config import QLIB_PROVIDER_URI
        except Exception:
            pytest.skip("无法解析 qlib 数据路径")
        if not (
            os.path.isdir(QLIB_PROVIDER_URI)
            and os.path.isfile(os.path.join(QLIB_PROVIDER_URI, "calendars", "day.txt"))
        ):
            pytest.skip(f"cn_data 不可用（{QLIB_PROVIDER_URI}），跳过数据回归用例")
