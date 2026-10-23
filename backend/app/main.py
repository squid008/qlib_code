# -*- coding: utf-8 -*-
"""
FastAPI 应用入口。

启动方式（在 qlib 环境）：
    cd backend
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from . import __version__, config, security
from . import metrics as _metrics_mod
from .logger import _log_swallowed, get_logger

# ★ v2.11.25：进程内指标（技术债 P2-#8 第一步 ✓）
# ★ v2.11.50：加慢请求告警（`should_warn_slow` ✓）
from .metrics import REGISTRY, should_warn_slow
from .routers import (
    backtest,
    data,
    datasets,
    export_xlsx,
    factors,
    factors_event,
    factors_formulas,
    factors_single,
    signal_test,
)

logger = get_logger(__name__)


def _warn_if_slow(request, status: int, seconds: float) -> None:
    """**慢请求当场告警一条** ✓（★ v2.11.50）—— 阈值与取舍见 `app/metrics.py` 的 `SLOW_REQUEST_S`。

    ⚠ 只记**路径**、不记 query ✗ —— query 里可能有参数/时间戳 ✓，
    日志里塞一堆动态串既难检索、也可能带上不该留的东西 ✓。
    ⚠ 阈值从模块读（`_metrics_mod.SLOW_REQUEST_S` ✓）而不是从 import 拷贝 ✓ ——
    测试会 monkeypatch 它 ✓，拷贝的副本会让测试看到的和实际用的不一致 ✗。
    """
    if not should_warn_slow(seconds):
        return
    try:
        get_logger("http").warning(
            "慢请求 %.2fs ≥ %.2fs（阈值）：%s %s ⇒ %s",
            seconds, _metrics_mod.SLOW_REQUEST_S,
            request.method, request.url.path, status)
    except Exception as e:      # noqa: BLE001 —— 日志失败绝不能影响响应 ✓
        _log_swallowed("main.py", e)


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
        # v2.1.0：导出接口回传 `X-Export-Filename`（中文文件名 ⇒ 前端显示用 ✓）。
        # ⚠ 不 expose 的话浏览器**读不到**自定义响应头 ✗（跨源下默认只放行少数几个 ✓）。
        expose_headers=["X-Export-Filename"],
    )

    # ★ v2.11.67：**可选**的 token 鉴权 + 限流（默认全关 ✓，见 `app/security.py`）。
    #   ⚠ 注册顺序有讲究 ✗→✓：Starlette 里**后注册的中间件在外层**（先执行 ✓），
    #   所以这里先注册 guard、后面注册 metrics ⇒ **metrics 包在 guard 外面** ✓
    #   ⇒ 401/429 也会被计入指标 ✓（否则"被拦了多少"这个数根本看不到 ✗）。
    @app.middleware("http")
    async def _guard_mw(request, call_next):
        path = request.url.path
        if path.startswith("/api") and not security.is_public(path):
            if not security.token_ok(request.headers.get("authorization")):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "需要 API token：请求头加 `Authorization: Bearer <BACKEND_API_TOKEN>` ✓"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            allowed, wait = security.LIMITER.check(security.client_key(
                request.client.host if request.client else None))
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁（上限 %d 次/分钟）⇒ 请等 %d 秒"
                                       % (security.RATE_LIMIT_RPM, wait)},
                    headers={"Retry-After": str(wait)},
                )
        return await call_next(request)

    # ★ v2.11.25（技术债 P2-#8 第一步 ✓）：**进程内指标** —— 只记总数 + 状态码 + 耗时 ✓。
    #   ⚠ 刻意**不按 URL 路径分桶** ✗（路径里有 task_id/日期等高基数片段 ⇒ 指标本身会变成内存泄漏 ✓）。
    #   要定位"哪个接口慢"，看日志的 `[吞]` 与响应里的 `timings` ✓。
    @app.middleware("http")
    async def _metrics_mw(request, call_next):
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception:
            REGISTRY.inc("http.requests")
            REGISTRY.inc("http.status.500")
            _el = time.perf_counter() - t0
            REGISTRY.observe("http.duration_s", _el)
            _warn_if_slow(request, 500, _el)
            raise
        _el = time.perf_counter() - t0
        REGISTRY.inc("http.requests")
        REGISTRY.inc("http.status.%d" % int(getattr(resp, "status_code", 0) or 0))
        REGISTRY.observe("http.duration_s", _el)
        _warn_if_slow(request, int(getattr(resp, "status_code", 0) or 0), _el)
        return resp

    @app.get("/api/metrics", summary="进程内指标（计数器 + 耗时）")
    def _metrics(format: str = "json"):
        """给"这台机器现在什么情况"一个**不用翻日志**的答案 ✓（见 `app/metrics.py` 的取舍说明）。

        ★ v2.11.41：`?format=text` ⇒ **Prometheus 文本格式** ✓（直接给 scrape 用 ✓，
        不用写 exporter ✓）。
        """
        if format == "text":
            return PlainTextResponse(REGISTRY.to_prometheus_text())
        return REGISTRY.snapshot()

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

    # ★ 2026-10-09：同样检查「横向统计字段（`mkt_*`，BLOCKSETNUM/INSUM）物化口径戳」✓
    #   为什么：横向统计是**物化**出来的（qlib 侧无法现算）⇒ 用的公式/板块口径变了就必须重物化 ✗，
    #   而"拿旧口径的 bin"同样是**静默偏差** ✗（数值看着合理，只是少算了 / 口径旧了 ✗）。
    #   ⇒ 状态挂 `/api/version`（中文提示走 JSON，理由同上：Windows 下日志流是 GBK ✗）。
    market_meta: dict = {}
    try:
        from .factors.market_stat import market_meta_state, stale_formula_names
        market_meta = market_meta_state()
        # ★ v1.20.87：**"文件在、内容旧"**也要能提示（用户 2026-10-09 追问「我改了公式，
        #   涉及的物化文件也要变，能不能提示呢」✓）—— 物化字段名只跟**公式名**有关 ✗
        #   ⇒ 改了被调公式正文后文件名不变、值却是旧口径 ✗✗，而"整列全 NaN"那道防线看不见它 ✗。
        #   物化时已把被调公式的**内容指纹**写进 meta ✓ ⇒ 这里比对 ✓。
        _stale = stale_formula_names()
        if _stale:
            market_meta.update({
                "ok": False,
                "state": "stale_formula",
                "stale_formulas": _stale,
                "message": ("被横向统计物化引用的公式正文已改：%s ⇒ 现有 `mkt_*` bin 仍是**旧口径**"
                            "（文件名不变、值有数 ⇒ 不报错 ✗）。请重跑 "
                            "`python backend/tools/materialize_market.py --overwrite` ✓"
                            % "、".join(_stale)),
            })
        _st = str(market_meta.get("state", "?"))
        _msg = "[market-meta] state=%s semantics=%s" % (_st, market_meta.get("expected", "?"))
        if market_meta.get("ok"):
            logger.info(_msg)
        else:
            logger.warning("%s -- MATERIALIZE REQUIRED: run"
                           " `python backend/tools/materialize_market.py --overwrite`"
                           " (see GET /api/version -> market_meta.message)", _msg)
    except Exception as _e:                                # noqa: BLE001
        logger.warning("[market-meta] check failed: %r", _e)

    # 路由
    app.include_router(backtest.router)
    app.include_router(data.router)
    app.include_router(datasets.router)
    app.include_router(factors.router)
    # ★ v2.9.11（P1-A ✓ 2026-10-18）：**公式库组**从 `factors.py` 拆出 ✓
    #   ⚠ 它自带 `prefix="/api/factors"` ✓ ⇒ **对外 URL 一个字都没变** ✓
    #   （守门：`tests/test_routers_smoke.py` 断言这些路径必须存在 ✓）。
    app.include_router(factors_formulas.router)
    # ★ v2.9.13（P1-A-2 ✓）：**事件研究组**已拆出 ✓（同为 `/api/factors` 前缀 ⇒ URL 不变 ✓）
    app.include_router(factors_event.router)
    # ★ v2.9.14（P1-A-2 ✓ 收尾）：**单因子测试组**已拆出 ✓（同 `/api/factors` 前缀 ⇒ URL 不变 ✓）
    app.include_router(factors_single.router)
    app.include_router(export_xlsx.router)      # v2.1.0：XLSX 导出（沿 /api/factors/export/*）
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
        # ★ v1.20.89：**实时**比对物化指纹 ⇒ 保存公式后刷新页面即可见 ✓
        #   （原来返回的是**启动时**抓的快照 ✗ ⇒ 不重启永远是旧结论 ✗，用户 2026-10-09
        #   实测"改了 `IS_GOLD_PIT` 却看不到提示" ✓）。本函数很轻（读 meta + 编译被引用的
        #   那两三个公式 ✓）⇒ 每次请求都算没问题 ✓。
        try:
            from .factors.market_stat import market_meta_state_live
            _mm = market_meta_state_live()
        except Exception:                                    # noqa: BLE001
            _mm = market_meta
        return {"version": __version__, "chip_meta": chip_meta, "market_meta": _mm}

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

    # ★★ v2.9.23（用户 2026-10-18 ✓）：**启动时也跑一次存储治理** ✗→✓
    #   原先只在「每次回测结束」时清 ✓ ⇒ 只要不跑回测，缓存就一路涨 ✗
    #   （实测 `feature_cache` 已 3.59GB > 配额 2GB ✓ —— 逻辑没坏，只是**没到时候** ✓）。
    #   ⇒ 启动时补一次 ✓，且**放后台线程**（⚠ 必须在事件循环外的普通线程 ✓，
    #     因为治理要遍历几万个文件、几秒钟 ✓ —— 阻塞启动会让前端以为"后端没起来"✗）。
    def _bg_storage_cleanup():
        try:
            from .engine.storage_cleanup import cleanup_storage

            st = cleanup_storage(force=True)      # force ✓：绕过节流（30 分钟 ✓）
            logger.info("[storage] 启动治理完成：%s", st)
        except Exception as _e:                   # noqa: BLE001 —— 治理失败不许影响启动 ✗
            logger.warning("[storage] 启动治理失败（已忽略）：%r", _e)

    try:
        import threading

        threading.Thread(target=_bg_storage_cleanup, name="storage-cleanup", daemon=True).start()
    except Exception as _e:                       # noqa: BLE001
        logger.warning("[storage] 启动治理线程起不来（已忽略）：%r", _e)

    return app


app = create_app()
