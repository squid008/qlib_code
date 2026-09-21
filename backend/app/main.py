# -*- coding: utf-8 -*-
"""
FastAPI 应用入口。

启动方式（在 qlib 环境）：
    cd backend
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, __version__
from .logger import get_logger
from .routers import backtest, data, factors, signal_test

logger = get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Qlib 量化回测平台",
        description="基于 Qlib 的量价因子机器学习回测系统，支持 Qlib / rqalpha(h5) 多数据源。",
        version=__version__,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 确保工作目录存在
    os.makedirs(config.WORK_DIR, exist_ok=True)

    # ★★ v1.20.45：启动即检查「`chip_*` 物化口径戳」✓ —— 让 **pull 了新代码却没重物化** 一眼可见 ✗
    #   为什么必须做：旧口径物化出来的 bin 是**静默偏差** ✗ —— 不报错、数值看着合理、
    #   连"`COST(5) ≤ COST(95)` 单调"都成立 ✗（塌缩后照样单调 ✓）⇒ 只能靠"戳"发现 ✓。
    #   典型场景：同事 `git pull` 拿到 v1.20.44 的换手率修正 ✓ 但没重物化 ✗
    #   ⇒ 他那边 `chip_*` 依旧是**放大 100 倍**换手率算出来的 ✗，且**毫无提示** ✗。
    #   ⇒ 这里 WARNING 一条 ✓，并把状态挂在 `/api/version` 上 ✓（前端/脚本/AI 都能直接看到 ✓）。
    chip_meta: dict = {}
    try:
        from .factors.chip_store import chip_meta_state
        chip_meta = chip_meta_state()
        # ⚠ v1.20.45：这条**刻意写成 ASCII** ✗ —— 本项目的 logging 流处理器在 Windows 下是
        #   **GBK** ✗，中文消息会触发 `UnicodeEncodeError`，结果只落到 `workdir/backend_err.log`
        #   里的 `Message: '[chip-meta] %s' Arguments: ...` ✗（等于**没有提示** ✓，实测踩到 ✓）。
        #   ⇒ 详细的中文提示一律走 **`/api/version`** 的 `chip_meta.message` ✓
        #     （JSON 是 UTF-8 ✓，已实测可读 ✓）。
        _st = str(chip_meta.get("state", "?"))
        _sm = str(chip_meta.get("expected", "?"))
        _nc = chip_meta.get("n_chip_cost_95", "?")
        _msg = "[chip-meta] state=%s semantics=%s n_chip=%s" % (_st, _sm, _nc)
        if chip_meta.get("ok"):
            logger.info(_msg)
        else:
            logger.warning("%s -- RE-MATERIALIZE REQUIRED: run"
                           " `python backend/tools/materialize_chip.py 400 --overwrite`"
                           " then `python backend/tools/verify_materialized.py`"
                           " (see GET /api/version -> chip_meta.message)", _msg)
    except Exception as _e:                                # noqa: BLE001
        logger.warning("[chip-meta] check failed: %r", _e)

    # 路由
    app.include_router(backtest.router)
    app.include_router(data.router)
    app.include_router(factors.router)
    app.include_router(signal_test.router)

    @app.get("/", summary="服务健康检查")
    def root():
        return {"service": "qlib-backtest-api", "status": "ok"}

    @app.get("/health", summary="健康检查")
    def health():
        return {"status": "ok"}

    @app.get("/api/version", summary="版本号")
    def version():
        """返回当前版本号 + `chip_*` 物化口径状态。

        ⚠ v1.20.45：**换机器 / `git pull` 新代码之后，先看这里** ✓ ——
        `chip_meta.ok == false` ⇒ 本机 `chip_*` 是**别的口径**物化的（或压根没物化 ✗）
        ⇒ 必须跑 `python backend/tools/materialize_chip.py 400 --overwrite` ✓，
        再 `python backend/tools/verify_materialized.py` 核对 ✓。
        """
        return {"version": __version__, "chip_meta": chip_meta}

    # 参数校验错误：返回友好信息，不暴露堆栈
    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(request: Request, exc: RequestValidationError):
        logger.warning("参数校验失败 %s %s: %s", request.method, request.url.path, exc.errors())
        return JSONResponse(status_code=422, content={"detail": "请求参数校验失败，请检查输入"})

    # 兜底：未捕获异常记录到服务端日志，前端只收到友好信息（不暴露堆栈）
    @app.exception_handler(Exception)
    async def unhandled_exc_handler(request: Request, exc: Exception):
        import traceback
        logger.error("未捕获异常 %s %s: %s\n%s",
                     request.method, request.url.path, exc, traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": f"服务内部错误: {exc}"})

    return app


app = create_app()
