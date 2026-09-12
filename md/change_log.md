# Changelog

本项目所有重要变更记录于此，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)（后端 `backend/app/__init__.py` 定义，前端标题栏显示）。

## [1.18.48] - 2026-09-12

### Added
- **「持仓期曲线」新增逐日盯市绩效指标：年化收益 / 最大回撤 / 夏普 / 索提诺 / 卡玛（组合 + 基准同口径）**（新增 `engine/perf_metrics.py`；改 `factors/single_test.py` / `factors/benchmark_curves.py` / 前端 `TopkCurveModal.tsx`）：
  - **动机**（用户提问）：图 1 只有净值曲线，看不出「这份超额值不值得承担这些风险」；且曲线点是**按调仓期**的（每期 1 个点），回撤/波动会被系统性低估（期内路径完全不可见）⇒ 采用**逐日盯市**口径。
  - **口径（关键，勿改坏）**：
    - 持仓集合由**调仓日**的名次/分位决定（期内不变）；期内逐日净值 = 组合内各股 `close_t / close_{T+1}` 的**等权买入持有**均值 —— ⚠ **不是**「逐日收益的等权均值」（那等价于每日再平衡，与 LABEL 口径不是同一个组合）；
    - 期内路径取 `[T+1, T+h]`；`T+h+1` 在「按调仓期划分」下**天然属于下一期**（它同时是下期的 T'+1），故本期期末值改由**该期收益 R**（= 曲线口径）推进；两者之间隔一处「最后一天收益」的自然跳变（真实收益，非误差）；
    - 成本按「往返费率 × 该期换手」在期首以**乘法**扣减（乘法可交换 ⇒ 与按整期扣的复利曲线同值）；
    - **只统计到最后一个完整持有期末**：裁掉基准宽表的 warmup 前段与尾部 `h+1+3` 天延展，否则样本数虚高、摊薄年化（实测基准 1471 → 1220，沪深300 年化 −2.07% → −2.49%）；
    - 指标定义见 `perf_metrics.py`：年化 = 几何口径；最大回撤 = `min(净值/累计峰值 − 1)`；夏普 = `日均收益/日标准差 × √252`（**rf = 0**，与回测页一致）；索提诺下行项用**半方差**口径（正收益视作 0 偏差）；卡玛 = 年化 / |最大回撤|。
  - **前端**：图 1 下方并排两张指标卡（**组合 / 基准**），显示 年化 · 最大回撤 · 夏普 · 索提诺 · 卡玛 + 年化波动 / 日胜率 / 样本天数；组合卡另附**三档成本年化**对照。⚠ **仅复利口径展示**（指标基于净值，算术累加不是净值口径）；调仓期 < 预测周期（各期持有区间重叠）时不提供并给出提示。
  - **实测**（csi300 / 负市值对数 / h=20 / 2021-01~2025-12）：组合（无成本）年化 **+19.28%**、最大回撤 **−33.17%**、夏普 **0.61**、索提诺 0.97、卡玛 0.58（0.004 档 +18.52%；0.008 档 +17.77%）；基准：沪深300 −2.49% / 回撤 −45.60% / 夏普 −0.05；中证1000 +4.03%；中证500 +4.85%。**性能增量 ≈ +0.3s/因子**（csi300 item_s 3.09 → 3.32s）。
  - **生产自检④**：逐日路径首点（T+1 当天）必须**恰为 1**（抓「期划分错位 / 价格分母取错 / 行对齐错」）—— 本轮正是靠它定位了 `T+h+1` 的期归属问题。
  - 新增纯函数模块 **`engine/perf_metrics.py`**（`nav_from_rets / max_drawdown / annualized_return / sharpe / sortino / calmar / compute_perf`）：`engine/metrics.py` 里的同类算法是**内联**的（不可复用、两处易漂移），抽出来供单因子测试与后续回测页共用。
- 版本 1.18.47 → 1.18.48。

### Notes
- ⚠ **统计噪声**：5 年 ≈ 60 个调仓期，夏普/索提诺/卡玛的置信区间很宽（±0.5 很常见）⇒ 界面必须与样本数一起看，别单看数字。
- ⚠ 指标**不含**涨跌停 / 停牌 / 流动性冲击（与曲线同一简化口径）。
- ⚠ 回测页（`analysis.py::_compute_layers`）的分层 / 多空 / 基准曲线**仍是算术累加**（那条路径并不调用 `cost_curves.py`），与单因子测试页的复利口径**尚未统一** —— 待后续版本处理。

## [1.18.47] - 2026-09-12

### Changed
- **「持仓期曲线」默认口径由「算术累加」改为「复利」（= 实盘满仓复投），算术口径保留可切换**（`engine/cost_curves.py` / `factors/single_test.py` / `factors/benchmark_curves.py` / 前端 `TopkCurveModal.tsx`）：
  - **动机**（用户提问「为啥是算术累加未复利？实盘小市值轮动难道不是复利？」并用真实数据验证）：实盘满仓轮动必然按**当前净值**配置资金 ⇒ **复利**；算术累加只在「固定金额配置、收益取出」或「每期收益极小且波动小」时才近似成立。实测 csi1000「负市值对数」h=20 / 2021-01~2025-12（61 期 / 4.84 年）：**算术累加净值 2.67（+166.9%）vs 复利累乘净值 4.38（+337.9%）**，差 **170.9 个百分点**（算术净值只有复利的 61%）。
  - **为什么方向不确定**：`log(1+复利) ≈ Σr − ½Σr²` ⇒ 强策略（期收益均值 ≫ 波动罚项）复利**高于**算术，弱/无效策略复利**低于**算术（波动损耗）—— 即算术口径同时**低估强策略、高估弱策略**。实测同一批十分位：默认档（强）复利 > 算术，而 **Q1/Q2（弱组）复利 < 算术**（1.1505 < 1.2006；0.8882 < 0.9538）。
  - **实现**（全部为**新增字段**；算术口径与历史**逐位不变**）：
    - `cost_curves._topgroup_cost_curves` 新增 `curves_compound`：每期净收益 `(1+r_t)(1−费率×换手_t)−1` ⇒ `cumprod(1+per)−1`（费率按**乘法**扣、**首期建仓不计费**，与算术口径同规则）；
    - `single_test._quantile_curves` 各组新增 `cum_compound`；`benchmark_curves.period_return_cums` 各指数新增 `cum_compound`（缺失语义与 `cum` 完全一致：尾部越界断其后、中间空洞只断当期）；
    - `topk_curves.items[]` 新增 `curves_compound`（三档费率）；前端 `api.ts` 类型同步（`curves_compound` / `cum_compound`）。
  - **前端**：弹窗顶部新增口径切换「**复利（实盘）** / 算术累加」，**默认复利**；后端旧版无新字段时自动回退算术（不空白）。**超额**随口径定义：算术 = 差值 `组合 − 基准`；复利 = **净值比** `(1+组合)/(1+基准) − 1`（两条净值相减无可解释含义）。**多空（最强 − 最弱）是差值口径，复利下不适用 ⇒ 仅在算术模式显示**（右侧轴一并隐藏）。口径说明框/免责文案同步改写，明确标注「两口径不可混比」。
  - **验证**：① 纯函数对拍 **25/25 PASS**（旧实现取自 `git show HEAD`：算术 `curves` 逐位相同 + 复利 `curves_compound` 与 `cumprod` 逐位自洽，含全负收益/零波动等边界用例）；② 端到端（csi300 / 负市值对数 / h=20）三个新字段全部存在，由算术差分复算复利自洽 `max|Δ| ≤ 1.7e-6`；③ `backend/tests` **180 passed**；④ 前端 `tsc -b` 通过。
  - 脚本（本地）：`ai_test/probe_arith_vs_compound.py`（两口径差异实测）、`ai_test/probe_v1847_compound.py`（新字段+自洽）、`ai_test/verify_v1847.py`（旧 vs 新对拍）。
- 版本 1.18.46 → 1.18.47。

## [1.18.46] - 2026-09-12

### Added
- **前端展示单因子测试阶段计时（`timing`）—— 结果表新增「耗时」列**（`frontend/src/api.ts` / `frontend/src/components/SingleFactorTestPanel.tsx`）：
  - **背景**：v1.18.43 后端已把 `timing{init_s/feature_s/item_s/total_s}` 挂在每个因子结果上，但**前端从未接线**（用户反馈"单因子测试计时前端看不到"）⇒ 本次补齐展示。
  - **列位置**：放在「周期」之后（靠左，横向滚动时不会被遮住），显示该行的 **`item_s`（本因子统计 + 事件研究）**；悬停给出三段明细：`本因子统计+事件研究 / 特征加载（全池共享，同次测试各因子相同）/ qlib 初始化（仅首个任务有值）` + 合计，并注明**合计不含 HTTP 往返、任务排队、股票池成分股解析与 label 表达式构建**（故小于前端墙钟）。
  - **兼容**：无 `timing`（后端 < v1.18.43、或结果来自旧版缓存任务）时显示 `-` 并在悬停中说明；`api.ts` 新增 `FactorTiming` 接口与 `timing?: FactorTiming | null` 字段；错误行 `colSpan` 16 → 17。
  - **实测**（csi300 / 2025-01-01~2025-06-01 / 龙腾四海8 / h=20）：`{init 0.0, feature 1.675, item 0.266, total 1.941}`。
- 版本 1.18.45 → 1.18.46。

## [1.18.45] - 2026-09-12

### Added
- **连续因子「因子研究」：持仓期净值曲线 + 十分位净值曲线 + 成本敏感度表 + 可切换基准**
  （单因子测试页；表格原「事件研究」「持仓曲线」两列合并为一列「**因子研究**」，按钮名不变）：
  - **口径（用户定稿）**：只做**收益最强组（默认档）/ TopK 的纯多头**（A 股空头收益拿不到）；费率
    `0.004/0.008` = **往返（买+卖）合计**，三档 `0 / 0.004 / 0.008`；**按调仓期扣、只扣实际调仓的股票**
    （**首期建仓不计费**）；沿用 `label`（h 期前视）口径；**曲线以 1 为起点**（各期收益**算术累加**、
    近似净值、非逐日盯市）；IC/RankIC 不扣费；页面显著标注「未考虑涨跌停 / 停牌 / 流动性冲击」。
  - **调仓期默认 = 预测周期 h**（"用预测周期调仓换股"）；多空 = **最强组 − 最弱组**（标注**不可实现**）。
  - **K 敏感度汇总表**：各 K 的每期换手 / 三档年化成本（252 交易日）/ 成本吞噬比例 + 免责说明。
  - **固定 K 档预设**（`1/2/3/5/10/20/50/100/10%/20%`，勾选式；整数 = 只数、10%/20% = 日均只数百分比）：
    **不勾 = 只算默认档（零额外开销）**；勾选的档随本次测试一起算出 ⇒ 弹窗内**纯前端切换、零重算**
    （每个额外 K 约 +0.1~0.2s，每预测周期各算一次）。
  - **可切换基准**（沪深300 / 中证1000 / 中证500 / 中证800 / 中证全指，**一次下发、前端纯切换**）：
    同口径 = 指数在**同一调仓日**的 T+1 → T+h+1 收益、各期算术累加（**价格指数、不含分红**）；
    默认基准按股票池映射（复用回测页 `_pick_benchmark`）。
  - 新增 API 参数 `quantiles`（默认 10）/ `rebalance_period`（缺省跟随 h）/ `topk_list`；新增返回字段
    `quantile_curves` / `topk_curves`（含 `benchmarks`）/ `topk_sensitivity`。

### Fixed
- **基准曲线「中间一个空洞 ⇒ 整条线截断」**：面板里 `SH000852` 恰好有一个 NaN（2025-04-16），旧实现
  「某期取不到价 ⇒ 该期及其后全部 None」把整条基准线截断（用户报「中证1000 只到 2025-04」）。现为
  指数缺日 **ffill** + **只有尾部越界才整条断**、中间空洞只断当期（两道独立防线，含回归断言）。
- **API `topk_list` 里 `K=1` 被当成「100% 只数」**（≈ 全池）：改为 **`<1` 才是百分比、`≥1` 一律只数**
  （1 ⇒ 1 只）；明细固定档上限 8 → 12。

### Performance
- **K 敏感度表用「名次矩阵批量版」**（一次算出全部 K）：整表增量 **128.9ms**；同进程"逐 K groupby + 集合"
  的脏写法复刻为 **1294.8ms**（**向量化 10×**）。
- **基准取数走项目面板求值器 `panel_features`（40ms 冷 / 4ms 热）**，⚠ **不要换回 qlib `D.features`
  （每次调用 11.1s，且不是一次性冷启动）** —— 已写进代码注释当护栏。
- 顺带两处脏写法优化（`topk_curves`）：组内名次只算一次（**−1.27s**）、调仓日名单改一次 groupby（**−0.33s**）。

### Notes
- 新增模块：`engine/cost_curves.py`（纯算法）、`factors/topk_sensitivity.py`（面板→名次矩阵适配）、
  `factors/benchmark_curves.py`（指数基准）—— 算法与适配分层，不塞进 1000+ 行的 `single_test.py`。
- 版本 1.18.44 → 1.18.45。

## [1.18.44] - 2026-09-12

### Performance
- **#6 第 3 步：`_seg_roll` 的 max/min 改走「固定窗口 van Herk / Gil-Werman」，端到端 −6.3%（数值零变化）**（`factors/panel_expr.py::_seg_roll_vanherk`）：
  - **背景**：`_seg_roll`（段内 pandas 2D rolling）占「趋势顶底离开底部」的 **37.5%**（Max 17.1% + Min 14.2%）、csi1000 整轮 **10.8%**；库内 16 个公式有 **12 个**用到「两参数（滚动）」的 MAX/MIN/MEAN/STD/SUM/VAR。
  - **先排除两条路（都是实测，不是推测）**：① **mean/std/sum/var 不可复刻** —— 新增 `ai_test/diag_roll_mean.py`，5 个候选（`math.fsum` 精确 / `np.sum` / 朴素增量 / **Kahan 复刻** / `cumsum` 差分）与 pandas 逐位对拍，**没有一个相同**（`large+small(1e8+1)` 池相对差达 **1e-4**，属算法结构差异）⇒ 保留 pandas 路径（沿 v1.18.4 对 sum/count 的先例）。② **分段 RMQ 无收益** —— 逐位全绿但同会话 A/B **慢 7.5%**（N 大 ⇒ 稀疏表 7 层 + 178MB 表随机 gather ≈ 310ms；旧矩阵在「段长相近」时矩阵 ≈ n、一次 Cython 滑窗 ≈ 291ms）⇒ 已回退。
  - **采用算法**：窗口长恒为 N ⇒ 以 **N 为块长、全局按 index 0 对齐**分块，块内 `f.accumulate` 得 `prefix`/`suffix`，长度恰为 N 的窗口 `[l, i]` 取 `f(suf[l], pre[i])`。**全局网格为何不跨段**：同块 ⇒ 由 `i−l = N−1` 推得窗口**恰等于该块**；跨块 ⇒ `suf[l]` 止于块尾、`pre[i]` 始于块首，**两者都是窗口的子集**（D 组实测确认）。**截断窗口**（每段前 `N−1` 行）单独用段内 `f.accumulate` 覆盖。并复刻 pandas 的 **`±inf` → NaN** 语义（`Rolling._prep_values` 的 `use_inf_as_na` 遗留行为）。
  - **微基准**（新增 `ai_test/bench_segroll_max.py`，n=3.17M / 1980 段等长 / NaN 5% + inf 1.5% / 逐轮交错取比值中位）：`old` 382~446ms ｜ `rmq` 0.93~1.10× ｜ **`vanherk` 2.91~3.72×**（135 → 45 ns/元素）；且 **old / rmq / vanherk / 逐段 pandas 四者逐位一致**。
  - **端到端 A/B**（`ai_test/ab_segroll.py 3 new` vs 同会话 `3 old --stash`，csi1000 / 3 公式 / n=3172661）：`_seg_roll` **4.078s → 2.169s（−46.8%）**、wall **37.76s → 35.38s（−6.3%）**、`Rolling` 构造 14 → 4（mean 仍走 pandas）。
  - **验证**：新增 `ai_test/test_seg_roll.py` 六组对拍 **总失败 0**（A 旧矩阵 vs 新快路 864｜A2 逐段 pandas vs 新快路 450｜B 朴素逐段逐位置参考 450｜C mean/sum 算法判定｜D 跨段污染专项｜E ±inf 专项 13）+ `backend/tests` **180 passed**。
  - ⚠ **本刀最值钱的两条教训**：① **对拍网抓到了全尺度检查看不见的真 bug** —— 覆盖截断窗口的循环写成 `if m > 1` ⇒ **`m == 1`（N == 2 / 段长 == 1）时段首行漏覆盖**、取到段外值（差异全在段起点），而全尺度检查里 N≥14、段长 1602 ⇒ m≥13 恒 > 1 **永远看不见**；② **对拍网「像卡死」的真凶** —— 参考实现遍历 `_seg_arrays` 的 `ss`（**每行**段起点，长度 n）而非 `_segment_bnd`（段边界，长度 = 段数）⇒ 结果正确但慢 O(n×段长)（317k 行 ≈ 63s）。
- 版本 1.18.43 → 1.18.44。

### Notes
- 本次只动 `panel_expr.py`（新增 `_seg_roll_vanherk` + `_seg_roll` 头部路由）；`ops_ext.py` / 其他算子未动。
- 遗留：`_seg_roll` 余下 6.2% 是 **mean/std/sum/var**（pandas，不可逐位复刻）；`_rightmost_bars_seg`（10.7%）｜`_rightmost_arg_best`（2.6%）。

## [1.18.43] - 2026-09-12

### Added
- **阶段计时（可观测性）：单因子测试结果新增 `timing` 字段，前端可直接显示「初始化 / 特征加载 / 该因子统计」三段耗时**（`factors/single_test.py::run_single_factor_tests`）：
  - 动机：用户反馈「前端开头十几秒，看不出慢在哪」。此前判断性能问题要靠外部探针（`profile_*` / `probe_*`）或 cProfile，费时且**容易得出自相矛盾的结论**（本轮已两次：cProfile 给旧实现注水、隔离基准把 `os.stat` 算进去导致虚高）。把它做成**每次测试都自带的现场数据**最省事。
  - 实现：只在 **3 个阶段边界**各打 `perf_counter()`（一次性 `qlib 初始化` / `_load_feature_panel` / 每个因子的 `_test_one`+事件研究），**每个因子约 0.4 µs**，无性能影响。结果写进每个因子结果的 `timing`：
    `{"init_s": 一次性 qlib 初始化, "feature_s": 面板/特征加载（全池共享、各因子相同）, "item_s": 本因子统计+事件研究, "total_s": 三者之和}`
  - ⚠ `total_s` **不等于**前端看到的 wall：剩余部分是 HTTP 往返 + 任务排队 + **股票池成分股解析 / label 表达式构建**（实测 `regress`：total 1.89s vs wall 4.03s；`CUP_POOL`：8.53s vs 10.09s）。
  - **实测样例**：`regress`（csi300）→ `{init 0.0, feature 1.169, item 0.721, total 1.89}`；`CUP_POOL` → `{init 0.0, feature **8.118**, item 0.415, total 8.53}` ⇒ 大公式的耗时**几乎全在特征加载**，一眼可见。
  - ⚠ `init_s` 只在**首个任务**有值（之后 `_ensure_qlib_init()` 是空操作）—— 首次请求的「开头十几秒」若含 qlib 初始化，看这个值。
  - 验证：`backend/tests` **180 passed**、lint 干净、API 回归 `regress` IC/RankIC 与历史**逐位一致**（`ai_test/sft_arith_regress.py` 顺带打印 `timing`）。
- 版本 1.18.42 → 1.18.43。

## [1.18.42] - 2026-09-12

