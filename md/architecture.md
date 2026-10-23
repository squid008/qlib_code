# 架构（★ v2.11.61）

> **为什么有这份文档**：仓库有 6600 行开发记录、28000 行文档，但**没有一张模块依赖图** ✗ ——
> 新人（包括三个月后的我）只能靠读 2664 行的文件猜"谁调谁" ✓。
> 本文所有数字与依赖关系都是**脚本实测**的 ✓，复核命令见文末 ✓（**不写"应该有"** ✗）。

---

## 0. 一分钟版

```
浏览器(React)  ──HTTP /api──▶  FastAPI(routers)  ──▶  task_manager(线程池)  ──▶  run_backtest
                                     │                                            │
                                     ├──▶ services/（产物 / 任务存储 / 数据集 / 运行时）
                                     └──▶ factors/ ──▶ panel_expr（面板算子 / bin 读取 / 进程池）
                                                       engine/ ──▶ qlib（进程级单例）
```

**三条主线**：① 回测（训练+预测+净值）② 单因子测试（快速诊断因子）③ 交易信号测试（给定信号算收益）。
三者**共用** `factors/panel_expr` 的数据加载与算子层 ✓，只是"谁来算净值"不同 ✓。

---

## 1. 后端目录与职责（实测：124 个 `.py` / 39,743 行）

| 目录 | 文件数 | 职责 | 能不能依赖上层 |
|---|---|---|---|
| `app/*.py`（config / logger / metrics / main） | 5 | 配置、日志、进程内指标、FastAPI 装配 | **最底层**，谁都能用 ✓ |
| `app/models/` | 2 | **纯数据契约**（`BacktestRequest` 等 pydantic 模型） | 不允许依赖任何业务 ✓ |
| `app/services/` | 9 | 跨切面能力：产物读写、任务存储、数据集切换、运行时、错误策略、公式文件同步、表达式缓存 | ⚠ 现有多处反向依赖（见 §3）|
| `app/engine/` | 27 | **执行引擎**：`task_manager`（线程池/并发/预算）、`qlib_engine`（跑 qlib）、`patches/`（qlib 补丁）、`utils`、缓存 | ⚠ 有 1 处反向依赖 |
| `app/datasource/` | 6 | 数据源抽象（`base` + `factory` + `qlib` / `rqalpha` 两实现）| 只依赖 models ✓ |
| `app/factors/` | 31 | **因子层**：公式解析（`parser/`）、面板求值（`panel_expr`）、算子（`ops_ext`）、单因子测试、事件研究、筹码 | 不依赖 routers ✓（实测 0）|
| `app/signals/` | 22 | **信号层**：`run_backtest`、`period_roll`、`batch`、`engine` | 不依赖 routers ✓（实测 0）|
| `app/exports/` | 7 | XLSX 导出（后端生成，含原生图表）| 独立叶子 ✓ |
| `app/routers/` | 15 | **HTTP 层**：参数校验 + 组装 + 返回；`factors.py` 已拆 5 个（`factors_event` / `_formulas` / `_models` / `_single` / `_single_state` / `_single_validate` / `_state`）| **最上层**，只往下调 ✓ |

---

## 2. 依赖矩阵（**实测**，2026-10-23）

行 = 包；列 = 它 import 的包；数字 = 该包内 import 语句出现次数（含相对导入 ✓）。

| ↓依赖→ | routers | factors | signals | engine | exports | datasource | services | models |
|---|---|---|---|---|---|---|---|---|
| **routers** | · | 26 | 18 | 9 | 6 | 2 | 17 | 1 |
| **factors** | . | · | · | 24 | 1 | · | 5 | · |
| **signals** | . | 4 | · | 12 | 1 | · | 1 | · |
| **engine** | . | **1** | · | · | · | 1 | 2 | 6 |
| **exports** | . | · | · | · | · | · | · | · |
| **datasource** | . | · | · | · | · | · | 1 | · |
| **services** | **1** | **9** | · | **8** | · | · | · | · |
| **models** | . | · | · | · | · | · | · | · |

