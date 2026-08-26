# Qlib 量化回测平台

基于 Qlib 的量价因子机器学习回测系统，提供 **React 前端 + FastAPI 后端 + Qlib 回测引擎** 的完整闭环，
并预留 **rqalpha(h5)** 多类型数据源接入接口。

## 架构

```
┌──────────────┐  HTTP(5173)  ┌────────────────┐  Python  ┌────────────────┐
│  React 前端    │ ──────────▶ │  FastAPI 后端    │ ───────▶ │  Qlib 回测引擎   │
│  Vite+TS+      │ ◀────────── │   (8000)       │ ◀─────── │  Alpha158+GBDT  │
│  Tailwind+     │   轮询进度   │  + 任务管理      │  结果     │  + TopK策略     │
│  Recharts      │            │  + 数据服务      │          │                │
└──────────────┘            └────────┬───────┘          └────────────────┘
                                     │
                        ┌────────────▼───────────┐
                        │    数据源抽象层          │
                        │  QlibDataSource(日线)   │
                        │  RQAlphaDataSource(h5) │ ← 预留：分钟/财报/行业/指数成分
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
│   │   │   ├── rqalpha_source.py #   rqalpha h5 预留
│   │   │   └── factory.py        #   数据源工厂
│   │   ├── engine/               # 回测引擎
│   │   │   ├── qlib_engine.py    #   Qlib 回测实现
│   │   │   └── task_manager.py   #   异步任务管理
│   │   └── routers/              # API 路由
│   │       ├── backtest.py       #   回测接口
│   │       └── data.py           #   数据接口
│   └── requirements.txt
├── frontend/                     # React 前端
│   ├── src/
│   │   ├── App.tsx               # 主界面
│   │   ├── api.ts                # API 客户端
│   │   ├── types.ts              # 类型定义
│   │   └── components/           # 组件（指标卡片/净值曲线）
│   ├── package.json
│   └── vite.config.ts            # Vite + 代理配置
├── start_backend.bat             # 启动后端
├── start_frontend.bat            # 启动前端
└── run_test2.bat                 # 运行数据验证脚本
```

## 快速启动

### 部署到新电脑
完整的拷贝清单、环境搭建、数据部署步骤见 **`deploy.md`**（含 requirements_qlib.txt / qlib_env.yml 依赖清单）。

### 1. 启动后端（FastAPI，端口 8001）

```bash
# 需在 qlib 环境（conda activate qlib）下运行
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
# 或直接双击 start_backend.bat
```

### 2. 启动前端（Vite，端口 5173）

```bash
cd frontend
npm install     # 首次
npm run dev
# 或直接双击 start_frontend.bat
```

然后浏览器访问 http://localhost:5173

