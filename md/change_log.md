# Changelog

本项目所有重要变更记录于此，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)（后端 `backend/app/__init__.py` 定义，前端标题栏显示）。

## [1.17.7] - 2026-09-09

### Performance
- **面板求值整链性能优化**（本机 profile 实测：CWH_BREAK_WAIT20_F1 4 万字符公式 300 只×2024-2025 **20.5s → 2.7s（warm，7.7×）**；CCCMA250 200 只×3 年 **9.9s → 1.9s（5.1×）**）：
  - `panel_expr._read_field_bin` **进程级 LRU 读盘缓存**（mtime 失效、字节上限默认 768MB，`QLIB_PANEL_BIN_CACHE_MB` 可调，`clear_bin_cache()`）：同一 (股,字段) 在 union_index/多组 evaluator/字段加载间重复全量 `np.fromfile` 是原最大单点（占 20.5s 中 10.7s≈52%），命中即免读。
  - `load_field_series` / `_union_index` **MultiIndex 构造批量化**：数千次"每股 from_arrays + concat/链式 append"→ 一次 `from_arrays`（消除 pandas factorize/categorical 构造链）。
  - `_by_group` **组界 numpy 向量化 + 纯 numpy 切片**：取代逐行 `inst[i]` 与每股 `s.iloc[a:b]`（消除 322 万次 `Index.__getitem__`）。
  - `ops_ext.dyn_bars_vec` **重写为真正 O(n·log n)**：新增 `_build_argmax_sparse` / `_dyn_arg_idx_vec`（稀疏表存"极值最右下标"，等值取更大下标），替换旧 O(n·w) 逐位 while 双循环（CCCMA 场景该函数 2.3s → 0.05s），注释声称的复杂度与实现终于一致。
- 验证：`test_ops_ext_vec` 26（朴素参考逐位对拍）+ `test_panel_expr` 15（真实数据 panel vs qlib 逐位 0 差）+ `test_golden_regression` 14 全绿。

### Fixed
- `tests/test_panel_expr.py` 硬编码 `D:\quant\qlib_code`（公司 D 盘路径）导致非 D 盘机器该用例 FileNotFoundError → 改为仓库根相对解析（本机 E 盘通过）。

## [1.17.6] - 2026-09-09

### Fixed
- **修复面板输出精度与 qlib 不一致导致的全 A 端到端零星触发差异，放开含 EMA 公式走面板**
  （`panel_expr.py` / `single_test.py`）：
  - 根因定位：面板求值内部全程 float64，而 qlib `D.features` 所有字段**最终输出整列
    float32**（内部 float64 求值、出口 cast）。两者在 `CLOSE/CHANGE` 等列产生 ~4~8e-6
    float32 ulp 级尾差，本不影响因子值，但会传导到涨停/停牌剔除判定
    （`mark_limit_up` 的 `close >= limit_up - 1e-6` 容差与尾差同量级）→ 全 A 端到端
    触发集零星翻面（"趋势顶底离开底部" 25444 vs 25427、约 15 处、daily ~0.002pp）。
  - 修复：面板求值保持 float64（精度与 EMA 递归收敛不变），**出口统一 cast float32**
    （新增 `panel_expr._cast_output_f32`）与 qlib 返回 dtype 对齐。实测触发集 0 差
    （24128 == 24128、daily 0 差）、面板相关对拍单测全过。
  - **放开含 EMA/EMA_TDX/SMA 字段默认走面板**（原 v1.17.5 因上述差异默认回退 qlib）；
    保留逃生口 `QLIB_SFT_PANEL_EMA=1` 可强制回退 qlib。
- 版本 1.17.5 → 1.17.6（后端 `backend/app/__init__.py` / README 顶部）。

### Chore
- **工程健康度清理**（一次审计驱动的技术债治理，净删 272 行）：
  - 修复 `patches/cancel_train.py` 相对导入 bug（`from .context` → `from ..context`）：此前
    LightGBM/XGBoost"训练中途取消"回调因 import 错误被 `task_manager` 的 `except: pass` 吞掉，
    从未真正生效；现在取消可中断训练块（此前只能等 qlib 检查点）。
  - 删除 `single_test.py` 旧单周期 `run_single_factor_test`（215 行死代码，已被多周期
    `run_single_factor_tests` 取代，全仓库无调用）。
  - 删除前端 `api.ts` 无引用函数：`translateFormula`/`getFactorOperators`/`listInstruments`/
    `getDailyBars` 及 `TranslateResult`/`FactorOperators` 类型（47 行）。
  - 前端移除已废弃字段 `n_days_learn`/`bins`（后端已不读取，仅模型占位兼容）。
  - 修正 SFT 请求 `parallel` 过时注释（字段已弃用仅兼容，不再生效）。
  - 本地一次性探针脚本清理：`ai_test/` 22 个、`backend/workdir` diag/repro/check 24 个
    （均未入 git，无版本历史影响）。

## [1.17.5] - 2026-09-09

### Added
- **面板执行器补齐均线/极值算子**（`panel_expr.py`），让含这些算子的自定义公式可走面板
  并行提速（此前遇 SMA/HHVBARS/LLVBARS 直接整批回退 qlib）：
  - `SMA(X,N,M)`：通达信递归均线 `ewm(alpha=M/N, adjust=False)`，与 qlib `ops_ext.SMA`
    逐位一致；含 SMA 字段进"精确起点"分组（qlib SMA 扩展=子特征透传、可为 0，非 Rolling）。
  - `HHVBARS(X,N)` / `LLVBARS(X,N)`：固定窗口距最近极值天数，复用 qlib `HHVBARS._bars`
    单调队列按组求值。
  - `DYN_HHVBARS` / `DYN_LLVBARS`：变量窗口（N 为序列，如 `LLVBARS(L,AT+1)`）走
    `dyn_bars_vec`。
  - `_warm_days`/`_tree_ext_days` 纳入 HHVBARS/LLVBARS 固定窗口（N-1）扩展。
- 新增 datareq 对拍单测 `test_panel_matches_qlib_sma` / `test_panel_matches_qlib_bars`：
  csi300 真实数据 panel vs qlib 逐位对齐（裸 SMA/嵌套窗口/动态嵌套动态/阈值判定 0 差）。

### Changed
- **含 EMA/EMA_TDX/SMA 字段默认回退 qlib**（`QLIB_SFT_PANEL_EMA` 默认 "1"）。面板均线
  求值虽在小池对拍与 qlib 0 差（`_tree_ext_days` 嵌套窗口递归 + float64），但全 A 端到端
  实测仍差（趋势顶底 25444/1.0589 vs qlib 25427/1.0355，用户 UI 复测确认）——差异只出现在
  全 A 规模、小样本对拍无法覆盖；在定位前含 EMA 公式保持走 qlib 保证对账稳定（与 v1.11.10
  一致）。仅无 EMA 公式（CWH 等）走面板提速。`=0` 可强制走面板（实验用）。
- 配对日口径注释完善（触发/非触发日截面均值只在"两组同日都有样本"的配对日上计算，
  保证 `daily_diff == daily_trig_mean - daily_not_mean` 自洽；避免对账脚本用全部交易日
  稀释未触发组的误导）。
- 版本 1.17.4 → 1.17.5（后端 `backend/app/__init__.py` / README 顶部）。

## [1.17.4] - 2026-09-09

### Fixed
- **配对日口径**：`daily_trig_mean`/`daily_not_mean` 统一在配对日集合（触发组与非触发组
  同日都有样本）上计算。旧逻辑 `daily_not_mean` 对全部有非触发样本的交易日平均（非触发组
  几乎天天有样本 → 分母含大量无信号平淡日），与只统计触发日的 `daily_trig_mean` 分母不同，
  直接相减无意义（触发集中在普涨日时未触发组全期均值被稀释，高估信号能力）。统一后
  `daily_diff == daily_trig_mean - daily_not_mean` 严格自洽（构造数据验证）。

### Changed
- 版本 1.17.3 → 1.17.4。

## [1.17.3] - 2026-09-09

### Added
- **HHVBARS/LLVBARS 支持变量窗口**（`LLVBARS(L,AT+1)`/`HHVBARS(H,BT+1)` 等，N 为变量
  序列、窗口随行变化）：通达信/益盟允许此类写法，同事"杯柄突破"公式使用。实现 =
  `ops_ext` 新增 `DYN_HHVBARS`/`DYN_LLVBARS`（动态窗口内最近极值位置，O(n·窗口)，
  实测 N 恒定 30 时与固定版逐位一致 117/117 max=0），`codegen._DYN_WINDOW_OPS`
  纳入 HHVBARS/LLVBARS（常量 N 仍走固定算子，变量 N 走 DYN_*）。此前 codegen 强制
  N 为常量整数报"必须为常量整数"。

### Fixed
- （无回归）parser 36 passed / golden 49 passed / panel 55 passed。

### Changed
- 版本 1.17.2→1.17.3。

## [1.17.2] - 2026-09-09

### Fixed
- **POWER 输入报"不支持的函数"**：`semantic.BUILTIN_FUNCS` 只有 POW 没有 POWER；同事
  公式用 `POWER(X,Y)`（Excel/部分软件写法）被白名单拦截。修复 = BUILTIN_FUNCS 加
  POWER + codegen 映射 `POWER → Power`（与 POW 同一算子，实测逐位一致）。
- **巨型布尔公式执行报 `unsupported operand type(s) for &: 'float' and 'bool'`**
  （同事报"float 和 bool"）：qlib 内建 And/Or 用 `np.bitwise_and/or_(left,right)` 不
  归一 dtype，当一侧是动态算子输出（float32 0/1）另一侧是比较结果（bool）时 numpy
  类型提升抛错（实测 `And(DYN_REF(...),Ge(...))` 必现、换序则偶发）。杯柄突破
  CUP_POOL 等大量 `A AND B AND C` + 动态窗口公式必触发。修复 = ops_ext 注册自定义
  And/Or 覆盖 qlib 内建：两侧先 `≠0` 归一数值布尔再逻辑运算、输出 0/1 float，与
  面板端 And/Or 语义一致（实测任意 float/bool/常量混输入不再报错）。端到端同事
  CUP_POOL csi300 success（daily_trig 0.004828）。

### Changed
- 版本 1.17.1→1.17.2。

## [1.17.1] - 2026-09-09

### Fixed
- **POW 函数报 "operator [Pow] is not registered"**：`parser/codegen.py` 曾把 POW 映射到 qlib
  不存在的算子名 `Pow`（qlib 内建为 `Power`）。修复 = codegen 新公式映射 `Power`，并在
  `ops_ext.py` 注册 `Pow` 别名算子（同一 `np.power` 语义），已存公式无需重存即可执行；
  面板 `panel_expr` 同步支持 `Power`/`Pow`。实测 qlib 与面板执行 Pow==Power 逐位一致。