### Added（默认关闭，**未启用**）
- **特征合并层 `features_merged/`（T2 §7.7-E #7）—— 完整实现 + 全量构建 + 全量逐位验证，但实测「无收益」，故默认关闭**：
  - **背景**：`features/<inst>/<field>.day.bin` = 6142 目录 / **321249 文件 / 3.2GB**，单文件仅 1.9~2.3KB。隔离基准（`ai_test/bench_io_format.py` / `bench_io_h5.py`）显示「字段级合并」把全池读盘从 `股票数×字段数` 降到 `字段数`（3213→7 次，**65×**）；**HDF5 无优势**（同粒度 1.57×；"单文件"红利来自合并而非格式，且裸二进制 64.1× > h5 单文件 61.0×）⇒ 定案「每字段一个大裸文件 + 列式索引」，而非 H5。
  - **实现**：
    - `backend/tools/build_merged_features.py`：构建 `features_merged/<field>.bin`（该字段全股票按代码升序拼接，逐股**原样字节**，仍 `<f4`+首元素=start_idx）+ `<field>.idx.json`（列式 `{inst, off, cnt, start}`）+ `manifest.json`；带 `--verify N` 抽样逐字节校验。
    - `panel_expr.py`：`_merged_dir/_merged_index/_merged_meta/_merged_values` + `_read_field_bin`/`_field_bin_meta` 的**旁路快路**（`QLIB_PANEL_MERGED=1` 开启，默认 **0**）；`clear_bin_cache()` 一并清合并层缓存。
  - **全量构建**：59 字段 / **3.20 GB / 153s** / 抽样 3009 对逐字节 **0 失败**。
  - **全量逐位验证**（新增 `ai_test/test_merged_layer.py`）：**321249 对全部 0 失败** —— ① 完整性对账（源 size≥8 的对 vs 索引集）**仅源有 0 / 仅合并层有 0**；② `_read_field_bin` ON vs OFF 返回的 `(start, float64 数组)` 逐位相同（含 NaN）；③ `_field_bin_meta` 元信息一致。（耗时 ~20min，OFF 侧把 32 万个文件逐个开了一遍。）
  - **⚠ 实测结论：端到端没有收益，反而略慢 ⇒ 默认关闭**（`ai_test/probe_readfield.py`，csi300 / 3 因子 / 4131 次读）：
    | | 整轮 wall | 特征加载段 | `_read_field_bin` |
    |---|---|---|---|
    | ON（走合并层） | 5.76s | 2.85s | 4131 次 / **0.437s（106µs/次）** |
    | OFF（原路径） | 5.67s | 2.78s | 4131 次 / **0.395s（95µs/次）** |
    逐轮交错 4 轮（`profile_feature_load.py --no-profile`）结论一致：ON ≈ OFF，无可辨识收益。
    **真因**：① 我最初「205 µs/次」的基准**每次读都带 `os.stat`（本机 71 µs）**，而真实代码早已用 `_dir_meta` TTL 避开 stat，真实单次读只有 **95 µs**；② 热页缓存下「开一个 1.9KB 小文件」比「memmap 切片 + 软件缺页」**更便宜**。⇒ 合并层的**收益上限只有 wall 的 ~7%**，且被 memmap 缺页成本吃掉。
  - ⚠ **过程中踩到的两个坑**：① 第一版读取侧**每次调用都 `os.stat` 那个 71MB 大文件**校验 mtime，被调上万次 ⇒ 端到端**反而慢 0.7s**（与 v1.18.36 给 `_dir_meta` 加 TTL 是同一个坑，已加 `QLIB_PANEL_MERGED_TTL`，默认 60s）；② 校验器用 `np.array_equal` 判等，**必须 `equal_nan=True`**（close 等字段含 NaN，否则整片误报 —— 今天第二次踩）。
  - **保留状态**：代码 + 工具 + 数据（3.2GB）都在，**默认关闭**。冷缓存 / 更大股票池（全 A 约 5.3 万次读）下可能仍有价值 ⇒ 用 `QLIB_PANEL_MERGED=1` 自行复测后再定；不需要可直接删除 `data/cn_data/features_merged/`。
  - **⚠⚠ 追测（同日，`ai_test/probe_merged_read_modes.py`）：全市场口径下三种取数方式**完全一样**，`#7` 彻底死心 —— 而且找到了**物理层原因**：
    | 取数方式 | `_read_field_bin`（全市场 37800 次） | µs/次 |
    |---|---|---|
    | 原路径（os.stat+np.fromfile） | 10.450 / **9.358** s | 276 / **248** |
    | 合并层 + memmap 切片 | 9.878 / **9.516** s | 261 / **252** |
    | 合并层 + **常驻 fd + `os.lseek`/`os.read`** | 9.745 / **9.576** s | 258 / **253** |
    ⇒ 这 250 µs/次**既不是 syscall、也不是软件缺页、也不是文件格式** —— 就是**存储本身**：读 37800 个平均 ~12KB 的片段（全 A 合计几百 MB），本机 ≈ **250 µs/次、~50 MB/s 的随机读**。而全 A 要读的 6 个字段（market_cap + close + label 的几只价格字段）**一个都省不掉** ⇒ **~10s 是本机的物理下限**。
    **两头堵的结论（可推广）**：磁盘快 ⇒ I/O 根本不会占到 32% 的性能、无需优化；磁盘慢 ⇒ 数据量说话、布局无用。**所以「读盘」这条路任何情况下都不该再投入**。（同时也再次证明：隔离基准里「开小文件 205µs/次」是虚高的，因为它把真实代码早已用 `_dir_meta` TTL 避开的 `os.stat` 算了进去。）
  - **用户实测反馈**：前端跑「开头十几秒」看不出效果 —— 与上述结论一致（该场景净收益 ≈ 0）。
- 版本 1.18.41 → 1.18.42。

## [1.18.41] - 2026-09-12

### Performance
- **T2 待办 #5：IC/RankIC 逐日 `groupby-apply` 改向量化分组求和，连续因子批量端到端 −17.6%（数值零变化）**（`engine/analysis.py::_compute_ic`，新增 `_group_corr`）：
  - **定位（新增 `ai_test/probe_stat_stage.py`：整轮 cProfile + 给 `panel_features`/`_compute_ic` 各挂累计计时）**：4 个市值类连续因子 / csi300（真 459 只）/ 2021-06~2026-06 —— 整轮 **16.69s** 里 **特征加载段只 0.84s（5.0%）**、**统计阶段 15.85s（95.0%）**，而统计阶段里 **IC/RankIC 段 `_compute_ic` 独占 9.02s（54.1% of wall，8 次调用）**。⇒ **连续因子的主战场不在特征计算，在统计阶段**（0/1 信号已跳过 IC，这正是 0/1 因子明显更快的原因）。
  - **原实现**：`df.groupby(level="datetime", group_keys=False).apply(lambda x: x["score"].corr(x["label"][, method="spearman"]))` —— 1211 天 × 4 因子 = **4844 次 `scipy.stats.spearmanr`**（内部两次 rank + `corrcoef`）+ 9688 次 `corrcoef`，每天一次 Python 级 apply 且每次构造两个 Series；整轮 cProfile 显示 pandas 包装开销惊人（`__setattr__` 15.6 万次、`Series.__init__` 2.9 万、`sanitize_array` 5.4 万、`Index.__new__` cum 2.0s）。
  - **改法**：① 先做**成对** dropna（`score`/`label` 任一为 NaN 的行剔除，**与原实现同序**）；② `pd.factorize(..., sort=True)` 编码交易日（保持与原 `groupby` 相同的**升序**分组顺序 ⇒ `points` 顺序不变）；③ `np.bincount` 分组求和算 Pearson（**先求组均值、再中心化**，与 pandas `nanops.nancorr` 同构）；④ RankIC = **组内 `groupby.rank()` 后走同一套**（pandas 自己的 spearman 实现就是「先 rank 再 pearson」）。
  - **⚠ 三个坑（都靠对拍抓到，详见下）**：
    1. **必须「成对」dropna 后再算秩** —— 若只按某一列剔 NaN，该列秩位会变、`rank_ic` 出错。**脏面板用例实测 20/20 天不同**（如 0.148331 vs 0.148344）；而**真实面板上这个 bug 不暴露**（因真实数据里 score/label 的 NaN 恰好同现）——**这类"只在脏数据上暴露"的 bug 必须靠专门的脏夹具抓**。
    2. **不要改用 `SeriesGroupBy.corr` / `DataFrameGroupBy.corr`** —— 它们内部走 `_python_apply_general` + 逐组 `_align_for_op`，实测**比原 apply 慢 20~44 倍**（0.05×/0.02×）。这一条与「用 pandas 内建一定更快」的直觉相反，是本轮最大的认知修正。
    3. `groupby.rank()` 的 `na_option` 默认 `keep`，与非 NaN 成对语义不同，故不能省 dropna。
  - **微基准（新增 `ai_test/bench_ic.py` + `ai_test/dump_ic_fixture.py` 抓真实夹具 541987×2 / 1211 天，三套夹具 × 4 实现对拍 + 交错取比值中位）**：
    | 实现 | 真实面板正确性 | 脏面板/边界 | 比值中位 |
    |---|---|---|---|
    | old（逐日 apply） | 基准 | 基准 | 1.00× |
    | `SeriesGroupBy.corr` | ✅ | ❌ rank_ic 20/20 天错 | **0.02×（慢 44×）** |
    | `DataFrameGroupBy.corr` | ✅ | ❌ 同上 | **0.05×（慢 20×）** |
    | numpy bincount（N3） | ✅ | ✅ | **3.09×** |
    | **★ 生产 `_compute_ic`** | **✅** | **✅** | **2.42~3.09×** |
  - **⚠ 测量方法（本轮又一课）**：cProfile 口径会**给旧实现注水** —— 旧路径整轮 2000 万次 pandas 调用被逐调用计费，故 cProfile 显示「IC 段 −63%」但「wall 只 −11%」，两者自相矛盾。**必须用不挂 cProfile 的真实墙钟**（`profile_feature_load.py --no-profile --repeat N`）。
  - **实测（真实墙钟，3 轮，同窗口，无 cProfile）**：
    | | run1（冷） | run2（热） | run3（热） |
    |---|---|---|---|
    | 旧 | 13.46s | **11.90s** | 12.10s |
    | 新 | 10.64s | **9.48s** | 10.30s |
    ⇒ 热轮 **12.00s → 9.89s（−2.11s，−17.6%）**、冷轮 **13.46s → 10.64s（−21%）**，**区间完全不重叠**。
    （cProfile 口径下 IC 段本身 9.24s → 3.37s。）
  - **验证（四重）**：① `ai_test/bench_ic.py` **三套夹具（真实 1211 天 / 脏面板含 NaN·并列·常量日·±inf / 边界单样本·全并列）全部与旧实现一致**（`points`/`mean_ic`/`icir`/`mean_rank_ic`/`rank_icir`）；② `backend/tests` **180 passed**；③ **API 回归 `regress` → `IC=−0.004751 RankIC=0.002204`，与 v1.18.24 起的记录逐位一致**（且其 wall 6.07s → 4.03s）；④ 上文真实墙钟 A/B。
  - **附带收益**：`_compute_ic` 同时被**回测 IC 分析**（`engine/qlib_engine.py`）与**单因子测试**（`factors/single_test.py`）共用 ⇒ 两处一起提速。
- 版本 1.18.40 → 1.18.41。

### Notes
- 本次只动 `engine/analysis.py` 的 `_compute_ic`（+新增 `_group_corr`）；特征计算/表达式内核/`panel_expr.py`/`ops_ext.py` **未动**。
- **方法论沉淀**：① 测 ≲5% 的改动用「同进程成对 + 交替顺序」；**测这类大改动仍要看真实墙钟，别信 cProfile 的绝对秒**（它按调用次数抽税，Python 调用多的一方被系统性惩罚）。② **脏数据夹具不可省**：本轮 N1/N2 在真实面板上"正确"，只有脏面板才暴露成对 dropna 缺失。
- 遗留：`_seg_roll`（#6，趋势顶底 37.5%）｜读盘字段级合并（#7，全 A 冷读 ~10.8s）｜`_rightmost_bars_seg`（10.7%）｜`_rightmost_arg_best`（2.6%）。

## [1.18.40] - 2026-09-12

### Performance
- **T2 第 3 刀（第 2 步）：DYN 窗口几何改「全程原地写」，几何 1.78×，端到端 ≈ −5.7%**（`factors/ops_ext.py` 新增 `_dyn_geom` / `_arange_cached`，四处查询入口改调用；**数值零变化**）：
  - **定位（新增 `ai_test/bench_query.py`，逐步计时）**：单次 DYN 查询 291ms 里 **~200ms 是「窗口几何」**（`_win_lens_vec` 50 + `minimum` 37 + `log2+floor+astype` 51 + `idx-lens+1` 27 + `idx-seg_start+1` 22 + `1<<js` 9），而几何**只依赖 `(nvals, seg_start, n)`**；且每个 pass 实测 3.2 ns/元素 —— **远低于流式带宽**，指向**分配**而非计算。
  - **真因**：原实现是四行链式表达式，逐条各分配一个 n 长临时数组（`where/floor/astype/maximum/(idx-seg_start+1)/minimum/log2/floor/astype/arange/(1<<js)/(idx-lens+1)` 共 **11 个**，n=3.17M 时 ≈275MB；整条查询约 17 个 ≈425MB）。
  - **改法**：抽出 `_dyn_geom(nvals, seg_start, n) -> (js, l1, l2)`，**全程 `out=` 原地写**，只用 5 个缓冲（mask/A/B/C/D）；`np.arange` 走 `_arange_cached`（面板求值里 n 固定，8ms/次 → 0）；`fmin/fmax` 合并写回第一个 gather 的结果（`out=`）再省一次分配。四处入口（`_dyn_rmq_vec` / `_dyn_rmq_vec_seg` / `_dyn_arg_idx_vec` / `_dyn_arg_idx_vec_seg`）各缩成 3 行。
  - ⚠ **`maximum(N, 1)` 必须在转 int 之后**（与 `_win_lens_vec` 同序）：`floor(±inf)` 转 int64 会得到 `INT64_MIN`，若先在浮点上取 max 再转，会留下 `INT64_MIN` 而从 `minimum(N, i-seg_start+1)` 里漏出来 → 语义错。`np.copyto(C, A, casting="unsafe")` 与 `astype(np.int64)` 是同一 C 转换路径，故逐位一致。
  - **两个被证否的假设**（都做了基准才否掉）：① `js` 用 `np.frexp` 取指数 —— **更慢**（106.9 vs 31.5ms，`log2` 是 SIMD 向量化的）；② `st[js, pos]` 改「按 js 分组后取单层」—— **更慢 3×**（155.6 vs 49.6ms，逐层布尔掩码的开销更大）；③ `np.searchsorted` 求 js 也**更慢**（57 vs 51ms）。`st.ravel()[js*n+pos]` 与 2D 花式索引持平（49.2 vs 49.6ms）。
  - **实测（⚠ 关键方法：端到端 A/B 测不出来）**：本次改动在 `CWH_MIX_D_PCT_R40`/csi1000 上只值 ~1s，而大池单进程墙钟跑间抖动 **±18%（≈±4s）** —— 3 轮 A/B 区间必然重叠（新 22.56/23.91/27.05 vs 旧 21.99/24.66/25.61，**无法分辨**）。故改用**同进程成对测量**（新增 `ai_test/probe_dyn_geom_ab.py`）：patch `_dyn_geom`，每次调用**既跑新实现也跑旧内联链的复刻**并**逐次交替先后顺序** ⇒ 抖动抵消：
    - 16 次调用：新 **1.669s** / 旧 **2.976s** ⇒ **几何 1.78×**（每次比值 1.46~2.21，全部 >1）
    - 折算到本次 wall 23.11s：**省 1.31s ≈ 5.66%**
    - 且顺带在**真实数据**上逐位校验 `js/l1/l2` 三条输出：**0 次不一致**
  - **验证（四重）**：① `ai_test/test_dyn_argmax.py` **1016 例 0 失败**（含建表新 vs 旧逐位、算子 vs 朴素逐位置参考、段感知 vs 逐段独立）；② `backend/tests` **180 passed**；③ 快照 `--tag dyn40 --compare arith2` → **18 列全部 `exact=True`**；④ 上述成对测量 + 真实数据逐位校验。
  - ⚠ **`_arange_cached` 是共享只读数组**（各调用点只把 idx 当输入、从不写入），故进程内共享安全。
- 版本 1.18.39 → 1.18.40。

### Notes
- 本次只动 `ops_ext.py` 的 DYN 查询入口与新增两个辅助函数；`_build_sparse` / `_build_argmax_sparse` / `_rightmost_bars_seg` / `_rightmost_arg_best` / `_win_lens_vec` / `panel_expr.py` **未动**。
- **方法论沉淀**：大池单进程端到端墙钟抖动 ±18%，**凡是 ≲5% 的改动都必须用「同进程成对 + 交替顺序」测**（把新旧两版都写在探针里、同一份真实数据上各跑一遍），否则会被噪声吞掉。
- 遗留：`_rightmost_bars_seg`（10.7%）｜`_rightmost_arg_best`（2.6%，**同样的 gather 模式，可套 v1.18.39 的值表改法**）｜`_seg_roll`（6.3~9.7%）｜DYN 查询里剩下的 2 次 gather + fmin（~96ms/次）。

## [1.18.39] - 2026-09-12

### Performance
- **T2 第 3 刀（第 1 步）：argmax 稀疏表重写（去 gather + 简化合并 + int32），大公式端到端 −12.5%**（`factors/ops_ext.py::_build_argmax_sparse`，**数值零变化**）：
  - **定位（新增 `ai_test/probe_dyn.py`，按函数拆建表/合并/查询 + 记 n/k/k_max/元素量）**：csi1000（真实 1980 只）× `CWH_MIX_D_PCT_R40`，wall 25.35s，其中 `_build_argmax_sparse` **7 次 / 4.509s / 17.8%**。最刺眼的对比是同形状的两次建表（n=3172661、k=7、22.2M 元素）：**float 版 `_build_sparse` 110ms / 4.78 ns/元素，argmax 版 718ms / 29.0 ns/元素 —— 慢 6.5×**。
  - **主因**：每层合并用 `vals_ext[ai + 1]` / `vals_ext[bi + 1]` 取候选值 —— 在 25MB 数组上做**随机 gather**（6 层 × 2 次 × 3.17M ≈ **2.7 亿次随机访问**），外加 ~20 遍逐元素运算（`a_ok`/`b_ok`/`a_nan`/`b_nan`/`b_better`/`tie`/`b_wins`/`where`…）。
  - **改法三条**（微型基准先行，见下）：
    1. **并行维护一张极值表 `sv`**，层合并的候选值改为读它的**连续切片** —— 彻底去掉 gather。`sv` 只需**相邻两层**（ping-pong 两个 n 长缓冲，不必存满 k 层）。
    2. **简化合并**（`_merge_adjacent_vals`）：稀疏表第 j 层的左右两半是**相邻区间** `[p,p+half)`/`[p+half,p+2half)`，且 `si[j-1][q] ∈ [q, q+2^(j-1)-1] ∪ {-1}`（对 j 归纳可证，含尾部「沿用 prev」的近似位）⇒ **两半下标只要都有效必有 `ai < bi`** ⇒ 原式 `(tie & (bi > ai))` 的 `bi > ai` **恒真**，可删；再与 `b_better` 合并即 `bv >= av`（等值自然取右）。**合并从 ~11 遍降到 6 遍**。
    3. **下标表降为 int32**（n < 2^31 恒成立；返回的下标只被 `i - j` 消费，产出 int64）。
  - ⚠ **`_dyn_best_idx_ext` 保持原样**：它也用于**查询**路径，而查询的两个候选 `st[js,l1]`/`st[js,l2]`（l2=l1+(lens−2^js)）区间**可以重叠**，`bi > ai` 不恒真 —— 简化版只能用在「相邻两半」的建表场景。
  - **微基准（新增 `ai_test/bench_argmax.py`，五变体交错 A/B，n=3172661、k_max=6）—— 五个实现全部逐位相同**：
    | 变体 | is_max=True | is_max=False |
    |---|---|---|
    | 原实现（gather） | 28.85 ns/元素 | 33.41 ns/元素 |
    | 值表去 gather | 1.64× | 1.62× |
    | ＋简化合并 | 1.81× | 1.92× |
    | **＋int32 下标（采用）** | **2.21×（13.1 ns）** | **2.34×（14.3 ns）** |
  - **实测（新增 `ai_test/ab_dyn.py`：子进程各 3 轮，样本区间不重叠）**：
    - **整次 panel 墙钟**：**26.25s → 22.97s（−3.28s，−12.5%）**（旧 25.94/26.25/27.97、新 22.72/22.97/23.24）
    - **`_build_argmax_sparse`**：**4.964s → 1.864s（−3.10s，−62.5%，2.66×）**，单次 718ms → ~225ms，**29.0 → 10.38 ns/元素**
    - **入口累计 `dyn_bars_vec_seg`**：**8.445s → 4.990s（−40.9%）**；`_dyn_best_idx_ext` 4.647s → 0.519s（只剩查询路径）
  - ⚠ **内存同时更省**：峰值从 ~250MB（int64 下标表 178MB + 25MB 哨兵 + 逐层临时量）降到 **~160MB**（int32 表 89MB + 2 个 n 长 float64 缓冲 50MB）—— 在 512MB 节点缓存的高压场景下，这部分也贡献了部分墙钟收益。
  - **验证（四重）**：① **新增 `ai_test/test_dyn_argmax.py` 1016 例 0 失败** —— A 建表**新 vs 旧逐位**（10 值池 × is_max × k_max∈{None,0,1,2,5,8,12} = 140）；B `dyn_bars_vec` vs **朴素逐位置参考**（120）；B' 等值/tie 专项（长平台/全等值/±inf 交替，156）；C **段感知 vs 逐段独立**（516）；D `_dyn_rmq_vec_seg` 回归（360）；② `backend/tests` **180 passed**；③ `snapshot_panel.py --tag dyn39 --compare arith2` → **18 列全部 `exact=True`**；④ 上文 A/B。
  - ⚠ **测试自身的坑**：`seg_start[i]` 是「该行所属段的**全局起始下标**」，不是段编号。测试里曾误传编号（0/1/2）→ C 组 **107 处假阳性**（生产代码是对的）。已改为 `to_seg_start()` 构造真起止。
- 版本 1.18.38 → 1.18.39。

### Notes
- 本次只动 `ops_ext.py` 的 `_build_argmax_sparse`（新增 `_merge_adjacent_vals`）；`_build_sparse` / `_dyn_rmq_vec*` / `_rightmost_bars_seg` / `_rightmost_arg_best` / `panel_expr.py` **未动**。
- **本刀第 2 步（已定位，未做）**：查询/管道自耗 —— `_dyn_rmq_vec_seg` 自耗 ~2.7s（3.570s 里扣掉 `_build_sparse` 0.897s）、`_dyn_arg_idx_vec_seg` 自耗 ~1.87s。可疑点：`np.log2` + `np.floor` + `astype` 三遍全表（`js = floor(log2(lens))` 可用 `np.frexp` 取指数一步到位）、`st[js, pos]` **2D 花式索引**从 89~178MB 表上随机 gather（可按 `js` 分组后从**单层**连续数组取，工作集小得多）、`_win_lens_vec` 的 5 遍。
- 遗留：`_rightmost_bars_seg`（10.7%）｜`_seg_roll`（6.3~9.7%，含 pandas 2D rolling）｜`_rightmost_arg_best`（2.6%，同样的 gather 模式，可套同一套改法）。

## [1.18.38] - 2026-09-12

