import axios from 'axios'
import type {
  BacktestRequest,
  BacktestTask,
  TaskIdResponse,
  DataSourceInfo,
  ModelArtifacts,
  BacktestResult,
  BacktestPartialResult,
  FactorCatalog,
} from './types'

// 通过 Vite 代理转发到后端，无需写死后端地址
const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ---------- 回测 ----------

export async function submitBacktest(req: BacktestRequest): Promise<TaskIdResponse> {
  const { data } = await http.post<TaskIdResponse>('/backtest', req)
  return data
}

export async function getBacktestTask(taskId: string): Promise<BacktestTask> {
  const { data } = await http.get<BacktestTask>(`/backtest/${taskId}`)
  return data
}

export async function listBacktests(): Promise<Record<string, BacktestTask>> {
  const { data } = await http.get<Record<string, BacktestTask>>('/backtests')
  return data
}

// 历史回测（从 artifacts 目录扫描，跨重启/跨版本可见）
export interface HistoryItem {
  task_id: string
  dir_name: string
  seq?: number | null
  has_params: boolean
  has_result: boolean
  has_meta: boolean
  has_artifacts: boolean
  images: Record<string, string>
  segments: string[]
  is_task_running?: boolean
  meta_summary?: {
    model?: string
    universe?: string
    start_year?: string
    end_year?: string
  }
  annual_return?: number | null // 年化收益（result.json 的 annualized_return），无结果时为 null
}
export async function listBacktestsHistory(): Promise<{ items: HistoryItem[] }> {
  const { data } = await http.get<{ items: HistoryItem[] }>('/backtests/history')
  return data
}

// 版本号
export interface AppVersion {
  version: string
}
export async function getAppVersion(): Promise<AppVersion> {
  const { data } = await http.get<AppVersion>('/version')
  return data
}

export async function getBacktestArtifacts(taskId: string): Promise<ModelArtifacts> {
  const { data } = await http.get<ModelArtifacts>(`/backtest/${taskId}/artifacts`)
  return data
}

export interface BacktestSnapshot {
  task_id: string
  dir_name?: string
  params?: BacktestRequest | null
  meta?: Record<string, string> | null
  images?: Record<string, string>
  segments?: string[]
}

export async function getBacktestSnapshot(taskId: string): Promise<BacktestSnapshot> {
  const { data } = await http.get<BacktestSnapshot>(`/backtest/${taskId}/snapshot`)
  return data
}

export function getBacktestImageUrl(taskId: string, name: string): string {
  return `/api/backtest/${taskId}/image/${name}`
}

export async function cancelBacktest(taskId: string): Promise<{ status: string }> {
  const { data } = await http.post<{ status: string }>(`/backtest/${taskId}/cancel`)
  return data
}

export async function resumeBacktest(taskId: string): Promise<TaskIdResponse> {
  const { data } = await http.post<TaskIdResponse>(`/backtest/${taskId}/resume`)
  return data
}

export async function deleteBacktest(taskId: string): Promise<{ status: string }> {
  const { data } = await http.delete<{ status: string }>(`/backtest/${taskId}`)
  return data
}

export async function getBacktestResult(taskId: string): Promise<BacktestResult> {
  const { data } = await http.get<BacktestResult>(`/backtest/${taskId}/result`)
  return data
}

// 滚动回测已跑段的部分结果（partial_result.json）：任务未完成但跑过若干段时可用
export async function getBacktestPartial(taskId: string): Promise<BacktestPartialResult> {
  const { data } = await http.get<BacktestPartialResult>(`/backtest/${taskId}/partial`)
  return data
}

// ---------- 并发能力 ----------

export interface ResourceSummary {
  cpu_logical: number
  memory_total_gb: number
  memory_available_gb: number
  task_mem_gb: number
  max_concurrent: number
  estimated_total_mem_for_max_gb: number
  memory_headroom_ratio: number
}

export interface BacktestCapacity {
  max_concurrent: number
  running: number
  queued: number
  available: number
  resource: ResourceSummary
}

export async function getBacktestCapacity(): Promise<BacktestCapacity> {
  const { data } = await http.get<BacktestCapacity>('/backtest/capacity')
  return data
}

// ---------- 因子库 ----------

