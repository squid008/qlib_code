# Qlib 量化回测平台

> **当前版本：v1.18.15**（语义化版本，后端 `backend/app/__init__.py` 定义，前端标题栏显示）
>
> 各版本更新记录见 **[`md/change_log.md`](./md/change_log.md)**（按 Keep a Changelog 规范）。
>
> **每个版本号递增都会打附注 tag**（自 v1.18.8 起确立约定；历史版本已一次性补齐，`v1.0.0` 起共 92 个 tag）。任意发版均可一键回退：
> `git fetch --tags` → `git checkout v1.18.7`（只看）或 `git reset --hard v1.18.7`（彻底回退，慎用）。

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
│   │   ├── factors/              # ★ 公式翻译器 + 因子 Handler
│   │   │   ├── parser/           #   Lexer/Parser/Semantic/CodeGen（益盟/通达信公式）
│   │   │   ├── ops_ext.py        #   外挂算子（BARSLAST/BARSSINCEN/DYN_*/SR/EMA_TDX 等）
│   │   │   ├── panel_expr.py     #   面板求值器（替代 qlib D.features，含预热缓冲 warmup_days）
│   │   │   ├── single_test.py    #   单因子测试（覆盖率/IC/分层/触发诊断）
│   │   │   └── handler.py        #   SelectedAlpha158/360 + FormulaHandler
│   │   ├── engine/               # 回测引擎
│   │   │   ├── qlib_engine.py    #   Qlib 回测实现
│   │   │   ├── board_exchange.py #   按板块涨跌停（主板10%/创业科创20%/北交所30%）
│   │   │   ├── feature_cache.py  #   特征磁盘缓存（CachedQlibDataLoader）
│   │   │   ├── periodic_strategy.py # 按持仓周期整体换仓策略
│   │   │   ├── analysis.py / metrics.py / artifacts.py / charts.py  # 分层/IC/产物/绘图
│   │   │   └── task_manager.py   #   异步任务管理
│   │   ├── services/             # 业务服务
│   │   │   └── custom_formulas.py #  自定义公式持久化（workdir/custom_formulas.json）
│   │   └── routers/              # API 路由
│   │       ├── backtest.py       #   回测接口
│   │       ├── data.py           #   数据接口
│   │       └── factors.py        #   公式翻译/算子/自定义公式 CRUD
│   └── requirements.txt
├── frontend/                     # React 前端
│   ├── src/
│   │   ├── App.tsx               # 主界面
│   │   ├── api.ts                # API 客户端
│   │   ├── types.ts              # 类型定义
│   │   └── components/           # 组件（净值/IC/分层曲线、指标卡、单因子诊断面板、公式编辑器等）
│   ├── package.json
│   └── vite.config.ts            # Vite + 代理配置
├── md/                           # 辅助文档（见文末「md/ 文档说明」）
├── start_backend.bat             # 启动后端
├── start_frontend.bat            # 启动前端
└── ai_test/                      # AI 临时验算文件（探查/诊断脚本、输出等，可随时安全删除，不入版本库）
```

## 快速启动

### 部署到新电脑
完整的拷贝清单、环境搭建、数据部署步骤见下方 **[部署指南](#部署指南)**（含 requirements_qlib.txt / qlib_env.yml 依赖清单）。

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
| GET | `/api/backtests/history` | 历史回测列表（含是否运行中） |
| GET | `/api/backtest/{task_id}/partial` | 滚动回测已跑段的部分结果（partial_result.json，未完成任务也可读） |
| GET | `/api/backtest/{task_id}/artifacts` | 模型交付物（公式/权重/超参数/特征） |
| GET | `/api/backtest/{task_id}/snapshot` | 回测参数快照（复现/复用用） |
| GET | `/api/backtest/capacity` | 并发回测能力（max/running/queued/available） |
| GET | `/api/data-sources` | 列出数据源及能力 |
| GET | `/api/version` | 后端版本号 |
| POST | `/api/factors/translate` | 翻译益盟/通达信公式为 qlib 表达式 |
| GET | `/api/factors/operators` | 列出公式翻译器支持的算子 |
| GET | `/api/factors/custom-formulas` | 列出已保存的自定义公式 |
| POST | `/api/factors/custom-formulas` | 编译并保存自定义公式 |
| PUT | `/api/factors/custom-formulas/{id}` | 修改（重新编译）自定义公式 |
| DELETE | `/api/factors/custom-formulas/{id}` | 删除自定义公式 |
| POST | `/api/factors/single-factor-test` | 提交单因子测试任务（异步；可选 `warmup_days` 预热缓冲） |
| GET | `/api/factors/single-factor-test/progress/{task_id}` | 查询单因子测试进度/结果 |
| POST | `/api/factors/single-factor-test/cancel/{task_id}` | 取消单因子测试任务 |
| GET | `/api/factors/single-factor-test/tasks` | 列出最近的单因子测试任务 |
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

- 特征：Alpha158（默认）/ Alpha360 / **自定义公式因子**（益盟/通达信公式，FormulaHandler，见 `md/自定义因子与因子库架构.md`）
- 模型：LightGBM（默认）/ XGBoost / 线性回归
- 策略：TopK 选股（可配置持仓周期/分层持仓）
- 涨跌停：**按板块区分**（主板 10% / 创业板、科创板 20% / 北交所 30%，`BoardAwareExchange`，外挂实现不改 qlib 内核）
- 特征缓存：同参数复用回测时，特征计算走磁盘缓存（`workdir/feature_cache/`），复用回测大幅加速
- 流程：数据 → 特征 → 训练 → 滚动预测 → 回测 → 风险指标（年化收益、夏普、最大回撤、胜率、净值曲线）+ IC / 分层分析
- **信号后处理（v1.14.0，默认关，零影响）**：主模型训练后可选的"信号合成"（`app/engine/signal_compose.py`；single 与滚动 custom 均可用，滚动为每段用该段训练窗重新训练门控模型，见 v1.16.0）：
  - `Meta-Gate`：在主模型 topK 候选内训练一个二分类 gate（学"未来收益>0"），按日把 gate 概率最低 `reject_ratio`（默认 25%）的候选剔除后再进回测（预测排序不变，纯风控闸门）
  - `触发叠加`：把触发型 0/1 公式（如冰谷火焰）作为特征列，在其触发样本上训练"触发专用模型"，每日从触发池选出 z 最高的 M 只（默认 5），作为主池外的 `weight`（默认 20%）小仓位叠加（`target_w` 权重进策略；`PeriodicTopKStrategy` 支持权重目标，无权重列自动回退等权 topk）
  - 二者可独立或组合开启；IC/分层仍按主模型诊断。默认关闭即完全复现旧行为（详见 `md/change_log.md` v1.14.0）
  - **硬规则闸门（v1.15.0）**：`hard_filters` 确定性过滤（市值上下限/股价下限），先于 gate/overlay 从候选剔除（同一通道将来可接财务规则：营收/股价/市值等）
  - **前端已可配置**：回测表单"信号后处理"区勾选 Meta-Gate 与硬规则闸门；Meta-Gate 下可多选"已保存公式"作为 Gate 附加特征（`extra_features`，v1.15.1，让机器自动在多个 01 间挑）；模型超参新增"随机种子 seed"（默认 0=同参可复现，多 seed 研究可改正整数）

### 关键踩坑记录
1. **mlflow 文件存储维护模式**：需设置 `MLFLOW_ALLOW_FILE_STORE=true` 并使用 sqlite 实验追踪后端
2. **股票代码格式**：qlib 回测的 exchange 用**大写**格式（`SH600000`），转小写会导致策略无法建仓
3. **回测结果提取**：从 `PortAnaRecord` 的 `report_normal_1day.pkl` 提取风险指标与净值
4. **复权数据口径（v1.6.1 修正，务必知晓）**：本机 qlib 数据的 `$close/$open/$high/$low` 原生即**后复权价** = 真实价 × `$factor`；`$change` 是真实价（东财"不复权"口径）涨跌幅。三种复权取价：
   - `none`（不复权/真实价）= `$close / $factor`
   - `backward`（后复权）= `$close`（数据原生）
   - `forward`（前复权）= `$close ÷ 每股最新因子`（以最新交易日归一，比率类特征/收益率与 backward 等价）
   - v1.6.1 前误按"`$close` 未复权、复权 = `close×factor`"实现（双重复权 factor²、除权日假跳空），已修复。
   - **后复权价绝对值因各行情软件复权起点基准不同，不可跨软件比较**；可比的只有"前复权价"（统一按最新交易日归一）与"收益率"。复权价与 `factor` 必须取自**同一份** qlib 数据（内部自洽），不可跨数据源混用。
   - 收益率口径：推荐用 `forward`/`backward`（含分红送转的真实可投资回报，除权日连续）；`none` 在除权日收益率含"除权跳空"，仅适合与行情软件 K 线对照，不建议作为训练/回测口径。
5. **涨跌停判定口径（v1.6.6 / v1.10.10）**：本机有 `tools/dump_states.py` 同步的**交易所涨跌停价标签**（`$limit_up/$limit_down`）时优先用标签判定（覆盖 ST 5% / 退市整理 10% / 创业板科创板 20% / 北交所 30% 全部规则）；**无涨跌停限制日（新股上市首 5 日等）标签为 0/NaN，不判封板**（v1.10.10 修复此前 `close≥0` 恒真导致的上市初期触发被误当涨停剔除）。无标签源自动回退按代码段倒推——主板 10%、创业板/科创板 20%、北交所 30%；创业板必须匹配 **`SZ30` 整段**（300/301/302…注册制扩容沿 30 段后延，未来 303/304/305 同属 20%），不能只认 `SZ300/SZ301`。真实价与反推昨收统一 round 到分（交易所/聚宽整分价口径，数据源真实价 = `$close/$factor` 除法有 float 尾差）；单因子测试面板另有 **"价格整分"** 开关（默认开）把真实价按分取整参与因子计算，与益盟/聚宽口径一致。统一实现在 `app/engine/limits.py`，单因子测试剔除与回测 `BoardAwareExchange` 共用。同一实体因重组更换代码段（如成飞集成 002190→中航成飞 302132）时历史数据按各自代码段归属，判定互不干扰。
6. **输出 dtype 必须与 qlib 对齐——"面板 float64 vs qlib float32"的全 A 端到端差异战役（v1.17.6 里程碑）**：
   - **现象**：面板执行器（`panel_expr.py`）替代 qlib 求值后，**小池/单股对拍全部 0 差**，但**全 A 端到端**（"趋势顶底离开底部" csi300→all、H=20、全剔除开关）触发集零星翻面（面板 25444 vs qlib 25427、约 15 处、daily +0.002pp）。曾反复误判"EMA 起点敏感"并两次回退/放开（v1.17.1 / v1.17.5）。
   - **真凶**：qlib `D.features` **所有字段最终输出整列为 float32**（内部 float64 求值、出口 cast，见 qlib `data.py`），而面板输出 float64 → 两者 `CLOSE/CHANGE` 等列有 ~4-8e-6 的 float32 ulp 尾差。因子值本身逐位一致，但尾差传导到**涨停/停牌剔除判定**（`limits.mark_limit_up` 用 `close >= limit_up - 1e-6`，1e-6 容差与尾差同量级）→ 边界触发翻面。**差异不在求值，在输出 dtype + 下游剔除容差**。
   - **修复**：面板求值保持 float64（EMA 递归收敛精度不变），**出口统一 cast float32**（`panel_expr._cast_output_f32`）与 qlib 返回 dtype 对齐 → 全 A 触发集 0 差（24128==24128、daily 0 差）。
   - **排查方法论（可复用于同类诡异差异）**：小池 0 差但全 A 有差时，不硬碰更大样本——**抓差异行做单股全字段组复现**（把全 A 问题降维成 15 只×单股逐只击破）；把"触发集"与"剔除环节"分离排查（剔前 0 差 ⇒ 差异必在剔除）；最后逐列对比 base/tag 发现 CLOSE 差 4e-6 → 定位到 dtype。**教训：自研求值器越精确（float64）≠ 与引擎一致；对账"基本对齐"的 0.00x pp 小差背后可能是系统性 dtype 不一致，值得一次性深挖到底。**
7. **单因子诊断的"预热缓冲"与 `SR` 的 250 天静态前扩（v1.18.6）**：
   - **背景**：面板执行器默认从 `start_date` 当天起加载特征，而 `_tree_ext_days` 只能前移**可静态推断的固定窗口量**；`DYN_*` / `BARSCOUNT` / `HHVBARS+Ref` 嵌套这类**动态窗口**真实所需历史无法静态推断 → 长回看公式在评估区间首日因子为 NaN / 未收敛（实测 CCCMA250 不复权：区间首日全市场"因子=1"股票数 **0**、因子覆盖率 71.5%→88% 爬坡）。
   - **方案**：新增 `warmup_days`（交易日）预热缓冲——面板普通组 `read_start = 可推断预热 + warmup_days`、起点敏感组 `精确扩展 + warmup_days`；qlib 回退路径从 `load_start` 起 `D.features`；**出口统一裁剪回 `[start_date, end_date]`**。入口：单因子请求体 `warmup_days` / 前端「预热缓冲」输入框（默认 250 ≈ 1 年、`0`=关闭、留空=服务端默认）/ 服务端 env `QLIB_SFT_WARMUP_DAYS`（默认 250）。
   - **语义边界（重要）**：输出仍只覆盖 `[start_date, end_date]`，**预热行绝不进入统计**；**固定窗口算子（Mean/Max/Ref…）结果与关闭预热时逐位相同**；`EMA`/状态类算子只是序列起点提前（值更接近长历史真值，不再复刻 qlib 在 `start_date` 的冷启动）→ 默认启用不会污染既有对账结论。
   - **口径澄清：`SR` 自带 250 天静态前扩**。`SR.get_extended_window_size() = 子ext + LOOKBACK_DAYS(250)`，而默认 `suspend_remove=True` 时表达式叶子被 `SR(...)` 包裹 → **qlib `D.features` 与面板本就无条件前读 ≥250 个交易日**。因此默认口径下 `EMA` 这类 ~50 天收敛的算子早已"预热过量"，再叠加 `warmup_days` 属**冗余**（实测 0/3/200 三档首日均值逐位相同）；**判断"预热是否生效"必须先把 `SR` 关掉（`suspend_remove=False`）或改用面板侧动态窗口公式验证**，否则会误判"预热没起作用"。预热真正解决的是**面板侧动态窗口公式**（其扩展量静态不可知）。
   - 验证：CCCMA250 不复权 / 全 A / H=10 / 2021-01-04~2026-06-01 → 触发 **627 → 628**（与"start_date 提前到 2019-07-01"的旧实证 628 一致）；`QLIB_SFT_WARMUP_DAYS=0` 复现 627。
8. **稀疏 0/1 信号要看"事件研究"，而不是按日配对检验（v1.18.7）**：
   - **背景**：稀疏触发信号（每日触发只数**中位数 = 1**）的「按日配对检验」会退化成**单票收益序列**。全 A 实测 CCCMA250：日差均值 +1.33%，但 **Top20 天贡献日差净和的 100.1%**、剔除后剩余日均 **-0.002%**、80% 净和仅需 14 天（3.5%）——因为触发当天截面常常只有 1 只票，"组均值"没有横截面意义（743 次触发里最频繁的股票也只出现 3 次）。此前那 1.33% 的显著性（HAC t=2.93）实际由 `SH605099` 一类连板妖股的单票行情撑起。
   - **方案**：新增**事件研究**——把**每次触发**对齐到 T=0（T+1 收盘买入、T+1+k 收盘卖出，k=1..40），输出 `curve`（各持有期均值/中位数/胜率/p10~p90）、`prob`（收益 >0/>10%/>20%/>50%/>100% 的事件占比）、`upside`（"期内最高点卖出"的赔率上限与曾达 +20%/+50%/+100% 比例）以及 Top/Worst 事件明细。接口 `POST /api/factors/event-study`（异步提交 + 轮询 + 取消，与回测/训练共用并发配额）；**并已并入单因子测试顺带计算**（面板新增 `PX` 列 + `_test_one` 回传触发样本索引），前端点按钮**直接秒开**、结果表新增「中位数」列，无需二次加载面板。
   - **结论判定变更**：**0/1 信号的「结论」改以事件研究为准** —— 中位数 ≥0.5% 且胜率 ≥55% → 有效✓；`|中位数| <1%` 且胜率 45%~55% 且均值显著更高 → **「彩票型」**（收益由极少数尾部事件撑起，不可作稳定 alpha）；中位数 ≤-0.5% 且胜率 ≤45% → 有效(反向)✓。连续因子仍走 IC / 分位判定。
   - **实测（CCCMA250 / 全 A / 2021-01-01~2026-06-01 / h=10 / 前复权，743 次触发）**：胜率全程 44.5%~52.8%、中位数长期 ≈0（k=40 仅 +0.213%，与均值 +3.331% 差 16 倍）→ **统计显著但不可作为稳定 alpha**；"期内最高点卖出"理想口径曾达 +50% 仅 **5.8%**、+100% 仅 0.7%。口径自检：k=10 均值 +1.6469% 与单因子测试「触发收益均值」逐位一致。
   - **默认口径**：单因子测试「剔除 ST(T+1)」改为**默认勾选**（ST/*ST/退市整理期个股收益分布与正常股差异极大；创业板/科创板仍默认关）。
   - **（v1.18.8）基准与超额曲线**：只给绝对收益无法回答"是不是市场给的" → 新增**日配对口径**的「基准（未触发组）」与「超额 = 触发 − 基准」，主图叠加基准灰虚线 + 独立超额小图 + 摘要卡片「k=N 超额」；**仅展示、不参与判定**。全 A 实测（CCCMA250 / h=10）：触发 +1.728% vs 基准 +0.328% → **超额 +1.400%**，且随持有期单调上升（k=40 +2.03%）→ **不是 beta 伪装 alpha**；但中位数仍 ≈0、超额同样靠尾部妖股 → 「彩票型」定位不变。
   - **（v1.18.9）「待观察」可解释性**：判定用**未舍入原始值**、而悬停胜率原只显示 1 位小数 → `win=0.549694` 显示 "55.0%" 却 `< 0.55` → 判「待观察」，极易困惑（实测 `CWH_MIX_D_PCT_R40` 中位数 +1.153%、显示胜率 55% 却待观察即此因）。现：悬停胜率显示到 **0.01%**；「待观察」新增**悬停原因**（逐条给出哪项未达标、差多少）。**仅展示层，判定逻辑未改**。
   - **（v1.18.10）修复「重新计算」后基准/超额曲线消失**：v1.18.8 的基准曲线只在「单因子测试顺带计算」路径里算，独立接口 `run_event_study` 未算 → 弹窗点「重新计算」（走独立接口）后基准灰虚线与超额小图自动隐藏。已在 `run_event_study` 补齐**同口径**计算（全样本宽表 → 日配对），并把 `baseline` 写入返回值。
   - **（v1.18.11）新增「中位数超额」曲线 + 图例可点击切换**：原超额仅**均值口径**（易被少数连板/妖股主导）→ 新增**中位数口径（事件级）**，`excess_median = 触发组中位数 − 未触发组中位数`（口径对称、不可与均值混用）。第二张图同时画两条线，并给出「均值 − 中位数 > 1% ⇒ 超额主要来自少数暴涨事件」的判读提示；摘要卡片同时显示两个口径。**两张图的图例均可点击隐藏/重新显示曲线**。两条路径（顺带计算 / 独立接口）共用同一函数，故「重新计算」的图同样生效。
   - **（v1.18.12）修复事件研究期数硬编码 + 第一张图补基准中位数**：① 顺带计算的期数原被硬编码为 `EVENT_MAX_K = 40`，导致「周期填 80」时事件研究仍只算 40 期（而尾部数据早已按 `max(horizons, 40)` 加载好）→ 改为 `es_k = max(EVENT_MAX_K, max(horizons))`，**周期填多少就真算多少期**，默认仍 40；② 第一张图补 `基准中位数(未触发组·事件级)` 曲线，与 `median` 口径对齐——此前 `median`（事件级）与 `baseline`（均值口径）不可直接相减，无法判断"中位数超额上升"来自触发组走强还是基准下跌。
   - **（v1.18.13）两个"胜率"改名区分（纯文案）**：差值列的 `daily_win` = **相对收益胜率**（配对日中触发组日均跑赢未触发组的交易日占比）；事件研究的 `curve[].win` = **绝对收益胜率**（触发事件持有 k 日收益 >0 的事件占比）。两者可差 3 个点以上（如 40 日 52.4% vs 55.3%），原同名极易误读为数据不一致。现全部标注清楚：差值列写「相对收益胜率」（内联小字为「日胜」）、结论列与事件研究弹窗一律写「绝对收益胜率 ≥55%」（「时间集中」属日配对语境故写相对）、导出表头 `胜率(%)` → `相对收益胜率(%)`。
   - **（v1.18.14）事件研究「未平仓/数据不足」透明化 + 榜单口径修复**：① **修复榜单 k 口径错配**——「贡献最大/最差事件」原固定按 **max_k 期**排序，与表头（当前「最长持有」档位）不符（滑到 2 日却显示 40 日收益），且末期满 NaN 的事件被**整个跳过**（尽管它在 k=2 上有效）→ 改为**逐 k 各出一份榜单**（`top_by_k`/`worst_by_k`），前端按当前 k 取值。② **新增「未纳入统计的触发（数据不足）」透明提示**——把 `end_date` 设到数据尾部附近时，末尾触发"到截止日还没平仓"，原先**静默消失**（不进曲线、不进榜单，界面无任何提示）→ 现于摘要卡片、概率表下方、页面底部三处标注数量与明细（代码/信号日/已得期数），并提示"建议把结束日期提前 max_k 个交易日以上"。实测：全 A `2026-01-01~2026-08-21`（数据尾 08-21）时 `n_short=1`（`SH600333 2026-08-18 已得 2 期`），滑到「最长持有=2」即可在榜单中看到它。
   - **（v1.18.15）修订因子库存储规范 §7（文档）**：用外部实测逐条核对后发现原稿两处写错——① **§7.2 轴序反了**：`(instrument × datetime)` 的理由写"横截面读取快"，实际 C-order 取某天=取列=跨 stride（HDF5 未分块时退化为整表扫描，实测 151ms vs 0.4ms）→ 改为 **`(datetime × instrument)` 连续、不压缩**，并把表述修正为"**轴序在 HDF5 里要命（未分块时），在裸 memmap 里不要命**"；② **§7.3 依赖说反了**："已依赖 h5py 所以零新增" → 实际 **`pyarrow` 才是正式依赖**（`h5py` 是随 rqalpha 数据源的可选依赖）。另补两块决策：**"先决定存不存/存多少"**（50MB/因子 → 1000 个 = 50GB，值只存活跃子集）与 **§7.5 数据库选型**（元数据/指标/血缘用 **PostgreSQL**；SQLite 因 `patches/qlib_parallel.py:115` 记录的 `database is locked` 实证**不可当主库**；因子值仍走文件）。另修正压缩结论：外部"随机浮点不可压"不能照搬——本仓大量 0/1 因子 + NaN，且正确杠杆是**量化**而非 codec。

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
| `deal_price` | 成交价基准 | `close` | close/open/vwap/avg_co/avg_ohlc（avg_co=(开+收)/2，avg_ohlc=(开收高低)/4，买卖对称共用） |
| `price_adjust` | 复权方式 | `forward` | forward=前复权(默认) / backward=后复权 / none=不复权(真实价)。取价语义与复权注意事项见上文"关键踩坑记录"第 4 条 |
| `open_cost` | 买入手续费 | 0.0005 | 比例，如 0.0005 = 0.05% |
| `close_cost` | 卖出手续费 | 0.0015 | 比例 |
| `min_cost` | 单笔最低手续费 | 5 | 元 |
| `impact_cost` | 滑点/市场冲击成本 | 0.002 | 比例（0.002 = 0.2%），单边每笔订单都计，一次买卖计两次 |
| `volume_threshold` | 成交量限制 | None（留空） | 单笔成交不超过当日成交量×比例（如 0.25=25%）；None=不限量理想成交（默认） |
| `limit_threshold` | 涨跌停限制 | 0.095 | **按板块区分**（`BoardAwareExchange`）：主板阈值=该值(≈10%)，创业板/科创板=×2(≈20%)，北交所=×3(≈30%)；None=不设涨跌停 |
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
| `meta.json` | 人工可读参数快照（股票池/起止/资金/手续费/复权方式等） |
| `train_signature.json` | **训练签名快照**（git commit/数据版本/依赖版本/特征MD5/参数MD5，供历史模型追溯） |
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

### 停止回测 / 取消
任务状态区每张任务卡片有**"取消"**按钮（任务运行/排队时可点），并支持**"一键取消所有"**。点击后：
- 任务标记为 `cancelling`，通过**协作式取消**在训练/预测/回测各阶段之间终止，最终状态变为 `cancelled`。
- 通过 **qlib 外挂补丁**（见下文"qlib 外挂补丁"），**LightGBM/XGBoost 训练过程中也能响应取消**（每 10 轮检查一次），不必等整个训练块结束。
- 前端会提示"正在等待当前训练/回测块结束"，训练块结束后会停止。
- 取消请求已受理但训练块仍在运行时（如大数据量的模型训练），需等待该训练块结束（通常数十秒到数分钟）。

> 说明：取消是**协作式**的（非强制杀进程），不会产生半写的模型文件。若想加快取消响应，可调小环境变量 `QLIB_CANCEL_CHECK_ITER`（默认 10）。

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
- 滑点（冲击成本）参考 0.1%~0.2%（本项目默认 0.2%），小资金可低至 0.05%
- 成交量限制建议 0.1~0.3（单笔不超当日成交量的 10%~30%）

## 多任务并行（阶段一）

支持多个回测任务并发执行，**自动按硬件能力限制并发上限**，避免 CPU 满载互抢或内存吃爆 OOM。

### 硬件自适应并发
- 后端启动时自动检测 **CPU 核数 + 可用内存**，计算本机能安全并发多少个回测：
  `并发上限 = min(CPU可承载数, 内存可容纳数)`（内存为主，默认留 30% 系统余量）
- **家里小机 / 公司大机 / 服务器** 会自动得到不同的合理上限，无需改代码
- 单任务内存默认按 `3.0 GB` 估算，可用 `QLIB_TASK_MEM_GB` 覆盖

### 数据共享缓存（省内存）
- 多个回测任务跑在**同一个 Python 进程（多线程）**，共享同一份 qlib 内存数据
- 新增**进程内数据缓存** `DataCache`：相同（股票池, 特征, 区间）的 label/基准收益计算只做一次，后续任务直接复用，避免重复磁盘 I/O 和表达式计算
- **不会**"一个任务复制一份数据"

### 并发能力提示（前端）
- 后端新增 `GET /api/backtest/capacity`：返回 `max_concurrent / running / queued / available` + 硬件资源摘要
- 前端提交回测前检查：若已达并发上限（`available == 0`），**提示"已达并发上限，请等待"并拒绝提交**
- 前端显示当前并发占用（`并发: 2/5`）

### 并发上限配置
| 配置 | 默认 | 说明 |
|---|---|---|
| `QLIB_MAX_CONCURRENT` | 自动检测 | 显式指定并发上限（如服务器固定 4） |
| `QLIB_TASK_MEM_GB` | 3.0 | 单个回测任务的估算内存（GB） |
| `QLIB_MEM_HEADROOM` | 0.3 | 系统保留内存比例（不用于回测，防 OOM） |

## qlib 外挂补丁（不改 qlib 内核）

本项目对 qlib 采用 **monkey-patch 外挂**方式扩展能力，**不修改 qlib 源码**，避免集成 233 个文件成"屎山"，也便于 qlib 升级。

统一放在 `backend/app/engine/patches/`：

```
backend/app/engine/patches/
├── __init__.py        # 统一入口（patch_qlib_parallel / patch_cancel_callbacks）
├── qlib_parallel.py   # 多线程并行：把全局 R 替换为线程本地版本，多任务并发不冲突
└── cancel_train.py    # 训练中途可取消：monkey-patch lightgbm/xgboost.train 注入每N轮取消检查
```

### 提供的补丁能力

| 补丁 | 解决什么 | 实现方式 |
|---|---|---|
| **多线程并行** | qlib 的 `R`（Recorder）是进程级全局单例，多线程并发 `R.start()` 会互相覆盖 active_experiment；且 mlflow 的 `end_run` 用全局 client 找不到线程独立 db 里的 run（"Run not found"）、线程本地 active run 栈不清理（"already active"） | `patch_qlib_parallel`：每个线程懒创建独立的 `QlibRecorder(ExpManager)` 替换全局 R；并 patch `MLflowRecorder.end_run` 改用自身 client+run_id 结束、手动清理 active run 栈 |
| **训练中途可取消** | qlib 引擎 `model.fit` 训练块内无取消检查点，取消要等整个训练块结束 | `patch_cancel_callbacks`：patch `lgb.train` / `xgb.train`，注入"每 10 轮检查取消"的回调 |

**为何不改 qlib 源码**：qlib 核心包 233 个 .py（约 5-6 万行），且顶层 import 所有子模块，无法只拷贝某模块单独用。改内核会导致升级困难（`pip install -U qlib` 会覆盖改动）、依赖暴增（pytorch/mosec/mlflow 等）、代码结构变乱。外挂补丁只在 **qlib 的稳定入口**（`R`、`lgb.train`、`xgb.train`）做 patch，风险低、可维护。

### 可配置项
- `QLIB_CANCEL_CHECK_ITER`：LightGBM/XGBoost 每多少轮检查一次取消（默认 10，越小取消响应越快）

## 历史回测（复现/管理）

### 序号
- 每个回测分配**稳定序号**（`seq.json`），最早创建的回测序号=1，递增。
- 删除某个回测后**序号不回收**（如删 3 后显示 1,2,4,5），只有**全部清空**后序号才重新从 1 开始。
- 序号与目录名无关（存在各目录的 `seq.json`），**改文件夹名不影响序号**。

### 批量删除
- 标题区"批量删除"按钮开启**勾选模式**，每行出现勾选框（运行中的任务勾选框禁用）。
- 勾选后表头"操作"旁出现"确定删除（N）"，点它弹确认框列出所有待删目录，确认后**批量删除**。

### 分页
- 历史回测每页显示 **20 条**，超过自动分页。
- 页码导航支持**省略号**（页数多时中间省略）+ **输入框跳页**（输入页码回车或点"跳转"）。

### 复用
- **"复用参数"**：把该回测完整参数填入表单（含 `load_model_task_id` 复用权重），点"开始回测"提交。
- **"复用回测"**：直接用该回测参数 + 复用模型权重**立即开始回测**（覆盖表单当前改动）。
- 若在复用权重后**修改了股票池 / 特征 / 自定义公式 / 训练划分方式（single↔滚动）**，提交时**自动改为新训练**（清掉 `load_model_task_id`）并提示，避免"特征不匹配"报错。
- 提交成功后**清掉复用标记**，提示条消失；后续改参数不再提示，直到再次"复用参数"。

## 配置环境变量

| 变量 | 说明 |
|---|---|
| `QLIB_PROVIDER_URI` | Qlib 数据目录 |
| `RQALPHA_BUNDLE_PATH` | rqalpha h5 bundle 目录（预留） |
| `QLIB_WORK_DIR` | 回测临时/实验工作目录 |
| `CORS_ORIGINS` | 允许的前端来源（逗号分隔） |
| `QLIB_MAX_CONCURRENT` | 显式并发回测上限（默认自动按硬件检测） |
| `QLIB_TASK_MEM_GB` | 单个回测任务估算内存（GB，默认 3.0） |
| `QLIB_MEM_HEADROOM` | 系统保留内存比例（默认 0.3） |
| `QLIB_CANCEL_CHECK_ITER` | 训练中途取消检查频率（每多少轮，默认 10） |
| `QLIB_DATA_CACHE_GB` | 进程内共享数据缓存上限（GB）。默认按机器可用内存动态自适应 `clamp(可用×15%, 0.5, 4)`（16G≈2G / 48G≈4G / 96G 服务器封顶 4G）；大内存服务器可设 `8` 等放大以提升跨任务复用命中 |

---

## 部署指南

把本平台从开发机拷贝到新电脑的完整步骤。按顺序操作。

### 一、需要拷贝/准备的清单

| 项 | 是否已准备好 | 说明 |
|---|---|---|
| `qlib_code` 整个文件夹 | ✅ | 项目本体（含后端/前端/脚本/依赖清单） |
| `requirements_qlib.txt` | ✅ 已生成 | qlib 环境完整 pip 依赖（**主力安装清单**） |
| `qlib_env.yml` | ✅ 已生成 | conda 环境清单（参考，conda 无法锁定 pip 包） |
| `vs_BuildTools.exe` | ✅ | 编译 C 扩展需 MSVC 工具链 |
| 数据包 `qlib_cn_1/2.tar.gz` | ✅ | A股 qlib 数据 `cn_data`（GitHub Release `data-2026-09-05` 两分卷，下载解压合并，见第四节） |
| `<你的qlib源码目录>` 源码 | ⚠️ 需确认 | **qlib 是源码安装的**（见下文第三步） |

### 二、新电脑需要安装的软件

1. **Anaconda / Miniconda**（Python 环境管理）
2. **Node.js ≥ 18**（前端运行必需）
3. **VS BuildTools**（编译 LightGBM 等 C 扩展，已有 vs_BuildTools.exe）

> 验证：`conda --version`、`node -v`、`npm -v`

### 三、搭建 Python 环境（重点）

你的 qlib 是 **源码方式安装**（`pip install -e <qlib源码目录>`），不是 pip 的 pyqlib。
`requirements_qlib.txt` 里有一行 `-e <qlib源码目录>`。

**方式 A：源码方式（推荐）**
1. 把 `<qlib源码目录>` 一起拷贝到新电脑
2. 创建并激活环境：
   ```bash
   conda create -n qlib python=3.10 -y
   conda activate qlib
   ```
3. 安装依赖：
   ```bash
   # 先去掉 requirements_qlib.txt 里的 "-e <qlib源码目录>" 这一行（路径不对）
   pip install -r requirements_qlib.txt
   # 再以源码方式安装 qlib（把路径改成新电脑实际的 qlib 源码位置）
   pip install -e D:\你的qlib源码目录
   ```

**方式 B：pip 安装 pyqlib（更省事，但版本可能有差异）**
```bash
conda create -n qlib python=3.10 -y
conda activate qlib
# 去掉 requirements 里的 "-e <qlib源码目录>" 行后
pip install -r requirements_qlib.txt
pip install pyqlib
```
> 注意：pyqlib 的 API 和你现在的源码版可能有细微差异，若回测报错优先用方式 A。

**关键依赖确认（requirements_qlib.txt 已含）**
- lightgbm==4.7.0、matplotlib、scikit-learn、scipy、fastapi、uvicorn、pydantic

### 四、部署数据

1. 下载 A股 qlib 数据包（`cn_data`，**两个分卷都要下，缺一不可**），二选一：

   **方式1：GitHub Release 下载（推荐）**
   打开 https://github.com/squid008/qlib_code/releases/tag/data-2026-09-05 ，下载两个分卷：
   - `qlib_cn_1.tar.gz`（987.5 MB）
   - `qlib_cn_2.tar.gz`（1289.1 MB）

   **方式2：命令行下载（需 gh CLI 且已登录 GitHub）**
   ```bash
   gh release download -R squid008/qlib_code data-2026-09-05 -D data
   ```

2. 把两个分卷**都**解压到同一 `qlib_code\data` 目录，自动合并为完整 `cn_data`：
   ```bash
   cd qlib_code
   tar -xzf qlib_cn_1.tar.gz -C data
   tar -xzf qlib_cn_2.tar.gz -C data
   ```

3. 数据路径通过 **环境变量** 或 **目录约定** 指定（不用改代码）：

   **方法1（推荐）设环境变量：**
   ```bash
   set QLIB_PROVIDER_URI=D:\你的数据路径\cn_data
   ```

   **方法2：放到项目目录下**
   把 `cn_data` 放到 `qlib_code\data\cn_data`（后端会自动识别）

   **方法3：放到当前用户主目录**
   `C:\Users\你的用户名\.qlib\qlib_data\cn_data`

> 配置优先级：环境变量 > 项目内 data/cn_data > 主目录 .qlib

### 五、启动前端

```bash
cd qlib_code\frontend
npm install        # 首次安装依赖（如果没带 node_modules）
npm run dev        # 启动，端口 5173
```

### 六、启动后端

```bash
cd qlib_code\backend
# 用 qlib 环境的 python（按你机器实际路径）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
# 或直接双击 start_backend.bat
```

> 端口说明：后端用 **8001**（8000 可能被其他服务占用）；前端 Vite 代理已指向 8001。

### 七、验证部署

浏览器打开 `http://localhost:5173`，然后：
1. 看"历史回测"是否为空（正常，新环境无历史）
2. 选股票池 csi300、模型 Linear、起止日期（如 2022-2023）
3. 起始资金填 100（万）
4. 点"开始回测"，确认能跑通并出曲线、指标、调仓记录、训练产物