### Performance
- **特征加载段第一刀：`eval` 的 field 节点去掉「恒等 reindex」+ 改位置 gather**（`factors/panel_expr.py`，**数值零变化**）：特征加载段 **4.38s → 4.01s（−9.4%）**，整轮 **−4.5%**（csi300 / 3 因子 / 各 3 轮真实墙钟，样本区间不重叠）。
  - **定因（新增 `ai_test/profile_get_indexer.py`）**：patch `Index._get_indexer` / `Index.get_indexer` / `MultiIndex.get_indexer`，每次调用用 `sys._getframe` **遍历调用栈找第一个不在 site-packages 里的帧**（比 `inspect.stack()` 快几个数量级），按 `文件:行(函数)` 聚合。结果：**特征加载段 19% 的 `_get_indexer` 几乎全部来自 `panel_expr.py:809` 的 `self.field(...).reindex(active)`**（target 81.4 万行、33ms/次；cProfile 口径 151 次 / 73.5s ≈ 19%）。
  - **两处都是"白算/贵算"**：
    - **`sr=False`（label/base/tag）：`active` 就是 `self._full` 同一个对象** —— `field()` 自 v1.18.31 起已按 `_full` 对齐 ⇒ 这次 `reindex` 是**恒等操作**，却仍跑一次全表 `MultiIndex.get_indexer`。→ 用 **`is` 同对象短路**（做法与 `_align` 一致）。
    - **`sr=True`：`active` 是 close 有效行压缩序列**，确需取子集；但它是 `_full` 的**布尔子集（同序）** ⇒ 「按位置 gather」与「按标签 reindex」**取值等价**（float64 逐位相同、index 同一对象），而 gather 只要 ~2ms（省 ~30ms/次）。
  - **改动**：
    - `_active_index` 新增缓存 `self._active_pos[sr]`（= `np.flatnonzero(掩码)`），并且 **close 全有效时直接返回 `self._full` 同一对象**（原为 `close_full[notna].index` 的新对象 —— 两者取值相同，复用同一对象还能让 `_seg_arrays` 段数组缓存命中同一份、并让上面的 `is` 短路生效）。
    - `eval` 的 `op == "field"` 分支三级：`active is self._full` → 恒等短路；有 `_active_pos` → `pd.Series(f.to_numpy()[pos], index=active)`；否则兜底原 `reindex`。
  - **实测**（`ai_test/profile_feature_load.py --no-profile`，用 `progress_cb` 的「计算特征数据」消息作阶段边界取**真实墙钟**，避免 cProfile 放大）：
    - **csi300 / 3 因子（可比口径，各 3 轮）**：特征加载段 **6.19/4.38/4.42s → 5.75/3.95/4.01s（热态 −9.4%）**；整轮 8.11/8.41s → 7.70/7.87s（−4.5%）
    - 1000 只 × CWH（重内存口径）：212/217s vs 265/335s（−18%~−35%）—— ⚠ **绝对墙钟不可比**（1000 只 × 1 因子 217s 而 300 只 × 3 因子仅 4.4s，量级严重不符，疑内存压力触发节点缓存 LRU 反复重算），**仅作方向参考**
  - **验证（三重）**：① **新增 `ai_test/test_field_take.py` 38 用例 0 失败** —— 新分支 vs 旧 `f.reindex(旧 active)` **逐位**（数值/index/dtype）+ `_active_pos` 与 active 自洽（`_full.take(pos).equals(active)`）+ **全有效掩码不变量**（`s[s.notna()].index.equals(s.index)`，这是"复用 `_full`"成立的依据）；② `backend/tests` **180 passed**；③ `snapshot_panel.py --tag dyn38 --compare powbase` → **18 列全部 `exact=True`**。
- 版本 1.18.37 → 1.18.38。
- 架构手册 §7.7-E 的「特征加载三刀」**第 1 刀已完成**，下一刀见该表 #2（`pandas rolling.calc` 19.5 万次）与 #3（DYN 内核族 41%）。

## [1.18.37] - 2026-09-12

### Performance
- **事件研究（0/1 信号）多周期重复计算去重 + 期数跟随周期 + 基线曲线提速**（`factors/single_test.py` / `factors/event_study.py`；实测全 A「趋势顶底离开底部」多周期 [20,40] **52.4s → 38.0s（−27.5%）**，数值逐位不变）：
  - **① 跨周期复用事件研究结果**：事件研究只取决于「触发事件集合 + es_k」，二者都与预测周期 h 无关（触发由因子自身决定、es_k 为所有周期的上界），而原实现位于 `for h in horizons` 循环内 —— N 个周期就重算 N 遍**完全相同**的 40 期结果（实测 `build_event_stats + compute_baseline_curves` ≈ 11.2s/次，勾 7 个周期白烧 ~67s）。现按 `col_name` 缓存、其余周期浅拷贝复用（两周期事件研究 MD5 指纹逐位一致）。
  - **② 事件研究期数改为跟随用户填的最大周期**：`es_k = max(horizons)`（原 `max(EVENT_MAX_K=40, max(horizons))`）—— 填 20 天从此只算 1..20 期（基线曲线是逐 k 大矩阵运算、k 越大越贵：单次 10.6s → 5.5s），不再白算到 40 期。弹窗「最长持有」默认 = 已算期数，想看更长期数点「重算至 N 期」。**判定口径不变**：0/1 判定取该行「周期」对应的 k（前端 `esPointOf` 用 `r.horizon`），而 `es_k ≥ horizon` 恒成立。
  - **③ `compute_baseline_curves` 内部三处**（逐位等价；11 组边界用例对拍 PASS：常规 / 触发股子集 / 无效代码 / 非日历日 / k 超尾部 / 空事件 / 全 NaN 列+±inf / 单事件 / 3 行极小表 / k=1 / 无宽表）：`entry_mat = F[entry_rows]` 与 `entry_rows < n_row` 提到 k 循环外（省每 k 一次 635 万 gather）；触发股剔除由「(配对日 × 标的) 635 万 bool 大矩阵 + 每 k `flag[r_idx]` 复制」改**稀疏 (行,列) 对**（每 k 只改 O(触发数) 个元素）；均值口径用「原地 NaN→0（`np.copyto(where=)`）+ sum/count」替代 `np.nanmean` 的整表 `_replace_nan` 拷贝；`trig_med_arr` 逐列 median 替代 `np.nanmedian(axis=0)` 的整块 flatten。实测 10.6s → 9.0s。
  - 踩坑记录：曾用 `np.nan_to_num(copy=False)` 替代 `_replace_nan`，实测**反而更慢**（其内部还要 `isposinf`/`isneginf`/`isnan` 各扫一遍：1.67s > 1.16s），最终改用 `np.copyto(where=)` 复用已算掩码。
- 版本 1.18.36 → 1.18.37（后端 `backend/app/__init__.py` / README 顶部）。

## [1.18.36] - 2026-09-12

### Performance
- **特征加载（面板求值）两处优化：单进程同口径 `_load_feature_panel` 56.73s → 53.54s（−5.6%）；数值零变化**（`factors/panel_expr.py` / `factors/ops_ext.py`）：
  - **背景（给并行 worker 内部做 profile）**：强制单进程面板（`QLIB_SFT_PANEL_MAX=999999`）+ cProfile 包住 `_load_feature_panel`（csi1000 规模；代码路径与并行 worker 内的 `_panel_features_chunk` 完全一致）。热点：`_dyn_best_idx` 7.8s、`pandas.rolling.calc` 3.7s、`Index._get_indexer` 3.5s、**`os.stat` 3.4s / 35643 次**、`np.fromfile` 3.0s / 17819 次。
  - **① `os.stat` 消除（35643 → 19803 次；3.44s → 1.98s）**：`_field_bin_meta` 原两条路径都先 stat。新增 **`_BIN_CACHE` 优先分支**（该字段已整列读过 → 直接由缓存给出 mtime / start / n_rows，免 stat 免开文件）与**目录级快照 `_dir_meta`**（一次 `os.scandir` 取该股票目录下全部 .bin 的 `(mtime_ns, size)`，替代逐字段 `os.stat`；**TTL 60s** 内命中零 syscall，`QLIB_PANEL_DIR_META_TTL` 可调、`0` = 每次校验；失效由目录 mtime 校验与 `clear_bin_cache()` 兜底；`set_panel_runtime` 切换数据目录时自动清空）。
  - **② 稀疏表层合并改「扩展数组」版 `_dyn_best_idx_ext`**：原 `_dyn_best_idx` 每次合并需 `np.maximum(ai,0)` ×2 + `vals[...]` ×2 + `np.where(..., nan)` ×2 + `isnan` ×2；现**一次构造 `_make_dyn_ext(vals)`**（index 0 = NaN 哨兵、i+1 = vals[i]），合并时用 `vals_ext[idx + 1]` 一次取候选值、NaN 判定改 `av != av`：**7.82s → 5.71s（−27%）**；语义逐位等价。
  - **实测**：单进程口径 `_load_feature_panel` **56.73s → 53.54s（−5.6%）**；并行全 A 端到端 66.10s（与 v1.18.35 的 66.26s 持平 —— 收益约 2.7s 落在并行/磁盘波动范围内，但两处热点的下降在单进程同口径下明确可复现）。
  - **验证**：① `_dyn_best_idx_ext` vs `_dyn_best_idx` 200 组 × max/min（含 -1 无效下标、NaN、±inf、大量等值）**全部逐位 PASS**；② `_field_bin_meta` 与原逻辑（逐字段 `os.stat`）在真实数据 600 组抽查**精确一致**，同规模查询 0.205s → 0.090s（**2.3×**）；③ 全 A 三因子 `results` 逐字段 **0 差异**；④ `pytest tests -q` 全绿。
- **剩余大头（下一刀候选）**：`pandas.rolling.calc` 3.6s / 43560 次（`_seg_roll` 的 2D rolling）｜`Index._get_indexer` 3.4s｜读盘 `np.fromfile` + `io.open` 4.6s（17819 次，冷缓存不可避免；可考虑 memmap / 合并字段读）｜`_dyn_rmq_vec_seg` / `_dyn_arg_idx_vec_seg` 各 ~2s。
- 版本 1.18.35 → 1.18.36。

## [1.18.35] - 2026-09-12

### Performance
- **事件研究段三处优化 + 0/1 信号不再计算 IC：全 A 端到端 78.97s → 66.26s（−16.1%；累计相对 v1.18.32 的 124.25s 降 46.7%）**（`factors/event_study.py` / `factors/single_test.py`，**数值零变化**）：
  - **① `_align_returns` 向量化（本轮最大头）**：原实现是「逐事件 Python 循环 + 内层逐 k 循环」（`n_ev × max_k` 次标量运算，且每个事件还要 `px_wide[c].values` 取一次整列）—— 「趋势顶底离开底部」触发事件上万时**单次调用 ~25s**（即上一版遗留的"事件研究段 ~25s 未定位"）。现改为：列位置 / 日期位置一次性 `get_indexer`、价格宽表一次转 numpy、逐 k 列向量化切片（40 次 O(n_ev)）。**实测该段 −13.9s**（单因子口径）。
  - **② `compute_baseline_curves` 只对有效配对日构造子矩阵**：原实现对全部配对日分配 `(配对日 × 全样本列)` 大矩阵（全 A ≈ 1172×5418 = 635 万）并做 nanmean / nanmedian —— 大 k 时绝大多数行整行无效仍参与计算；现只对「该日存在 T+1+k 价」的行构造子矩阵。
  - **③ 触发股宽表改由全样本宽表取列子集**（新增 `_event_px_from_full`）：内容完全等价（unstack / ffill 逐列独立），省掉 `px[mask]` 布尔索引 + 二次 unstack + sort_index + ffill：**3.86s → ~0.05s**。执行顺序改为「先构造/复用全样本宽表 → 再派生触发股宽表」（全样本宽表本身已有进程级缓存）。
  - **④ 0/1 信号跳过 IC / RankIC / ICIR**：0/1 只有两档取值 —— Pearson IC 退化为"点二列相关"，RankIC（秩只有两档）与 ICIR 无解释意义；且 0/1 信号的结论本就以事件研究（中位数 / 绝对收益胜率 / 日配对超额）为准。跳过计算后全 A 约省 3s/因子（实测两因子 `5.96→3.00s`、`4.81→2.72s`）。**副作用（预期行为变化）**：0/1 信号不再参与「方向矛盾」判定（该分支依赖 IC/ICIR）；表格与导出中 0/1 行的 IC/RankIC/ICIR 显示为 `-`。
  - **实测**（全 A 5418 只 / 2021-01-01~2026-06-01 / 周期 40 / 前复权 / 预热 250，3 因子）：

| 指标 | v1.18.32 | v1.18.34 | **v1.18.35** |
|---|---|---|---|
| 总耗时 | 124.25s | 87.99s | **66.26s（累计 −46.7%）** |
| 统计阶段 | 94.98s | 58.88s | **36.31s** |
| 「趋势顶底」段（含事件研究） | 50.0s | 37.08s | **16.99s** |
| 逐因子 `_test_one` | 19.1/16.6/14.8s | 10.9/6.0/4.8s | **10.9/2.6/2.5s** |

  - **验证（数值零变化）**：① `_align_returns` 与 `compute_baseline_curves` 均从 `git show HEAD:` 取旧实现逐位对拍 → **各 4 组 ALL PASS（atol=0）**（覆盖空事件、max_k=1/5/40/60、非交易日、缺失标的、NaN 20%）；② `_test_one` 新旧对拍（csi300、3 因子、58.5 万行面板）**0 差异**（0/1 的 4 个 IC 字段由有值→`None` 属预期变化，已单独核对）；③ 全 A 三因子端到端 `results` 逐字段 **0 差异**。
- **剩余大头（下一刀候选）**：特征加载 ~28s（42%，需 profile 并行 worker 内部）｜事件研究段 ~14s（其中 `_full_px_wide` 首建 3.1s、baseline 剩余）｜负市值对数 `_test_one` 10.9s（IC ~4s + 分位段）。
- 版本 1.18.34 → 1.18.35。

## [1.18.34] - 2026-09-11

### Performance
- **单因子测试 `_test_one` 位置索引化：全 A 端到端再降 22%（累计相对 v1.18.32 降 29.2%）**（`factors/single_test.py`，**数值零变化**）：
  - **定位**：对 `_test_one` 单次调用单独 cProfile（全 A 6,410,621 行面板、20.05s）→ `Index._get_indexer` 2.7s + `ndarray.take` 2.5s + `_take_nd_ndarray` 1.2s + `MultiIndex.equals` 3.1s ≈ **9.5s 全部耗在 `df.loc[g.index]` 的 MultiIndex 对齐**上（剔除开关对每组样本最多 5 次 `.loc`，触发组 + 未触发组 + 分位段共 11 次大索引对齐）。
  - **改动**：`_test_one` 全程位置索引化 —— 开头用布尔掩码定位 `_sub_pos`（df 行位置），并把因子/标签/行情/涨跌停与 ST 标签列**预取为 numpy 视图**（`_NR`）；新增 `_frame(pos, cols)` 按位置取列构造小 DataFrame（index 用 `Index.take(pos)`，与等价 `df.loc[...]` 完全一致）；`_exclude` 改为接收/返回**位置数组**、剔除判定走 numpy 掩码；分位段的 `meta` / `quint` 同步改位置切片。
  - **评估后未做**：IC 向量化做过微基准（1308 日 × 5400 只、706 万行）—— 逐段 `scipy.rankdata` 版与 pandas `groupby.apply( corr )` 版**同为 ~4.3s、无收益**，且数值逐位一致但收益为零，故**保留原实现**（避免无谓风险）。
  - **实测**（全 A 5418 只 / 2021-01-01~2026-06-01 / 周期 40 / 前复权 / 预热 250，3 因子）：总 **113.38s → 87.99s（−22.4%；累计相对 v1.18.32 的 124.25s 降 29.2%）**；统计阶段 85.05 → **58.88s**；逐因子 `_test_one` **18.37→10.94s / 15.62→5.96s / 14.14→4.81s**（0/1 因子降幅最大，因其剔除组样本更小、对齐成本占比更高）。
  - **验证（数值零变化）**：新增对拍脚本（临时 `ai_test/verify_test_one.py`：从 `git show HEAD:` 取旧 `_test_one` exec 到独立命名空间，在真实 csi300 流程中同时跑新旧实现并逐字段比较，覆盖 IC / 分位 / 触发组 / 剔除 / 日配对全路径）→ **3 因子、58.5 万行面板、逐字段 0 差异**；全 A 三因子结果与 v1.18.33 逐字段 **0 差异**。
- **剩余大头（下一刀候选）**：特征加载 27.2s（31%）、事件研究 ~34s（39%，其中「趋势顶底」段 37.08s 内已插桩项仅 ~5.9s → **仍有 ~25s 未定位**）、`_test_one` 合计 21.7s（25%）。
- 版本 1.18.33 → 1.18.34。

## [1.18.33] - 2026-09-11

### Performance
- **单因子测试端到端四项低风险优化（数值零变化；全 A 实测 −8.7%）**（`factors/event_study.py` / `factors/single_test.py` / `factors/panel_expr.py` / `factors/ops_ext.py`）：
  - **起因（端到端实测定位）**：全 A（过滤后 5418 只 × 2021-01-01~2026-06-01 × 周期 40 × 前复权 × 预热 250）总 **124.25s**，分阶段为：解析股票池 1.8s、**特征加载「切块求值」27.3s（22%）**、**统计阶段 95.0s（76%）**；统计阶段内部 `_test_one`（逐因子求值+统计）50.4s（41%）、**事件研究（0/1 信号顺带算）≈41s（33%）** —— 结论：**大头已不再是「切块求值」**，而是统计阶段（其中事件研究占三成）。
  - **① `compute_baseline_curves` numpy 化**：原实现对每个 k（40 次）做全表 `shift(-(k+1))` / `where(~flag)` / `reindex(days)` / `to_numpy` → 改为**一次性转 numpy**，逐 k 只在「配对日」行上取子矩阵做除法/截面均值/中位数，当日触发股用「配对日 × 标的」小布尔矩阵标记（原实现是全表 DataFrame 逐点赋值）：**8.78s → 2.24s（−74%）**。口径不变（`ret[t]=F[t+1+k]/F[t+1]−1`、剔除当日触发股、先按日截面均值再对配对日平均、中位数取配对日全部样本）。
  - **② 全样本 PX 宽表进程级缓存**：`single_test._full_px_wide` 新增 `_FULL_WIDE_CACHE`（只保留最近 1 份；key = 形状 + 区间端点 + 首值指纹；提供 `clear_full_wide_cache()`），同一进程连续跑多个单因子测试/多因子时复用，省 ~3.1s/次（单份约 50MB，返回值只读）。
  - **③ `MultiIndex.equals` 热点治理**：`panel_expr._align`、And/Or 分支、出口两条快路与 `ops_ext` mask 对齐处统一加 **`is` 同对象短路 + 等长前置**（cProfile 实测原 `MultiIndex.equals` 占 6.2s / 6723 次，逐元素比较 object level）。
  - **④ `_inst_codes` 加速**：原逐行 `.astype(str).str.upper()`（全 A 单次调用 832 万次 `str.upper`，≈4~5s）→ 改 `pd.factorize` 取 unique 标的（约 5000 个）做 `str().upper()` 再按整数 codes 回填；逐元素语义不变（含 NaN → `'NAN'`）。
- **验证（数值零变化，双重）**：① 新旧实现对拍脚本（临时 `ai_test/verify_baseline_np.py`：从 `git show HEAD:` 取旧实现 exec，与本模块新实现对同一批随机输入比较，4 组覆盖 NaN 20% / max_k=1 / max_k=60 / 越界 code）→ **全部严格相等（atol=0）**；② 全 A 三因子端到端 `results` 逐字段比对 → **0 处差异**；③ 总耗时 **124.25s → 113.38s（−8.7%）**（统计阶段 94.98→85.05s；`_test_one` 三项各 −0.6~1.0s；2 因子含 cProfile 口径 81.81→69.51s，−15.0%）。
- **剩余大头（后续候选）**：特征加载 26.4s（23%）、`_test_one` 合计 48.1s（42%）、事件研究 ~34s（30%，其中趋势顶底段仍约 25s 未定位：疑在触发索引 level 提取 / `df_full` 列切片 / `es_inst` 解析）。
- 版本 1.18.32 → 1.18.33。

## [1.18.32] - 2026-09-11

### Changed
- **0/1 信号「有效✓」判定收紧：日配对超额改为「按持有期缩放的门槛」，并新增「日配对稳定」条件**（新增 `components/verdictRules.ts`；`SingleFactorTestPanel.tsx` + `EventStudyModal.tsx`）：
  - **起因（用户实测）**：因子「趋势顶底离开底部」20 日 —— 事件级中位数与绝对收益胜率都达标，但**日配对均值超额仅 +0.081%**（不足千分之一）却被判「有效」：该量级连一次往返交易成本（约 0.2~0.35%）都覆盖不了，与噪声无异；同一样本的**事件级中位数超额为 +1.872%**，两者相差 23 倍，正是"触发时间高度集中、收益靠少数触发密集日"的典型特征。
  - **原第③条**（v1.18.20）：`超额 > 0` —— 门槛过松（0.0001% 也能通过）。
  - **新第③条**：`超额 ≥ 门槛(k)`，其中 `门槛(k) = max(0.5%, 0.025% × k)` —— 0.5% 约合覆盖一次往返交易成本后仍有富余；0.025%/交易日 ≈ 年化 6% 的机会成本门槛（k ≤ 20 日由 0.5% 覆盖）。例：20 日 → 0.50%、40 日 → 1.00%、60 日 → 1.50%。
  - **新第④条**：**日配对稳定** = `|HAC t| ≥ 2` 或 `日胜率 ≥ 55%`；反向镜像为 `|HAC t| ≥ 2` 或 `日胜率 ≤ 45%`；两项皆缺失（配对日 < 2）→ 不通过（无法验证稳定性，宁严勿松）。
  - 「有效(反向)✓」为完整镜像（中位数 ≤ −0.50%、绝对收益胜率 ≤ 45%、超额 ≤ −门槛、稳定性反向通过）。
  - **判定规则抽为共享模块 `verdictRules.ts`**（`excessThresholdOf` / `pairStabilityOf`）：表格「结论」列（含导出 xlsx）与事件研究弹窗提示**共用同一套门槛**，杜绝两处口径漂移。
  - **弹窗同步**：`EventStudyModal` 新增 `pair` prop（该行的 `daily_t_hac` / `daily_win`），提示语按同一门槛重写，并在末尾注明「本提示按当前『最长持有 k 日』口径；表格结论按该行『周期』列口径」——两者持有期不同是此前"表格判有效、弹窗显示待观察"观感的来源，并非 bug。
  - **「待观察」悬停原因**逐条列出四项对比与差额（如 `超额 +0.081% <0.500%（差 0.419%）✗`、`日配对不稳定 |HAC t| 1.23 <2 且 日胜率 52.00% <55% ✗`）。
  - **无基准时**（旧结果 / 基准计算失败）第③条不启用（向后兼容），第④条仍要求。
  - **影响**：判定口径收紧，一批此前判「有效」的因子将转为「待观察」（尤其**超额薄 / 逐日不稳定**者，如上述「趋势顶底离开底部」20 日）；连续因子判定不变。