- **面板 EMA 嵌套固定窗口序列偏移**（v1.17.1 修复代码，默认未启用）：`_expr_ext_days`
  原只按最外层 EMA 的 N-1 前移 read_start，未递归累加输入链内的 Max/Min/Ref 窗口
  （如 `EMA(Max($h,34),4)` qlib extended=36 而面板只前移 3 天），EMA 起点晚 33 天导致
  整条序列偏移（趋势顶底类 0/1 阈值比较翻面）。修复 = `_tree_ext_days` 递归整棵树
  extended（复刻 qlib `get_extended_window_size`）。因"全 A 并行面板 + 含 EMA"端到端
  数值仍待复验，含 EMA 公式默认继续回退 qlib（与 v1.17.0 一致），本次仅固化为正确基础。
- **loky resource_tracker 弹窗**：此前只 patch 了 loky worker 的 Popen，漏了
  `resource_tracker.spawnv_passfds`（CreateProcess flags 硬编码 0）→ 每任务弹一个黑窗。
  补 patch 后 detached 环境实测全部子进程无可见窗口。

### Changed
- 版本 1.17.0→1.17.1。

## [1.17.0] - 2026-09-09

### Fixed
- **单因子测试多周期进度文案误导（"运行 1/8 周期，已完成 3"）**：`factors.py` 进度渲染原用旧并发语义 `运行 {len(running)}/{n} 周期`（runnning = 并发占用周期数）。v1.16.5 起多周期已改单执行体串行统计后 running 恒为 1，该数字永远显示"1/n"，与"已完成 k"并列矛盾。改为进度维度口径 `已完成 k/n 周期`（与进度条一致）。实测 3 周期 csi300：0/3→1/3→2/3 正确流转。
- 版本 1.16.9→1.17.0。

## [1.16.9] - 2026-09-09

### Changed
- **面板级执行器全 A 并行化（v1.16.9，全 A 单因子 300s → ~20-40s / 较 qlib loky 快 7 倍+）**：`panel_expr.panel_features_parallel` 按股票切块、多进程并行（每块独立跑 `panel_features`，跨股天然独立、结果 concat 等价）。实测全 A 5854 只 CWH+字段组 **37.5s vs qlib 275s vs 旧单进程面板 300s+**；端到端全 A 5260 只单因子 API 任务 ~21s success。>1000 只自动走并行（`QLIB_SFT_PANEL_MAX`/`QLIB_SFT_PANEL_JOBS` 可调），≤1000 保持单进程。
- **修复隐蔽跨股票污染 BUG（`groupby-rolling` 组排序错位）**：`groupby(level=0).rolling` 默认 `sort=True` 按股票字典序输出，而代码按位置 `to_numpy` 回填 → 输入顺序非字典序（如 csi300 后接 BJ 新股）时窗口混入他股数据、整池错位。修复 = rolling 全部加 `sort=False`（此前 csi300 对拍通过仅因代码恰好字典序掩盖，并行混池 datareq 测试抓出）。
- **并行子进程不弹黑色命令行窗口**：ProcessPoolExecutor spawn 的 python.exe 在无控制台后端下每个都新建 console 窗口 → 新增 `_nowin_spawn()` context manager 把 `multiprocessing.spawn._python_exe` 临时切到 pythonw.exe（GUI 子系统无 console），池用完还原；已验证子进程确用 pythonw、求值逐位一致。qlib loky 用自己的 `_python_exe` 不受影响。
- **特征计算阶段按块进度显示（单因子测试加载 6→30 平滑推进，不拖慢）**：`panel_features_parallel` 新增 `progress_cb`（默认 6→30 线性、每完成一块报"面板并行，k/n 块完成"）；回调只在主进程 `as_completed` 收结果时触发（微秒级），不进入子进程计算路径。端到端实测进度 6→14→30 平滑。
- **前端移除"并行测试"死选项**（后端早已单执行体共享加载，`parallel` 仅保留前端语义不再生效，UI 勾选误导）：`SingleFactorTestPanel.tsx` 删 state/提交参数/勾选框。
- **重启后端进程规范**：DETACHED 无控制台环境下 spawn cmd.exe 会弹可见窗口 → 统一用可执行文件直启（python/node）不经 cmd，加 `CREATE_NO_WINDOW`。
- 验证：新增 `test_panel_parallel_matches_single`（1100 只混池并行 vs 单进程逐位一致 + 行集对齐 qlib）；全量单测 172 passed。
- 版本 1.16.8→1.16.9。

## [1.16.8] - 2026-09-09

### Added
- **面板级表达式求值器 `app/factors/panel_expr.py`（做法2 第一版，替换单因子测试特征加载）**：自研递归下降解析器 + `PanelEvaluator` 在整块 MultiIndex(instrument, datetime) 面板上用 pandas groupby-rolling/shift 向量化求值（跨股票结果拼成一面板，公共子式按 str 缓存只算一次）。目标：绕过 qlib 逐股票拆 job 调度（实测全 A 边际 ~30ms/只 × 5260 只），把 CWH 这类巨型公式全 A 从 ~152s 级降到 10~20s 级。
- SR 语义对齐 qlib（2026-09-09 实证）：SR = 剔停牌(NaN)行后在"有效行压缩序列"上求值，出口 scatter 回全日历（停牌日 NaN）；非 SR 字段在全日历普通求值（停牌日保留前值）。含字段覆盖并集 union index、窗口预热 read_start 前移，与 qlib 行集/数值逐位对齐（rtol=1e-4 对拍通过）。
- `tests/test_panel_expr.py` 新增 10 用例（解析重建 / SR 语义合成验证 / datareq 真实对拍 ×2：无 SR CWH + SR 连续因子）。

### Changed
- `single_test._load_feature_panel`：特征加载改走面板求值器，失败自动回退 qlib D.features（环境变量 `QLIB_SFT_PANEL=0` 强制 qlib）。
- **池子规模阈值（v1.16.8 实测定标）**：面板为单进程求值器，中小池（实测 ≤1000 只：1000 只 CWH+字段组 19.3s vs qlib 52.3s，快 2.7×）占优；**全 A 超大池 qlib loky 8-worker 并行反而更快**（5399 只 ~300s vs 152s），且面板节点全量缓存会顶高内存 → `>1000 只自动回退 qlib`（`QLIB_SFT_PANEL_MAX` 可调）。全 A 并行化留待 v1.16.9。
- `ops_ext.py` 有状态算子提取共享纯函数（barslast_vec/dyn_*_vec），类实现与面板共用单一数值源。
- 版本 1.16.7→1.16.8。

## [1.16.7] - 2026-09-09

### Changed
- **DYN/BARSLAST 系列有状态算子从 Python 逐行循环改为 numpy 全向量化（巨型公式提速 ~20%，全 A 单次 CWH ~190s → ~152s）**：`ops_ext.py` 的 `BARSLAST` / `BARSSINCEN` / `DYN_MIN` / `DYN_MAX` / `DYN_COUNT` / `DYN_REF` / `DYN_SUM` 原实现逐位置 for 循环（CWH_BREAK_WAIT20_F1 这类 4 万字符公式里这类算子出现几十次，每只股票每处都跑一遍 Python 循环是热点）。重写为全向量化：BARSLAST/BARSSINCE 用"满足位前缀最大 + 下标差"；DYN_MIN/MAX 用稀疏表 RMQ 按窗口长度分桶批量查询；DYN_COUNT/SUM 用前缀和数组广播；DYN_REF 用 fancy indexing。
- 对拍验证：新增 `tests/test_ops_ext_vec.py`（26 用例），把向量化实现与朴素逐行参考在含 NaN/0/负窗口/超大窗口截断边界的随机序列上逐位比对，全部一致（rtol=atol=0、equal_nan）；全部单测 161 passed。
- 复验（真实数据）：csi300 CWH_BREAK_WAIT20_F1 冷启动 12.7s / loky 热池 7.7s；全 A 5260 只单次 ~152s（向量化前 ~190s）。
- 版本 1.16.6→1.16.7。

## [1.16.6] - 2026-09-09

### Changed
- **qlib 并行取数后端 multiprocessing → loky（根治"加载特征慢"的每批固定开销）**：实验证实本机每次 `D.features` 调用都有与股票数/字段数几乎无关的 ~16s 固定开销，头号元凶是 qlib 默认用 joblib `multiprocessing` 后端——**每次调用都新建进程池**（Windows spawn + joblib 轮询 sleep 占 ~98% 时间）。`resource._apply_kernels` 现在同时设置 `C["joblib_backend"]=loky`（环境变量 `QLIB_JOBLIB_BACKEND` 可回退 multiprocessing）。loky 进程池**跨调用自动复用**：实测连续调用简单字段 第1次 ~8s → 第2次起 ~0.4s（此前每批固定 ~16s）；CWH 大公式 200 股 31.2s(mp) → 11.2s(loky 复用)；loky 与 multiprocessing 数值一致（CWH 含 DYN/BARSLAST 自定义算子全列 allclose）。
- 验证：单因子真实场景 CWH_BREAK_WAIT20_F1 + csi300 + 8 周期并行 46.7s(mp) → **37.7s(loky)**；简单因子预期 ~16s → <1s（进程池已热）。
- 版本 1.16.5→1.16.6。

## [1.16.5] - 2026-09-08

### Fixed
- **单因子测试结果含 NaN/Inf 时 progress/result 接口 500，任务 UI 看起来"卡死"**：并行多周期（如 CWH_BREAK_WAIT20_F1 + 1..60 日 8 周期）下，某周期统计可能产生非有限 float（极端样本：60 日收益、0/0 比值、触发组样本过小、HAC 方差钳制等），starlette `json.dumps` 抛 `Out of range float values are not JSON compliant` → 前端轮询永远 500 → 画面停在最后一次成功进度（"测试因子 1/1，已完成 7"），误以为任务卡在加载/统计。修复：`factors.py` 新增 `_json_safe` 递归把结果中非有限 float（NaN/±Inf）替换为 `null`，`progress` 端点返回 result 前应用。任务实际早已 success，仅结果回传失败。
- 版本 1.16.4→1.16.5。

## [1.16.4] - 2026-09-08

### Changed
- **单因子测试多预测周期共享一次特征加载（根治"并行多周期加载半天"）**：此前并行多周期（如 1,2,3,5,8,10,20,60）时每个周期各调一次 `D.features` 全量加载——而各周期只有 label（`Ref($close,-h-1)` 天数）不同，因子表达式 / 基础行情 / 涨跌停标签完全相同，等于把同一棵大表达式（如自定义公式 CWH_BREAK_WAIT20_F1 展开后 40746 字符）重复算了 N 遍，且每次重复支付 ~16s 固定加载开销。现 `single_test` 新增 `run_single_factor_tests(label_horizons)`：把「因子列 + 全部周期的 LABEL_{h} 列 + base/tag」**合并进同一次** `D.features`（字段超 32 才兜底拆批防内存峰值），随后各周期在内存面板取对应 label 列做统计（不再触发 qlib 加载）。`factors.py` 并行多周期改为**单执行体占 1 个并发槽**（不再每周期一槽），排队/取消语义不变。
- **单因子测试进度模型重构为单调展示**：共享特征加载为整体阶段（0-30%），随后逐周期统计（30+70×完成占比 100%），进度条单调不回跳；排队中显示"排队等待并发单元"，加载中显示加载进度，统计中按周期显示内部进度。读侧统一合成 `message/progress`（`_sft_render_view` 增加 load 阶段字段）。
- 复验（用户真实场景：CWH_BREAK_WAIT20_F1 自定义公式 40746 字符 + csi300 + 2025-01~2026-08 + 并行 1,2,3,5,8,10,20,60 天）：总耗时 **46.7s 完成**（其中共享加载 ~42s 一次、8 周期统计 ~5s）；此前该场景 >360s 仍卡在"加载特征数据"（理论 ~336s+ = 8 次全量加载）。单周期短区间冒烟 10.1s 正常。
- 版本 1.16.3→1.16.4。