export async function getFactorCatalog(dataset = 'Alpha158'): Promise<FactorCatalog> {
  const { data } = await http.get<FactorCatalog>('/factors/catalog', {
    params: { dataset },
  })
  return data
}

// ---------- 自定义公式持久化（后端 workdir/custom_formulas.json） ----------

export interface CustomFormula {
  id: string
  name: string
  text: string // 用户原文公式（前端原样显示）
  expression: string // 编译后的 qlib 表达式（回测用，前端默认不展示）
  created_at: string
  updated_at: string
}
export async function listCustomFormulas(): Promise<{ items: CustomFormula[] }> {
  const { data } = await http.get<{ items: CustomFormula[] }>('/factors/custom-formulas')
  return data
}
export async function createCustomFormula(formula: string): Promise<CustomFormula> {
  const { data } = await http.post<CustomFormula>('/factors/custom-formulas', { formula })
  return data
}
export async function updateCustomFormula(id: string, formula: string): Promise<CustomFormula> {
  const { data } = await http.put<CustomFormula>(`/factors/custom-formulas/${id}`, { formula })
  return data
}
export async function deleteCustomFormula(id: string): Promise<void> {
  await http.delete(`/factors/custom-formulas/${id}`)
}

// ---------- 单因子测试（不训练模型，快速诊断因子预测力） ----------