- 验证：`npx tsc -b --force` EXIT=0；`read_lints` 0 diagnostics。
- 版本 1.18.31 → 1.18.32。

## [1.18.31] - 2026-09-11

### Performance
- **小公式的「固定开销」大修：并集 index 免 factorize + 字段按位铺值 + 出口免两次全量 MultiIndex 运算**（`factors/panel_expr.py`，**数值零变化**）：
  - **背景**：v1.18.30 之后，短公式的耗时**与公式无关** —— 用户因子 `负市值对数`（35 字符）实测 `_apply` 仅占 **0.4%**，**99.6% 是 `load_field_series` 38.5% + `_union_index` 21.7% + `[其余]` 39.1%**。
  - **逐项定因**（新增 `ai_test/profile_fixed.py` / `profile_fixed2.py`，300 只 / 2 字段 / 354977 行）：
    - **`_union_index` 131.7ms/次**：逐股循环（含 600 次 `_field_bin_meta`）**76.2ms** + **`MultiIndex.from_arrays` 45.4ms** + `np.repeat` 5.6 + concatenate 4.0
    - **每个字段**：`from_arrays` ~48ms + `sort_index()` 6.5ms + `Series.reindex(_full)` 25ms + 逐股循环 25ms
    - **出口**：`df.index.union(full_out)` 21.0ms + `reindex(full_out)` 21.2ms + 时间戳掩码 5.2ms —— 而当 `read_start == start_time`（无预热前移）时 `df.index` **已正好等于** `full_out`，这三步**全是恒等操作**
  - **免 factorize 构造 MultiIndex**（新增 `_build_multiindex`）：`from_arrays` 的价值只在于对 35 万个 object 字符串做 hash-factorize；改为直接给 levels+codes —— level0 用「各段 instrument 的首次出现序」+ `np.repeat(段序号, 段长)`（300 次 Python 操作 + O(n) numpy），level1 用 `pd.factorize(拼接日期)` 复现同一「首次出现序」。**实测 21.5ms → 8.4ms（2.6×）**。
    - ⚠ **绝不能在 Python 里遍历那 35 万个 object**（第一版用 `dict.fromkeys(cc)` + `np.fromiter(... for v in cc)` → 反而 **43ms**，比 `from_arrays` 还慢）；codes 必须只由「段」列表构造。
    - ⚠ **level1 必须保持「首次出现序」、不能换成升序**：`MultiIndex` 的 codes 比较 / lexsort 依赖 levels 顺序，换成升序会让 `sort_index()` 结果与原来不同（`ai_test/probe_mi_build.py` 专门验证）。
  - **字段按位铺值**（新增 `_union_layout` / `_load_field_on`）：`_union_layout` 一次遍历同时产出 (并集 index, **各股在 index 中的行区间 layout**, req_lo, req_hi)；`_load_field_on` 按「日历下标差」把字段值直接铺到并集 index 上（`field()` 原来要 `load_field_series(...)` + `reindex(_full)`）。等价性：字段在某股的可用区间 `[max(start_idx,req_lo), min(start_idx+n-1,req_hi)]` 必落在该股 union 区间内（union 取的是各字段覆盖的 min-start / max-end），其余位置填 NaN —— 与原 `reindex` 补 NaN 一致。**省掉每字段一次的 `from_arrays` + `sort_index` + `reindex(_full)`（~80ms/字段）**。
  - **出口两条免算快路**：① `df.index.equals(full_out)` 时直接返回（union + 两次 reindex + 掩码全免，35 万行约 48ms）；② 若 `df.index.equals(last_ev._full)` 则 `df.index ⊇ full_out`，`union` 必等于 `df.index`，跳过这次 `MultiIndex.union`（约 21ms）。
  - **实测**：
    - **固定开销型因子 `负市值对数`：0.744s → 0.222s（−0.522s，−70.2%）**，而 `_apply` 独占**不变**（0.005s → 0.004s）⇒ 收益全在固定开销
    - **用户三因子**：`趋势顶底离开底部` **1.201s → 0.541s（−55%）**、`CWH_MIX_D_PCT_R40` **2.050s → 1.476s（−28%）**
    - **CUP_POOL**：8.359s → 8.003s（−4.3%；样本区间有重叠，量级与「4 字段 × 索引/reindex ≈ 0.35s」相符）
    - `cd backend && pytest tests -q` 自身也从 **129s → 107s**
    - 改后归因（剖面工具已同步包住新热路径）：`_union_layout` 0.097s（36%）+ `_load_field_on` 0.072s（27%）+ `[其余]` 0.094s（35%）
  - **验证（三重）**：① **新增 `ai_test/test_field_layout.py` 1138 用例 0 失败** —— `_build_multiindex` vs `from_arrays` 的 `equals` / `get_level_values` / `_segment_bnd` / `_seg_arrays` / `sort_index`，layout 与 index 自洽性（行区间连续、段内 instrument 与日期、全覆盖），以及 `_load_field_on` vs `load_field_series(...).reindex(idx)` **逐位**（5 组 universe/区间/字段组合，含 `close+market_cap`、`close+limit_up+is_st`）；② `backend/tests` **180 passed**；③ `snapshot_panel.py --tag fixed31 --compare powbase` → **18 列全部 `exact=True`**。
- 版本 1.18.30 → 1.18.31。

### Notes
- 本次只动 `panel_expr.py` 的字段读取与出口组装路径；**算子求值、`_by_group`、`_roll`/`_ref`、各 DYN 内核未动**（`_apply` 独占耗时不变，可作为「只改开销、不改算法」的佐证）。
- `load_field_series` / `_union_index` 保留（前者成为对拍用的参考实现，后者是 `_union_layout` 的薄封装）。
- **剩余（下一步候选）**：`_union_layout` 的 **78% 是逐股循环里的 `_field_bin_meta`**（每次都先 `os.stat` 再查缓存，实测 ~127µs/次 × 600 次 = 76ms）；可考虑「先查 `_BIN_CACHE`（已整列读过的字段免 stat）」或给 meta 缓存加 TTL。

## [1.18.30] - 2026-09-11

### Performance
- **T5：`_roll` / `_ref` 去 pandas groupby，改「段作列矩阵 + 一次 2D 滚动」与 numpy 段内移位**（`factors/panel_expr.py`，**数值零变化**）：
  - **背景**：v1.18.29 之后，`Mean/Max/Min/Sum/Std/Var`（`_roll`）与 `Ref`（`_ref`）仍走 **pandas groupby**，实测单次 **`_roll` 55~66ms、`_ref` 22.9ms**（354794 行 / 300 段），远超内核应有个量级。
  - **微基准定因**（新增 `ai_test/bench_roll.py`、`bench_roll2.py`）：
    - `groupby(level=0).size()` **18.5ms** vs `groupby(整数段码).size()` **4.4ms** ⇒ 取 MultiIndex level=0（object 字符串）作组键要物化 35 万个字符串；
    - 全表 `pd.Series(arr).rolling(40).mean()` 只要 **11.0ms**，而 groupby 版 **58.7ms** ⇒ **81% 是 groupby 调度开销**；
    - **整数段码 groupby 虽然逐位相同但更慢（100ms）** ⇒ 此路否掉；
    - **逐段 pandas rolling 只快 1.1~1.3×** ⇒ 否掉。
  - **关键判据（决定方案）**：pandas 的 `max/min` 是**窗口局部**的 → 「全表 rolling + 只修段首 N-1 行」可行（56.3ms，收益小）；但 **`mean/std/sum/var` 是「增量累积、历史相关」** 的 —— 实测**即使只看「距段首 ≥ N-1 的不跨段安全区」，全表值与逐段值也有 33 万处不同**（对拍 FAIL）。故只有「让每条段的累积链独立」才既保逐位又快。
  - **采纳方案 G**：把各段**当作矩阵的列**（段尾 NaN 补齐），对整块做**一次** `DataFrame.rolling(N, min_periods=1)` —— **pandas 的 2D 滚动内核逐列独立累积**（每列自有累积链与 NaN 重置）⇒ 与逐段 rolling **逐位相同**，且 **2.1~2.6×**（mean 59.7→23.4ms、max 63.0→25.3、std 55.1→22.7、sum 48.9→18.8、min 55.3→24.7、var 47.8→22.5）。
  - **`_ref` 改动**：shift 只是搬值、**不含任何算术** ⇒ 逐段纯 numpy 移位与 `groupby(level=0).shift(k)` **逐位相同**（实测），而 **22.9ms → 1.6ms（14×）**。
  - **新增** `_shift_seg()` / `_seg_grid()`（+ `_SEG_RC_CACHE`，与 `_SEG_CACHE` 同生命周期、由 `eval_expr` 开头一并清空）/ `_seg_roll()`；`_ref` / `_roll` 改写为 numpy 段感知路径，sr 两路同步。
  - ⚠ **矩阵分块**：按「块内最长段 × 块内段数 ≤ 4n」贪心分块，避免「一只超长 + 一堆超短」时矩阵爆内存（段在扁平数组中连续 ⇒ 块对应一段连续区间，直接切片、无需布尔掩码）。
  - ⚠ **顺带消除了一个已知坑**：原 `_roll` 的 sr 分支注释写着「必须 `sort=False`，否则 rolling 按组字典序排序、按位置回填会整池错位（实测 csi300+BJ 复现）」；新实现是**位置序**构造，天然没有这个隐患。
  - **实测**：
    - **CUP_POOL 同脚本 A/B（各 3 轮，样本区间不重叠）**：墙钟 **9.618s → 9.225s（−0.393s，−4.1%）**、`_apply` 独占 **7.153s → 6.615s（−0.538s，−7.5%）**（与「Mean 6 次 + Ref 19 次」的实测量互相印证）
    - **用户三因子**（300 只 / 2021-06-01~2026-06-01）：`趋势顶底离开底部` **1.873s → 1.201s（−36%）**、`CWH_MIX_D_PCT_R40` **2.789s → 2.050s（−26%）**；按算子：`Max` 104→**39ms**、`Min` 92→**39ms**、`Mean` 82→**29ms**（趋势顶底）；`Ref`（8 次）26.4→**9.8ms**、`Mean` 74.5→**31.6ms**（CWH）
    - **快照墙钟**：29.10s（v1.18.28）→ **27.84s（−4.3%）**
  - **验证（三重）**：① **新增 `ai_test/test_roll_ref_seg.py` 3214 用例 0 失败** —— 新实现 vs **旧 pandas 实现逐位对拍**（7 种段布局含**段长 1/2、长短悬殊（1,2,3,400,5,6,250,8）**、4 种 NaN 率含**全 NaN**、sr 两路 × 6 个滚动函数 × 8 个 N（1/2/3/5/40/100/250/999，**含 N 远大于段长**）+ 9 个 k（−300/−5/−1/0/1/2/5/40/500），比对**数值 / index / dtype**）；② `backend/tests` **180 passed**；③ `snapshot_panel.py --tag roll30 --compare powbase` → **18 列全部 `exact=True`**。
- 版本 1.18.29 → 1.18.30。

### Notes
- 本次只动 `panel_expr.py` 的 `_ref` / `_roll`（+ 三个新辅助函数、一个新缓存）；`_by_group` / 各 DYN 内核 / 二元算子分支**未动**。
- **`_by_group` 的「调度开销」本轮实测已近于零**：CWH 上 `_by_group` 0.888s、其 17 个 seg_fn 调用者自时间和 0.873s ⇒ 残差 ≈ 0.015s（0.5%）—— 那条 33% 是**内核本身**，不是调度（v1.18.22 的 seg_fn 快捷路径 + v1.18.23 的段数组缓存已把调度消掉）。
- 未做（本轮未触及、且在 CUP_POOL / 用户三因子中均未出现）：`IdxMax/IdxMin/Rank/Slope/Rsquare/Resi/Quantile` 仍走 `_by_group(s, None, _seg_window_op)` 的**逐段 pandas 循环**，可用同一套矩阵法改造。

## [1.18.29] - 2026-09-11

### Performance
- **T2 第 3 项：逐元素算术族改走 numpy ufunc（消掉常量列物化 + pandas 分派），大公式端到端 −8.8%**（`factors/panel_expr.py`，**数值零变化**）：
  - **背景**：v1.18.28 之后，`_by_group`（33%）之外**最大的一块就是逐元素算术族** —— `Sub 0.931 + Mul 0.692 + Greater 0.665 + Add 0.609 + Div 0.194 + Less 0.185 + Power 0.168 + Le/Ge/Gt 0.28 + And 0.579 ≈ 4.33s / 39%`。原实现 `getattr(a, fn)(b)` 走 pandas 算术分派（本机**无 numexpr** → `_evaluate_standard` → `operator.*` → 再包回 Series）。
  - **微基准（新增 `ai_test/bench_arith.py`，交错 A/B，n=354794、含 NaN）推翻了一个错误假设** —— 「大头是 pandas 分派」只对了一半：
    - 两列同 index 的 `Mul/Add/Sub/Div` **本就贴着内存带宽极限**（0.60~0.80ms，numpy 仅快 ~1.0×）→ 这部分改与不改无意义；
    - 真正的浪费是**常量操作数**：`_as_series` 把常量**物化成一整列**（写 ~2.8MB，**0.85ms/次**；大公式单轮约 **951 个**常量操作数 ⇒ **0.7~0.8s / 6~7%**），而 ufunc 对标量广播零成本（实测 1.45ms → 0.68ms，**2.1×**）；
    - `Greater/Less` 的 `a.where(a >= b, b)` 要过两遍 Series 比较 + 掩码写回（1.65ms → `np.where` 0.88ms，**1.9×**）；
    - `And/Or` 的 `(a!=0)&(b!=0)` 过两次 Series 比较 + 对齐 + 包装（3.68ms → 2.33ms，**1.6×**）；
    - **17 组含 NaN 对拍全部逐位相同**。
  - **改法**：新增 `_NUM_UFUNC` 表 + `_is_num_scalar()`；二元分支加 numpy 快路（**常量保持标量**交给 ufunc 广播、`max/min` 用 `np.where`、比较算子走 ufunc 后 `astype(np.float64)`），并**保留原 pandas 兜底分支**处理「非常量且非 Series」的入参。
  - ⚠ **必须套 `np.errstate(all="ignore")`**：pandas 2.3.3 在内部抑制了 numpy 告警（实测 `Series.div` **0 条**），直接调 numpy 会漏出 `divide by zero` / `invalid value`（实测各 1 条）。开销仅 ~1us/次。
  - ⚠ **`max/min` 不能用 `np.maximum`/`np.minimum`** —— 它们**传播 NaN**，而 `a.where(a >= b, b)` 的语义是「a 为 NaN 时**取 b**」；须用 `np.where(av >= bv, av, bv)`（NaN 比较为 False → 取 b，与原式逐位一致）。
  - ⚠ **「Series ** 标量」必须保留 pandas 路径**：`ndarray ** 标量` 走 numpy 的**快速标量幂**（`x**2 → x*x`、`x**0.5 → sqrt`），而 `np.power(ndarray, 标量)` 走通用数组内循环，两者在特殊值上不同 —— 实测 `(-inf) ** 0.5`：前者（及 pandas / `**` / `sqrt`）= **nan**，后者 = **inf**。`Log(0)` 会产出 `-inf`，故按旧行为保留（该路径本身已够快，且不必再物化常量列）。「标量 ** Series」（常量在左）旧行为本就是数组路径（指数是数组，numpy 不启用标量快速幂），仍走 numpy。
  - ⚠ **`And/Or` 的 `fillna` 必须在 `_align` 之前**：`_align` 新引入的缺失行仍是 NaN，而 pandas 里 `NaN != 0` 为 **True**，与「原始 NaN 先填成 0 → 判 False」语义不同。若一概用 `(v != 0) & ~isnan(v)`，会在 union 路径上产生 **525/75 处**差异（被对拍当场抓出）。同 index（面板常态）时无缺失行，fillna 才可安全挪到 numpy 侧（省掉两次 pandas fillna，约 2.9ms/次）。
  - **实测**（新增 `ai_test/ab_arith.py`：同脚本各跑 3 轮，样本区间**不重叠**）：
    - **整次 panel 墙钟**：**11.628s → 10.603s（−1.03s，−8.8%）**（旧 11.595/11.628/11.808、新 9.816/10.603/10.945）
    - **`_apply` 独占合计**：**9.002s → 7.603s（−1.40s，−15.5%）**，与「算术族 + And/Or」实测 4.334s → 2.941s（**−1.393s**）互相印证
    - **`_as_series` 常量广播**：**951 次 / 0.708s → 2 次 / 0.002s**
    - 单次均值（`调用数 / 基线 / 改后`，ms）：`Sub` 541/1.72/1.29、`Mul` 288/2.40/1.12、`Greater` 192/3.46/1.33、`Add` 318/1.92/1.14、`Div` 133/1.46/1.20、`Less` 66/2.80/1.61、`Power` 84/2.00/1.06、`Le` 58/2.48/1.25、`Ge` 50/2.21/1.36、`And` 110/5.26/4.68
  - **验证（四重）**：① **新增 `ai_test/test_arith_numpy.py` 3632 用例 0 失败** —— 新快路 vs **旧 pandas 实现逐位对拍**，覆盖 `_BIN_ELEM` + `_BIN_CMP` + `And` + `Or` **全部算子** × `S/S`（含**下标不同 union 路径**）/`S/c`/`c/S`/`c/c`/`None` × **8 种值池**（含 ±inf / 全 NaN / 半 NaN / 常量列 / 负底数） × 10 个常量（含 `np.float64`/`np.int64`），比对**数值 / index / dtype 三项**；② `backend/tests` **180 passed**；③ `snapshot_panel.py --tag arith2 --compare powbase` → **18 列全部 `exact=True`**；④ 上文 A/B。
- 版本 1.18.28 → 1.18.29。

### Notes
- 本次只动 `panel_expr.py` 的二元算子分支与 `And/Or`；`_ref`/`_roll`/`_by_group`/各 DYN 内核**未动**。
- 遗留可做：`_by_group` 调度（33%）｜`DYN_REF`（8.4%）｜`HHVBARS`/`LLVBARS`（各 7.5%）｜`DYN_MIN`+`DYN_MAX`（9%）｜`Ref`（4.8%）｜`Mean`（4.4%）｜`[其余]`（15.3%：下标构造 + DataFrame 组装 + 2 次 reindex）。

## [1.18.28] - 2026-09-11

### Performance
- **T2 第 2 项：DYN_REF 段感知内核提速 2.0×，大公式端到端 −6.0%**（`factors/ops_ext.py`，**数值零变化**）：
  - **背景**：v1.18.27 拿下 HHVBARS/LLVBARS 后，`DYN_REF` 成为**新的最大单项**（126 次 × 9.9ms ≈ 12.8%）。内核只是「回退 N_i 个位置再取值」，理论上是几次轻量 pass，但 ≈20 ns/行明显偏离纯带宽下限。
  - **微基准逐项排查**（新增 `ai_test/bench_dynref.py`，n=354794）：

    | 变体 | 随机 N | 恒定 N |
    |---|---|---|
    | `cur` 现状（v1.18.27） | 7.42ms | 7.23ms |
    | 去冗余 `np.trunc` | 6.87 | 5.91 |
    | + 去**布尔掩码三连**、改 `np.take(mode="clip")` + 一次 `np.where` | 6.15 | 5.45 |
    | + **线程局部 scratch** + 缓存 `arange(n)` | **4.05** | **3.34** |

  - **三处等价改动**：① `np.trunc(nvals).astype(np.int64)` 中的 trunc 是**冗余**的（float→int64 转换本身就向零截断），原先要两遍全表扫描；② 去掉**布尔掩码三连** —— `j[ok]` 压缩、`vals[j[ok]]` 非连续 gather、`out[ok]=` 掩码写回，每处都要一次全表 `nonzero`；改为全量 `np.take(mode="clip")`（越界先 clip 保安全，随后掩码置 NaN）+ 一次 `np.where`；③ **复用线程局部 scratch 缓冲 + 缓存 `arange(n)`**，消除每趟多 MB 临时数组的 malloc/free（微基准显示这一项独占一半以上收益）。
  - ⚠ **为什么 scratch 必须线程局部**：单因子测试 / 事件研究各跑在一个**后台线程**里，股票池小于 `QLIB_SFT_PANEL_MAX`（默认 1000）时是**进程内**求值 → 两个任务并发会共用模块级缓冲而**串数据**；大池走 `ProcessPoolExecutor`、各 worker 进程独立。故用 `threading.local()`。
  - **实测**（新增 `ai_test/bench_dynref_ab.py`：同进程交替 A/B，主判据 = 内核累计耗时，126 次/轮）：
    - **`dyn_ref_vec_seg` 内核**：**1.190s → 0.613s（−48.5%）**（单次 9.44ms → 4.87ms；样本 旧 1.232/1.190/1.196、新 0.641/0.626/0.613）
    - **整次 panel 墙钟**：**9.62s → 9.04s（−0.58s，−6.0%）**（样本区间不重叠；内核差 0.577s ≈ 面板差 0.58s，互相印证）
  - **验证（三重）**：① 新增 `ai_test/test_dyn_ref_seg.py` **400 用例 0 失败**（**三方**对拍：新实现 vs 朴素逐位置参考 vs 旧实现；覆盖段长 1/2/长短悬殊、NaN 率 0/10/50/100%、N 为 0/1/常量/随机/负/1e18/全 NaN/跨段，共 10 段长组合 × 4 NaN 率 × 10 N 型）；② `backend/tests/test_ops_ext_vec.py` **26 passed**；③ `snapshot_panel.py --tag dynref --compare powbase` → **18 列全部 `exact=True`**（快照 wall 36.09s → **29.10s**）。
  - 说明：单独建对拍网是因为既有单测只覆盖**非段感知**的 `dyn_ref_vec`（`DYN_REF._load_internal` 走它），段感知路径此前只有面板快照覆盖，对本次改写的粒度太粗。
- 版本 1.18.27 → 1.18.28。

### Notes
- 本次只动 `ops_ext.dyn_ref_vec_seg`，并新增线程局部 scratch 工具（`_dyn_scratch` / `_dyn_arange`，供后续 DYN 内核复用）；`dyn_ref_vec`（非段路径）未动。
- 返回值始终是 `np.where` 产出的新数组，scratch 不会泄露给调用方。
- 同日快照 wall 轨迹：**36.09s**（v1.18.24 基线）→ 35.25（pow）→ 39.81/34.91（T3，噪声大）→ 30.89（bars）→ **29.10s**（本版），累计 **−19.4%**。