## [1.16.3] - 2026-09-08

### Fixed
- **单因子测试"加载特征数据"巨慢（长期卡 2/12 不动、取消半天无反应）**：实测本机每次 `D.features` 调用存在与股票数/字段数基本无关的 ~16s 固定开销（300~1500 只、2~21 个字段单次均 ~16s，二次调用亦无缓存）。`single_test` 原 `batch_size=2` 将 12 个字段拆 6 批调用 → 固定开销乘 6（约 96s+，字段越多越慢）。修复：**一次全载全部字段**（`batch_size = min(len(fields), 32)`，超 32 仅防内存峰值兜底拆批）。验证：csi300（338 只，2025-01~2026-08）单因子任务提交到完成 **23.3s**（旧逻辑光加载 ~96s）。全 A 5260×12 字段单次 32.9s / 0.32GB 可接受。
- **并发协调"回测优先"引入的锁内重入死锁（后端所有 API 卡死）**：`try_acquire_slot` / `external_wait_slot` 在持有 `self._lock` 时又调用 `has_pending_backtests()`（内部再次 `with self._lock`），`threading.Lock` 不可重入 → 线程永久卡在锁上、健康检查与全部接口超时。修复：锁内改用 `_has_pending_backtests_locked()` 私有方法。

### Changed
- **并发协调语义（回测优先 + 阻塞等槽）**：`TaskManager` 新增——存在排队等待配额的回测任务（pending）时，外部任务（单因子并行等）不再新增占用配额，把释放的槽优先让给排队回测，避免单因子把回测饿在队列；新增 `external_wait_slot`（外部任务**阻塞式**等待配额，条件变量唤醒，取代逐秒 `sleep` 忙轮询；支持 `cancel_check` 回调，取消排队中的单因子立即退出）与 `wake_external_waiters`（取消时唤醒排队 worker）。
- **排队/进度提示读侧统一合成**：单因子任务 worker 线程不再各自写 `state["message"]`（多 worker 竞争写同一字段是"排队文案/周期进度文案来回跳"根源），只维护结构化字段（`running_h` / `queued_h` / `done_n` / `per_h_prog` / `per_h_msg`）；对外 `progress`/`message` 由 `_sft_render_view` 在读取时统一合成（整体进度 = 已完成周期×100 + 运行中周期内部进度的均值），文案稳定不横跳。progress / tasks / cancel 端点均接入。
- 版本 1.16.2→1.16.3。

## [1.16.2] - 2026-09-08

### Changed
- **单因子并行测试不再吃光全部回测并发**：外部任务（单因子等共享并发配额）合计最多占「当前最大并发 - 1」个槽（预留槽数可用环境变量 `QLIB_RESERVED_BACKTEST_SLOTS` 调整，默认 1），回测始终有至少 1 个槽可用（此前单因子并行测试一个任务就能占满全部并发，导致回测长期排队）。
- **单因子并行 worker 数 = min(预测周期数, 外部软上限)**：worker 完成一个周期后自动取下个周期，不再启动超过软上限的空转排队线程（此前每周期一个线程，多出线程长期空转在"排队等待并发单元"，观感像卡死）。
- **排队提示改聚合视图**：有周期正在运行时显示「并行测试中：K/总 周期运行(列表)；等待并发单元（h 日）…」，而非只报"排队等待"，避免掩盖运行进度。
- 冒烟：5 周期并行全程无排队噪音完成；6 周期（>软上限）正常完成、多余周期随 worker 释放立即顶上。
- 版本 1.16.1→1.16.2。

## [1.16.1] - 2026-09-08

### Fixed
- **回测"只买不卖"持仓膨胀（影响所有带涨跌停限制的历史回测）**：`BoardAwareExchange._update_limit` 的涨跌停判定执行早于真实价列 `$close_real` 注入，回退用**后复权 `$close`** 与**真实价涨跌停标签**（`$limit_up/$limit_down`）比较 → `limit_sell` 近乎全 True（卖出全被锁死）、`limit_buy` 近乎全 False（买入畅通）→ 每个调仓日只买不卖、持仓膨胀到 topk 的 2-3 倍，净值失真。修复：`_update_limit` 内**自足注入 `$close_real = $close/$factor`**。验证：修复后卖出/买入对称成交、持仓恒定 topk、零拒单（60 池 short：此前 annual 15.5% 失真口径 → 修复后 25.3%）。
- **日截面剔除静默失效**：`BoardAwareExchange.get_forbidden_mask` 方法签名（def 行）曾丢失，导致剔除逻辑挂到相邻方法、exclude 开关静默不生效。已恢复。
- **基准曲线起点比净值早一天**：qlib 基准日收益首行含"窗口首日相对前收"的段外收益（如 2025-01-02 的 -2.91%），导致基准曲线从 0.97 起步、与策略净值（1.0）不同起点。修复：`metrics.normalize_benchmark_curve` 在**整条曲线拼接完成后**统一除以首点归一到 1.0（rolling 不可在段内归一，否则丢段首收益），并同步重算 `benchmark_return` / 年化超额，single 与 rolling 出口均接入。验证：基准首点 0.9709 → 1.0。

### Changed
- **策略调仓诊断日志**：`PeriodicTopKStrategy` 调仓日输出 sell/buy 数量与明细（持仓数、目标内、各跳过原因计数；零订单时打完整原因），便于审计"净值=1 / 持仓膨胀"类问题。
- **Meta-Gate 提示文案**：更新为"勾选下方'Gate 附加特征'（已保存公式）即可让 gate 自动学习多个 01 因子"（前端无触发叠加入口，旧"0/1 请走触发叠加"文案删除）。
- 版本 1.16.0→1.16.1。

## [1.16.0] - 2026-09-08

### Added
- **信号后处理支持滚动（custom）回测**：Meta-Gate / 触发叠加 / 硬规则闸门 从"仅一次性训练 single"扩展到滚动逐段可用。引擎把一次性分支的信号合成抽成 `_maybe_compose_signal` helper，single 与滚动每段共用：滚动每个 segment 用「该段训练窗」重新训练门控/叠加模型并覆盖该段回测信号（与主模型"每段重训"语义一致；失败自动回退主信号不阻塞）。IC/分层仍按主模型诊断（sr 原始预测）。
- **前端放行**：回测表单"信号后处理"区在自定义滚动模式下不再灰化禁用；滚动说明补充"门控模型逐段重训、非一次性套全程"提示。
- **滚动 A/B 验证**（主板 300 / alpha158 / 2025 全年 37 段 / 每段重训 3 月）：
  - R0：滚动无 gate → 年化 33.71%、累计 32.33%、回撤 -11.04%
  - R1：滚动 + Meta-Gate（候选内拒尾 25%）→ 年化 **41.94%**、累计 40.18%、回撤 -10.28%
  - 结论：滚动形态下 gate 同样有增量（年化 +8.2pp 且回撤改善）；逐段 gate valid AUC ≈0.63，与 single 量级一致。
- 版本 1.15.3→1.16.0。

## [1.15.3] - 2026-09-08

### Fixed
- **回测"日截面剔除"开关（`exclude_st` / `exclude_stock_gem` / `exclude_stock_kcb`）开启时净值恒 1、零成交**：`BoardAwareExchange._mark_forbidden` 按调仓日分组的掩码 Series 未去掉 datetime 层（index 仍是 `(instrument, datetime)` 双层），策略 `get_forbidden_mask` 用单层股票代码 reindex 全部落空 → `fill_value=True` 把所有候选判为"禁买"清空 → 每个调仓日空仓。修复：分组后 `droplevel` 掉 datetime 层（按 index 名定位，兼容顺序）。此开关在真实回测里此前从未被验证过（历史成功任务均为关闭），现全配置复验通过：mixed+自定义公式+Meta-Gate+01 Gate 特征+剔除开关（主板 400/2025-2026）年化 20.8%、累计 33.9%、329 笔 vs 关闭剔除同池 14.9%。
- **Meta-Gate 的 `extra_features`（01 触发公式等）导致 gate 训练失败并静默回退主信号**：`D.features` 列名是表达式原文（含 `( ) , /` 等特殊 JSON 字符），LightGBM 报 `Do not support special JSON characters in feature name`。修复：`signal_compose._extra_cols` 对齐后统一重命名为 `extra_feature_0/1/...`（train/test 同序，按位置对齐）。

### Changed
- **回测并发上限动态化**：`task_manager` 原先在进程启动时按当时内存/CPU 一次性探测并固定（启动时被其他训练进程占用内存 → 上限被压到 1 且内存释放后不恢复，需重启）。改为动态配额：每次准入/展示按当前 `resource.max_concurrent()` 实时探测（`QLIB_MAX_CONCURRENT` 环境变量可固定），排队任务用条件变量唤醒；内存恢复后自动放宽，无需重启。
- **调仓日"零订单"诊断日志**：`PeriodicTopKStrategy` 在调仓日生成 0 单时输出原始候选/禁买/过滤后/目标/待买及各跳过原因计数，便于定位"净值恒 1"类问题（此前空仓无任何日志）。
- 版本 1.15.2→1.15.3。

## [1.15.2] - 2026-09-08

### Changed
- **三处因子/特征多选统一支持搜索过滤**（数百个也能快速定位）：
  - 特征选择面板（`FeatureSelectPanel`）：按特征名过滤，命中组保留、无匹配给出提示；组结构/悬停查看不变
  - 自定义公式面板（`FormulaPanel`）：右侧公式列表按"公式名或原文"过滤
  - Meta-Gate"Gate 附加特征"（App）：新增搜索框 + 已选计数 + 全选/清空
- 版本 1.15.1→1.15.2。

## [1.15.1] - 2026-09-08

### Added
- **Gate 附加特征（`meta_gate_opts.extra_features`）**：支持把任意多个 qlib 表达式（典型：若干 01 触发公式；连续因子亦可）作为 Meta-Gate 的附加输入特征——训练与推理两端特征一致（`_extra_cols`：D.features + 老股过滤 + 名称级对齐），树用 feature importance 自动在多个 01 间挑选组合（"机器选门控"的事中输入）。勾连续因子同样合法（作为普通 gate 输入特征），仅建议单输出公式。
- **前端"Gate 附加特征"多选（方案 A）**：Meta-Gate 勾选后列出已保存公式多选框（复用后端保存的 `expression`，无需再翻译），勾选写入 `extra_features`；容器带滚动条（数百因子可容纳），复用历史参数可反查回勾选状态。Meta-Gate 提示文案澄清"与具体触发因子无关、01 触发走触发叠加通道"。
- 冒烟：gate+冰谷火焰作附加特征（1000 池/2024-25/seed0）年化 -7.84% vs 同池 S0 -12.05%（同一 01 因子以"gate 特征"接入与"叠加仓"接入结论相反——不同接入方式需分别验证）。
- 版本 1.15.0→1.15.1。