export interface SingleFactorTestItem {
  id: string
  name: string
  expression: string
  source: string // custom / alpha158 / alpha360
  source_formula?: string // 用户原文公式（custom=保存原文；目录因子=表达式本身），仅展示用
}
export interface SingleFactorTestRequest {
  universe: string
  start_date: string
  end_date: string
  label_horizon: number
  label_horizons?: number[] // 批量预测周期（如 [1,2,3,5,10,15,20]），传了则忽略 label_horizon
  parallel?: boolean // 并行测试：多个周期各占一个共享并发单元同时跑（与回测/训练共用配额）
  factors: SingleFactorTestItem[]
  exclude_limit_up_signal?: boolean // 剔除信号日(T)涨停
  exclude_limit_up_trade?: boolean // 剔除成交日(T+1)涨停
  exclude_suspended?: boolean // 剔除成交日(T+1)停牌/无行情
  exclude_st_t1?: boolean // 剔除成交日(T+1)处于 ST/*ST/退市整理 的样本（日截面，T+1 当日状态）
  exclude_stock_gem?: boolean // 剔除创业板（SZ30 段）
  exclude_stock_kcb?: boolean // 剔除科创板（SH688）
  freeze_suspended_price?: boolean // 停牌日价格冻结计入未来收益（对齐聚宽口径 B，默认开）
  suspend_remove?: boolean // 信号停牌行语义：true=SR删行(益盟/回测一致，默认)；false=NaN占位(聚宽口径)
  price_round?: boolean // 真实价按分取整参与因子计算（仅不复权生效，默认开；与益盟/聚宽整分口径对齐）
  price_adjust?: string // 复权方式：none/forward/backward（缺省=不复权）
  // 特征加载额外预热缓冲（交易日，v1.18.6）：缺省=后端按 250 交易日（≈1年）预热；
  // 0=关闭。用于长回看/动态窗口公式（DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套）在区间首日
  // 即有收敛值；出口仍裁剪回评估区间，固定窗口公式结果不变
  warmup_days?: number
  // ---- 连续因子「分位 / 持仓期曲线」参数（v1.18.45）----
  quantiles?: number // 分位组数（缺省 10 = 十分位；5 = 旧五分位）
  rebalance_period?: number | null // 调仓期（交易日）；**不传/null ⇒ 跟随预测周期 h**
  topk_list?: number[] // 明细曲线要算的 K：**<1 视为「日均只数的百分比」**（0.1=10%、0.2=20%），**≥1 视为只数**（1 = 1 只）
}
export interface FactorTestGroupStats {
  count: number
  mean_ret: number
  median_ret: number
  limit_up_excluded?: number // 该组总剔除样本数（信号日涨停 + 成交日涨停 + 停牌）
  limit_up_excluded_t?: number // 该组信号日(T)涨停剔除数
  limit_up_excluded_t1?: number // 该组成交日(T+1)涨停剔除数
  suspended_excluded?: number // 该组成交日停牌/无行情剔除数
  extra_excluded?: number // 该组 ST(T+1)/创业板/科创板剔除数（勾选对应开关时）
}
export interface QuintileGroup {
  quantile: number // 1=最低值组 … N=最高值组（每日横截面分位；N=请求参数 quantiles，默认 10=十分位）
  count: number // 该组剔除后样本总数（观测数）
  n_days: number // 参与交易日数（日截面口径）
  mean_ret: number // 该组日截面平均未来收益（每天组均值再跨日平均，原始小数）
}
// 分位「累计收益曲线」（十分位图数据；与 quintile_ret 的 mean_ret 同源，故数字自洽）
export interface QuantileCurves {
  n_groups: number
  n_periods: number // 调仓期数（= 曲线点数）
  dates: string[] // 调仓日 YYYY-MM-DD（每期一个点）
  baseline_mean: number // 各组均值之平均（等频分组 ⇒ = 全样本均值，超额的同口径基准）
  best_quantile: number // **超额收益最强的分位组**（默认档展示的那一档，可能是 Q10/Q9/Q2…）
  worst_quantile: number // 最弱分位组
  groups: {
    quantile: number
    mean_ret: number // 各调仓期收益均值（原始小数）
    excess: number // 相对 baseline_mean 的超额（与 mean_ret 排序等价）
    cum: number[] // 累计收益（逐期 cumsum **算术累加**，原始小数）
    cum_compound?: number[] // v1.18.47 复利累乘 Π(1+r)−1（前端默认展示口径）
  }[]
  // ⚠ 多空 = **最强组 − 最弱组**：A 股空头收益拿不到，**不可实现，仅作有效性参考**
  long_short: { quantile: [number, number]; cum: number[] }
}
// TopK 持仓期收益曲线（含三档成本）
export interface TopKCurveItem {
  kind: 'decile' | 'topk' // decile = 分位组（逐日等分，只数逐日变化）；topk = 固定只数
  k: number // 目标持仓只数（分位档为其中位只数，仅作展示）
  quantile?: number // 仅 kind='decile'：该档是第几分位组
  turnover: number[] // 逐日换手（仅调仓日非零；首期建仓为 0）
  curves: Record<string, number[]> // 三档成本**算术累加**曲线，键 "0.0000"/"0.0040"/"0.0080"
  // v1.18.47 复利口径（前端**默认展示**）：每期净收益滚入本金 ⇒ Π[(1+r)(1−费率×换手)]−1；
  // 与 curves 同换手、同"首期建仓不计费"规则。⚠ 两口径不可混比（强策略复利显著更高）
  curves_compound?: Record<string, number[]>
  // 逐日盯市绩效指标（v1.18.48）：三档成本各一份（键同 curves）；口径不适用（如调仓期 <
  // 预测周期）时为 null
  perf?: Record<string, PerfMetrics> | null
}
/** 逐日盯市绩效指标（annual/max_drawdown/sharpe/sortino/calmar 等；原始小数，比率为无量纲数） */
export interface PerfMetrics {
  n_days: number // 样本交易日数
  nav_end: number | null // 期末净值（起点 1）
  total_return: number | null
  annual_return: number | null // 几何年化（252 交易日/年）
  max_drawdown: number | null // 负数
  sharpe: number | null // rf = 0
  sortino: number | null // 下行标准差用半方差口径
  calmar: number | null // 年化 / |最大回撤|
  vol_annual: number | null // 年化波动
  win_rate: number | null // 日胜率
  max_dd_days: number | null // 峰值→最深回撤点的交易日数
}
// 可切换基准（指数）：与组合曲线**同轴同口径** —— 每期 = 指数在同一调仓日的
// T+1 → T+h+1 收益（与 LABEL 同口径），各期算术累加；价格指数（不含分红）。
export interface TopKBenchmark {
  code: string // 如 SH000300
  name: string // 展示名：沪深300 / 中证1000 …
  cum: (number | null)[] // 逐调仓期**算术累加**；**数据不足的期及其后为 null**（前端断线，不猜）
  cum_compound?: (number | null)[] // v1.18.47 复利累乘（与组合曲线同口径切换；缺失语义同 cum）
  perf?: PerfMetrics | null // 逐日盯市绩效指标（与组合同口径，v1.18.48）
}
export interface TopKCurves {
  rebalance_period: number // 调仓期（交易日）；默认 = 预测周期 h
  horizon: number | null // 该行的预测周期 h（调仓期默认值来源）
  n_days: number // 日轴长度（交易日）
  n: number // **调仓次数**（= 曲线点数 = 期数）
  dates: string[] // 调仓日 YYYY-MM-DD（每期一个点）
  side: 'high' | 'low' // 曲线取的那一端（由 best_quantile 决定，高/低分位侧）
  default_quantile: number | null // 最强分位组（items[0] 的 quantile）
  items: TopKCurveItem[] // items[0] = 默认档 = 最强分位组；其后为固定 K 档
  // 可切换基准（多候选一次下发 ⇒ 前端纯切换、零重算）；取数失败时为 undefined
  benchmarks?: { default: string | null; items: TopKBenchmark[] } | null
  benchmark_error?: string | null
}
// K 敏感度汇总表：各 K 的换手 / 三档年化成本 / 成本吞噬比例
export interface TopKSensitivityRow {
  k: number
  avg_turnover: number // 每个调仓期的平均换手（小数，0.0766=7.66%）
  gross_per_period: number | null // 每期毛收益（label h 期前视口径，**不做年化**，仅作量级参考）
  ann_cost: Record<string, number> // 年化成本（小数；键 "0.0040" 等，252 交易日/年）
  cost_eaten: Record<string, number | null> // 成本吞噬比例 = 每期成本/每期毛收益（毛收益 ≤0 ⇒ null）
}
export interface TopKSensitivity {
  rebalance_period: number
  n_days: number
  n_periods: number
  trading_days: number
  default_k: number | null
  ks: number[]
  rows: TopKSensitivityRow[]
  note: string // 口径/免责说明（未考虑涨跌停、停牌、流动性冲击等）
}
export interface SingleFactorTestResult {
  id: string
  name: string
  source: string
  expression: string
  horizon?: number // 该行对应的预测周期（批量测试时每 因子×周期 一行）
  source_formula?: string // 用户原文公式（custom=保存原文；目录因子=表达式本身）
  coverage: number | null // 因子值非空比例
  nonzero_ratio: number | null // 非零比例
  is_binary: boolean // 是否 0/1 二值信号
  grouping: 'binary' | 'quantile' | null // 触发分组方式
  quintile_ret: QuintileGroup[] | null // 分位平均收益（连续因子），识别U型/倒U型；组数=quantiles（默认 10）
  trigger: FactorTestGroupStats | null // 触发组（>0.5）未来N日收益
  not_trigger: FactorTestGroupStats | null // 未触发组（<=0.5）
  diff: number | null // 触发均值 - 未触发均值
  p_value: number | null // Mann-Whitney U p 值
  daily_diff: number | null // 按日配对检验：逐日差值均值（与 diff 同口径）
  daily_t: number | null // 按日差值序列单样本 t 统计量（普通）
  daily_t_hac: number | null // Newey-West HAC 稳健 t（修正自相关/异方差）
  daily_acf1: number | null // 日差值序列 lag-1 自相关系数
  daily_acf5: number | null // 日差值序列 lag-5 自相关系数
  daily_var_ratio: number | null // 方差稳定性：后半段/前半段方差比
  daily_win: number | null // 日差值>0 的交易日占比（胜率，0-1）
  daily_n: number // 参与配对的交易日数
  daily_trig_mean: number | null // 信号组逐日截面收益均值（日均），用于 0/1 信号双柱展示
  daily_not_mean: number | null // 非信号组逐日截面收益均值（日均）
  ic: number | null
  rank_ic: number | null
  icir: number | null
  rank_icir: number | null
  n_obs: number
  error: string | null
  // 事件研究（v1.18.7）：仅 0/1 二值信号且触发样本非空时有值
  // （后端在单因子测试里顺带计算，点「事件研究」按钮直接展示，无需二次提交）
  event_study?: EventStudyResult | null
  // 连续因子「分位累计收益曲线」（v1.18.45）：与 quintile_ret 同源
  quantile_curves?: QuantileCurves | null
  // 连续因子「TopK 持仓期收益曲线 + 三档成本」（v1.18.45）：items[0]=默认档 K=10%
  topk_curves?: TopKCurves | null
  topk_curves_error?: string | null // 该段出错时的原因（不影响其余统计字段）
  // 各 K 的换手 / 年化成本 / 成本吞噬比例（v1.18.45；与 topk_curves 同一批调仓日）
  topk_sensitivity?: TopKSensitivity | null
  topk_sensitivity_error?: string | null
  // 阶段计时（后端 v1.18.43 起回传；前端 v1.18.46 起展示为表格「耗时」列）：
  // init_s = 一次性 qlib 初始化（仅首个任务有值）／feature_s = 面板特征加载（全池共享，各因子相同）
  // ／item_s = 本因子统计 + 事件研究／total_s = 三者之和。
  // ⚠ total_s ≠ 前端 wall：不含 HTTP 往返、任务排队、股票池成分股解析与 label 表达式构建。
  timing?: FactorTiming | null
}
/** 单因子测试的阶段计时（秒） */
export interface FactorTiming {
  init_s: number
  feature_s: number
  item_s: number
  total_s: number
}
export interface SingleFactorTestProgress {
  task_id: string
  status: 'running' | 'success' | 'failed' | 'cancelled'
  progress: number // 0-100
  message: string
  result?: { items: SingleFactorTestResult[]; total: number } | null
  error?: string | null
}
export async function createSingleFactorTest(
  req: SingleFactorTestRequest,
): Promise<{ task_id: string }> {
  // 异步提交：返回 task_id，随后轮询 getSingleFactorTestProgress
  const { data } = await http.post<{ task_id: string }>('/factors/single-factor-test', req)
  return data
}
export async function getSingleFactorTestProgress(
  taskId: string,
): Promise<SingleFactorTestProgress> {
  const { data } = await http.get<SingleFactorTestProgress>(
    `/factors/single-factor-test/progress/${taskId}`,
  )
  return data
}
export async function cancelSingleFactorTest(
  taskId: string,
): Promise<{ ok: boolean; message: string }> {
  const { data } = await http.post<{ ok: boolean; message: string }>(
    `/factors/single-factor-test/cancel/${taskId}`,
  )
  return data
}
export interface SingleFactorTestTaskInfo {
  task_id: string
  status: 'running' | 'success' | 'failed' | 'cancelled'
  progress: number // 0-100
  message: string
  ts: number
  cancel_requested: boolean
}
export async function clearSingleFactorTest(): Promise<{ ok: boolean; cleared: number }> {
  // 删除后端已完成的单因子测试任务/结果（释放进程内存）；运行中任务保留
  const { data } = await http.post<{ ok: boolean; cleared: number }>(
    '/factors/single-factor-test/clear',
  )
  return data
}
export async function getSingleFactorTestTasks(
  limit?: number,
): Promise<{ tasks: SingleFactorTestTaskInfo[] }> {
  const { data } = await http.get<{ tasks: SingleFactorTestTaskInfo[] }>(
    '/factors/single-factor-test/tasks',
    { params: { limit } },
  )
  return data
}