## [1.18.27] - 2026-09-11

### Performance
- **T2 第 1 项：HHVBARS/LLVBARS 换 van Herk / Gil-Werman，内核 −64.5%、大公式端到端 −19.9%**（`factors/ops_ext.py`，**数值零变化**）：
  - **背景**：T2 细分把「非 `_by_group` 的 53.8%」按算子拆开，发现 **HHVBARS+LLVBARS = 3.574s（29.9%）** 是最大单项，单次 125~173ms / 354794 行 = **350~490 ns/行**，远高于 O(n) 纯向量化的量级。
  - **根因**（`ai_test/bench_hhvbars_n.py` 扫描 N）：v1.18.22 的「分块 `sliding_window_view` + 反转 argmax」每行的 `argmax(axis=1)` 要扫过 N 个元素 → 整体是 **O(n·N)** 的内存流量。实测 n=354794：N=5→30ms、N=60→86ms、N=125→191ms、N=250→306ms、N=400→520ms，**随 N 近似线性**。
  - **改法**：满窗口换 **van Herk / Gil-Werman「块内前缀-后缀极值」** —— 按 N 把数组 reshape 成 `(m, N)`，行内做前缀/后缀累积。长度恰为 N 的窗口 `[l, i]` 中，`suf(l) = [l, 块尾]` 与 `pre(i) = [块首, i]` **无缝拼成** `[l, i]`（相邻块边界端点相接、两半都在窗口内）→ 每点 O(1)、总 **O(n)**、复杂度与 N 无关；且因两半都不越出窗口，**天然满足「窗口不跨段」，无需按段建块**。
  - **「最右极值下标」的两处不对称（本次最容易踩的坑）**：前缀用「等值 + `maximum.accumulate`」得到的是「**最后一次**达成」✓；但把同一套照搬到反转数组上得到的是原坐标的「**最左**」✗ —— **方向正好相反，第一版被 202 用例对拍当场抓出 68 处失败**。后缀要的是「原坐标最右」=「反转坐标最左」=「**首次**达成」，须改用**严格递增（jump）**判定。
  - **按 N 二选一（避免小 N 劣化）**：新实现有固定开销（`axis=1` 逐行累积，行越短行数越多），实测交叉点 **N≈13** → `N <= _BARS_SLIDE_MAX_N(16)` 仍走分块滑窗、否则走 van Herk。实测（新/旧 ms）：N=4 44.5/26.1、N=16 42.5/44.9、N=64 40.9/93.7、N=100 40.2/133.3、N=250 40.7/360.8、N=400 37.5/528.4 → **改动整体不劣化**。
  - **实测**（`ai_test/bench_bars_ab.py`：同进程交替 A/B，主判据 = 内核累计耗时）：
    - **HHVBARS+LLVBARS 内核**：**3.281s → 1.166s（−64.5%）**（样本 旧 3.459/3.281/3.370、新 1.170/1.166/1.173）
    - **整次 panel 墙钟**：**12.02s → 9.63s（−2.39s，−19.9%）**（样本区间不重叠）
    - 按算子细分复测：`LLVBARS` 2.075s→**0.582s**、`HHVBARS` 1.499s→**0.599s**；两者占 wall 由 **29.9% → 12.1%**，「最大单项」已让给 `DYN_REF`（12.8%）
  - **验证（三重）**：① `ai_test/test_rightmost_bars_seg.py` **202 用例 0 失败**（覆盖 N≤16 与 N>16 两侧、段长/等值/NaN 边界、真实规模 300 段×700 行）；② `backend/tests/test_ops_ext_vec.py` **26 passed**；③ `snapshot_panel.py --tag bars --compare powbase` → **18 列全部 `exact=True`**（该次快照 wall 亦由 36.09s 降到 30.89s）。
- 版本 1.18.26 → 1.18.27。

### Notes
- 本次只动 `ops_ext.py` 的 `_rightmost_bars_seg`（+ 新增 `_BARS_SLIDE_MAX_N` 常量）；`_rightmost_arg_best`、NaN 语义、段边界语义、「等值取最右」全部未动。
- 小 N（≤16）仍走 v1.18.22 的分块滑窗实现（阈值由实测交叉点选定），故本次改动对任何 N 都不劣化。

## [1.18.26] - 2026-09-11

### Performance
- **T3：RMQ 内核族改为「按需建层 + 2D 一次 gather」，内核 −48.3%、大公式端到端 −4.5%**（`factors/ops_ext.py`，**数值零变化**）：
  - **消融先定位**（新增 `ai_test/ablate_dyn_rmq.py`）：`_dyn_rmq_vec_seg` 18 次调用 **1.370s（占整次 panel 9.9%）**，其中 `_build_sparse` **0.598s（43.7%）**、查询循环 **0.772s（56.3%）**；关键数字是 **n=431028、窗口最长 400 → 只需 9 层，却固定建了 19 层**。
  - **根因两条**：① `_build_sparse` 永远建 `log2(n)+1` 层，长面板上远超实际需要的 `floor(log2(max(lens)))+1` 层；② 查询是「按层遍历 + 每层全表布尔掩码」，其中 10 个空层纯属空转。
  - **改法**：`_build_sparse` / `_build_argmax_sparse` 改为返回 **2D 连续数组** `st[k, i]` 并支持 `k_max` 只建所需层；4 个查询函数（`_dyn_rmq_vec`、`_dyn_rmq_vec_seg`、`_dyn_arg_idx_vec`、`_dyn_arg_idx_vec_seg`）改成一次 fancy-index：
    `func(st[js, idx-lens+1], st[js, idx-(1<<js)+1])` —— 把「19 层各自的掩码 + gather + scatter」压成「一次 gather + 一次 func」。
  - **数值为什么必然一致**：`fmin/fmax` 满足结合律/交换律且忽略 NaN，一次查询只是合并两个长 `2^js` 的重叠块；新旧读的是**同一批稀疏表元素**（`st[js, l]` 与 `st[js, r-span+1]`），故逐位相同。`_dyn_best_idx` 的「等值取最右」语义也未动。
  - **评估后改方案**：原计划做「分块 RMQ（块内 O(n)、跨块 O(1)）」，但消融显示真实问题只是**层数超配 + 逐层空转** → 用更小、更安全的改动即拿到收益，无需引入块分解。
  - **实测**（新增 `ai_test/bench_t3_ab.py`：同进程交替 A/B，把旧实现逐字复制进来运行期覆盖 6 个符号，**主判据 = 内核累计耗时**，剔除面板级噪声）：
    - **RMQ 内核族（6 个符号）**：**1.728s → 0.894s（−48.3%）**（样本 旧 1.74/1.73/1.85、新 0.92/0.89/0.95）
    - **整次 panel 墙钟**（300 只 / 2021-06~2026-06 / 354794 行）：**12.39s → 11.83s（−4.5%）**（样本 旧 12.39~12.67、新 11.83~12.22，区间不重叠）
    - 消融口径：`_dyn_rmq_vec_seg` 单函数 **1.370s → 0.759s**；建的层数 **19（固定）→ 5~9（按需）**
  - **验证**：`tests/test_ops_ext_vec.py` **26 passed**（该文件自带独立朴素实现 `_ref_dyn_minmax` 做逐位置 RMQ 对拍）；`snapshot_panel.py --tag t3 --compare powbase` → **18 列全部 `exact=True`（maxabs=0、nan_mismatch=0）**。
  - ⚠ **方法学教训（重要）**：首次用「整次 panel 墙钟」做判据时得出**相反结论（新 −5.7%）** —— 本机上跨进程/跨次运行的 panel wall 波动达 **±10%**（同一快照负载实测 34.91 / 35.25 / 39.81s）。改成**同进程交替 + 内核累计耗时**后信噪比立刻正常。**面板级墙钟不足以判断 5% 量级的改动。**
- 版本 1.18.25 → 1.18.26。

### Notes
- 本次只动 `ops_ext.py` 的稀疏表构建与 4 个 RMQ 查询函数；`fmin/fmax` 语义、段边界 clip、`_dyn_best_idx`「等值取最右」全部未动。
- 附带内存收益：`n=431028` 时稀疏表由 19 层（float64 ≈65MB；argmax 版 int64 亦 ≈65MB）降到 9 层（≈31MB）。

## [1.18.25] - 2026-09-11

### Performance
- **常量指数不再被广播成整列，`Power(x, 2)` 类节点提速 15×，大公式端到端 −3.9%**（`factors/panel_expr.py`，**数值零变化**）：
  - **定位**：cProfile 的 `tottime` 里 `_operator.pow` 长期是异常项（v1.18.23 实测 0.248s/84 次 ≈ 3ms/次，是 `add` 的 15 倍）。给剖面脚本加「调用者追溯」后确认调用方是 **pandas `_evaluate_standard`**（`pandas/core/computation/expressions.py:67`，算术分派的 numexpr 回退分支）——本机 **`numexpr` 未安装**，故所有逐元素算术都落到 `operator.*`。
  - **根因**：`panel_expr._eval_op` 的 `pow` 分支先经 `_as_series(b, template)` 把常量指数**广播成整列 Series**，于是 `np.power` 走「**数组指数**」的逐元素通用 `pow()` 循环，丢掉 numpy/glibc 对「**标量指数**」的特化。42000 行实测：
    - `np.power(nd, 2.0)` 标量 **0.0119ms**（glibc `y==2 → x*x`）｜`np.power(nd, 2.0 数组)` **0.7852ms**｜`np.power(Ser, Ser2)` **0.8990ms**｜`np.power(Ser, 2.0)` **0.0586ms**
  - **扫描确认影响面**：新增 `ai_test/scan_pow_exponents.py`（深度感知解析）→ CUP_POOL **84 个 `Power` 节点，指数 100% 是常量 `2`**（`Pow(`/`^` 均 0 次）。
  - **改法（6 行）**：pow 分支保留标量形态 —— `b_scalar = b if (np.isscalar(b) and not isinstance(b, (str, bytes))) else None`，`return np.power(a, b if b_scalar is None else b_scalar)`。仅 b 是标量时 `template` 必为 `a`，故 `_align` 后同 index，取标量无语义损失。
  - **为什么只改 pow**：实测 `add/sub/mul/div/ge` 的「广播后」反而**略快**（0.0578 vs 0.0625ms）——pandas 内部同样要广播；这个红利是 **glibc 对标量指数 2 的 `x*x` 特化**独有，并非普遍的广播开销问题。
  - **实测（同进程 A/B，新增 `ai_test/bench_pow_scalar.py`：把 `panel_expr` 视角下 `np.isscalar` 换恒 False 精确复现旧分支，同 config 取 3 次最小）**：CUP_POOL / 300 只 / 2021-06~2026-06 / **354794 行** → **12.980s → 12.480s（−0.500s，−3.9%）**；定向剖面同 config：`_operator.pow` **0.120s → 0.008s（84 次，单次 1.43ms → 0.095ms，15.0×）**。
  - **数值验证**：`snapshot_panel.py --tag powfix2 --compare powbase` → **18 列全部 `exact=True`（maxabs=0、nan_mismatch=0）**。机理上标量路径走 `x*x`（精确），数组路径是 1ulp 级通用 `pow`；输出统一 `_cast_output_f32`（float32）后差异不可见。改前快照 36.09s vs 改后 34.91s / 35.25s，方向一致。
- 版本 1.18.24 → 1.18.25。

### Notes
- 本次**只动 `panel_expr.py` 的一处 pow 分支**，`ops_ext` 内核与其它算子均未动；不含 `Power` 的公式，结果与耗时都不受影响。
- 评估后未做：**安装 numexpr**（`pandas` 会改走 numexpr 后端，可能改变逐位结果，与"与 qlib/聚盟逐位对账"的口径冲突；且单算子调用下 numexpr 自身开销可能吃掉收益）。

## [1.18.24] - 2026-09-11

### Fixed
- **修复「结束日期填成 6 月 31 日」导致单因子测试 / 事件研究报 `day is out of range for month: 2026-06-31`**（三层防御，根治在前端 `DateInput`）：
  - **根因（前端）**：`components/DateInput.tsx` 的 `validDate()` 只校验 `D >= 1 && D <= 31`，**未校验该月是否真有这一天**。日历弹层本身正确（用 `new Date(y, m, 0).getDate()` 取当月天数），但**键盘手输** `2026` / `06` / `31` 能通过校验并被提交；`SingleFactorTestPanel` 与 `App.tsx` 提交前又只校验 `^\d{4}-\d{2}-\d{2}$`（**仅格式、不校验真实性**）→ 非法日期一路放行到后端，`panel_expr._union_index` 的 `pd.Timestamp(end_time)` 抛 `DateParseError`，而 `single_test.py` 的 `except Exception: load_end = end_date` 把非法值**原样透传**，最终报成误导性的「**特征计算失败**」。
  - **修复 ①（根治，前端）**：`DateInput.tsx` 新增 `monthDays(y, m)`（该月实际天数，含闰年），`validDate()` 改为 `D <= monthDays(y, m)`；`emit()` 对超界日**自动钳到月末并回写输入框**（`2026-06-31` → `2026-06-30`，用户可见被修正，而非静默丢弃）；并 `export isValidDateStr(v)` 供各表单复用。
  - **修复 ②（前端提交校验）**：`SingleFactorTestPanel.run()` 与 `App.startBacktest()` 改用 `isValidDateStr()` 做「格式 + 日期真实存在」校验，非法时提示「日期无效（如 6 月没有 31 日）」。
  - **修复 ③（后端兜底，防 API 直连）**：`factors/single_test.py` 新增 `_bad_date_arg(start, end)`；`routers/factors.py` 的 `/single-factor-test` 与 `/event-study` 前置校验并返回 **HTTP 400** 明确信息（此前会是「特征计算失败」）；`run_single_factor_tests` 入口同步前置校验并返回可读错误。
  - **实测**：`POST /api/factors/single-factor-test` 与 `/event-study` 传 `end_date=2026-06-31` → **HTTP 400** `{"detail":"结束日期无效：2026-06-31（该日期不存在，请检查年/月/日，如 6 月没有 31 日）"}`；合法日期回归（csi300 / 2024-01-01~2025-12-31 / 周期 40）→ `status=success`、IC/RankIC 正常。
  - 附：辅助函数命名为 `monthDays` 而非 `daysInMonth` —— 组件内已有同名局部变量（当月天数数字），否则 `tsc` 报 `TS2349: This expression is not callable`。
- 版本 1.18.23 → 1.18.24。

## [1.18.23] - 2026-09-11

### Performance
- **T2/T4：缓存段边界与段起止数组，大公式再提速 5.7%（累计相对 v1.18.21 提速 14.0%）**（`factors/panel_expr.py`）：
  - **定位过程**：`_by_group` 内部拆解得出「非 `_by_group` 部分」占端到端 53.8%，但那是个杂项桶，无法直接优化 → 改用 **cProfile 的 `tottime`**（函数自身耗时）拆成可行动条目。
    - ⚠ cProfile 输出里 `builtin/interp` 占 **51%**，那是**插桩自身开销**（100 万次调用 × ~2.4μs），不代表真实耗时；但它暴露了"链路上有 100 万次 Python 调用"这个真实信号。
  - **实际可优化项（Top tottime）**：`nt.stat` 0.203s/2400 次、`ndarray.repeat` 0.178s/1747 次（= `seg_start/end_arr`）、`re.Pattern.match` 0.133s/49321 次、`parse_prefix` 0.113s（后两者属 `parse_expr`，共 ~8.4%）、`_operator.pow` 0.248s/84 次。
  - **实施**：新增 `_seg_arrays(idx, n)` —— 按 `(id(idx), n)` 缓存 `(bnd, seg_start, seg_end)`。`_by_group` 在一次求值内被调上百次（每个动态节点一次），而同一 evaluator 内 `idx` 是**稳定对象**（`_active_index` 已缓存）、段划分完全不变，故可复用。
    - **安全性**：缓存键用 `id(idx)` 且**缓存中持有 `idx` 引用** —— 持有引用保证对象不被回收、`id` 不会被复用，**杜绝"地址复用取错缓存"**；且取出时再用 `hit[0] is idx` 身份校验。`idx` 本就在 evaluator 生命周期内常驻，无额外内存。缓存由 `eval_expr` 开头 `_SEG_CACHE.clear()` 清空（与 `_node_cache` 同生命周期），另有 64 项兜底上限。
  - **收益实测**：base **8.36s → 7.88s（−0.48s，−5.7%）**；`hhvbars_seg` 0.86s、`dyn_ref_vec_seg` 0.89s、`_dyn_rmq_vec_seg` 0.82s、`llvbars_seg` 0.47s（四项合计 3.04s / 38.6%，仍为后续目标）。
  - **累计**：v1.18.21 基线 9.16s → T1 后 8.36s → **T2 后 7.88s，相对原始 −14.0%**。
  - **数值验证**：面板快照 `--tag after2 --compare before` → **8 列全部 `exact=True`（maxabs=0、nan_mismatch=0）**。

### Notes
- **评估后未做**：
  - **`_field_bin_meta` 的 `os.stat` 缓存** —— `_BIN_META_CACHE` 已存在（v1.18.4，含 mtime 校验），那 2400 次 `stat` 是**必要的变更检测开销**，跳过它就检测不到数据更新，**不值得**。
  - **`parse_expr` AST 缓存**（~8.4%）—— `parse_expr` 内部有全局 `_CANON` 结构去重表且每次解析开头 `clear()`，缓存树会引入**跨表达式 `Node.key` 冲突**的微妙风险，收益亦不确定，**暂缓**。
- cProfile 分析脚本：`ai_test/profile_cprofile.py`（本地专用）。
- 版本 1.18.22 → 1.18.23。

## [1.18.22] - 2026-09-11

### Performance
- **重写固定窗口 HHVBARS/LLVBARS 段感知内核 `_rightmost_bars_seg`，大公式端到端提速 8.7%**（`factors/ops_ext.py`）：
  - **背景**：先用端到端剖面 + `_by_group` 内部逐项拆解 + **逐内核消融**三级定位，实测（CUP_POOL / 300 只 / 2021-2023）只有 4 个动态内核被调用，其中 **`hhvbars_seg` 18.3% + `llvbars_seg` 9.2% = 27.5%** 是最大单项（~140ms/次）。
  - **根因**：原实现用**完整稀疏表**做 RMQ（`_build_rightmost_arg_sparse`，O(n·log n) 内存+时间），再用 `for k, layer in enumerate(st)` **逐层查询**（log n 层 × 每层全表掩码扫描 + 两次花式索引 + `_rightmost_arg_best` 逐元素比较）→ 28 万行 × ~18 层 ≈ 500 万+ 元素的多趟操作。**但 HHVBARS/LLVBARS 的窗口 N 是完全固定的，根本不需要 RMQ。**
  - **改法**：两段式纯向量化实现（O(n)）——
    - **满窗口**（段内偏移 `r >= N-1`，占绝大多数）：**分块** `np.lib.stride_tricks.sliding_window_view` + **反转 argmax/argmin**（`N-1-rev.argmax(axis=1)` 即"最右"极值）。分块（`BLK = 1<<16`）是为避免 `(n × N)` 窗口视图内存爆炸。
    - **不满窗口**（每段前 N-1 个，数量极少）：段内前缀累积（`fmax/fmin.accumulate` 求前缀极值 + 下标 `maximum.accumulate` 求"最右"）。
  - **语义严格保持**：窗口 `[max(seg_start, i-N+1), i]` 不跨段；**等值取最右**（对齐原 `_rightmost_arg_best` 的 `tie → bi > ai`）；`vals[i]` 为 NaN 或窗口内全 NaN → 输出 NaN。
  - **⚠ 定位过程中的一次重要纠错**：上一轮曾把 `pd.Series(out, index=idx)`（重建 28 万行 Series）指认为"43.6s 的元凶"——**实测仅 0.153ms/次、占内部 0.6%**。错因是端到端插桩的 `OPS_EXT_FNS` **只列了 `*_vec`（逐段回退路径）、漏了 `*_seg`（段感知路径）**，导致"内核 0.020s"是漏抓、`_panel_dyn − 0.020s` 被误读为"Python 调度"（实际就是 `*_seg` 内核）。**教训：粗粒度相减会严重误导，必须逐项拆解 / 消融定位。**
  - **收益实测**（同一基准，base 9.16s → 8.36s）：
    - 端到端 **−0.80s（−8.7%）**；`hhvbars_seg` 1.68s→**0.97s（−42%）**、`llvbars_seg` 0.84s→**0.64s（−24%）**，两者合计 2.52s→**1.61s（−36%）**。
    - 两个探针互相印证（端到端 −0.80s ≈ 内核 −0.91s）；剩余 19.3% 为分块滑窗必需的 O(n) 计算量。

### Notes
- **数值正确性双重验证（全部通过）**：
  - **单元对拍**（新增 `ai_test/test_rightmost_bars_seg.py`，从 `git show HEAD:` 取旧实现对比新版）：**202 用例、0 失败**，覆盖段长 1/2/小于N/大于N/远大于N、N=1/N=段长/N>段长、段数 1~40、NaN 占比 0~100%（含窗口内全 NaN）、**大量等值**（专测"同值取最右"）、全常量/全 NaN/严格递增递减、以及**真实规模 300 段 × 700 行 × N=9/60/250**。
  - **端到端面板快照**（`ai_test/snapshot_panel.py --tag after --compare before`）：**8 列全部 `exact=True`（maxabs=0、nan_mismatch=0）**。
- **新增/用到的一次性分析脚本**（均在 `ai_test/`，本地专用）：`profile_sft_e2e.py`（端到端阶段+桶级剖面）、`profile_bygroup_deep.py`（`_by_group` 内部逐项，含复刻一致性校验）、`ablate_seg_kernels.py`（逐内核消融）、`test_rightmost_bars_seg.py`（单元对拍）。
- **后续优化目标已登记在 `md/开发记录.md`「性能优化遗留项」**：T2 `rest`（非 `_by_group` 部分，占端到端 53.8%，最大单块）｜T3 `_dyn_rmq_vec_seg`（7.7%，动态窗口 → 分块 RMQ）｜T4 `seg_start/end_arr` 缓存（3.7%）｜T5 `_ref`/`_roll`（~9%，语义细节多、慎动）。**明确不做**：`dyn_ref_vec_seg`（已近最优）、`Series(ctor)`/`to_numpy`（0.6%/0.1%）、读盘 bin→h5（3.2%，常数项）、以及"`(arr, idx)` 贯通改 `eval` 契约"（= 重写引擎）。
- 版本 1.18.21 → 1.18.22。