## [1.15.0] - 2026-09-07

### Added
- **硬规则闸门（确定性过滤，`hard_filters`）**：`signal_compose` 增加 `apply_hard_filters`，在 gate/overlay 之前对预测候选做**确定性硬过滤**——支持 `min_mktcap_bn / max_mktcap_bn`（总市值上下限，亿元；数据 `$market_cap` 单位元×1e8）、`min_price`（真实股价下限，元= `$close/$factor`）；任一启用即把不满足（含 NaN）的候选从回测信号剔除。设计定位是"信号闸门/合成"基础设施的一类（硬规则消费者），未来财务规则（营收/净利/股价/市值）都走同一通道。
- **随机种子可复现（v1.15）**：LightGBM 默认 `model_params.seed=0`（此前不固定、同参重跑结果不同，影响 A/B 可信）；`model_params` 白名单放行 `seed`，前端"模型超参"新增"随机种子 seed"（XGBoost 同步放行，多 seed 稳健性研究可填不同正整数）。
- **前端"信号后处理"设置区（回测表单）**：可勾选 Meta-Gate（含候选内剔除比例）与硬规则闸门（市值上下/股价下限，留空不限）；滚动 custom 模式下禁用并提示（后端限制 single）。
- `hard_filters` 与 seed 的引擎/字段/校验同步（`models/backtest.py`、`qlib_engine._run_single` 与 rolling 校验）。
- 说明：硬规则闸门通过 1000 只均匀抽样池/2024-25 真实回测验证"过滤正确执行"（S3 结果与 S0 明确不同）。**S3 在该环境更差是大市值集中 vs 小盘相对强行的语义结果，不是实现问题**——硬过滤必须可配置、按环境选择。

## [1.14.0] - 2026-09-07

### Added
- **信号后处理（Signal Post-Processing，默认关=现状零影响）**：主模型训练后、回测前可选的"信号合成"层，支持两档（可独立/组合开启，仅**一次性训练 single** 模式；滚动 custom 模式会明确报错提示），实现在 `backend/app/engine/signal_compose.py`：
  - **Meta-Gate（回测请求 `meta_gate` / `meta_gate_opts`）**：在主模型 topK 候选内训练一个二分类 gate（LightGBM binary，学"未来 5 日收益>0"，标签可选绝对/截面相对、样本可选全量/主模型看多子集），预测 gate 概率 z，**逐日把 topK 内 z 最低 `reject_ratio`（默认 25%）的候选剔除**后再回测。排序仍由主模型 score 决定，gate 只做"该不该买"的风控闸门——用于把原型验证的"候选内拒尾"语义固化进真实回测。
  - **触发叠加（`trigger_overlay_opts`，默认不传/None）**：`{enabled, formula(触发因子 qlib 表达式), topk(默认5), weight(默认0.2)}`。引擎把触发公式列算出（`D.features` + 老股过滤/名称级对齐），在其触发样本子集上训练"触发专用模型"（binary），每日从当日触发池选 z 最高的 M 只作为主池外小仓位，与主池合成逐日目标权重 `target_w`（主池 `(1-weight)` 等权 + 触发池 `weight` 等权，逐日归一）进回测。
- **权重目标策略**：`PeriodicTopKStrategy` 新增 `weight_col`（回测引擎恒传 `target_w`）；当预测信号含该列时按权重整体换仓（卖出不在目标权重集合的旧仓、买入目标内新仓按权重预算配资金），**不含该列时完全走旧的等权 topk 路径**（向后兼容，S0 行为不变）。
- 引擎接线：`_run_single` 在主模型 `SignalRecord` 后调用 `compose_final_signal` 覆盖回测 signal（IC/分层仍按主模型诊断）；失败自动回退主信号不阻塞回测。产物/训练签名已覆盖参数，便于 A/B 与追溯。
- 说明：两档能力来自当日 AI 原型的实证研究（Meta-Labeling 方法综述已收进 `md/研报集合.md` 研报 3；`ai_test/meta_*.py` 记录了原型与诊断）。**当前为默认关的可选能力，不做默认行为改变**；真实回测增益需跨池/跨区间 A/B 证据积累后再决定默认策略，勿以单区间数字定论。

## [1.13.0] - 2026-09-07

### Added
- **M3 有状态算子落地（非未来函数部分）**，自定义公式可直接使用（`ops_ext.py` 新增 5 算子 + 注册，`codegen.py` 移出占位进映射，前端"插入函数"手册同步，单测新增 15 条）：
  - `FILTER(X,N)`：信号过滤——条件成立输出 1 后，其后 N-1 个周期抑制不再输出（距上次触发 ≥N 且条件再成立才再输出）
  - `SMA(X,N,M)`：通达信递归加权均线 `Y=(M·X+(N−M)·Y前)/N`（非简单平均 MA，M 越小越平滑）
  - `BARSSINCE(X)`：数据起点起条件**首次**成立到当前的周期数（与 `BARSLAST` 相对；从未成立返回 0）
  - `HHVBARS(X,N)` / `LLVBARS(X,N)`：距 N 周期内最高/最低值所在位置的周期数（含当日→0；多日同极值取最近；单调队列 O(n)）
- **未来函数类明确不支持**：`BACKSET/ZIG/PEAK/TROUGH/SAR` 涉及未来数据确认，从设计上不做（翻译报"不支持的函数"），避免信号前视
- 冒烟验证：`SMA/FILTER/BARSSINCE/HHVBARS/LLVBARS` 在真实 qlib 数据上求值语义正确（FILTER 抑制、BARSINCE 递增、HHVBARS/LLVBARS 0~9 范围）

## [1.12.0] - 2026-09-06

### Added
- 单因子测试面板新增 **"导出结果"** 按钮（位于"清理结果（释放内存）"左侧；`.xlsx` 一个文件两个工作表，纯前端生成、不占后端并发；`frontend/src/components/SingleFactorTestPanel.tsx`）：
  - Sheet1「因子指标」：与界面表格一致的逐行指标（覆盖率/信号类型/触发与未触发组数及收益/差值/日差值/HAC t/胜率/配对日数/p 值/Q1~Q5 收益/IC/RankIC/ICIR/结论），另附"公式"列便于复核；error 行也一并导出（指标列置 `-`，错误信息放"结论"列），方便定位失败因子
  - Sheet2「因子与公式」：因子名与其保存公式（`source_formula` 原文优先，目录因子回退表达式），按 因子×来源 去重
  - 结论判定提取为模块级共享 `verdictOf`，"结论"列（方向矛盾/有效✓/有效(反向)✓/时间集中/待观察）与导出文件同源，杜绝两处判定口径漂移
  - 0/1 信号行的分位收益信息不再缺失：新增「触发组日均(%) / 未触发组日均(%)」两列（对应界面"分位收益"列的双柱）；0/1 无 Q1~Q5 分组故不混填 Q 列，避免"触发组 vs 最低值组同列"的误导性对比
- 前端新增依赖 `xlsx`（SheetJS）。为兼容同事 pull 后未 `npm install` 的情况：**动态 import 按需加载**——未安装时其余功能不受影响，点导出给出"请在 frontend 目录执行 npm install"的明确提示；`npm run build` 部署前仍需 `npm install`

## [1.11.0] - 2026-09-05

### Fixed
- 单因子测试提交报错现在直接显示后端返回的 `detail`（如勾选"剔除ST(T+1)"但本机无 `is_st` 标签时的 400 提示），此前前端只显示 axios 通用文案 "Request failed with status code 400"，看不到具体原因与解决办法（无 `detail` 时回退原 message；`frontend/src/components/SingleFactorTestPanel.tsx`）

## [1.10.10] - 2026-09-05

### Added
- 单因子测试新增 **"价格整分"** 开关（位于"停牌删行"旁，默认勾选）：真实价按分取整（round 2 位）参与因子计算，与益盟/聚宽（整分原始价）指标口径对齐；仅"不复权"模式生效
  - 动机：后复权 float32 bin ÷ factor 还原的浮点尾差，会把"恰等于阈值"的指标值（如短期线 5.00/15.00）推过判定边界，产生与聚宽触发日差一天的样本
  - 实现：`ops_ext.py` 新增 `ROUND(X,N)` 算子；`adjust_expr(..., round_prices=True)` 在 none 模式下把 `$close/$open/$high/$low/$vwap` 替换为 `ROUND((price/$factor),2)`（`single_test.run_single_factor_test`/`SingleFactorTestRequest` 新增 `price_round`，前端面板同步）
  - 效果（"趋势顶底离开底部"20 日全A 2021-01-01~2026-03-01，与聚宽对账）：触发边界样本 190→33（仅Qlib 106→10 / 仅聚宽 84→23）

### Fixed
- **无涨跌停限制日被误判涨停剔除（新股上市首 5 日）**：rq bundle 在无涨跌停日的 `limit_up/limit_down` 标签为 **0**，`mark_limit_up/down` 原先直接 `close >= limit_up` 比较 → `close >= 0` 恒真，把所有上市初期触发当涨停剔除（也影响回测成交判定）。修复：标签 **≤0 / NaN 视为该日无涨跌停限制**，不判涨停/跌停（`limits.py`）
- 修复后与聚宽最终对齐：触发数 **25,427 vs 25,422**（差 5），触发组日截面 **1.036% vs 1.0395%**（差 0.004pp），整体均值 3.317% vs 3.322%；EMA 两侧已同用 `ewm(span=4, adjust=True)`（非差异来源）

## [数据包] data-2026-09-05（非代码版本，A股 qlib 数据交付）

- 发布 `cn_data` 全量数据包分卷：`qlib_cn_1.tar.gz`（987.5 MB）+ `qlib_cn_2.tar.gz`（1289.1 MB）
- 内容：基础行情 + `market_cap` + moneyflow 45 字段（`mf_*`）+ 交易所标签 `limit_up/limit_down/is_st`（每只股票 59 个 bin）
- 下载与解压合并步骤见 README「部署数据」/ md/deploy.md「四、部署数据」

## [1.10.9] - 2026-09-05

### Changed
- 回测（多因子训练）日期数字字号升一档到 `text-base`（16px），比 TopK 再大一号

## [1.10.8] - 2026-09-05

### Changed
- 日期三段输入框每段宽度放宽（年 4rem / 月日 2.2rem，加 px-1 内距），数字不再被压缩在窄列，视觉与 TopK 输入对齐

## [1.10.7] - 2026-09-05

### Changed
- 回测（多因子训练）表单的开始/结束日期数字字号调大到 `text-sm`，与 TopK 等数字输入对齐；单因子测试面板保持小号（`text-xs`）不变（DateInput 新增 fontSize 参数）

## [1.10.6] - 2026-09-05