**读法**：`routers` 依赖最多（26 次 factors + 18 signals + …）✓ 符合"最上层" ✓；
`exports` / `models` 是**叶子**（不依赖任何业务包 ✓）。

---

## 3. ⚠ 分层违规：19 处（**如实记录，不画漂亮的假图**）

约定：`models/config`（L1） ← `engine/datasource`（L2） ← `factors/signals/exports`（L3） ← `routers`（L4）。
**下层 import 上层 = 违规** ✗（改上层时不知道下层还依赖它 ✓）。

| 文件 | 次数 | 指向 | 严重度 |
|---|---|---|---|
| `services/datasets.py` | **7** | factors / engine / **routers(1)** | ⚠⚠ 唯一一条 `services → routers`（L334）✓ 最刺眼 |
| `services/artifacts_service.py` | 6 | engine / factors | ⚠ 产物层要知道引擎的内部类型 |
| `services/qlib_runtime.py` | 3 | factors | ⚠ `qlib.init` 归它管，但要读 factors 的缓存开关 |
| `services/custom_formulas.py` | 2 | factors | ⚠ 公式持久化 ↔ 公式编译 |
| `engine/qlib_engine.py` | **1** | factors（L27）| ⚠ **引擎反向依赖因子层** ✓ |

**为什么没立刻修**：这 19 处不是"写错了" ✓，而是**职责边界当初没划清** ✓
（例如"切数据集"天然要知道引擎和因子缓存的清法 ✓）。
修法是把"清缓存/取默认"这类**契约**下沉到 `models` 或一个 `contracts` 层 ✓ ——
那是**一次独立重构**（3~4 轮 ✓），已记入 `md/技术债.md` 的架构债 ✓。

---

## 4. 调用链 ①：一次回测（从点按钮到出净值）

```
POST /api/backtest                     routers/backtest.py:40  create_backtest
  └─ manager.submit(req)               engine/task_manager.py:230   ← 并发预算/排队/线程池
       └─ （工作线程）run_backtest(...)  signals/engine.py:557        ← 614 行的编排函数（最大债）
            ├─ datasource/*            取数（qlib / rqalpha 两实现）
            ├─ factors/panel_expr      面板特征（bin 读取 → LRU 缓存 → 算子求值）
            ├─ models/*                LightGBM 训练 + 预测
            └─ 产出                     workdir/tasks/<id>/…（净值/持仓/指标/图）
POST /api/backtest/{id}  → routers/backtest.py:90  ← 前端每 3 秒轮询（taskPoll.ts）
GET  /api/backtest/{id}/result → 读产物（services/artifacts_service.py）
```

**关键点**：`submit` **立刻返回 task_id** ✓（前端不阻塞）；真正的计算在线程池里 ✓；
进度/心跳靠 `task_manager` 的状态文件 + 轮询 ✓（`workdir/tasks/`）。

## 5. 调用链 ②：一次单因子测试（不训练模型）

```
POST /api/factors/single-factor-test     routers/factors_single.py
  └─ factors/single_test.py  run_single_factor_tests   ← 539 行
       ├─ panel_expr.panel_features(...)  取面板（同一条数据链路 ✓）
       ├─ 逐因子算 IC/多空/分位（`_quantile_curve_block` 597 行 ← 第二大债）
       └─ 0/1 信号时顺带算事件研究（factors/event_study.py）
```

**为什么它和回测分开**：不训练模型 ⇒ 秒级出结果 ✓，用来**筛因子** ✓（回测是最终验证 ✓）。

---

## 6. 全局状态与生命周期（**改代码前必须知道的三件事**）