## [1.18.21] - 2026-09-11

### Changed
- **事件研究主图默认只显示「成对同口径」曲线，隐藏孤立线**（`components/EventStudyModal.tsx`，**仅展示层**）：
  - **动机**：主图原有 7 条线，其中 `mean`（事件级均值）是**该口径下唯一的孤立线** —— 没有同口径基准可比，极易被误用来减 `baseline`（日配对口径）。本项目已实际发生过一次（用户据 `6.326% − 3.067%` 得出不存在的 "+3.259% 超额"，见 v1.18.19）。
  - **默认只留两对（同口径、可互比）**：
    - ① 日配对（每个配对日 1 票）：`trigger_pair` ↔ `baseline` —— **可相减，差值即下方「超额曲线」**
    - ② 事件级中位数（每个事件 1 票）：`median` ↔ `baseline_median`
  - **`mean` / `p25` / `p75` 默认隐藏**（`hidden` 初值 `{mean: true, p25: true, p75: true}`），点击图例可随时开启。
  - 图例名称与顺序同步调整（成对的挨着）：`触发组(日配对)` / `基准·未触发组(日配对)` / `中位数(事件级)` / `基准中位数·未触发组(事件级)` / `p75(事件级·默认隐藏)` / `p25(事件级·默认隐藏)` / `均值(事件级·无同口径基准)`（`mean` 另加虚线样式以示区别）。
  - 标题警示同步改写：说明默认只显示两对同口径曲线，并明确「**请勿用均值(事件级)减基准(日配对)**」。
  - **未做**（评估后认为不必要，见 Notes）：不加「日配对·中位数」曲线，也不补「事件级均值的基准」。

### Notes
- **为何不加「日配对·中位数」**：日配对的内层用均值，语义是「那天**等权买入所有触发股**」的真实收益 —— **可交易**；换成中位数则变成「中间那只股票」的收益，**不可执行**，且其诊断价值与 `curve.median`（事件级中位数）重叠。
- **为何不补「事件级均值的基准」**：`mean` 的诊断用途（与 `median` 的差距 = 是否「彩票型」）由弹窗的 `upside` 表与概率表承担，无需基准即可判读；补一条基准线只会让主图更拥挤。
- 判定逻辑与后端字段**均未改动**（`mean` 仍在 `chartData` 中，仅默认不显示）。
- 验证：`npx tsc --noEmit -p tsconfig.json` → **EXIT=0**；`read_lints` → **0 diagnostics**（过程中把标题里误写的 markdown 星号 `**同口径**` 改为 JSX `<b>`）。
- 版本 1.18.20 → 1.18.21。

## [1.18.20] - 2026-09-11

### Changed
- **0/1 信号的「有效✓」新增第三条门槛：超额（触发组 − 未触发组·日配对）必须 >0**（`components/SingleFactorTestPanel.tsx`）：
  - **动机（重要判据升级）**：中位数为正只说明"典型一次触发是赚的"，但**若整体跑不赢同期未触发组，等于"什么都不选也比它强"**，信号没有实用价值。
  - 新判定（三者**同时**满足）：① 事件研究中位数 ≥0.50%　② 绝对收益胜率 ≥55%　③ **超额 >0**。
  - **反向同理**：`有效(反向)✓` 要求 `超额 <0`（因子反向有效时，触发组本应跑输）。
  - **口径选择**：用 `baseline.excess`（**均值·日配对**，直接对应"整体收益是否跑赢未触发组"），而非 `excess_median`（中位数·事件级）；后者仍在弹窗超额图中作参考。
  - **优雅降级**：无 `baseline`（旧结果 / 基准计算失败）时**不启用第③条**，避免因基准缺失而误伤全部结论；此时「待观察」悬停会注明"超额 无法计算（无基准，未参与判定）"。
  - **悬停原因同步**（`watchReasonOfBinary`）：新增超额项，输出如 `超额 -1.234% ≤0（跑不赢未触发组，差 1.234%）✗`，并在原因汇总里列出「超额为负（跑不赢未触发组）」。
  - 「有效✓」标签悬停、以及表格底部说明均同步写明三条门槛与第③条的动机。
- **事件研究弹窗：主图旁新增「配对日 / 事件」行 + 触发集中度提示**（`components/EventStudyModal.tsx`）：
  - 显示 `配对日 N 天 / 事件 M 个　·　平均每个配对日触发 X.X 个`。
  - 当 `M/N ≥ 3` 时追加琥珀色提示：`⚠ 触发高度集中 —— 事件级均值易被少数密集触发日抬高，请以「日配对」超额为准`。
  - **这正是"事件级均值为正、日配对超额为负"这类现象的判读依据**：两者相差越大，触发在时间上越集中。
- 弹窗内的**结论提示**（`verdictHint`）同步加入超额条件：中位数与胜率都好看但超额为负时，改为 warn 提示「触发组整体跑不赢同期未触发组 —— 收益靠少数「触发密集日」撑起，无横向选股价值，判定为「待观察」」，避免与表格判定矛盾。

### Notes
- 本次为**判定逻辑变更**（非展示层），会使部分原先判「有效」的因子转为「待观察」（尤其**触发高度集中、超额为负**者）。已实测的典型样本「趋势顶底离开底部」（60 日）即属此类。
- 连续因子判定未受影响（仍走 IC/ICIR + 日配对，无超额概念）。
- 验证：`npx tsc --noEmit -p tsconfig.json` → **EXIT=0**；`read_lints` → **0 diagnostics**（过程中曾因 JSX 内 `>0` 未转义、以及误用顶层 `r.baseline`（实际在 `r.event_study.baseline`）各修一次）。
- 版本 1.18.19 → 1.18.20。

## [1.18.19] - 2026-09-11

### Fixed
- **修复事件研究主图「两套口径画在一起」导致误读**（`components/EventStudyModal.tsx`；**仅展示层，判定逻辑未改**）：
  - **现象**（用户报障）：因子「趋势顶底离开底部」/ 2021-01-01~2026-09-01 / 预测 60 天 —— 面板显示 60 日均值 **6.326%**、基准 **3.067%**（看起来正超额 +3.259%），但下方**超额日配对曲线却是负数**，自相矛盾。
  - **根因**：主图两条线**口径不同**，相减无意义 ——
    - `curve[].mean`（红线）= **事件级**（`event_study.py:131` `x.mean()`，每个事件 1 票，易被少数暴涨事件主导）→ 6.326%
    - `baseline`（灰虚线）= **日配对**（`compute_baseline_curves` 先按日取未触发组截面均值、再对配对日平均，每个配对日 1 票）→ 3.067%
    - 而**超额曲线 `excess` = `trigger_pair − baseline`**（同口径）→ **负数才是真实超额** ✓ 两者不矛盾。
    - 前端旧注释「baseline … → 与 mean 同口径」**本身就是错的**（已修正）。
  - **修复**：① 主图**新增 `trigger_pair` 线**（触发组·日配对，紫实线）—— 与 `baseline` 同口径、可直接目视差值，**该字段后端 v1.18.8 就已返回，只是前端未画**；② **修正口径注释**（`chartData` 上方详述两套口径与「只有 trigger_pair 与 baseline 可相减」）；③ 各线图例**明确标注口径**：`均值(事件级·勿直接减基准)` / `中位数(事件级)` / `基准(未触发组·日配对)` / `触发组(日配对·与基准同口径)`；④ 主图标题追加**口径警示**（琥珀色）。
  - **顺带暴露的信号质量问题**（非 bug，仅供判读）：超额为负 + 事件级均值为正 → 说明**触发在时间上高度不均匀**（收益高的那几天恰好密集触发）：事件级口径下那几天贡献几百张「票」把均值抬高，日配对口径下只算 1 张「票」→ 真实（日配对）超额为负。**此类因子的正向表现依赖少数「触发密集日」，实盘难复现。**

### Notes
- 判定逻辑**一行未改**（0/1 信号仍看**事件级中位数 ≥0.5% 且胜率 ≥55%**，与均值/超额无关，故"有效"与"超额为负"可同时成立）。
- 验证：`npx tsc --noEmit -p tsconfig.json` → **EXIT=0**；`read_lints` → **0 diagnostics**。
- 「是否要求超额为正才判有效」属**判定逻辑变更**（会牵连所有因子结论），留待单独一版讨论。
- 版本 1.18.18 → 1.18.19。

## [1.18.18] - 2026-09-11

### Docs
- **§7.6 新增 G 节「单因子测试耗时剖面实测」**（`md/自定义因子与因子库架构.md`；纯文档，无代码改动；新增脚本 `ai_test/profile_sft_e2e.py`）：
  - **方法**：阶段级用 `progress_cb` 打点；桶级复用 `profile_chunk.py` 插桩（包 `load_field_series`/`parse_expr`/`_panel_dyn`/`_roll`/`_ref`/`_union_index`）。⚠ 关键前提：`QLIB_SFT_PANEL_MAX` 默认 1000，**超过即走 `panel_features_parallel`（子进程）→ 插桩失效**，故强制单进程（`MAX=999999`）。
  - **口径**：csi300 → 459 只 / 2021-01-01~2026-06-01 / h=10。
  - **实测**：

| 场景 | 端到端 | 特征加载 | **读盘** | **`_panel_dyn`** | rest(pandas) | `_union_index` |
|---|---|---|---|---|---|---|
| CCCMA250（3.4k 字符） | 10.73s | 9.30s (86.6%) | 2.36s (**22.0%**) | 2.63s (24.5%) | 3.87s (36.0%) | 0.88s (8.2%) |
| CUP_POOL（**279.9k 字符**） | 80.26s | 78.62s (98.0%) | 2.55s (**3.2%**) | **43.61s (54.3%)** | 29.57s (36.8%) | 1.18s (1.5%) |
| CCCMA250（qlib 回退） | 21.92s | 20.71s | — | — | — | — |

  - **① 读盘是「常数项」**：公式大 **82 倍**，`load_field_series` 只 **2.358s→2.550s（+8%）**，占比 22.0% → **3.2%**（其中还含 MultiIndex 构造/reindex，纯 I/O 更小）→ **实证"bin 转 h5"上限只有几个百分点**，与 F 节呼应。
  - **② 真正大头是 `_panel_dyn`**：2.63s → **43.61s（54.3%）**，随公式规模爆炸。CCCMA250 下 2.630s 中 **ops_ext 内核仅 0.020s（0.8%）→ 99.2% 是 Python 调度**；CUP_POOL 下 666 次调用 = **65.5ms/次**。**代码级根因**（`panel_expr.py` `_by_group` 的 `seg_fn` 分支）：虽已用 v1.18.4 的 `*_vec_seg` 一次性内核，但**每次算子调用仍要做全表级转换与包裹**（`to_numpy` → 重建 `ss/se` → 内核 → `asarray/broadcast` → **重建 `pd.Series(59.7万行)`**），666 次 ≈ 3.97 亿行 Series 构造。→ **优化方向：中间结果以 `(arr, idx)` 贯通、只在出口包一次 Series；`ss/se` 按 `n` 缓存。**
  - **③ `rest`（pandas 算术/reindex/组装）稳定占 ~36%**（两场景几乎一致）→ 第二优化目标。
  - **④ 面板比 qlib 回退快 2.0×**（10.73s vs 21.92s）且 **IC/diff 完全一致**（-0.005468 / -0.002988）→ 数值等价、性能更优。另：`parse_expr` 非瓶颈（280k 字符仅 0.566s）；`reconstruct` 已 0 次调用。
  - **优化优先级**：① `_panel_dyn` 全表调度（54.3%）② rest/pandas（36%）③ `_union_index`（8.2%）④ `_ref`/`_roll`（9.2%）⑤ ~~读盘~~（**3.2%，最不值得动**）。
- 版本 1.18.17 → 1.18.18。

## [1.18.17] - 2026-09-11

### Docs
- **新增 §7.6「因子值压缩与量化策略」**（`md/自定义因子与因子库架构.md`，与 loop_code 侧交流后定稿；纯文档，无代码改动）：
  - **① 压缩收益按因子类型分派**（两边实测并列）：**0/1 触发型 4.5~68×**（本仓 `sig01` 非零 46% → lzf **4.48×**；loop_code 二值因子触发 8.29% → gzip9 **68.7×**）｜**连续型 1.2~2.3×**（loop_code 真实因子 lzf 1.21~1.87× / gzip9 1.36~2.33×）｜随机浮点 **1.00×**（双方各自复现）。**触发率越低压缩越好，两数字不矛盾**——不要拿 68.7× 承诺所有 0/1 因子。
  - **② 存储层不硬编码因子类型**：入库记录 `dtype`/`nonzero_ratio`/`binary_ratio`，写入时据此**自适应开关压缩**（稀疏形态开、连续形态不开）。好处：同时服务"公式因子（0/1）+ 自动挖掘（连续）"两类来源，无人需记忆"我这个因子该不该压"。
  - **③ 统一省空间杠杆 = 量化**：**uint8 截面分位（256 级）+ gzip = 5.1~5.7×**，且**保住截面排序语义**（IC 是秩相关，分位等价于排序 → IC/分组/多空近乎无损）；备选 `float16`（2.0× 但丢精度）。纯 codec 对连续因子仅 1.2~2.3× → **量化收益高出一倍以上**。
  - **④ 不把"分箱桶号"当主存储**：不可逆损失（丢组内排序 → 无法重算 IC、改分组数、回归/中性化、试 "Top30 只"）。并显式区分两个层次：**评估展示口径**（分 5 组 / 10 档 / Top10%）是**消费侧参数、与存储无关**；**存储量化**（uint8 分位）才涉存储。本仓 §6.2 写"分 5 组"、loop_code 侧 `decile_stats(n_grp=10)` **不冲突**——诊断留 10 档（判 `D1<D2` 顶档污染）、展示给 5 组，同一份值随便分。
  - **⑤ 稀疏存储只在触发率 <1% 时划算**（反直觉）：loop_code 实测触发率 8.29% 时，**稠密 gzip 0.24MB 比稀疏 `(t,i)` 索引 2.78MB 省 11 倍**（索引要存行号+列号 ≥8 字节/非零位）。
  - **⑥ 误区澄清：「把行情 bin 全转 h5 就能提速」不成立**：bin 的影响只是"文件数多"（32 万个 .bin），**已被治住**（`_field_bin_meta` 只 stat + `_read_field_bin` 进程级 LRU 缓存，v1.17.7 实测 CWH 4 万字符 300 只 **20.5s→2.7s 7.7×**、CCCMA250 200 只 **9.9s→1.9s 5.1×**）。转 h5 只能继续压剩余读盘，**帮不上计算**——而当前大头是计算（巨型公式 4630 算子节点、`_by_group` 逐段调内核、**调度开销是内核 6 倍**）。**行情 bin 与"因子值"是两种数据**：行情 bin 有增量更新优势、不建议全转（正解是"加横截面只读副本并存"）；**因子值本来就该存 h5**（新产生的派生数据，不存在"从 bin 转"）。**真正路径 = M6 模式 B（因子值预计算入库）**：算一次存 h5（单日 0.4ms）→ 训练/测试直接读、不再重算。
  - §7.3 结尾同步指向 §7.6，避免两处结论重复。
- 版本 1.18.16 → 1.18.17。

## [1.18.16] - 2026-09-11

### Docs
- **用本仓库真实数据实测存储选型，据实测修正 §7 的压缩与轴序结论**（新增 `ai_test/bench_factor_store.py`，结果落 `ai_test/bench_factor_store.log`）：
  - **实测口径**：全 A 前 5000 只 / 2021-01-01~2026-06-01 → **1308 日 × 4841 只 = 24.2MB(float32)**；直接读 `data/cn_data` 真实收盘价，并派生 4 种代表性分布（真实价格 / 日收益率 / **0/1 稀疏信号** / 人造随机浮点）；对比 9 种存储方案（npy mmap ×2 轴序、HDF5 连续 ×2 轴序、HDF5 chunk+lzf ×3、Parquet 宽表、Parquet 长表），每项 5 次取中位数。
  - **① 压缩结论被实测修正（重要）**：外部"随机浮点不可压"**只对随机浮点成立**（实测复现 **1.00×**）；而**本仓大量 0/1 因子可压 4.48×**（24.2MB → **5.4MB**），真实价格/收益率仅 1.11×/1.15×。→ 结论改为：**0/1 类因子值得压缩**（且主访问模式"单日读"不受影响，仍 0.0ms）；**连续型因子不必压**。
  - **② 轴序惩罚的量级与规模相关**：外部（50MB/2500 行）`(inst,date)` 取单日 151ms；**本仓（24.2MB/1308 行）只有 3.4~4.0ms**（`(date,inst)` 为 0.0ms）。方向一致，**但幅度随规模放大**（24MB 可基本驻留页缓存）→ 若扩到全历史（6455 日 × 6000 只 ≈ 155MB）会显著恶化，**轴序更必须写对**。
  - **③ chunk 零和再次确认**：`(date,inst)+chunk(1日)+lzf` 单日仍 0.0ms，但 **250 日读从 4.3ms 涨到 40.8ms（~9×）**、单股全史从 3.5ms 涨到 34.6ms（~10×）；镜像方案 `(inst,date)+chunk(50股)` 反之（单股全史 0.0ms、单日 24.5ms）。
  - **④ Parquet 补充观察**：`pq_long` 全面最差（体积 1.95~2.2×、单日读 76~82ms、250 日 130~138ms）；而 `pq_wide`（inst 行 × date 列）+zstd 在 **0/1 数据上仅 2.1MB**（优于 h5+lzf 的 5.4MB，列式对 0/1 极友好），但**列数 = 交易日数 → 不适合长历史**（2500 天即 2500 列）。
  - **⑤ npy mmap 仍是最快**：两种轴序单日均 0.0ms、全量 5.4~16.8ms，印证"**轴序在裸 memmap 里不要命**"。
- 版本 1.18.15 → 1.18.16。

## [1.18.15] - 2026-09-11

### Docs
- **修订 `md/自定义因子与因子库架构.md` §7（因子值存储规范）**。起因：用外部实测数据（5000 股 × 2500 日 × float32，NVMe）逐条核对本仓库引用，发现原稿**有两处写错**，已修正；同时补上"存不存"与"数据库"两块原本缺失的决策。
  - **§7.2 轴序反了**：原文 `data: (instrument × datetime)`，理由写"**横截面读取快**"→ **实际相反**。因子研究的主访问是横截面（取某一天所有股票），要取的是**行**；C-order 下 `(inst, date)` 取某天 = 取**列** = 跨 stride 访问，HDF5 **未分块时退化成整数据集扫描**（实测 `(inst,date)` 单日 **151ms** vs `(date,inst)` **0.4ms**；前者"读 20 天比读全量还慢 8 倍"）。
    - 已改为 **`(datetime × instrument)` 连续、不压缩**；理由改写为**正确表述**：**"轴序在 HDF5 里要命（未分块时），在裸 memmap 里不要命"**——裸 `.npy` memmap 两种轴序都快（按页取数、不做 chunk 对齐），HDF5 可用 `chunk=(1日, n_inst)` 规避（实测单日 0.1ms），但 **chunk 形状是零和的**（单日快了，单股全史从 0.1ms 崩到 286ms）。
  - **§7.3 依赖说反了**：原文"项目**已依赖 h5py**，用 h5 零新增依赖"→ 实际 `backend/requirements.txt` 里 **`pyarrow` 才是正式依赖**，`h5py` / `tables` 是**注释掉的可选项**（`requirements_qlib.txt` 的 `h5py==3.16.0` 位于文件末尾、注释标明仅供 rqalpha h5 读取）→ **用 Parquet 才是零新增依赖**。已改，并补充说明**依赖不该是选型主因**（h5py 装一下就有），推荐 HDF5 的真实理由是**自描述（attrs 带轴/单位/版本/ast_hash）+ 每因子一文件（增量友好、并发隔离）**。
  - **新增「存储前提：先决定存不存/存多少」**：单因子全历史 ≈ **50MB** → 1000 个 = **50GB**、10 万个 = **5TB** → **值只存"入库/活跃子集"（几百~几千）**，其余只留 **AST + 指标 + 签名向量**按需重算（当前 `panel_expr` 实时面板求值已够快）。**格式优先级低于容量与访问模式**。
  - **§7.3 压缩结论修正**：外部"人造随机浮点不可压"（lzf 仅省 3.8%、全量读慢 3.5×）**不能照搬**——本仓因子**大量是 0/1 信号 + 大片 NaN**（未上市/停牌），压缩率会显著更高，**须用真实因子值实测**；且**正确杠杆是量化**（float16 减半 / int16 分位桶 1/4）而非换 codec。
  - **新增 §7.5 数据库选型**（按"**将来上服务器、多人并发写**"定调）：
    - **主库 = PostgreSQL**（元数据/指标/血缘）：`SQLAlchemy 2.0.52` 已是硬依赖 + `alembic 1.19.1` 现成 → 代码侧近零成本；**原生支持多写者**，是服务器多人场景的正解。
    - **因子值仍走文件**（DB 不适合存 50MB 级矩阵，且值可重算、不必强一致）；**实验追踪保持 mlflow**（上服务器后可一并指向 PG）；Redis 仅作缓存/队列/锁；**MongoDB 不用**（pymongo 虽已装但无文档型需求）。
    - **SQLite 明确不可当主库**（有实证）：`backend/app/engine/patches/qlib_parallel.py:115` 注释已记录多任务并发写同一 `mlflow.db` 触发 `database is locked`，故改为线程独立 db；将来 FastAPI 多 worker + 后台任务会**原样重演**。
    - 附**本地→服务器迁移策略**：连接串配置化（本地可先用 SQLite + WAL + 单写连接），换 PG 只改配置 + 跑一次 alembic 迁移；并给出建库表初版建议（`factor` / `factor_version` / `factor_metric` / `factor_value_index` / `lineage`）。
- 版本 1.18.14 → 1.18.15。

## [1.18.14] - 2026-09-11