### Fixed
- "封板不可交易"勾选框高度 36px → 35px 微调对齐

## [1.10.5] - 2026-09-05

### Fixed
- 回测"封板不可交易"勾选框高度调到 `h-[36px]`，与旁边含 spinner 的 number 输入框等高

## [1.10.4] - 2026-09-05

### Fixed
- 回测"封板不可交易"勾选框高度与右侧输入框对齐（固定 `h-[30px]`）
- **日期三段输入串位/少位问题根治**：把三段从"受控 state"改为**非受控 + refs 直接读写 DOM**——每段输入即时生效，不再因受控 state 竞争出现"年丢一位 / 月变 00 / 日变 01"等错位；外部重置（复用历史/日历选定）在非编辑态同步写回，编辑中不被覆盖

## [1.10.3] - 2026-09-05

### Changed
- 日期三段输入框**恢复日历按钮**（自绘月历弹层：‹› 切月、点日期直接选定；不再依赖浏览器原生 date 的日历，同时保留三段键盘自动跳段）
- 回测"涨跌停限制"勾选文案缩短为"封板不可交易"（完整说明在悬停 title，避免占两行）

## [1.10.2] - 2026-09-05

### Fixed / Changed
- **日期三段输入外观重做**（近似原生 date 输入框：单容器 + 内嵌年月日三段无边框输入）；修复年份失焦补零 BUG（年不足 4 位时不再补成 `0202`，仅月/日单数字补零 1→01）；编辑过程中不再被外部 value 重置打断
- **开始日期输入完整后自动跳到结束日期的年份框**（回测与单因子测试均支持，新增 `focusYear()` 接口）
- **回测"涨跌停限制"去掉比例数字输入**：改为勾选"封板不可交易（自动按板块/交易所标签）"——主板 10%/创业科创 20%/北交 30% 自动识别，有交易所标签时直接按标签（ST 5%/退市整理 10% 也正确）；取消勾选 = 不设涨跌停。后端字段 `limit_threshold` 兼容保留（勾选=0.1，取消=null）

## [1.10.1] - 2026-09-05

### Fixed
- **无标签 dump 的机器自动降级，不再崩溃**（如同事 pull 后没跑 `tools/dump_states.py`）：
  - 新增标签数据可用性探测 `field_bin_available`（缓存）；单因子测试的 `$limit_up/$limit_down/$is_st` 字段改为**按可用性动态加载**——缺标签时涨跌停判定自动回退"昨收×板块幅度"倒推、ST 剔除开关因无列自动失效（不崩、可正常跑）
  - 回测 quote 的 `$is_st/$limit_up/$limit_down` 订阅全部按可用性门控；勾选"剔除ST/退市"但本机无 `is_st` 标签时**明确报错**（提示先执行 dump_states.py），避免"勾了但没剔除"的静默错误
- **补齐回测涨跌停的交易所标签接入**（此前单因子已用标签、回测仍走倒推）：
  - `BoardAwareExchange` 开启涨停限制时订阅 `$limit_up/$limit_down`，`mark_limit_up/down` 支持 `$` 前缀列名 → 回测涨/跌停同样按交易所标签判定（ST 5% / 退市整理 10% / 创业科创 20% / 北交 30% 自动正确）

## [1.10.0] - 2026-09-05

### Added / Changed
- **日期输入改为"年月日三段"键盘输入**（`DateInput` 组件）：单因子测试与回测的开始/结束日期共用；输入满 4 位年份自动跳到月份、满 2 位月份自动跳到日期，Backspace 空段回跳上一段，失焦自动补零，仅在年月日齐全合法时才提交（不再产生不完整日期）
- **多因子训练回测新增"日截面剔除"**（交易设置区，"交易成本与成交设置"改名 **"交易设置"**）：
  - 剔除ST/退市 / 剔除创业板(SZ30) / 剔除科创板(SH688) 三个勾选（默认关）
  - 实现：`BoardAwareExchange` 订阅 `$is_st` 并构建当日 forbidden 掩码（ST 按当日状态、创/科按代码恒定），买入侧 `check_order` 直接禁 buy；`PeriodicTopKStrategy` 选 TopK 前调用 `exchange.get_forbidden_mask` 剔除候选（避免 topk 空位，已持有者随调仓卖出）；只动回测上层，不改 qlib 内核
  - `BacktestRequest` 新增 `exclude_st/exclude_stock_gem/exclude_stock_kcb` 字段

## [1.9.4] - 2026-09-05

### Fixed / Changed
- **结果列因子底下不再常驻公式行**；鼠标放到因子名上 hover 显示用户保存的原文（`source_formula` 优先，回退到本地回查，再回退到 qlib 表达式）
- **根因修复**：v1.9.2/v1.9.3 后源_formula 链路已建立，但面板 `useEffect` 同步自定义公式时漏注入 `original: f.text`，导致勾选 source_formula 一直 fallback 到 expression（编译后）；现补齐同步段

## [1.9.3] - 2026-09-05

### Fixed
- **结果表因子列：用户原文与 qlib 表达式内容相同时不再重复展示**。原 v1.9.2 显示源码但若用户保存的"自定义公式"原文本身就是 qlib 表达式（直接复制粘贴的 expression），会显示为长串且与编译后无差异——现改为：内容一致时隐藏灰色公式行，改显示 `（原文即为 qlib 表达式）` 提示，hover 公式名看完整 expression

## [1.9.2] - 2026-09-05

### Fixed
- **单因子测试结果/悬停公式显示"编译后表达式"根治**：用户原文不再依赖前端按 (source,id) 回查勾选项（历史/恢复场景可能匹配不上而回退表达式），改为**原文随请求下发、随结果回传**——请求 `factors[].source_formula`，结果每行直接带 `source_formula`（custom=保存原文，目录因子=表达式本身），展示层优先使用；旧结果无该字段时仍回退本地回查/表达式

## [1.9.1] - 2026-09-05

### Fixed / Added
- **单因子测试面板收起不再丢结果**：收起改为 CSS 折叠（组件保持挂载）而非卸载——收起再点开，上次结果与勾选仍在，运行中的任务收起后继续轮询，完成自动显示
- **结果表上方新增"清理结果（释放内存）"按钮**：删除后端已完成的测试任务/结果（`POST /single-factor-test/clear` 新增，释放进程内 `_SFT_TASKS` 内存）并清空本次展示；运行中的任务不受影响。展示保留到用户主动清理，不随收起丢失
- 确认前端勾选列表悬停与结果表因子列均显示**用户原文公式**（此前页面停留在旧构建导致仍显示编译后表达式，已重新构建产物，需强制刷新或重新部署）

## [1.9.0] - 2026-09-05

### Added
- **单因子测试支持批量预测周期**：
  - 周期输入支持三种写法：单值 `2`（行为不变）、逗号枚举 `1,2,3,5,10,15,20`、range 区间 `1:5:20`（起点:步长:终点，含终点 → 1,6,11,16）；值域 1~250，非法输入提交时提示
  - 结果按"**因子分组、组内周期升序**"组织：A因子1天、A因子2天、B因子1天…，每行带 `horizon`（结果表新增"周期"列，单周期时无感）
- **单因子测试"并行测试"开关**：多个预测周期各占一个共享并发单元同时跑（与回测/训练共用并发配额信号量）——回测任务占用多时自动少跑/排队，占用释放后自动顶上，无需手动指定并发数；`task_manager` 外部任务由"一任务 1 slot"升级为"一任务可持多个 slot"（并发: x/y 如实反映并行占用的单元数）
- **交易所涨跌停价 + ST 状态标签 dump 与接入**：
  - 新增 `tools/dump_states.py`：`E:\rq\bundle`（`stocks.h5` 原生 `limit_up/limit_down` + `st_stock_days.h5` ST 区间）→ qlib bin `$limit_up` / `$limit_down` / `$is_st`（5522 只全 A 已完成）
  - 涨停/跌停判定 `mark_limit_up/down` **优先用交易所标签**（`close >= limit_up`）：自动覆盖 ST 5% / 退市整理 10% / 创业板科创板 20% / 北交所 30%，替代原"昨收×板块幅度"倒推（倒推法不识别 ST 5% 涨停会漏判；保留作无标签源的回退/对账）