### 八、常见问题

| 问题 | 解决 |
|---|---|
| `import qlib` 报错 | qlib 未安装，见第三步 |
| 后端启动报"数据路径不存在" | 确认 QLIB_PROVIDER_URI / data/cn_data 存在 |
| 前端 `npm run dev` 报错 | 确认 Node.js ≥18，`npm install` |
| 8001 端口被占用 | 改 `start_backend.bat` 端口，并改 `frontend/vite.config.ts` |
| 回测结果全 0 / 无调仓 | 起始资金太小（<1万）买不起一手；或数据区间无数据 |
| summary.png 中文乱码 | 需安装微软雅黑字体（Windows 自带） |

### 附：如何重新导出依赖（在家更新后）
```bash
conda activate qlib
pip freeze > requirements_qlib.txt        # 生成 pip 依赖
conda env export --no-builds > qlib_env.yml  # 生成 conda 环境
```

---

## md/ 文档说明

`md/` 目录存放项目的辅助说明文档，按用途分类：

| 文档 | 说明 |
|---|---|
| `md/deploy.md` | 部署指南（已整合进本文 README，此文件保留一份独立副本供直接查看） |
| `md/start_stop.md` | 前后端启停脚本（`start_backend.bat` / `start_frontend.bat` / `stop_*.bat`）的使用说明 |
| `md/数据源接入.md` | 数据源抽象层（Qlib 日线 / rqalpha h5 分钟/财报/行业/指数成分）如何接入与扩展 |
| `md/自定义因子与因子库架构.md` | 因子能力总览：0 章"现状能力（已落地）"（Alpha158/360 勾选、catalog 接口、Provider 扩展、自定义公式）+ 2.0 架构设计稿（因子库、评估看板、预处理、h5 存储） |
| `md/change_log.md` | 版本更新记录（Keep a Changelog 规范，v1.3.2 起，README 顶部有链接） |
| `md/两地 git 工作流.md` | 家/公司两地协作的 Git 工作流约定 |
| `md/研报集合.md` | 量化研报要点摘录（核心方法论 + 对本项目的可落地点/差距清单，可续加） |
| `md/开发记录.md` | 每次开发更新/修复要点记录（本地专用，已 .gitignore 排除，不上传 GitHub） |
| `md/upload.md` | 本地专用文档（已在 .gitignore 排除，不上传 GitHub） |

> 提示：`md/upload.md` 被 `.gitignore` 排除，仅本地可见，不随仓库上传。