| 状态 | 谁管 | 生命周期 | 坑 |
|---|---|---|---|
| **qlib 初始化** | `services/qlib_runtime._ensure_qlib_init` | **进程内只生效一次** ✓ | 二次 init **静默无效** ✗ ⇒ 切数据集**必须重启进程** ✓（`md/技术债.md` P2-#23 实测确证）|
| **provider_uri / features 目录** | `config.QLIB_PROVIDER_URI`（**导入期常量**）| 进程级 | 同进程 `reset_caches()` **只清缓存、不清路径** ✗ |
| **`.day.bin` LRU 缓存** | `factors/panel_cache`（768MB，`QLIB_PANEL_BIN_CACHE_MB` 可调）| 进程级 | 有**内存基线闸门** ✓（`tests/test_mem_baseline.py`）|
| **面板日历 / 目录缓存** | `panel_expr._CAL` / `_FEATURE_DIR` | 进程级 | `reset_caches()` 会清 ✓，但重新解析仍取上面那个常量 ✗ |
| **任务状态** | `engine/task_manager` + `workdir/tasks/*.json` | 跨进程（落盘）| 进程重启后靠落盘恢复 ✓ |
| **进程池**（面板并行）| `panel_expr._panel_executor` | 首次创建 | **Windows 用 spawn** ⇒ 脚本入口必须 `if __name__ == "__main__"` ✓（否则子进程重跑整个脚本 ✗）|

---

## 7. 前端结构（`frontend/src`，47 个文件 / 17,546 行）

| 位置 | 职责 |
|---|---|
| `App.tsx`（1700 行）| 顶层状态机 + 标签页路由（**最大债之一**）|
| `components/` | 业务面板与弹窗；**纯逻辑已陆续外提**：`sftLogic.ts`（单因子判定 ✓）、`topkCurveLogic.ts`（刻度/格式化 ✓）、`verdictRules.ts` |
| `api.ts`（**1006**）+ `api/` | 后端契约。已拆 `http.ts`（axios 单例）/ `exportXlsx.ts` / `eventStudy.ts`，`api.ts` 做 barrel ✓（消费者不用改 ✓）|
| `taskPoll.ts` | 任务轮询（3 秒）|
| `downloadFile.ts` / `captureChart.ts` | 存盘 / 图表截图 |

**数据流**：`App` 持有列表与选中项 → 传给面板 → 面板调 `api.ts` → 轮询 `taskPoll` → 结果回填。
**约定**：组件**不许**直接 `fetch` ✓（一律走 `api.ts`，便于统一超时与 mock ✓）。

---

## 8. 已知架构债（与 `md/技术债.md` 对应）

| 债 | 现状 | 为什么难 |
|---|---|---|
| `single_test.py` **2664 行**（唯一 >2600）| 有行数棘轮 ✓ | 内部全是共享状态的闭包 ⇒ 要"状态对象化" ✓ |
| `>300` 行函数 **5 个**（614/597/539/501/399）| 有行数棘轮 ✓ | 同上 |
| 前端 `>1300` 行组件 **4 个** | 已从 6 降到 4 ✓ | 剩余每个要外提 200+ 行 |
| **19 处分层违规** | 本文 §3 ✓ | 要新增 contracts 层（3~4 轮）|
| `panel_expr` 求值调度 vs 算子族 | 边界偏模糊 | 要真面板/进程池才能验 ✓ |

---

## 9. 怎么复核本文（别信我，信命令）

```bash
# 依赖矩阵 + 分层违规（含相对导入 ✓）
python - <<'PY'   # 或把这段存成 scripts/dep_matrix.py
import os, re
from collections import defaultdict
APP='backend/app'; PKGS=['routers','factors','signals','engine','exports','datasource','services','models']
e=defaultdict(lambda: defaultdict(int))
for pkg in PKGS:
    for r,_d,fs in os.walk(os.path.join(APP,pkg)):
        for f in fs:
            if f.endswith('.py'):
                t=open(os.path.join(r,f),encoding='utf-8').read()
                for pat in (re.compile(r'(?:from|import)\s+app\.(\w+)'), re.compile(r'^\s*from\s+\.{1,3}(\w+)', re.M)):
                    for m in pat.finditer(t):
                        if m.group(1) in PKGS and m.group(1)!=pkg: e[pkg][m.group(1)]+=1
for pkg in PKGS: print(pkg, dict(e[pkg]))
PY

# 规模（文件数 / 行数 / 巨型函数）
python scripts/abs_metrics.py && cat scripts/abs_metrics.json
```

> ⚠ 本文的表格会过期 ✓ —— `scripts/abs_metrics.py` 里的数字是**实时**的 ✓；
> 有冲突时**以脚本输出为准** ✓，并顺手更新本文 ✓。
