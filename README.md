# Qlib 量化回测平台

基于 Qlib 的量价因子机器学习回测系统：**React 前端 + FastAPI 后端 + Qlib 引擎**，
从「写公式 → 单因子诊断 → 训练/回测 → 产物复现」一条闭环。

> 旧版 README（含版本号沿革、引擎踩坑长文、部署全文）已归档到
> [`md/README-2026-10-21-归档.md`](./md/README-2026-10-21-归档.md) ✓ —— 需要查历史细节时看它。

| | |
|---|---|
| 后端 | FastAPI，`http://127.0.0.1:8001` |
| 前端 | Vite + React + TS + Tailwind + Recharts，`http://127.0.0.1:5173`（`/api` 已代理到 8001） |
| 引擎 | Qlib + Alpha158/360 + LightGBM，按持仓周期整体换仓 |
| 数据频率 | **日频** ✓（分钟频数据接口有，但**回测不支持** ✗） |

## 架构

```
┌──────────────┐  HTTP(5173)  ┌────────────────┐  Python  ┌──────────────────┐
│  React 前端    │ ──────────▶ │  FastAPI 后端    │ ───────▶ │  Qlib 回测引擎     │
│  Vite+TS+      │ ◀────────── │   (8001)       │ ◀─────── │  Alpha158+GBDT    │
│  Tailwind+     │   轮询进度   │  + 任务管理      │  结果     │  + TopK 策略       │
│  Recharts      │             │  + 数据服务      │          │                  │
└──────────────┘             └────────┬───────┘          └──────────────────┘
                                      │
                         ┌────────────▼───────────┐
                         │    数据源抽象层          │
                         │  QlibDataSource(日线)   │
                         │  RQAlphaDataSource(h5) │ ← 仅数据查询（分钟/财报/指数成分）
                         └────────────────────────┘
```

## 目录结构

```
qlib_code/
├── backend/                      # FastAPI 后端
│   ├── app/
│   │   ├── main.py               # 应用入口
│   │   ├── config.py             # 配置
│   │   ├── models/               # Pydantic 数据模型
│   │   ├── datasource/           # ★ 数据源抽象层
│   │   │   ├── base.py           #   统一数据结构 + 抽象接口
│   │   │   ├── qlib_source.py    #   Qlib 数据源（日线）
│   │   │   ├── rqalpha_source.py #   rqalpha h5（仅数据查询）
│   │   │   └── factory.py        #   数据源工厂
│   │   ├── factors/              # ★ 公式翻译器 + 因子 Handler
│   │   │   ├── parser/           #   Lexer/Parser/Semantic/CodeGen（益盟/通达信公式）
│   │   │   ├── ops_ext.py        #   外挂算子（BARSLAST/BARSSINCEN/DYN_*/SR/EMA_TDX 等）
│   │   │   ├── panel_expr.py     #   面板求值器（替代 qlib D.features，含预热缓冲）
│   │   │   ├── single_test.py    #   单因子测试（覆盖率/IC/分层/触发诊断）
│   │   │   └── handler.py        #   SelectedAlpha158/360 + FormulaHandler
│   │   ├── engine/               # 回测引擎
│   │   │   ├── qlib_engine.py    #   Qlib 回测实现
│   │   │   ├── board_exchange.py #   按板块涨跌停（主板10%/创业科创20%/北交所30%）
│   │   │   ├── feature_cache.py  #   特征磁盘缓存
│   │   │   ├── periodic_strategy.py # 按持仓周期整体换仓策略
│   │   │   ├── analysis.py / metrics.py / artifacts.py / charts.py
│   │   │   └── task_manager.py   #   异步任务管理
│   │   ├── signals/              # 交易信号测试（事件驱动回测，含离场规则）
│   │   ├── exports/              # XLSX 导出（含 `_meta` 口径参数，供回灌复现）
│   │   ├── services/             # 业务服务
│   │   └── routers/              # API 路由（backtest / factors / data / exports …）
│   ├── tests/                    # pytest（纯逻辑用例 + 引擎 NAV 指纹）
│   ├── requirements.txt          # 运行时依赖（声明）
│   ├── requirements-dev.txt      # 测试/检查工具
│   └── constraints.txt           # 精确版本锁（全部 `==`）
├── frontend/                     # React 前端
│   ├── src/
│   │   ├── App.tsx               # 主界面
│   │   ├── api.ts                # API 客户端
│   │   ├── types.ts              # 类型定义
│   │   ├── components/           # 图表 / 面板 / 弹窗 / 公式编辑器
│   │   └── __tests__/            # vitest 单测
│   ├── e2e/                      # Playwright（真浏览器）
│   └── vitest.config.ts          # 单测 + 覆盖率门槛
├── md/                           # 文档（见文末索引）
├── start_backend.bat             # 一键启动后端
├── start_frontend.bat            # 一键启动前端
└── ai_test/                      # 临时验算脚本（可随时删，不入库）
```

## 快速启动

```bash
# 后端（需 conda activate qlib）
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
# 或直接双击 start_backend.bat

# 前端
cd frontend
npm install            # 首次
npm run dev            # → http://127.0.0.1:5173
# 或直接双击 start_frontend.bat
```

**数据路径**：`data/active_dataset.json` 记录的**当前数据集优先** ✓（环境变量 `QLIB_PROVIDER_URI`
默认不压过它 —— 要强制生效需设 `QLIB_PROVIDER_URI_FORCE=1` ✓）。换机器/部署数据见 [`md/deploy.md`](./md/deploy.md)。