- **单因子测试日截面剔除开关**（放"停牌删行"左侧，触发/未触发/分位收益同口径）：
  - 剔除ST(T+1)：成交日处于 ST/*ST/退市整理 的样本（用 T+1 当日状态，无未来函数）
  - 剔除创业板（SZ30）/ 剔除科创板（SH688）
  - 结果表剔除数悬停提示增加"ST/创/科剔除 N"
- 前端交互：预测周期输入框不再常驻显示解析提示（避免顶高布局）；勾选列表与结果表公式改显**用户原文公式**（hover 全文），结果表公式截短至 ~14 字符不再撑宽表格；结论列"方向矛盾"改红色

## [1.8.1] - 2026-09-05

### Fixed
- 修复 CI 前端 tsc gate（v1.8.0 引入）：公式手册弹窗 `lastFocus` 状态只写不读（TS6133 `noUnusedLocals`）→ 移除
- 清理手册弹窗**未生效**的键盘逻辑与提示：↑↓ 选择 / Enter 插入因列表行不可聚焦从未真正触发；底部说明改为「单击选中查看说明 · 双击插入 · Esc 关闭」
- 手册弹窗**双击插入后自动关闭**，焦点回到公式编辑窗并定位光标到插入点之后（便于直接输入参数）

## [1.8.0] - 2026-09-05

### Added
- **公式编辑器新增"插入函数"手册弹窗**（前端）：
  - 公式编辑区新增 **"插入函数"** 按钮 → 打开 `FormulaHandbookModal` 弹窗，内置 `formulaHandbook.ts` 手册数据（函数/字段按分类展示 + 关键字搜索，清单与翻译器白名单对齐）
  - **双击手册条目** → 将函数/字段名插入到公式光标处（新建公式框 / 编辑中的公式框均支持，跟随最近聚焦，插入后恢复光标）
- **四个通达信函数补齐实现，公式可直接写且真实可算**：
  - `EMA_TDX(X,N)`：通达信递归式 EMA（`ewm(adjust=False)`，Y_t=(2X_t+(N-1)Y_{t-1})/(N+1)）；此前只能靠翻译层全局开关 `EMA_SEMANTICS="tdx"` 让 `EMA` 生效，现公式里可显式直接写
  - `SGN(X)`（别名 `SIGN`）：取符号（X>0→1，X<0→-1，X=0→0）→ 外挂算子 `SGN`
  - `INT(X)`：向零方向截断取整（3.7→3，-3.7→-3）→ 外挂算子 `TRUNC`
  - `BETWEEN(X,A,B)`：X 是否介于 A、B 之间（含边界；A、B 大小任意，内部取 min/max）→ 外挂算子 `BETWEEN`
  - `ops_ext.py` 注册 `SGN/TRUNC/BETWEEN` 三个新算子；`codegen.py` 补齐 `FUNC_QLIB` 映射 + 参数个数校验（友好报错）；`semantic.py` 白名单补齐（顺带去重）
  - 翻译器单测新增 `TestBasicExtFuncs`（生成断言 + 参数错误用例）；各算子已过 qlib 端到端执行验证（SGN 输出 {-1,0,1}、TRUNC==np.trunc、BETWEEN 输出 0/1 且停牌 NaN 保留）
- 前端公式手册补充 `EMA_TDX/BETWEEN/SGN/INT` 条目；手册所列函数至此已**全部实测"可翻译 + 可计算"**

## [1.7.1] - 2026-09-05

### Added
- **资金流买卖量字段 + `L2_VOL(n[,b|s])`**（moneyflow3 源的量 `_bq/_sq`，单位=手）：
  - `dump_moneyflow.py` 新增 15 个量 bin：`mf_vol_<main|xl|l|m|s>`（净流入量）+ `mf_vol_<档>_b/_s`（买卖量）；现共导出 45 个字段（净额/净占比/量 × 净+买卖方向），已全量重 dump 到 `data/cn_data`
  - 公式翻译器新增 `L2_VOL(n[,b|s])`（与 `L2_AMO` 同构）：`L2_VOL(n)` = 档位 n 净流入量（手，可为负），`L2_VOL(n,b|s)` = 买入量/卖出量；公式里也可直接引用 `MF_VOL_MAIN` / `MF_VOL_L_B` 等字段
  - 至此益盟 `BIGORDER/ORDERAMT` 的改写覆盖**净额、单边买卖、`×vol` 量**三类口径（此前 `×vol` 标注"无买卖数据不支持"）
- 翻译器单测新增 `L2_VOL` 档位/方向/越界用例

## [1.7.0] - 2026-09-05

### Changed
- **EMA 语义默认回切 qlib 内建 EMA**（`ewm(span=N, adjust=True)`，与聚宽/同事 notebook `qsdd_signal` 逐位一致）：公式翻译层默认生成 qlib 内建 `EMA`；通达信递归语义保留为外挂 `EMA_TDX` 算子 + 翻译层 `EMA_SEMANTICS` 开关（`"qlib"`/`"tdx"`）可切换，重存公式后生效
- **单因子测试未来收益默认开启"停牌+退市尾段冻结"**（`freeze_suspended_price` 默认 True）：
  - 窗口内停牌日按停牌前收盘价冻结（对齐聚宽 `get_price` 停牌日 close=前收的天然口径）；
  - 触发后退市/长期停牌到数据末（未来不足 N+1 个交易日）的样本，按该股最后收盘价结算真实亏损（原 `Ref` 落空被剔除、漏记退市崩盘损失）
  - 趋势顶底离开底部（全A 2021-01-01~2026-06-01，20 日）：口径 A 0.428% → 停牌冻结 0.339% → **含退市尾段 0.195%（保守口径，默认）**
- **单因子面板新增"停牌删行"开关**（`suspend_remove`，默认开）：
  - 勾选 = SR 删行（益盟/通达信"行情无停牌行"，与回测特征一致）
  - 取消 = 停牌日保留 NaN 占位（qlib 官方原生 / 同事聚宽 notebook 口径，与聚宽对账时用）
- 前端单因子面板移除"停牌价冻结"勾选（该口径已默认开启，不再暴露选项）

### 聚宽对账结论（趋势顶底离开底部，2021-01-01~2026-06-01 全A 不复权）
- 双方共同触发样本（~49885）的未来收益 label **逐样本 100% 一致**：聚宽停牌日 `close=pre_close`（前收冻结）与 Qlib `ffill` 冻结完全等价；EMA 语义差异仅 ~0.01pp
- 剩余 ~0.1pp 差异归因：
  - 主因（~0.07pp）：**SR 删行 vs 聚宽 NaN 占位**的停牌行语义差，导致停牌/退市股触发时点差 1~3 天（D 类）——产品默认 SR 删行；切 NaN 占位后触发组日截面 0.228%，贴齐聚宽 0.2440%（差 -0.016pp）
  - 次要（~0.02pp）：退市整理/长期停牌股两源行情数据边界差
- 遵循"只外挂不动 qlib 数据"原则：差异通过翻译层/ops/上层逻辑开关处理，不改 `data/cn_data`

## [1.6.9] - 2026-09-04

### Added
- **单因子测试新增"停牌价冻结"口径**（`freeze_suspended_price`，对齐聚宽/通达信因子诊断）：
  - 未来收益 label 对停牌日采用 **停牌前最后收盘价冻结**，把"触发后一路暴跌至停牌/退市"样本的真实亏损计入（此前 `Ref` 未来价落在停牌 NaN 被静默剔除，漏算崩盘损失）
  - `run_single_factor_test` 与 `/api/factors/single-factor-test` 支持该参数（默认关闭保持现状，对账/复现聚宽时开启）
  - 与聚宽对账实测（2021-01-01~2026-06-01，全A 不复权，趋势顶底离开底部）：触发组日截面从 **0.4285% → 0.3258%**（聚宽 0.2440%，差异主因即停牌日价格冻结 vs NaN，详见开发记录 09-04 #9）

## [1.6.8] - 2026-09-04

### Added
- **资金流数据换源 moneyflow3 + 公式买卖方向参数**：
  - 数据源从 `E:\rq\moneyflow`（2022-10 整月缺失、仅净额）切换为 **moneyflow3**（2013-01-04 ~ 2026-08-19，保留 4 档小/中/大/特大 买卖分开的量与金额；与 moneyflow2 同主键净额逐位一致）
  - `dump_moneyflow.py` 重写：自动检测源结构（moneyflow3 原始买卖 / 旧 net 字段），从 moneyflow3 派生 **30 个 qlib 字段**：
    - 兼容 10 个净额/净占比 `mf_amount_<档>` / `mf_pct_<档>`（数值与 moneyflow2 一致，旧公式/模型不受影响）
    - 新增 20 个买卖方向字段 `mf_amount_<档>_b/_s`、`mf_pct_<档>_b/_s`
  - 公式翻译器 `L2_AMO(n[,b|s])` / `L2_PCT(n[,b|s])`：
    - 无方向参数 → 净额/净占比（**向后兼容**）
    - 带 `b`/`s` → 买入/卖出方向（如 `L2_AMO(0,b)` = 主力买入额）
  - 口径：所有 pct 统一以当日总成交额（4 档买之和）为分母 → `b − s == net`、`pb − ps == pct` 成立（float32 舍入量级）；`main = xl + l`
  - 已全量 dump：156,960 字段文件（5,232 只 × 30 字段）

### Changed
- `dump_moneyflow.py`、`factors/parser`（codegen/parser/semantic）支持 L2 买卖方向；相关 25 条翻译器单测

## [1.6.7] - 2026-09-04

### Fixed
- **公式 `EMA` 语义对齐通达信/聚宽（递归式 EMA）**：通达信公式系统的 `EMA(X,N)` 是递归式 `Y_t=(2X_t+(N-1)Y_{t-1})/(N+1)`（= pandas `ewm(alpha=2/(N+1), adjust=False)`）；而 qlib 内建 EMA 走 `ewm(span=N, adjust=True)`（从序列起点整段归一化），两者在**序列开头**（上市初期/次新股）存在初值差异、随后指数收敛。对含 EMA 的第三方公式（趋势顶底系列）在次新股上的信号判定有明显影响
  - 新增外挂算子 **`EMA_TDX`**（`factors/ops_ext.py`，继承 qlib EMA、仅把 `_load_internal` 改为 `adjust=False` 递归式）；公式翻译器把公式里的 `EMA` 一律映射到 `EMA_TDX`（`factors/parser/codegen.py`），**公式文本写法不变**（仍写 `EMA`）
  - 存量公式 expression 已迁移（含 EMA 的 2 条：趋势顶底 `914a3494aee2`、趋势顶底离开底部 `97c3c31b8be1`）；特征缓存 key 含表达式，自动按新表达式重算，无需清缓存
  - 归因验证（与聚宽对账）：口径修正使触发组日截面 0.499%→0.4868%（约 -0.01pp，主要改次新股信号翻转），非触发组不变 → EMA **不是**聚宽对账整体余差（触发组 +0.12pp）的主因，余差指向起算价/剔除口径，另行对账
  - qlib 内建 EMA 未改动（ML 长序列因子场景不受影响）

## [1.6.6] - 2026-09-04

### Fixed
- **涨停剔除口径对齐交易所/聚宽（重要，影响单因子测试与回测的涨跌停样本）**：
  - 创业板涨跌停判定号段从 `SZ300/SZ301` 扩为 **`SZ30` 整段**：注册制扩容后号段沿 30 段后延（301 之后启用 302/303/…），此前只认两个前缀会把新号段（如 302132 中航成飞）当主板 10% 漏剔除；现 `SZ302/SZ303/…` 及未来 304/305 均按 20% 判定
  - 真实价与反推昨收统一 **round 到分**再算涨停价：数据源真实价 = `$close/$factor` 是 float 除法，有尾差（如 `SZ300001` 显示 27.710001 本应 27.71），直接与涨停价比较会在整分边界与聚宽/交易所整分价口径错位；单因子测试剔除与回测 `BoardAwareExchange` 同步生效
  - 新增 38 条 limits 单测锁定（板块幅度 / 整分口径 / float 尾差封板不漏判 / 新号段涨 10% 不得误判涨停）
- 修复 golden 回归测试文件自引入起一直漏入库（当时提交只 `git add -u` 暂存了已跟踪文件，CI clone 实际不含该文件）的问题

### Added
- **GitHub Actions CI 提交 gate**（`.github/workflows/ci.yml`）：push/PR 自动跑 后端 pytest + ruff + 前端 `tsc --noEmit`；qlib 上游源码安装、setup-python pip 缓存
- **golden 回归测试**（`tests/test_golden_regression.py`）：锁定复权表达式 / SR 算子逻辑与包装 / 缓存 key 敏感性 / L2 资金流翻译等纯逻辑口径
- **回测主流程 e2e golden**（`tests/test_e2e_backtest.py`，`datareq`+`e2e` 标记，默认排除于日常全量）：custom 滚动两段 + single 一次性训练两条，断言 首段净值不平（防"首段预热"缺陷复发）/ 分层 5 组键齐全 / 交易记录非空；`scripts/check.ps1 -IncludeE2E` 手动对跑（拆分重构前后对比用）
- 覆盖率基线（`pytest --cov=app`）：纯逻辑 38%；加入 2 条 e2e 后主流程模块 `qlib_engine` 15%→73%、`metrics` 16%→67%、`charts` 3%→86%

### Changed
- **磁盘存储治理**：回测结束自动按配额 + LRU 节流清理 `feature_cache`/artifacts 磁盘占用（`engine/storage_cleanup.py`），防长期跑回测磁盘爆满
- **多任务并行核数协调**：多回测任务进程内共享 qlib `C["kernels"]`，按"当前运行任务数"动态分配每任务并行核数（逻辑核 ÷ 运行任务数，环境变量 `QLIB_TASK_JOBS` 可显式指定）；单因子外部任务槽位同步计数，qlib.init 后自动重设
- **tools 脚本默认路径外置**：`dump_moneyflow.py` / `dump_market_cap.py` 的 `--qlib-dir` 默认 = `QLIB_PROVIDER_URI` → 仓库内 `data/cn_data`（不再写死 `D:\quant`）；moneyflow 源目录支持 `MONEYFLOW_SRC_ROOT` 环境变量
- CI 依赖补全 `pytest/pyarrow/pydantic-settings`；`.gitignore` 补根目录 `workdir/` 与 `.coverage`

## [1.6.5] - 2026-09-03

### Added
- **A 股资金流向数据（moneyflow）接入 qlib**：
  - 新增 `backend/tools/dump_moneyflow.py`：把东财"资金流向"日频 h5（`E:\rq\moneyflow`，2016-01-04~2026-07-28，10,557,071 行）转成 qlib feature bin（`mf_amount_*` / `mf_pct_*` 前缀，共 10 字段：主力/超大单/大单/中单/小单的净额与净占比），幂等可 `--force`/`--limit`；`cn_data` 已全量写入 ~5,451 只 × 10 bin（54,510 个文件，432.5 MB），源 vs bin 逐日比对一致、勾稽 `main=xl+l` 通过
  - 公式翻译器新增 **`L2_PCT(n)` / `L2_AMO(n)`**（n=0 主力 / 1 超大单 / 2 大单 / 3 中单 / 4 小单 → 映射到 `mf_pct_*` / `mf_amount_*`），10 个 `mf_*` 字段也可在公式中直接引用；后续同事复制益盟公式只改函数名即可（益盟 `BIGORDER/ORDERAMT` 不做自动转换）
- **停牌"删行"语义统一（益盟/聚宽口径，重大语义变更）**：
  - qlib bin 在股票区间内是"每日历日一行、停牌日 NaN"，导致 `Ref/窗口` 在复牌初期把停牌日当一天（复牌首日 `Ref=NaN`、窗口被截断），与益盟/聚宽（行情无停牌行）不一致
  - 新增外挂算子 **`SR(X[, M])`**（`factors/ops_ext.py`）：按停牌掩码剔除 NaN 行后返回连续交易日序列，读取向前扩展 250 个交易日跨停牌段取历史（复牌首日 `Ref`=停牌前收盘）；`SR($factor,$close)` 支持按 `$close` 掩码删行（factor 停牌日仍有值）
  - `CachedQlibDataLoader` 默认对 **feature 组表达式统一包 SR**（`strip_suspended=True`）：多因子训练（A158/360/自定义公式/mixed）特征全部按益盟删行语义计算；**label 不包**
  - 单因子测试：因子表达式包 SR，涨停/停牌判定元数据（CLOSE/CHANGE/T1_*）保持原始含 NaN（停牌剔除逻辑不受影响）
  - 验证：万科 2016 停牌半年复牌首日 `Ref(close,1)` 由 NaN → 35.514（停牌前收盘）；`RET:CLOSE/REF(CLOSE,1)-1` 复牌日 = -0.0999

### Changed
- **特征计算语义变更**：训练特征从"补 NaN 行"改为"删停牌行连续交易日"。**此前所有训练/回测结果不可直接复现，需重新跑**（新语义与益盟/聚宽一致）

## [1.6.4] - 2026-09-02

### Added
- **混合特征集 mixed（Alpha158 子集 + Alpha360 子集 + 自定义公式，一份 dataset 同时使用）**：
  - 新增 `MixedHandler`（`factors/handler.py`）：Alpha158/360 特征名加来源前缀 `A158_`/`A360_` 避免跨来源重名（两套都含 CLOSE0/OPEN0 等同名因子）
  - 回测 `feature=mixed` 时 `_build_dataset` 自动走 MixedHandler：`selected_features` 传带前缀特征名（None=该来源全量），`custom_formulas` 作为附加特征并入（非 mixed 且传了公式仍保持"仅公式特征"旧行为）；`BacktestRequest` 参数说明同步（`models/backtest.py`）
  - 特征目录支持 mixed：`get_factor_catalog(dataset=mixed)` 返回加前缀的 Alpha158/360 子集供前端勾选（`factors/catalog.py`、`routers/factors.py`）
  - **特征磁盘缓存 key 增加列名维度**（`engine/feature_cache.py`）：同一批表达式在不同 Handler 下会映射不同列名（混合模式加前缀），缓存 key 若只含表达式会跨场景命中脏缓存，现 key = 表达式 + 列名
  - 前端特征集下拉新增"混合(158+360+公式)"，勾选以 `A158_/A360_` 前缀名提交（`App.tsx`）
- **滚动回测首段预热**（`engine/qlib_engine.py`、`engine/analysis.py`）：滚动首段把预测窗口前移 `n_days_hold` 个交易日（回测窗口不变），回测首日恰是全局调仓网格第 0 天，可取得 T-1 信号（shift=1 防前视）正常建仓——消除此前首段开头约一个持仓周期的空仓平段（净值开头一条平线）；预热期预测不参与 IC/分层/汇总（`_compute_analysis` 新增 `clip_start` 裁剪），指标口径不受污染
- **partial_result 带目标结束日 + 未完成收益曲线进度化**：`partial_result.json` 记录回测参数 `end_date`；前端 NavChart 对"未跑完"（最后净值日早于结束日）的任务把 X 轴切换为真实日历时间轴并延伸到 `end_date`，右侧留白 = 尚未跑到的区间，直观看到进度；已完成曲线维持原紧凑分类轴外观

### Fixed
- **滚动净值拼接重复日期**：相邻段测试窗口按自然月叠加（`_add_period` 加月返回下月同一天）时，段 N 尾日与段 N+1 首日重叠 1 个交易日（如 09-01 出现两次），全局净值曲线现按日期去重（保留先出现的点），正常执行与断点续跑两条路径都覆盖

### Changed
- 单因子面板"开始测试/收起"按钮容器宽度补偿微调，与上方表单第 4 列按钮左对齐（`SingleFactorTestPanel.tsx`）

## [1.6.3] - 2026-09-02

### Fixed（内存治理：反复跑单因子/回测后进程内存持续爬升）
- **单因子任务结果瘦身**（`routers/factors.py`）：任务列表保留 50 条元信息（供刷新恢复 running），但完整 result（含全部因子大 dict）只保留最近 5 条，更早的 `result` 置 `None`，任务结束自动释放——此前每条成功任务都完整驻留，多次测试累积数百 MB。
- **回测进程内共享缓存自动逐出 + 自适应上限**（`engine/data_cache.py`）：`SHARED_CACHE` 此前只进不出、无上限（滚动回测每段缓存一份全池×区间的 label/ret 面板）。现增加节流式自动逐出（超限按"引用最少优先"淘汰），上限按机器可用内存动态自适应 `clamp(可用×15%, 0.5G, 4G)`；可用环境变量 `QLIB_DATA_CACHE_GB` 显式覆盖（服务器可调大）。与并发预算共用 `engine/resource.py` 内存口径，多人并发时缓存自动收紧。
- **基准收益缓存加 LRU 上限**（`engine/analysis.py`）：`_BENCH_CACHE` 200 条封顶。

### Changed
- 单因子 5 分位悬停：配对日数单独换行放最后一行；删除"已按剔除开关过滤"误导说明（不勾选时确实不剔除，剔除与否由开关控制）。

## [1.6.2] - 2026-09-02

### Fixed / Changed（单因子测试统计口径与展示收敛）
- **5 分位收益升级为日截面口径**（连续因子）：此前组均值为跨日"观测加权平均"且**未应用剔除开关**（与触发/未触发组口径不一致）。现改为分位前先按剔除开关过滤（真实价涨停/停牌判定），每组收益 = 每天组内均值 → 再对所有参与交易日平均（与 0/1 双柱、日配对检验一致），每组新增 `n_days`（参与交易日数）；悬停直接显示 5 组日截面收益（一行一组）+ 各自配对日数。
- **剔除明细收敛为悬停**：触发数/未触发数列的行内 `-X T涨停/-X T+1涨停/-X停牌` 改为只显示剔除后样本数，明细移到数字 hover 提示，避免挤占"结论"列。
- **0/1 双柱悬停 t 与结论判定同口径**：此前悬停显示普通 `daily_t`，与"有效/时间集中"判定用的 HAC 稳健 t 不一致；现统一为 HAC 优先（旧结果回退普通 t），文案精简并显示配对日数。
- 信号列文字："连续·分位" → "连续"。
- 涉及：`backend/app/factors/single_test.py`、`frontend/src/components/SingleFactorTestPanel.tsx`、`frontend/src/api.ts`。

## [1.6.1] - 2026-09-02

### Fixed（重大）
- **修正复权方向错误（此前双重复权，特征/label/回测价失真）**：
  - **数据事实**：本机 qlib 数据 `$close/$open/$high/$low` 原生即**后复权价**（= 真实价 × `$factor`），`$change` 为真实价（东财"不复权"口径）涨跌幅。此前 `adjust.py` 误当 `$close` 为未复权价、复权 = `price × factor`，导致：
    1. forward/backward **双重复权**（price × factor²），除权日假跳空 +63% 级；
    2. "不复权"实际用了后复权价而非真实价；
    3. `mark_limit_up` 用复权价 `close/(1+change)` 反推昨收，除权日 factor 跳变导致**涨停误判偏多约 53%**（实测 400 只：复权口径 T 涨停 5555 vs 不复权 3631）。
  - **修复**（价格语义统一）：`none` = 真实价 `$close/$factor`；`backward` = 原生后复权价 `$close`；`forward` = 前复权 `$close/每股末因子`（比率类特征与 backward 等价）。涉及 `engine/adjust.py`、`engine/board_exchange.py`（quote 层 + 涨跌停判定恒用真实价 `$close_real`）、`engine/qlib_engine.py`（quote 订阅 `$factor`）、`factors/single_test.py`、`factors/handler.py`。
  - **验证**：SH600188 `$close/$factor` = 东财不复权价 33.87/18.75 逐分一致；SH600262 前复权 13.44 与东财/通达信/聚宽逐分一致；随机股票前复权抽查全部对上。
  - **兼容**：老任务（v1.5.0 前用原生 `$close` 训练）续跑复权映射由 `none` 改为 `backward`，保证旧权重特征一致（`routers/backtest.py`）。
  - **注意**：此前用 forward/backward 训练的模型（基于错误的 factor² 特征）**需重新训练**。
- **单因子测试非信号组同口径剔除**：剔除开关（信号日涨停 T / 成交日涨停 T+1 / 成交日停牌）此前只作用于信号组，现信号组与非信号组应用相同规则，`diff` 更公平；`_stat_group` 下发剔除明细，前端非信号组列同步显示剔除数（顺带修复信号组剔除标签从未显示的问题）。

### Changed
- 单因子面板参数行去掉 `-mx-3`，与下方"剔除开关"文字左右对齐（`SingleFactorTestPanel.tsx`）。

## [1.6.0] - 2026-09-02

### Fixed
- **修复滚动回测"只买不卖"结构 BUG（收益虚高 45%~254%）**：段长 ≤ 持有期（如 `test_win=1week`、`n_days_hold=5`）时，每段从纯现金账户独立回测、持仓不跨段 → 段首调仓从空账户取持仓只有买入、没有卖出 → 每周换仓跳过卖出成本（约 15-18%/年），结果虚高
  - **持仓跨段传递**：每段回测初始账户 = 上一段末持仓（qlib 原生 `account` dict 支持，未改内核）；段首调仓能卖出不在新 topk 的旧持仓
  - **调仓日全局连续**：策略按"回测起点全局交易日序号 % n_days_hold"判断调仓日，与段长无关（段长 1/2/3/5 天均每 n_days_hold 天调仓一次）
  - **绕开 qlib return 列 BUG**：带初始持仓的账户 qlib `report` 的 `return` 列首日把"初始持仓市值"误算为收益（实测 372%），`_extract_result` 改用 `account / initial_account` 计算段内净值
  - 验证：周滚动 Linear 买入 235/卖出 205（修复前 0 卖出），年化恢复正常；35 组段长×持有期组合逻辑验证全部正确

### Changed
- **成交量限制默认留空**（`volume_threshold` 默认 `null`）：前端回测表单默认不限量理想成交，便于观察等权效果；需限制时手动填 0.25 等
- 卖出语义确认（未改策略）：跌停/停牌股票卖不掉（按板块幅度 主板10%/创业科创20%/北交所30%），剩余资金仍平均分给所有买入目标（买入只数不变、每只金额变小），贴合真实市场

## [1.5.0] - 2026-09-01

### Added
- **复权方式（不复权 / 前复权 / 后复权）**：单因子测试与多因子回测均支持 `price_adjust` 参数，前端回测表单与单因子面板新增"复权方式"下拉框
  - 特征/label 表达式与回测成交价按复权因子（factor）调整；未修改 qlib 内核（handler 表达式层 + `BoardAwareExchange` quote 层实现）
  - 复用老回测参数默认"不复权"；复用模型权重时若复权方式与源不一致自动改新训练
  - 数学事实：对比率类特征与收益率，前复权与后复权完全等价（整体差一个常数因子，分子分母同时缩放抵消），实质区别是"复权 vs 不复权"
- **训练签名快照**：每次回测在产物目录生成 `train_signature.json`，记录 git commit、数据版本、依赖版本、特征指纹 MD5、参数 MD5，供历史模型追溯与复现定位
- **历史回测列表显示年化收益列**（替换原"模型"列）

### Changed
- **按日配对检验的 t 值改为 Newey-West HAC 稳健 t**（修正自相关/异方差）为主显示；鼠标悬停可查看普通 t、lag-1/lag-5 自相关（含置信带显著标注）、方差稳定性（后半/前半方差比）

## [1.4.9] - 2026-09-01

### Fixed
- 续测后历史列表立即刷新（提交续测后源目录 `is_task_running` 及时更新）
- 删除按钮乐观禁用（点击续测后该行删除按钮立即变灰，不等后端刷新）
- 续测失败/完成后按钮恢复（刷新时清理残留的续测乐观标记，仅保留仍在运行的目录）

## [1.4.8] - 2026-09-01

### Fixed
- 历史回测列表自动刷新：提交回测后立即刷新历史；轮询改用 `anyDone` 统一刷新（修复多个任务同时完成时 `break` 导致漏刷新、历史列表只显示最新一个）
- 并发回测 `Run not found` / `already active`：
  - patch qlib `MLflowRecorder.end_run`，改用 recorder 自身 `client` + `run_id` 结束（原 `mlflow.end_run` 用全局 client 找不到线程独立 db 里的 run → "Run with id=... not found"）
  - 手动清理 mlflow 线程本地 active run 栈（否则同一线程下次 `start_run` 报 "Run with UUID ... is already active"）
  - 验证：3 个回测并发 + 中途取消 1 个均正常

## [1.4.7] - 2026-09-01

### Changed
- 0/1 信号"分位收益"列双柱悬停直接显示分组收益：鼠标放到柱子上立即显示信号组/非信号组的日截面均值与配对日差（不再先显示容器说明）；"逐日截面口径"说明移至表格底部说明文字

## [1.4.6] - 2026-09-01

### Added
- 成交价基准新增复合均价：`avg_co`（开盘+收盘）/2、`avg_ohlc`（开收高低）/4，共 5 档（close/open/vwap/avg_co/avg_ohlc），买卖对称共用同一基准；复合均价由 BoardAwareExchange 订阅 `$open/$high/$low` 注入列实现
- 单因子测试任务列表接口 `GET /api/factors/single-factor-test/tasks`；前端刷新后自动恢复未完成/取消中的任务并展开定位面板
- 并发控制（单因子与回测共用配额）：单因子测试与回测共用并发配额（信号量），超配额自动排队（排队中可随时取消）；并发统计计入单因子任务；前端"并发: x/y"定时刷新 + 提交/结束即时更新
- 0/1 信号分位收益双柱：显示信号组 vs 非信号组的逐日截面收益均值双柱（防信号聚集虚高），悬停查看两组数值与配对日差
- 文档：`md/start_stop.md` 新增后台静默启动与 IDE 内预览说明；新增 `md/开发记录.md`（本地专用，已加入 `.gitignore`）

### Changed
- 单因子测试取消优化：取消检查细化到单个因子内部的重计算步骤（IC/检验/分位），取消响应更快

## [1.4.5] - 2026-09-01

### Added
- **成交日(T+1)涨停剔除**：单因子测试原只剔除"信号日(T)涨停"，但实际调仓在 T+1 收盘买入，T+1 涨停封板才买不到。新增 T+1 涨停剔除，与回测 `BoardAwareExchange` 的撮合约束口径一致；剔除数分三层独立统计并在表格展示（T 涨停 / T+1 涨停 / 停牌）
- **停牌剔除**：成交日(T+1) 停牌/无行情同样买不到，新增剔除（T1 行情为 NaN 即剔除）
- **剔除开关**：前端参数区新增三开关（信号日涨停 / 成交日涨停 / 成交日停牌，默认全开）

### Changed
- **判定口径统一**：涨停判定统一为"涨停价四舍五入口径"（昨收×(1±板块幅度) 四舍五入到分）。抽出公共模块 `app/engine/limits.py`，单因子测试与回测 `BoardAwareExchange` 共用（原回测用 `change≥阈值` 判定，现与单因子测试一致）
- 时间线：信号日 T 收盘出信号 → T+1（成交日）收盘买入 → T+n+1 收盘卖出；T 日涨停为选股过滤（无前视），T+1 涨停/停牌为真实交易摩擦

## [1.4.4] - 2026-08-31

### Fixed
- 修复点"查看"历史回测未同步自定义公式/特征勾选状态：查看历史任务只填了表单参数，面板勾选状态保持旧的（如本地公式仍勾着）→ 与表单不一致。修复：抽出公共 `syncCustomSelections`，`handleViewResult`（查看）与 `handleUseParams`（复用参数）共用——历史没用自定义的一律清空勾选

## [1.4.3] - 2026-08-31

### Fixed
- 修复复用回测误判"自定义公式与复用源不同"：页面加载时本地已保存的自定义公式会自动填入表单，复用回测用 `{...form}` 作基底时未清掉 → 与复用源（无公式）不一致，触发"自动改新训练"。修复：复用回测显式把 `custom_formulas`/`selected_features` 设为源任务的值（含 null）

## [1.4.2] - 2026-08-31

### Fixed
- 修复滚动回测段间净值断层：`report_normal` 的 `account` 列在部分日数据异常时与净值曲线（cumprod）不一致，段拼接用错误的 `end_account` 更新 `global_nav` 导致段边界净值跳变（如 -27%/-87% 断崖）。修复：统一用段收益率累乘更新 `global_nav`（与段内 nav 严格一致），不再依赖 `end_account`；复用段改用已保存的段末绝对净值

## [1.4.1] - 2026-08-31

### Fixed
- 修复多任务并发时 mlflow SQLite `database is locked`：并发补丁给每个任务线程独立 sqlite（`exp_{tid}.db`）做隔离，但引擎启动时 `R.set_uri(主db)` 把线程独立 db 覆盖成了主 `mlflow.db` → 多任务并发写同一 db 触发锁。修复：并发补丁忽略 uri 覆盖（保持线程独立 db）+ 主 db 连接加 `busy_timeout=30` 兜底
- 说明：mlflow run 记录写在线程独立 db，回测结果/历史不受影响（读的是 artifacts 文件）

## [1.4.0] - 2026-08-31

### Added
- 单因子测试新增"按日配对检验"：把每天的 `触发组均值 − 未触发组均值` 当做一个日收益序列，报告其均值、t 值和胜率（展示在差值列下方）。规避同日收益横截面相关导致的假显著，不受触发集中爆发日拉高收益均值的影响
- 新增"时间集中"结论判定：总差值方向显著但按日配对不显著（|t|<2 或胜率≈50%）时判为"时间集中"（慎用），典型如暴跌抄底类信号
- 内置自定义公式新增：负市值（`Mul(-1,$market_cap)`）、负市值对数（`Mul(-1,Log($market_cap))`）
- 文档：`md/自定义因子与因子库架构.md` 补充按日配对检验的统计口径说明

### Changed
- p 值显示优化：极小 p 值（<0.001）改用科学计数法显示真实量级，不再退化成 `0.0000*`

## [1.3.3] - 2026-08-31

### Fixed
- 修复复用参数/复用回测误带 `resume_task_id`：表单残留导致新回测被当成"续测源任务"，复用源目录秒完成并覆盖源任务产物。前端复用参数/复用回测/提交时强制清空 `resume_task_id`；后端段缓存跳过增加日期窗口校验（日期不一致不复用，防止误用他人段结果）
- 修复续测任务读不到已跑段 partial：任务状态栏点击续测任务无显示。后端 `get_backtest` 对续测任务按 `resume_task_id` 找源目录读 `partial_result.json`
- 查看状态一致性：查看正在运行/正被续测的任务显示"滚动训练进行中"而非"已停止/中断"；正在跑时不显示"续测"提示
- 取消即时反馈：点取消/一键取消后前端立即乐观更新为 `cancelling`（不再依赖轮询）

### Added
- 点击任务状态卡片可直接查看该任务的曲线/分层/IC/已跑段，再点取消展示，点其他任务切换

## [1.3.2] - 2026-08-31

### Added
- 历史回测"未完成"任务新增常驻"续测"按钮：复用源 artifacts 目录从断点继续滚动回测，跳过已完成段（断点续跑）；续测任务沿用源任务名显示
- 续测占用保护：目录正被续测写入时，历史表格对应行的删除按钮自动禁用（防误删）
- 未完成任务可查看已跑段结果：取消/中断/失败但已跑过若干段的滚动回测，可查看已跑段的净值曲线/分层/IC/段产物（读 `partial_result.json`，新增接口 `GET /api/backtest/{task_id}/partial`，任务不在内存/后端重启后仍可读）
- 说明：调仓明细（TradeLog）只在完整 `result.json` 中有，未完成任务暂无；续测为"新建任务 + 复用源目录"机制，task_id 会变化但任务名沿用源目录名