### Fixed
- **修复事件研究「贡献最大 / 最差事件」榜单的 k 口径错配，并让「未持满 max_k 期但在更短 k 上有效」的事件不再被漏掉**（`factors/event_study.py::build_event_stats`、`components/EventStudyModal.tsx`、`api.ts`）：
  - **问题一（口径错配）**：榜单原先固定用 `mat[:, -1]`（= **max_k 期**）排序，而前端表头写的是「持有 `lastPoint.k` 日收益」（= 当前「最长持有」档位）。于是把「最长持有」滑到 2 时，**表头写「持有 2 日」、表格数值却是 40 期的**——表头与数值口径不一致（该问题早于本次引入）。
  - **问题二（静默漏掉）**：一个触发若在 max_k 期上未平仓（末几期取不到价格），`np.isfinite(last[idx])` 为假会被 `continue` **整个跳过**，**尽管它在 k=1、2 上完全有效**。
  - 修复：新增 `top_by_k` / `worst_by_k`（**逐 k 各出一份榜单**，键为字符串化 k）；前端按当前「最长持有」取对应那份（旧结果无该字段时回退顶层 `top_events`/`worst_events`）。期内最高改用 `np.fmax.accumulate`（逐 k 的"到 j 期为止最高"，忽略 NaN）。
  - 旧字段 `top_events` / `worst_events` 保留（= max_k 期口径），不破坏既有调用方。

### Added
- **事件研究新增「未纳入统计的触发（数据不足）」提示**（避免静默丢弃）：
  - **背景**：把 `end_date` 设到接近数据尾部时，靠近末尾的触发在 `T+1..T+1+max_k` 上拿不到全部收盘价（"到截止日还没平仓"）。这类事件**不进 curve 的对应 k、也不出现在任何榜单里**，界面上完全看不出来，容易被误读为"样本凭空变少"。
  - 后端新增字段：`max_k`（本次实际算到的最长期）、`n_unaligned`（连买入价都取不到）、`n_short`（能买入但有效期数 < max_k，典型"未平仓"）、`short_events`（明细：代码 / 信号日 / 已得期数，缺得最多的在前，最多 200 条）。
  - 前端三处标注：① **摘要卡片**「触发事件数」下方显示「N 个未平仓」（悬停给出 max_k 期下的可统计数、完整说明与指向）；② **概率表**下方提示本次有多少触发在 max_k 期上尚未平仓；③ 页面底部新增琥珀色区块**列明细**，并提示"若数量偏多，说明评估区间末端已接近数据末尾，建议把结束日期提前 max_k 个交易日以上"。
  - `run_event_study` 的错误文案同步区分两种情形；当**全部**触发都未平仓时，明确提示"请把结束日期提前 max_k 个交易日以上，或延后数据"，不再笼统报"行情缺失"。

### Notes
- **实测（全 A / 2026-01-01~2026-08-21 / max_k=40 / `CWH_MIX_D_PCT_R40`，数据尾部 2026-08-21）**：`n_raw=30、n_aligned=26、n_short=1、n_unaligned=0`；明细 `SH600333 2026-08-18 已得 2 期`——正是"信号日离数据末尾只剩 2 个交易日"的场景。该事件现在**会出现在 k=2 的榜单里**（把「最长持有」滑到 2 即可看到，且表头数值口径一致）。
- 另：全部未平仓的极端用例（区间 `2026-06-01~2026-08-21`）原先报"触发事件无法对齐到买入价（行情缺失）"，现改为明确提示"全部 N 个触发在 40 期上均未平仓，请把结束日期提前"。
- 验证：`npx tsc --noEmit` EXIT=0；`read_lints` 0 diagnostics；Python 语法检查通过。
- 版本 1.18.13 → 1.18.14。

## [1.18.13] - 2026-09-11

### Changed
- **区分「绝对收益胜率」与「相对收益胜率」，消除同名混淆**（`components/SingleFactorTestPanel.tsx` / `components/EventStudyModal.tsx`，**纯文案，判定逻辑与所有数值均未变**）：
  - **背景**：界面上两处都叫"胜率"，含义完全不同 —— 差值列的 `daily_win` 是**相对收益胜率**（配对日中「触发组日均 > 未触发组日均」的交易日占比），事件研究的 `curve[].win` 是**绝对收益胜率**（触发事件持有 k 日收益 > 0 的事件占比）。实测同一因子两者可差 3 个点以上（如 `CWH_MIX_D_PCT_R40` 40 日：52.4% vs 55.3%），极易被误读为"数据不一致"。
  - **统一命名**（共 14 处）：
    - **差值列**：悬停 → `相对收益胜率 x.xx%（触发组日均 > 未触发组日配对的交易日占比）`；单元格内联小字 `胜` → `日胜`
    - **结论列悬停**：0/1 判定相关（有效✓ / 彩票型 / 待观察原因）一律写 **`绝对收益胜率 ≥55%`**；「时间集中」属日配对语境，写 **`相对收益胜率≈50%`**
    - **「中位数」列**：悬停与列头提示 → **`绝对收益胜率`**
    - **事件研究弹窗**：摘要卡片、结论提示（3 条）、中位数列悬停 → **`绝对收益胜率`**
    - **导出表头**：`胜率(%)` → **`相对收益胜率(%)`**；页面底部说明同步改为「日均差值 t 值 / 相对收益胜率」
- 版本 1.18.12 → 1.18.13。

## [1.18.12] - 2026-09-10

### Fixed
- **修复「周期填 80 天，事件研究却仍只显示已算 40 期」**（`factors/single_test.py`）：事件研究顺带计算的期数被硬编码为常量 `EVENT_MAX_K = 40` —— `build_event_stats(_px, _ev, EVENT_MAX_K)`、`compute_baseline_curves(..., EVENT_MAX_K)` 与回传前端的 `params.max_k` 全都用它，导致**无论「周期」填多少，事件研究永远只算 40 期**、弹窗永远显示「(已算 40 期)」。而尾部加载长度早已按 `max(horizons, EVENT_MAX_K)` 延展 —— **数据是够的，只是没用上**。
  - 修复：新增 `es_k = max(EVENT_MAX_K, max(horizons))`，`build_event_stats` / `compute_baseline_curves` / `params.max_k` / 尾部加载长度（`n_need`）全部改用它 → **周期填 80 就真算到 80 期**，同时默认口径（周期 ≤40 时）保持 40 期不变。
  - 前端无需改动：`computedMaxK` 读 `params.max_k`，「已算 N 期」与「是否需要重算」自动跟随。

### Added
- **事件研究第一张图新增「基准中位数（未触发组·事件级）」曲线**（`components/EventStudyModal.tsx`）：此前第一张图上 `median`（触发组·**事件级**中位数）与 `baseline`（基准·**均值**口径）**口径不对齐**，二者垂直距离**不能**读作"中位数超额"，容易误判。
  - 现同时给出两条基准线：`基准(未触发组·均值口径)`（灰虚线，与 `mean` 配对）、`基准中位数(未触发组·事件级)`（青点线，与 `median` 配对）；`chartData` 新增 `baseline_median` 字段。
  - 配合 v1.18.11 的第二张图，「**中位数超额上升到底来自触发组走强还是基准下跌**」现在可以一眼看出。

### Notes
- 版本 1.18.11 → 1.18.12。

## [1.18.11] - 2026-09-10

### Added
- **事件研究新增「中位数超额」曲线；两张图均支持点击图例隐藏/重新显示**（`factors/event_study.py::compute_baseline_curves`、`components/EventStudyModal.tsx`、`api.ts`）：
  - **口径**：原超额只有**均值口径**（日配对：先按日取截面均值 → 再对配对日平均），会被少数连板/妖股主导。新增**中位数口径（事件级）**：把配对日上的**全部样本**（日 × 标的）汇成一份取中位数，触发组取全部触发事件的 k 期收益中位数。两者**口径不同、不可混用**，`excess_median = trigger_median − baseline_median`。
  - **后端输出新增** `trigger_median` / `baseline_median` / `excess_median`；`EventStudyBaseline` 类型同步扩展（旧结果无该字段时前端自动不画该线）。
  - **前端**：第二张图同时画 **均值超额（紫实线）** 与 **中位数超额（青虚线）**；标题下注明两口径含义；图下给出口径差值提示——`均值 − 中位数 > 1%` 时提示「超额主要来自少数暴涨事件」，否则提示「两者接近，超额较普遍而非仅靠尾部」；摘要卡片「k=N 超额」同时显示两个口径。
  - **图例交互**：两张图的 `Legend` 均可**点击隐藏/重新显示**对应曲线（`hidden` state + `hide` 属性，光标变手型）。
  - 第一张图的基准线更名为「基准(未触发组·均值口径)」，避免与中位数口径混淆。

### Notes
- **两条计算路径同时生效**：单因子测试顺带计算（`single_test.py`）与独立接口（`run_event_study`）**共用 `compute_baseline_curves`**，因此「重新计算」后的图同样带新曲线（v1.18.10 已先把独立路径接到同一函数上）。
- **冒烟验证**（`ai_test/verify_es_baseline.py`；csi300 / 2023-10-01~2023-12-31 / max_k=20 / `CWH_MIX_D_PCT_R40`）返回字段 `['baseline','baseline_median','excess','excess_median','ks','n_pair_days','trigger_median','trigger_pair']`；同样本两口径对比（k=20）均值超额 `+6.147%` vs 中位数超额 `+6.421%`（该样本仅 1 个事件，故两口径接近；样本充足时通常均值明显更高）。
- 验证：`npx tsc --noEmit` EXIT=0；`read_lints` 0 diagnostics；Python 语法检查通过。
- 版本 1.18.10 → 1.18.11。

## [1.18.10] - 2026-09-10

### Fixed
- **修复事件研究弹窗「重新计算」后基准曲线与超额曲线消失**（`factors/event_study.py::run_event_study`）：v1.18.8 的 `compute_baseline_curves` **只在 `single_test.py` 的「单因子测试顺带计算」路径里被调用**，独立的 `run_event_study` 完全没算基准 → 表格行的结果（顺带算）带 `baseline`，而弹窗点「重新计算」（走 `POST /api/factors/event-study`）拿到的是独立接口结果、**没有 `baseline`** → 前端 `EventStudyModal` 自动隐藏基准灰虚线与超额小图，表现为"一重新计算，基准和超额就没了"。
  - 修复：在 `run_event_study` 的第 6 步（对齐 + 统计）之后新增**第 7 步**——按 `_resolve_instruments` 取全样本代码 → `load_px_wide` 构造全样本宽表 → `compute_baseline_curves(piv, full_piv, ev, max_k, cancel_check=_check)`，口径与顺带计算**完全一致**（日配对：每日剔除当日触发股后取等权均值）；返回值新增 `baseline` 字段。
  - 基准计算仍**单独兜底**（异常时置为 `{"error": ...}`，不拖垮事件研究主结果）；失败时前端自动隐藏该图，与原先表现一致。
  - 进度文案新增 `88% 计算基准（全样本收盘价宽表，N 只）...`。

### Notes
- **冒烟验证**（`ai_test/verify_es_baseline.py`，直调 `run_event_study`；csi300 / 2023-10-01~2023-12-31 / max_k=10 / `CWH_MIX_D_PCT_R40`）：返回 **`baseline 字段存在: True`**、`n_pair_days=1`，并逐 k 给出触发组/基准/超额（k=1 `+0.694% / -0.448% / +1.142%`；k=10 `-0.797% / -1.903% / +1.106%`）→ 字段与计算链路均正常。
- 版本 1.18.9 → 1.18.10。

## [1.18.9] - 2026-09-10

### Changed
- **单因子「待观察」结论现在可直接看到原因**（`SingleFactorTestPanel.tsx`，**仅展示层，判定逻辑一行未改**）：
  - 「待观察」标签新增**悬停原因**（`VerdictStats.watchReason`）：0/1 信号走事件研究路径时逐条对比三档门槛，输出例如
    `结论：待观察（胜率未达标）` / `事件研究（持有 20 交易日，n=743）：中位数 +1.153% ≥0.50% ✓；胜率 54.97% <55%（差 0.03%）✗` / 三档门槛释义 / 「判定使用未舍入原始值」提示；连续因子（或无事件研究结果）列出未通过项（无差值、差值 ≤0、p≥0.05、日配对不显著、测试异常）。标签带**点线下划线** + `cursor-help`，提示可悬停。
  - **悬停胜率显示精度 0.1% → 0.01%**：事件研究悬停（中位数列）`toFixed(1)` → `toFixed(2)`；差值列悬停的日配对胜率 `fmt(..., 1)` → `fmt(..., 2)`。

### Notes
- **起因**：实测 `CWH_MIX_D_PCT_R40` 事件研究中位数 +1.153%（远超 0.50% 门槛）、面板胜率显示 **55%**，却仍判「待观察」。根因是**显示精度 ≠ 判定精度**：判定用后端未舍入的原始值（`_r(v, nd=6)`，如 `win=0.549694`），而悬停只显示 1 位小数（`55.0%`）→ `0.549694 >= 0.55` 为 `false` → 判「待观察」。
- **覆盖面**：`win ∈ [0.5495, 0.55)` 一律显示 "55.0%" 却不通过；同理 `med ∈ [0.00495, 0.005)` 显示 "0.500%" 却失败。本次把显示精度对齐到 0.01%，此类困惑不再出现。
- **刻意未改**：
  - 判定门槛本身（`胜率 ≥55%`、`|中位数| <1%` 均属人为设定、**无统计依据**；严谨做法为二项检验或中位数-胜率联动）——改动会牵连所有因子结论，留待单独一版讨论。
  - 导出 xlsx 的胜率位数、差值单元格内联小字的胜率位数（保持紧凑并与既有导出模板兼容）。
- 版本 1.18.8 → 1.18.9。

## [1.18.8] - 2026-09-10

### Added
- **事件研究新增「基准（未触发组）」与「超额」曲线**（`factors/event_study.py::compute_baseline_curves`、`factors/single_test.py`、前端 `EventStudyModal.tsx` / `api.ts`）：此前只有**绝对收益**，无法回答"这些收益是不是市场本身给的"（2021-2026 区间市场自身涨幅不可忽略）。
  - **口径（日配对）**：对每个触发日 T，取【T 当天未触发的股票】在 T+1..T+1+k 的等权平均收益（剔除当日触发股），再对所有配对日求平均 → 基准；**超额 = 触发组日配对均值 − 基准**（三者同一天集合，自洽）。与单因子测试的 `daily_diff` 同口径。
  - **计算**：复用面板已有的 `PX` 列 → `unstack` 为全样本宽表（约 1300×5000，50MB 量级，**跨因子只构造一次**）→ 宽表 `shift` 逐 k 求前向收益（C 实现，毫秒级）→ 剔除当日触发股后取每日均值。40 期总开销约 3~5 秒。
  - **前端**：主图叠加**灰虚线**＝基准；新增**超额曲线独立小图**（紫色 + 0 轴参考线）；摘要卡片新增「k=N 超额」格；随「最长持有」联动截断。
  - **仅展示、不参与判定**（判定维持 v1.18.7 的事件研究口径，避免同时改动两个变量）。

### Fixed
- **修复 `build_event_stats` 的 `NameError: n_ev`**：重构抽出 `_align_returns` 时误删 `n_ev = len(events)` → 事件研究整体抛异常，且被外层 `except` 静默吞掉（表现为"结果里没有 event_study"）。同时把**基准计算单独兜底**，基准失败不再拖垮事件研究主结果。
- 单因子结果新增诊断字段 `event_study_error`（事件研究失败原因），便于定位此类静默失败。

### Notes
- **全 A 实测**（CCCMA250 / 2021-01-01~2026-06-01 / h=10 / 前复权 / 743 事件 / 398 配对日）：

| k | 触发组（日配对） | 基准（未触发组） | 超额 |
|---|---|---|---|
| 1 | +0.180% | +0.116% | +0.064% |
| 5 | +0.841% | +0.169% | +0.672% |
| 10 | +1.728% | +0.328% | **+1.400%** |
| 20 | +2.199% | +0.214% | +1.986% |
| 40 | +2.595% | +0.561% | +2.034% |

- **结论**：基准极低（k=10 仅 +0.33%）→ 该信号**不是"beta 伪装成 alpha"**，超额真实存在且随持有期单调上升；但中位数仍 ≈0、胜率 ≈50%，超额同样由尾部少数妖股贡献 → **「彩票型」定位不变**。
- 版本 1.18.7 → 1.18.8。

### CI / 发版约定
- **每个版本号递增都打附注 tag**（annotated tag，含 tagger/日期/说明），确立于本版。历史 `v1.0.0` → `v1.18.7` 已一次性补齐，共 **85 个版本 tag**（`v1.18.3` / `v1.18.5` 为连续修改的中间态、无独立提交，故未打）。
- 回退方式：`git fetch --tags` → `git checkout vX.Y.Z`（只查看）或 `git reset --hard vX.Y.Z`（彻底回退）。
- 空间代价可忽略：tag 只是指向已有 commit 的指针，附注 tag 约 200~400 字节/个；本仓库 `.git` 总计约 3.6 MB。
- ⚠ 注意：tag 会令所指向对象**永久可达**，故误提交的大文件一旦被打 tag 就无法靠 `git gc` 回收（需 `filter-repo` 重写历史）。当前 `.gitignore` 已忽略 `backend/workdir/*`（仅保留 `custom_formulas.json`）、`ai_test/`、`workdir/`，训练权重与产物不会入库。
- **历史 tag 说明修复**：`v1.0.0` 的 message 因早期用 `git tag -m` 传中文而损坏（UTF-8 字节被按 GBK 解码，显示为 `棣栦釜姝ｅ紡鐗?`）。已按原始字节反推还原原文并以 UTF-8 重建 tag 对象（**保留原 tagger 与时间戳，指向的 commit 不变**，对象 `4d040ee` → `2b81aab`，已 force 覆盖远程）。`v1.0.1` / `v1.0.2` 经查原文即纯英文，无需处理。
- 教训：**tag / commit 的 message 一律用 `-F <UTF-8 文件>` 写入，不要用 `-m` 传中文**（Windows 下会按系统代码页编码）。

## [1.18.7] - 2026-09-10

### Added
- **单因子诊断新增「事件研究」**（新增 `backend/app/factors/event_study.py`；`routers/factors.py`；前端 `components/EventStudyModal.tsx` / `SingleFactorTestPanel.tsx` / `api.ts`）：稀疏 0/1 信号的「按日配对检验」在触发日只有 1 只票时会**退化成单票收益序列**（实测 CCCMA250 / 全 A：每日触发只数中位数 = 1，Top20 天贡献日差净和的 100.1%，剔除 Top20 后剩余日均 -0.002%），此时均值与显著性均不可信。事件研究改以**每次触发**为样本单位，给出概率/赔率画像。
  - 后端 `run_event_study(...)`：复用 `_load_feature_panel` 与 `_test_one` 的触发分组/剔除口径（T 涨停、T+1 涨停、T+1 停牌、ST、创业板、科创板），再取触发标的收盘价序列，按 **T+1 收盘买入、T+1+k 收盘卖出**对齐，输出 `curve`（各 k 的均值/中位数/胜率/p10/p25/p75/p90/最大/最小）、`prob`（收益 >0 / >10% / >20% / >50% / >100% 的事件占比）、`upside`（"期内最高点卖出"的赔率上限 + 曾达 +20%/+50%/+100% 比例 + 下限参考）、`top_events` / `worst_events` 明细。
  - 接口：`POST /api/factors/event-study`（异步提交，与回测/训练共用并发配额）、`GET /factors/event-study/progress/{task_id}`、`POST /factors/event-study/cancel/{task_id}`、`POST /factors/event-study/clear`。价格口径统一走 `adjust_expr("$close")`，与 label 的收益口径一致（forward/backward 用后复权价、none 用真实价，可选按分取整）。
  - 前端：单因子测试结果表新增「事件研究」列（仅 0/1 信号可点），弹窗用 recharts 展示持有期收益曲线（均值/中位数 + p25~p75 分位线）、目标收益概率表、赔率卡片与贡献最大/最差事件明细；支持自定义最长持有期（1~120 交易日）、重新计算与取消。
- **事件研究并入单因子测试（免二次计算）**：此前点「事件研究」会**再跑一个独立任务并重新加载一遍全量面板**（全 A 5.4 年实测 56s，其中九成是重复加载）。现改为在单因子测试内顺带完成：
  - 面板新增 `PX` 列（`adjust_expr("$close")`，与 label 收益口径一致）；尾部加载区间由 `end + max(h) + 3` 延到 `end + max(h, 40) + 3` 个交易日（**统计区间不变** —— 默认 `freeze_suspended_price=True` 时面板在统计前已裁回 `[start_date, end_date]`）
  - `_test_one` 新增 `trig_index_out` 出参回传触发样本索引；`run_single_factor_tests` 对 `is_binary` 结果**顺带调用** `build_event_stats`（同因子的不同周期复用价格宽表），把 `event_study` 直接挂在结果行上 → **不新增任务、用户零额外操作**
  - `event_study.py` 抽出可复用的 `load_px_wide` / `build_event_stats`（独立接口 `POST /factors/event-study` 保留，用于改最长持有期重算）
  - 前端：结果表新增 **「中位数」列**（事件研究中位数，悬停看均值/胜率/样本数）；点「事件研究」**直接展示已有结果（秒开，不再发请求）**；**0/1 信号的「结论」改为以事件研究为准** —— 中位数 ≥0.5% 且胜率 ≥55% → 有效✓；`|中位数| <1%` 且胜率 45%~55% 且均值显著更高 → 新增 **「彩票型」** 标签；中位数 ≤-0.5% 且胜率 ≤45% → 有效(反向)✓；连续因子仍走 IC/分位判定；导出表同步加列