// ---------- 事件研究（0/1 稀疏信号：触发事件对齐 T=0 的收益分布） ----------
// 用途：稀疏 0/1 信号的"按日配对检验"在触发日只有 1 只票时会退化成单票收益序列，
// 均值/显著性不可信；事件研究以"每次触发"为样本单位，给出概率/赔率的真实画像。

export interface EventStudyCurvePoint {
  k: number // 持有交易日
  n: number // 该期有效样本数
  mean: number | null // 平均收益（原始小数）
  median: number | null
  win: number | null // 胜率（>0 占比）
  p10: number | null
  p25: number | null
  p75: number | null
  p90: number | null
  max: number | null
  min: number | null
}
export interface EventStudyProbPoint {
  k: number
  gt0: number | null // 收益 >0 的事件占比
  gt10: number | null
  gt20: number | null
  gt50: number | null
  gt100: number | null
}
export interface EventStudyUpside {
  mean: number | null // T+1 买入后 max_k 日内"最高点卖出"的收益分布
  median: number | null
  p75: number | null
  p90: number | null
  p99: number | null
  max: number | null
  reach20: number | null // 曾达到 +20% 的事件占比
  reach50: number | null
  reach100: number | null
  dn_mean: number | null // 同期"最低点卖出"（下限参考）
  dn_median: number | null
  dn_min: number | null
}
export interface EventStudyEvent {
  code: string
  dt: string
  ret: number | null
  max_ret?: number | null
}
export interface EventStudyBaseline {
  ks: number[]
  // ── 均值口径（日配对：先按日取截面均值，再对配对日平均）──
  trigger_pair: (number | null)[] // 触发组日配对均值（每个 k）
  baseline: (number | null)[] // 基准：未触发组日配对均值
  excess: (number | null)[] // 均值超额 = 触发组 − 基准
  // ── 中位数口径（事件级：全部样本收益的中位数，与均值口径不可混用）──
  trigger_median?: (number | null)[] // 触发组事件级中位数
  baseline_median?: (number | null)[] // 基准：未触发组事件级中位数
  excess_median?: (number | null)[] // 中位数超额 = 触发组中位数 − 基准中位数
  n_pair_days: number // 配对日数
}
export interface EventStudyResult {
  factor: { id: string; name: string; expression: string; source_formula?: string }
  params: {
    universe: string
    start_date: string
    end_date: string
    max_k: number
    price_adjust: string
  }
  n_events: number // 剔除后参与统计的事件数
  n_aligned: number // 成功对齐到 T+1 买入价的事件数
  n_raw: number // 剔除前触发数
  ks: number[]
  curve: EventStudyCurvePoint[]
  prob: EventStudyProbPoint[]
  upside: EventStudyUpside
  top_events: EventStudyEvent[]
  worst_events: EventStudyEvent[]
  // 基准（未触发组）与超额曲线（日配对口径）：仅 0/1 信号有；仅展示、不参与判定
  baseline?: EventStudyBaseline | null
  // 逐 k 榜单（键为字符串化的 k）：前端按当前「最长持有」取对应那份，
  // 保证「表头写持有 k 日」与「数值确实是 k 期收益」口径一致；
  // 同时让「未持满 max_k 期、但在更短的 k 上有效」的事件也能出现在榜单里。
  top_by_k?: Record<string, EventStudyEvent[]>
  worst_by_k?: Record<string, EventStudyEvent[]>
  // 数据不足的事件统计（避免"静默丢弃"）：
  //   场景 = 把 end_date 设到接近数据尾部时，靠近末尾的触发在 T+1..T+1+max_k 上
  //   拿不到全部收盘价（"到截止日还没平仓"）→ 不进入对应 k 的统计、也不出现在
  //   top/worst 明细里。此前界面上完全看不出来，容易被误读为"样本凭空变少"。
  max_k?: number // 本次实际计算到的最长持有期
  n_unaligned?: number // 连买入价都拿不到（完全无法对齐）的事件数
  n_short?: number // 能买入但有效期数 < max_k 的事件数（典型的「未平仓」）
  short_events?: { code: string; dt: string; n_valid_k: number }[] // 明细（缺得最多的在前，最多 200 条）
}
export interface EventStudyProgress {
  task_id: string
  status: 'running' | 'success' | 'failed' | 'cancelled'
  progress: number
  message: string
  result?: EventStudyResult | null
  error?: string | null
}
export interface EventStudyRequest {
  universe: string
  start_date: string
  end_date: string
  factor: SingleFactorTestItem
  max_k?: number // 最长持有交易日（1~120，默认 40）
  exclude_limit_up_signal?: boolean
  exclude_limit_up_trade?: boolean
  exclude_suspended?: boolean
  exclude_st_t1?: boolean
  exclude_stock_gem?: boolean
  exclude_stock_kcb?: boolean
  price_adjust?: string
  price_round?: boolean
  suspend_remove?: boolean
  freeze_suspended_price?: boolean
  warmup_days?: number
}
export async function createEventStudy(req: EventStudyRequest): Promise<{ task_id: string }> {
  const { data } = await http.post<{ task_id: string }>('/factors/event-study', req)
  return data
}
export async function getEventStudyProgress(taskId: string): Promise<EventStudyProgress> {
  const { data } = await http.get<EventStudyProgress>(`/factors/event-study/progress/${taskId}`)
  return data
}
export async function cancelEventStudy(
  taskId: string,
): Promise<{ ok: boolean; message: string }> {
  const { data } = await http.post<{ ok: boolean; message: string }>(
    `/factors/event-study/cancel/${taskId}`,
  )
  return data
}

// ---------- 数据 ----------

export async function listDataSources(): Promise<DataSourceInfo> {
  const { data } = await http.get<DataSourceInfo>('/data-sources')
  return data
}