### 数据路径配置
数据路径按以下优先级查找（不用改代码）：
1. 环境变量 `QLIB_PROVIDER_URI`
2. 项目目录下 `data/cn_data`
3. 当前用户主目录 `~/.qlib/qlib_data/cn_data`

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/backtest` | 提交回测任务，返回 task_id |
| GET | `/api/backtest/{task_id}` | 查询任务状态/进度/结果 |
| GET | `/api/backtests` | 列出所有任务 |
| GET | `/api/data-sources` | 列出数据源及能力 |
| GET | `/api/data/daily-bars` | 日线数据 |
| GET | `/api/data/minute-bars` | 分钟数据（rqalpha） |
| GET | `/api/data/financial` | 财报数据（rqalpha） |
| GET | `/api/data/industry` | 行业分类（rqalpha） |
| GET | `/api/data/index-constituents` | 指数成分（rqalpha） |
| GET | `/api/data/instruments` | 证券列表 |
| GET | `/api/data/calendar` | 交易日历 |

## 数据源说明

### Qlib 数据源（已启用）
- 提供 A 股**日线**行情（open/high/low/close/volume/amount/factor）
- 数据路径默认 `~/.qlib/qlib_data/cn_data`（当前用户主目录），可用环境变量 `QLIB_PROVIDER_URI` 覆盖
- **注意**：Qlib 数据本身只有日线，不含分钟/财报/行业/指数成分

### rqalpha h5 数据源（已实现）
- 提供 **分钟线（1min）、财报、指数成分、日线** 等 Qlib 缺失的数据
- 实现于 `backend/app/datasource/rqalpha_source.py`，通过 `h5py` 直接读取 rqalpha bundle 的 h5 文件
- 启用条件：`RQALPHA_BUNDLE_PATH` 指向 bundle 目录（如 `E:\rq\bundle`），目录存在则自动注册
- **依赖**：需安装 `h5py`（`pip install h5py`）
- 依赖 `E:\rq\bundle` 存在 `h5/equities/`（分钟）、`finance/pit/`（财报）、`constituents/index/`（指数成分）等子目录

> 注：行业分类（industry）能力目前未实现（capabilities 标记为 false）。

## 回测引擎说明

- 特征：Alpha158（默认）/ Alpha360
- 模型：LightGBM（默认）/ XGBoost / 线性回归
- 策略：TopK 选股
- 流程：数据 → 特征 → 训练 → 滚动预测 → 回测 → 风险指标（年化收益、夏普、最大回撤、胜率、净值曲线）

### 关键踩坑记录
1. **mlflow 文件存储维护模式**：需设置 `MLFLOW_ALLOW_FILE_STORE=true` 并使用 sqlite 实验追踪后端
2. **股票代码格式**：qlib 回测的 exchange 用**大写**格式（`SH600000`），转小写会导致策略无法建仓
3. **回测结果提取**：从 `PortAnaRecord` 的 `report_normal_1day.pkl` 提取风险指标与净值

## 训练/测试划分（滚动训练）

支持两种训练/测试划分方式（前端"训练/测试划分"区块可切换）：

| 模式 | 说明 |
|---|---|
| `single`（一次性） | 用回测开始前 N 单位（默认约1年）的数据训练一次，整个回测区间用同一模型预测 |
| `custom`（滚动） | 每个测试窗口开始时，用「测试窗口起点往前 N 单位」的最新数据**重新训练**，再预测并回测该窗口 |

### 滚动训练参数
- `train_win` / `train_unit`：训练窗口数值 + 单位（`day`/`week`/`month`）
- `test_win` / `test_unit`：测试窗口数值 + 单位
- `step_win` / `step_unit`：滚动步长（可选，默认每次推进一个测试窗口，即不重叠）

### 滚动训练的特点
- **每周期重训**：每个测试窗口用截止该时点的最新数据重新训练模型，最接近真实场景
- **无未来数据泄漏**：训练集只使用测试窗口之前已发生的数据（`fit_end_time` 设为训练窗口末尾）
- **账户连续**：各段账户资金自然延续（下一段期初 = 上一段期末），净值曲线无缝衔接
- **结果拼接**：各段净值/调仓记录拼接成完整结果；各段回测在 mlflow 中分别留痕

### 注意
- 滚动训练比一次性训练**慢很多**（每个测试窗口都要重训），窗口越小段数越多耗时越长
- 周/日单位会产生很多段，回测耗时显著增加

## 交易成本与成交设置

回测支持配置以下交易参数（后端 `BacktestRequest` 字段 + 前端表单）：

| 参数 | 含义 | 默认 | 说明 |
|---|---|---|---|
| `deal_price` | 成交价基准 | `close` | close/open/vwap |
| `open_cost` | 买入手续费 | 0.0005 | 比例，如 0.0005 = 0.05% |
| `close_cost` | 卖出手续费 | 0.0015 | 比例 |
| `min_cost` | 单笔最低手续费 | 5 | 元 |
| `impact_cost` | 滑点/市场冲击成本 | 0.0005 | 比例，作为额外成本计入 |
| `volume_threshold` | 成交量限制 | 0.25 | 单笔成交不超过当日成交量×比例；None=不限量 |
| `limit_threshold` | 涨跌停限制 | 0.095 | 按 |change| 判断；None=不设涨跌停 |
| `trade_unit` | 每手股数 | 100 | A股一手100股；None=不按手数取整 |

### 训练产物（可复现）
每次回测训练完成后，会保存**模型交付物**，前端在结果区展示"训练产物"区块，接口 `/api/backtest/{task_id}/artifacts`：

| 模型类型 | 可复现的交付物 |
|---|---|
| **Linear** | 完整线性公式（截距 + 158 个权重 + 特征名），权重按 |权重| 排序，可下载 CSV |
| **LightGBM/XGBoost** | 完整树模型序列化文件（`model.txt`，可直接 `lgb.Booster(model_file=...)` 加载复现）+ 超参数 + 树数量 |
| 通用 | 特征列表（可下载） |

**产出物包括**：
- `model_artifacts.json`：模型信息、特征名、超参数、线性权重（存于 `backend/workdir/artifacts/{task_id}/`）
- `model.txt`：树模型序列化文本

这样训练过程不再是"黑盒"——每个阶段的公式、参数、权重都有记录，可以复现或审查。

### 收益曲线颜色
- 策略净值：**红色 + 加粗**（strokeWidth 3）
- 基准曲线：**蓝色**（默认粗细）

### 回测产物目录（可复现、可对照）
每次回测在 `backend/workdir/artifacts/` 生成一个**可读命名的目录**：

```
{日期}-{时间}_{模型}_{股票池}_{起始年}_{结束年}_{task_id}
例：20260823-233306_Linear_csi300_2021_2021_a095f900c714
```

目录内容：
| 文件 | 说明 |
|---|---|
| `params.json` | 完整回测参数（**复现模式**直接读取，一键填充表单） |
| `meta.json` | 人工可读参数快照（股票池/起止/资金/手续费等） |
| `nav_curve.png` | 净值曲线截图（策略红粗 + 基准蓝），**含标题参数** |
| `params_snapshot.png` | 参数快照图（含回测结果指标 + 全部参数） |
| `segment_XX/` | 滚动训练时每段独立子目录，含该段的 `model_artifacts.json` + `model.txt` |

**滚动训练每段的模型交付物都独立保存**，可查看/复现每个阶段的模型。

### 复现模式
前端底部"历史回测"区块：
- 列出所有成功回测（显示可读目录名、模型、区间、资金、年化）
- 点**"复用参数"** → 自动把该次回测的完整参数填回表单（起始资金自动换算为万元），并附带 `load_model_task_id` 复用权重
- 点**"查看"** → 打开该次回测的完整结果（指标/曲线/训练产物/调仓记录）
- 点**"曲线"** → 直接查看该次回测的净值曲线截图

**复用模型权重（不重新训练）**：点"复用参数"后提交回测，会直接加载之前训练好的模型权重（`model.pkl`），**跳过耗时训练**，直接预测+回测。Linear 为确定性模型，复用结果与原结果完全一致（已验证 total_return 一致到小数点）。

### 停止回测
任务状态旁有**"停止回测"**按钮（任务运行/排队时可点），点击后任务标记取消，进度回调检查取消标志后终止，状态变为 `cancelled`，"回测进行中"按钮自动复原。适用于滚动训练等长耗时任务（在段之间停止）。

### 滚动段截图
每个滚动段在 `segment_XX/` 下独立生成：
- `summary.png`：该段的**净值曲线 + 参数横排**合并图（一张图，参数横排在下方网格）
- `nav_curve.png`、`params_snapshot.png`：单图
- `model.pkl` + `model_artifacts.json`：该段的模型对象与交付物

### 收益曲线纵轴
净值曲线 Y 轴**不从 0 开始**，而是从「策略净值与基准的历史最小值中较小者」×0.9 开始，放大收益波动的可视化差异。

### 布局
- 结果区顺序：指标卡片 → 净值曲线 → **训练产物** → **调仓记录**（训练产物在调仓记录上方，不用拉到底）

### 起始资金单位
- 前端起始资金以**万元**输入（如 100 万输入 `100`），提交时后端自动转为元
- 避免输入一长串 0

### 注意
- 后端默认端口 **8001**（8000 可能被其他服务占用），前端 Vite 代理已对应指向 8001

### 调仓记录
回测结果中会返回 `trades`（逐日逐笔调仓明细），前端以表格展示，支持**按月分页 + 翻页 + 日历/月份快捷定位**，并可按方向/代码筛选：

| 字段 | 含义 |
|---|---|
| `date` | 调仓日期 |
| `instrument` | 股票代码（大写，如 SH600183） |
| `direction` | 方向：1=买入，-1=卖出 |
| `amount` | 目标股数 |
| `deal_price` | 成交价 |
| `trade_value` | 成交额（股数×成交价） |
| `trade_cost` | 该笔成本（手续费+滑点） |
| `ffr` | 成交率（受成交量限制影响，<1 表示部分成交） |

**说明**：`ffr < 1` 意味着因成交量限制（`volume_threshold`），实际成交低于目标量，这是真实市场的部分成交模拟。

### 起始资金对成交率的影响
`initial_capital`（起始总资产）会影响成交率，因为单笔买入金额受**当日成交量 × volume_threshold** 限制：

| 起始资金 | 前5笔买入成交率（实测） |
|---|---|
| 100万 | 100%、100%、100%、100%、100%（全部满额） |
| 1亿 | 89%、45.8%、96.9%、24.1%、37.7%（大量部分成交） |

- **资金越小**：每笔买入金额小，容易落在成交量限制以内 → 满额成交
- **资金越大**：单笔买入金额大，超过当日成交量限制 → 部分成交（更贴近真实大资金冲击成本）

如果你想观察"满额成交"的效果，把起始资金设小（如 100万）即可；要模拟真实大资金的交易难度，用大资金。

### 重要验证结论（回测正确性）
通过**严格对照实验**（相同模型 Linear、相同窗口 2021-01~2022-06，仅交易成本不同）验证：

| 配置 | 年化收益 | 累计收益 |
|---|---|---|
| 理想化（零成本、不限量、无涨跌停） | **16.97%** | 25.09% |
| 带成本（手续费+滑点+成交量限制+涨跌停+手数） | **12.50%** | 18.33% |

加入交易成本后年化收益下降约 4.5 个百分点，**证明手续费/滑点/成交量限制真实生效**，回测结果会随成本假设而变化，避免高估收益。

### 默认成本假设的合理范围（A股）
- 佣金约 0.025%（可低至万2.5），印花税卖出 0.1%
- 滑点（冲击成本）建议 0.05%~0.1%
- 成交量限制建议 0.1~0.3（单笔不超当日成交量的 10%~30%）

## 配置环境变量

| 变量 | 说明 |
|---|---|
| `QLIB_PROVIDER_URI` | Qlib 数据目录 |
| `RQALPHA_BUNDLE_PATH` | rqalpha h5 bundle 目录（预留） |
| `QLIB_WORK_DIR` | 回测临时/实验工作目录 |
| `CORS_ORIGINS` | 允许的前端来源（逗号分隔） |