### Changed
- **单因子测试「剔除 ST(T+1)」改为默认勾选**（前端 `SingleFactorTestPanel.tsx`）：ST/*ST/退市整理期个股的收益分布与正常股差异极大，评估触发型信号时应默认排除；创业板/科创板仍默认关。需本机数据含 `is_st` 标签（缺失时后端会明确报错并提示取消勾选）。事件研究的剔除口径随面板参数自动跟随。

### Notes
- **口径自检**：CCCMA250 / 全 A / 2021-01-01~2026-06-01 / h=10 / 前复权 → `n_events = 743`（剔除前 823），**k=10 均值 +1.6469% 与单因子测试「触发收益均值」逐位一致**；k=40 均值 +3.331%、中位数 +0.213%、胜率 50.7%。
- **实测结论（743 次触发）**：胜率全程 44.5%~52.8%、中位数长期 ≈0（k=40 仅 +0.213%，与均值差 16 倍）→ 典型「多数白干、少数暴赚」；按"期内最高点卖出"的理想口径，曾达 +50% 仅 **5.8%**、+100% 仅 0.7%，同期最低点中位数 -7.8%。故该信号统计上显著但**不可作为稳定 alpha**（触发稀疏、无法组合分散）。
- 辅助脚本（本地 `ai_test/`，不上传）：`ccma_event_study.py`（离线版事件研究）、`test_event_study.py`（函数级验证）、`test_event_study_api.py`（API 端到端验证）。
- 版本 1.18.6 → 1.18.7。

## [1.18.6] - 2026-09-10

### Added
- **单因子诊断新增「特征加载预热缓冲」**（`panel_expr.py` / `single_test.py` / `routers/factors.py` / 前端 `SingleFactorTestPanel.tsx`）：长回看 / 动态窗口公式（`DYN_*`、`BARSCOUNT`、`HHVBARS+Ref` 嵌套等）的真实扩展天数**无法静态推断**（`_tree_ext_days` 只覆盖固定窗口），此前面板只前移「可推断量」→ 评估区间首日因子 NaN / 未收敛。物证（CCCMA250 不复权）：SZ002414 2021-01-04 因子 NaN、区间首日全市场「因子=1」股票数 = 0、因子覆盖率 71.5%→88% 爬坡。
  - 新参数 `warmup_days`（交易日）：请求体新增可选字段，前端新增输入框（默认 250 ≈ 1 年，`0` = 关闭，留空 = 取服务端默认）；服务端默认取环境变量 `QLIB_SFT_WARMUP_DAYS=250`。
  - 面板路径：普通组 `read_start = 可推断预热 + warmup_days`、起点敏感组 `精确扩展 + warmup_days`；`panel_features` / `panel_features_parallel` 新增入参，`_panel_features_chunk` 改收 5 元组 payload（向后兼容 4 元组）。
  - qlib 回退路径：`D.features(start_time=load_start)` 从 `load_start`（按交易日历回退 `warmup_days` 天）起加载，出口统一裁剪回 `[start_date, ...]`（对面板路径幂等；裁剪失败 / 裁空直接报错，不静默保留未裁剪面板）。
  - **语义边界**：输出仍只覆盖 `[start_date, end_date]`，预热行绝不进入统计；固定窗口算子（`Mean`/`Max`/`Ref`…）结果与关闭预热时**逐位相同**；`EMA`/状态类算子序列起点提前（值更接近长历史真值，不再复刻 qlib 在 `start_date` 的冷启动）。

### Notes
- 端到端验证：CCCMA250 / 全 A / 2021-01-04~2026-06-01 / h=10 / 不复权 → 触发 **627 → 628**（与「`start_date` 提前到 2019-07-01」的旧实证 628 一致）；`QLIB_SFT_WARMUP_DAYS=0` 复现旧口径 627。
- **SR 自带 250 天前扩（口径澄清）**：默认 `suspend_remove=True` 时表达式叶子被 `SR(...)` 包装，而 `SR.get_extended_window_size() = 子 ext + 250` → qlib `D.features` 本身就会无条件前读 ≥250 交易日；故默认口径下再叠加 `warmup_days`，对 `EMA` 这类 ~50 天收敛的算子属**冗余**（`ai_test/verify_warmup_fallback.py`：SR 口径 0/3/200 三档首日均值逐位相同；关掉 SR 后 0 vs 3 出现差异）。预热真正解决的是**面板侧动态窗口公式**（扩展量静态不可知）。
- 验证：`ai_test/verify_warmup_fallback.py` ALL PASS（回退路径预热生效 + 三档行数一致 → 出口裁剪无损、无预热行泄漏）；`pytest tests/test_panel_expr.py -m datareq` **8 全绿**（含新增 `test_panel_warmup_days`：输出日期轴恒等于评估区间交易日、`MA5` 逐位不变、`BARSCOUNT` 首日均值随预热变大）；前端 `tsc -b` 通过。
- 版本 1.18.5 → 1.18.6。

## [1.18.5] - 2026-09-10

### Fixed
- **修复 `DYN_HHVBARS` / `DYN_LLVBARS` 方向反转（回归 bug，影响所有依赖它的公式）**（`ops_ext.py`）：`_dyn_best_idx` 里 `better` 布尔语义写反 —— `is_max` 分支用 `better = (av > bv)`（字面含义是"a 更优"）却被直接当作"b 胜"返回，导致动态窗口极值取到**反面**（`is_max` 时返回窗口最小值、`is_min` 时返回最大值）。
  - **影响面**：`DYN_HHVBARS`/`DYN_LLVBARS` 及一切依赖它们的自定义公式（如 `CCCMA250`：报出「旧版 500~700 触发信号 → 现版仅 51」）。用户实测 `CCCMA250` 全 A / 2021-01-01~2026-01-01 / 周期 10 / 前复权，修复后触发信号 **45 → 694**，回到旧版区间。
  - **根因溯源**：v1.17.7（`e6a3cb5`）把 `dyn_bars_vec` 由"逐位 while 扫描"重写为稀疏表 RMQ 时引入；v1.17.3 旧实现用 `np.fmax/np.fmin` 语义正确。面板侧与 qlib 侧共用同一函数，故内外对账不暴露。
  - **修复**：`b_better = (bv > av) | (np.isnan(av) & ~np.isnan(bv))`（`is_max`；`is_min` 用 `<`），`b_wins = b_ok & ((~a_ok) | b_better | (tie & (bi > ai)))`。三处调用共用同一函数，改一处即可。
  - **验证**：新增交叉对拍 `ai_test/test_dyn_semantics.py`（动态窗口 vs 固定窗口 `HHVBARS._bars` 两套独立实现）修复后 24/24 通过；`pytest test_ops_ext_vec.py test_panel_expr.py` 41 全绿。
- 版本 1.18.4 → 1.18.5。

## [1.18.4] - 2026-09-10

### Changed
- **面板巨型公式性能大修（纯优化，数值逐位不变）**：600 股 × 8 公式（含 CUP_POOL 27.9 万字符）× 2021 全年快照墙钟 **68.57s → 34.30s（-50%，2.0×）**，18 列全部 `nan_mismatch=0 / maxabs=0 / exact=True`、形状恒为 `(144306, 18)`。四项改动（`panel_expr.py` / `ops_ext.py`）：
  - ① **`_by_group` 段感知向量化**（本版最大收益）：原实现"逐段（每只股票一段）调用单段内核"，每段仅 ~200 行却要 ~10 次 numpy 调用，**小数组下调度开销远大于计算本身**；CUP_POOL 一次求值上百个动态节点 × 数百只股票 = 数万次内核调用（微基准：`dyn_ref_vec` 单次 `_by_group` 7.19ms 中内核 5.89ms / 逐段循环 1.09ms / 组界 0.04ms）。现改为**把各段拼成一个长数组 + 段起止数组（`seg_start`/`seg_end`）一次性处理全部段**；`ops_ext.py` 新增 `*_seg` 内核族（`seg_start_arr`/`seg_end_arr`/`bars_count_seg`/`barslast_vec_seg`/`barsince_vec_seg`/`barsincen_vec_seg`/`filter_vec_seg`/`dyn_ref_vec_seg`/`_dyn_rmq_vec_seg`/`dyn_bars_vec_seg`），并为固定窗口 `HHVBARS/LLVBARS` 单独新增 `hhvbars_seg`/`llvbars_seg`
  - ② **`_segment_bnd`（组界）**：由 `idx.get_level_values(0).to_numpy()`（object 字符串数组做逐元素富比较，93000 行 ≈ 60ms，而一次 CUP_POOL 求值要调用上百次 → 累计 8s+）改为 `MultiIndex.codes[0]` 整数比较（≈0.043ms，**48×**）；`_by_group` 与 `_pair_seg_corr` 共用
  - ③ **`_union_index` 只 stat 不读整列**：新增 `_field_bin_meta`（读 4 字节头部 + `st_size // 4 - 1` 推 `n_rows`，按 `st_mtime_ns` 缓存）替代整列 `_read_field_bin` 读盘
  - ④ **`parse_expr` 零拷贝词法 + AST 结构去重（hash-cons）**：词法由 `regex.match(text[pos:])` 改为 `regex.match(text, pos)`（消除每 token 一次字符串切片拷贝）；`Node` 增加"结构规范 int key"（`_CANON` 表 + `__slots__`），求值缓存 key 与 `reconstruct` 字符串重建改为 O(1) 属性读取（CUP_POOL 原 `reconstruct` 递归 262 万次 / 343M 字符，占单块 15.4%）

### Fixed
- `_field_bin_meta` 头部 dtype：`.day.bin` 首 4 字节是**以 float32 存的**起始日历 idx（`_read_field_bin` 用 `np.fromfile(dtype="<f4")` + `int(arr[0])`），首版误用 `"<i4"` 读成 float32 位模式（真实 791 → `0x44480000` = 1145421824），使 `_union_index` 把绝大多数股票判为"无覆盖"而丢行 —— **输出 144306 → 64895 行**（只有 `start == 0` 的老股因 float32 0.0 位模式也是 0 而碰巧正确）。

### Notes
- 段感知内核的**逐位等价**由双重兜底保证：`ai_test/test_seg_kernels.py` 单测（随机 2 万行 / 135 段，含 NaN、0、大量并列值、段长 1，N ∈ {1,2,3,8,60}，12 + 7 + 30 项全绿）＋ 面板快照对拍（18 列 exact）。
- **有意放弃两处段感知**（为保位一致）：`DYN_SUM`/`DYN_COUNT` 内部是 `cumsum` 前缀和相减，全局前缀和与逐段前缀和的**浮点累加顺序不同** → 差 1 ULP（0.9 vs 0.9000000000000004），无法逐位一致；`_dyn_kernel_seg` 对 sum/count 返回 `None`，`_by_group` 回退逐段循环（这两个内核本身很轻，收益损失可接受）。
- **发现既有语义不一致（本版未改，待决策）**：`ops_ext._dyn_best_idx` 的 `better` 判定方向写反（`is_max` 时 `better = (av > bv)`，但该布尔被直接当作"b 胜"使用），导致 `DYN_HHVBARS`/`DYN_LLVBARS` 实际返回的是**窗口内极值的反面**（实测窗口 `[nan, -0.4, 1.8, -0.4]` 在 i=3 时 `HHVBARS._bars` = 1.0（j=2，正确）而 `dyn_bars_vec(is_max=True)` = 0.0（j=3，取到最小值））。面板侧与 qlib 侧共用同一函数，故两端对账不暴露。固定窗口 `HHVBARS/LLVBARS` 走 `_bars` 单调队列（正确），因此本版段感知为它单独实现 `hhvbars_seg`/`llvbars_seg`，**不**复用 `dyn_bars_vec_seg`。
- 验证：`pytest tests/test_panel_expr.py tests/test_ops_ext_vec.py tests/test_formula_parser.py tests/test_segments.py` **80 全绿**；`tests/test_golden_regression.py tests/test_limits.py` **23 全绿**；面板快照 18 列逐位一致。
- 版本 1.18.3 → 1.18.4（后端 `backend/app/__init__.py` / README 顶部）。

## [1.18.3] - 2026-09-10

### Changed
- **面板节点缓存上限改为「按可用内存自适应两档」**（v1.18.1 的 512MB 固定默认 → 512MB/1024MB 自动选择；`panel_expr.py`）：
  - 新增 `_auto_node_cache_mb(n_jobs)`：显式 `QLIB_SFT_PANEL_NODE_CACHE_MB` 优先（可指定 2048 等任意值）；否则 可用内存 ≥10GB **且** 每 worker ≥2GB → **1024MB**，其余 → **512MB**
  - 新增 `set_node_cache_mb(mb)`；`_worker_init` 增加 `node_cache_mb` 入参 —— 档位在 `panel_features_parallel` 内先作用于本进程（`n≤1000` 的单进程分支同样生效），再随 initargs 注入 worker（比依赖 spawn 继承 env 更可靠）
- 两档划分依据（本次对照实验；全 A 5312 只 × 2024 全年 × 7 worker × CUP_POOL 27.9 万字符）：**512MB = 52.4s / worker 峰值合计 5.95GB**、**1024MB = 46.5s / 9.56GB（-11%）**、2048MB = 39.8s / 16.73GB（-24%），三档输出数值**完全一致**（校验和恒等）；常规/中型公式（≤5 万字符：CCCMA250 峰值 176MB、CWH 5.3 万字符峰值 241MB）缓存从不触顶、两档完全等价 —— 故只取两档，兼顾大内存机提速与小内存机安全（需要 2048MB 可显式设 env 自担内存）
- 验证：不设任何 env 端到端 —— 全 A 并行**自动取 1024MB**（47.5s / worker 峰值 9.55GB / chk=5594，与手工 1024 档 46.5s / 9.56GB 吻合）；`n=500` 单进程分支亦取 1024MB；panel/ops 回归 **48 全绿**
- 复现脚本（临时，`ai_test/`）：`bench_node_cache.py`（单进程 patch `PanelEvaluator.eval` 统计命中/未命中 + 缓存峰值）、`bench_node_cache_parallel.py`（真实 `panel_features_parallel` + worker 峰值 RSS 采样）
- 版本 1.18.2 → 1.18.3（后端 `backend/app/__init__.py` / README 顶部）。

## [1.18.2] - 2026-09-10

### Fixed
- **面板并行求值「取消后内存不释放」根治**（全 A 多公式场景用户实证：点取消后 worker 仍 100% CPU + 峰值内存驻留 **7 分钟不减**、系统可用内存跌破 1GB；`panel_expr.py`）：
  - 原取消路径（异常分支）仅 `shutdown(wait=False, cancel_futures=True)`——只能停主进程收块/取消**未派发**任务，**已派发给 worker 的大块无法中途取消**，worker 跑完当前块才退出（全 A 大公式单块可跑数分钟）
  - 新增 `_force_terminate_executor()`：取消/异常分支先 terminate 进程池全部 worker（executor 私有 `_processes`），OS **立即回收 worker 内存**；executor 随即不再使用（调用方已中止），安全。正常求值路径零改动
  - 实测（全 A 5854 只 × 趋势顶底大公式 × 2 字段、4 worker）：取消瞬间 n=4 rss=1043MB 在跑 → 异常抛出后 worker 全灭、内存全释放（<0.3s 确认）；修复前同场景 7 分钟不减。panel/ops 回归 41 全绿 + 非数据单测 168 全绿
- 版本 1.18.1 → 1.18.2（后端 `backend/app/__init__.py` / README 顶部）。

## [1.18.1] - 2026-09-10

### Fixed
- **多公式 × 并行面板求值的内存放大根治**（用户全 A 极端压测实证：批内 6 公式含 CUP_POOL 24.6 万字符，求值早期内存仅 ~23%、随后爬升至 90%+ 触发系统清理；`panel_expr.py`）：
  - **节点缓存 LRU 封顶**：`_node_cache` 由 dict 改 `OrderedDict` 并按 Series 值字节记账，默认上限 **512MB/worker**（`QLIB_SFT_PANEL_NODE_CACHE_MB` 可调），写满从最旧 `popitem(last=False)` 逐出——超巨型公式（单树数千节点 × 每节点 ~MB）不再把单 worker 撑到数 GB；命中 `move_to_end` 刷新，普通公式近端命中不受影响
  - **节点缓存按表达式释放**：`eval_expr` 开头清空——多公式共用同一 evaluator 时中间节点缓存不再跨公式线性累积（N 公式 × 全树 → 仅当前 1 公式 × 全树）；表达式内公共子式缓存收益完整保留，字段/读盘走 `_field_cache` / 读盘 LRU 不受影响，对求值数值零影响
  - **并行 worker 数内存自适应**：`panel_features_parallel` 由单一 CPU 维度（每块 ≥300 只、≤12 worker）改为 **CPU 维度 ∩ 内存维度**——可用内存 ÷ 单 worker 预算（`QLIB_SFT_PANEL_MEM_PER_JOB_GB` 默认 3GB）取整；可用内存低于低水位（`QLIB_SFT_PANEL_LOW_MEM_GB` 默认 6GB）再收敛到 ≤ 可用/2；`QLIB_SFT_PANEL_JOBS` 显式指定则完全覆盖
  - **每 worker 读盘缓存预算随内存均分**：新增 `set_bin_cache_mb()` 并注入 `_worker_init`——显式 `QLIB_PANEL_BIN_CACHE_MB` 优先，否则各 worker ≈ 可用内存 ×40% ÷ worker 数（夹在 64~768MB），消除 "12 worker × 768MB" 的数 GB 缓存驻留
- 实测（本机）：CUP_POOL 120 只两年单进程 **峰值 RSS 722MB**（封顶前数 GB）；2000 只 × 5 表达式并行 **峰值 RSS 合计 2.25GB**（旧估算 >6GB）；panel/ops 回归 41 全绿
- 性能说明：求值速度**无回退**（单进程 profile 与 v1.17.7 持平或更快：CWH_BREAK_WAIT20_F1 300 只 ×2024-2025 warm ~3.5s）；内存维度仅在机器可用内存紧张时收敛并发以换取不 OOM，速度随并发线性缩减，可经 env 放宽
- 版本 1.18.0 → 1.18.1（后端 `backend/app/__init__.py` / README 顶部）。

## [1.18.0] - 2026-09-10

### Added
- **Alpha158 缺失的 8 个窗口算子接入面板**（`panel_expr.py`），至此 Alpha158/Alpha360 目录全部算子均走面板，不再因"算子缺失"整批退化 qlib：
  - `IdxMax` / `IdxMin` / `Quantile` / `Rank`：逐股复刻 qlib Rolling 语义（rolling/expanding，min_periods=1；Rank = pandas `rolling.rank(pct=True)`，旧 pandas 回退 percentileofscore）
  - `Slope` / `Rsquare` / `Resi`：**直接复用 qlib `_libs` Cython** `rolling_*` / `expanding_*` 内核；Rsquare 附"窗口 std≈0（atol 2e-5）置 NaN"（同 qlib）
  - `Corr(X,Y,N)`：新增 `_corr_pair_panel` / `_pair_seg_corr` 逐段双序列 rolling corr + 任一侧 std≈0 置 NaN（复刻 qlib PairRolling 行为）
- `_warm_days` / `_tree_ext_days` 扩展窗口识别（纳入新算子，Corr 取第三参 N）；这些算子是固定窗口 → normal warm 组，无起点敏感问题
- 验证：60 只 csi300 真实数据 15 列对拍 **13 列严格 0 差**、`Corr` 差 ≤5.5e-6（qlib 中间 float32 vs 面板 float64 的 ulp 级差）；panel/ops 回归 41 全绿
- 版本 1.17.9 → 1.18.0（后端 `backend/app/__init__.py` / README 顶部）。

## [1.17.9] - 2026-09-10

### Added
- **面板补齐 5 个此前会整批退化到 qlib 的算子**（`ops_ext.py` + `panel_expr.py`）：
  - `BARSCOUNT` / `BARSSINCE` / `FILTER`：抽取纯函数 `barsince_vec` / `filter_vec` 供 qlib 与面板共用同一数值源，面板经 `_by_group` 逐组调用
  - `TRUNC` / `BETWEEN`：逐元素算子（np.trunc / 区间含边界，NaN 语义对齐）
- **起点敏感状态算子精确分组**：修复"统一 warm 组多读历史"导致的系统性偏移（csi300 对拍 BARSCOUNT 差 32 / BARSSINCE 差 37 / FILTER 差 1）——BARSCOUNT/BARSSINCE/FILTER 按其 qlib 自身子树扩展（可为 0）进"精确起点"组（与 EMA 敏感组同机制）；分类重构对既有算子完全等价
- 目录算子覆盖扫描：**alpha360 全支持**；Alpha158 仍缺 8 个窗口类算子（Corr/IdxMax/IdxMin/Quantile/Rank/Resi/Rsquare/Slope），列入待办
- 验证：60 只 csi300 真实数据 panel vs qlib 逐位 0 差；panel/ops 回归 41 全绿
- 版本 1.17.8 → 1.17.9（后端 `backend/app/__init__.py` / README 顶部）。

## [1.17.8] - 2026-09-10

### Fixed
- **单因子测试"配对日"极稀疏场景口径**：触发样本极少（如 CWH 全 A 2022-2023 两年仅 1 个触发 → 配对日仅 1 天）时，`daily_diff`/`daily_n` 原只在配对日 ≥2 才输出，界面呈现"触发组日均非空但配对日数 0"的矛盾观感。改为**配对日 ≥1 即填真实值**（日差 = 当日触发组−未触发组日截面均值差，与两组日均严格自洽：`0.100491−(−0.033058)=0.133549`）；t/胜率/HAC 等统计推断仍要求 ≥2（单配对日无统计意义）。前端差值子行按是否可算显示，无配对日时悬停说明"无同日样本不计算"（`single_test.py` / `SingleFactorTestPanel.tsx`）
- **面板求值期间取消不可用**（多因子×多周期大公式点取消后卡在 loading，公司实证）：
  - `panel_expr.panel_features` / `panel_features_parallel` 新增 `cancel_cb` 取消检查点（表达式级 / 块级）；并行收块由 `as_completed` 阻塞改为 **0.25s 轮询**，取消命中后 `shutdown(wait=False, cancel_futures=True)` **立即返回**（原 with 退出会等在跑块）
  - `single_test._load_feature_panel` 两处 `except Exception` 前补 `except FactorTestCancelled: raise`（否则取消被吞成"回退 qlib"继续算）；qlib 回退批量循环逐批检查
  - 实测：全 A 5446 只 CCCMA250 面板并行求值中取消 **2.08s 生效**（原需等整批块算完）；排队中取消 1.35s
  - 局限（已知）：已在跑的 worker 块无法 kill（取消后跑完当前块自行退出）；单条巨型表达式内部仍为表达式/块粒度检查
- 版本 1.17.7 → 1.17.8（后端 `backend/app/__init__.py` / README 顶部）。

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