## 提交前必过（五道闸门，CI 里全都在拦）

```bash
# 后端（cd backend）
python -m mypy                                # 19 个源文件，0 错误
python -m pytest tests -m "not datareq" -q    # 纯逻辑用例（含引擎 NAV 指纹、相对性能门）
python -m ruff check app tests tools
python -m pip_audit -r requirements.txt       # 依赖漏洞（阻断式）

# 前端（cd frontend）
npx tsc -b                                    # strict
npm test                                      # vitest
npm run test:cov                              # 覆盖率门槛 39%
npm run test:e2e                              # Playwright（需先 npx playwright install chromium）
```

- ⚠ **`-m "not datareq"` 不能去掉** ✗：带 `datareq` 标的用例要真实行情数据，本地没数据会失败 —— CI 也只跑这个子集。
- ⚠ **改数值口径**（净值/成本/复利/回撤/换手）**必须跑** `tests/test_engine_golden.py`（6 配置精确 NAV 指纹）——
  它会在你提交前告诉你“数字变了”，而不是等用户发现。
- ⚠ **升级依赖** ⇒ 单独一个 commit，**只改 `backend/constraints.txt`** ✓（便于一眼定位“哪次升级导致变红”）。

## 主要 API

| 分组 | 端点 | 说明 |
|---|---|---|
| 回测 | `POST /api/backtest` · `GET /api/backtest/{id}` · `GET /api/backtest/{id}/result` | 提交 / 轮询 / 取结果 |
| 回测 | `GET /api/backtests/history` · `.../{id}/snapshot` · `.../artifacts` · `.../partial` | 历史 / 参数快照 / 产物 / 已跑段 |
| 因子 | `POST /api/factors/translate` · `GET /api/factors/operators` | 公式翻译 / 算子清单 |
| 因子 | `POST /api/factors/single-factor-test` · `GET .../progress/{id}` · `POST .../cancel/{id}` | 单因子测试（异步） |
| 信号 | `POST /api/factors/signal-test/parse` · `.../run` | CSV 解析 / 交易信号回测 |
| 数据 | `GET /api/datasets` · `POST /api/datasets/active` · `GET /api/data/*` · `GET /api/version` | 数据集切换 / 行情 / 版本 |

完整路由见 `backend/app/routers/`（每个端点都有 `summary=`）。

## 数据源

三套数据集可同时存在，界面顶栏切换 ✓：

| 名称 | 口径 | 字段 | 说明 |
|---|---|---|---|
| `cn_data2` | **tushare** | 全字段 | **主数据集**：行情 = `raw × tushare adj_factor`；财务/资金流/筹码齐全 |
| `cn_data` | **米筐 / qlib** | 全字段 | 由米筐 era 备份解出，行情按 qlib 官方口径重建；财务带公告日/修订日 |
| `cn_data3` | qlib 官方原始 | 仅 10 个行情字段 | 只作对照：`FINANCE(q)` / 资金流 / 筹码取不到数 |

- **口径自证**：数据集根目录 `.dataset.json` 标口径与字段基准；不一致时**只提示不报错**。
- **分钟频**：`rqalpha` h5 只提供**数据查询**（分钟线/财报/指数成分）✓，
  **回测引擎只支持日频** ✗ —— 分钟频回测暂不支持。
- 物化字段（`fin_*` / `pre*` / `chip_*` / `mf_*` 等）**不入 git** ⇒ 每台机器按 [`md/deploy.md`](./md/deploy.md) 各跑一次，
  工具写盘成功会自动落数据戳 `<数据集>/.data_version`（否则“改了数据还用旧面板”）。

更多（两套数据源切换的两大坑、复权物化体检、行业分类未实现等）见 [`md/数据源接入.md`](./md/数据源接入.md)。

## 文档

| 文档 | 说明 |
|---|---|
| [`md/开发记录.md`](./md/开发记录.md) | **顶部是「当前基线」**（版本 / 用例数 / 覆盖率门槛 / 模块覆盖 / 故意停手处）—— 开工先看这几行 |
| [`md/change_log.md`](./md/change_log.md) | 版本更新记录（Keep a Changelog；更早已按卷归档在 `md/change_log/`） |
| [`md/deploy.md`](./md/deploy.md) | 部署指南（新机器、Python 环境、数据部署、物化字段） |
| [`md/start_stop.md`](./md/start_stop.md) | 启停脚本说明 |
| [`md/数据源接入.md`](./md/数据源接入.md) | 数据源抽象层、切换口径、复权物化体检、未实现能力 |
| [`md/自定义因子与因子库架构.md`](./md/自定义因子与因子库架构.md) | 因子能力总览与因子库设计 |
| [`md/formulas_sync.md`](./md/formulas_sync.md) | 公式库多地同步口径 |
| [`md/两地 git 工作流.md`](./md/两地 git 工作流.md) | 家/公司两地协作约定 |
| [`md/研报集合.md`](./md/研报集合.md) | 研报要点与可落地点 |
| [`md/大版本更新测试对照记录.md`](./md/大版本更新测试对照记录.md) | 2.0.0 验收归档（两套数据源 × 前后复权逐列结果） |
| [`md/README-2026-10-21-归档.md`](./md/README-2026-10-21-归档.md) | 旧版 README 全文（版本号沿革、引擎踩坑长文、部署全文） |
