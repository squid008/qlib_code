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

// 公式翻译：益盟/通达信公式 → qlib 表达式
export interface TranslateResult {
  name: string
  expression: string
  inputs: string[]
  has_patch: boolean
  source_formula: string
}
export async function translateFormula(formula: string, patchable = false): Promise<TranslateResult> {
  const { data } = await http.post<TranslateResult>('/factors/translate', {
    formula,
    patchable,
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

// 算子分类清单（前端公式编辑器提示/灰显）
export interface FactorOperators {
  supported: string[]
  patched_need_impl: string[]
  level2_no_data: string[]
  ignored_plot: string[]
}
export async function getFactorOperators(): Promise<FactorOperators> {
  const { data } = await http.get<FactorOperators>('/factors/operators')
  return data
}

// ---------- 单因子测试（不训练模型，快速诊断因子预测力） ----------

export interface SingleFactorTestItem {
  id: string
  name: string
  expression: string
  source: string // custom / alpha158 / alpha360
}
export interface SingleFactorTestRequest {
  universe: string
  start_date: string
  end_date: string
  label_horizon: number
  factors: SingleFactorTestItem[]
}
export interface FactorTestGroupStats {
  count: number
  mean_ret: number
  median_ret: number
  limit_up_excluded?: number // 触发组中信号当日涨停被剔除的样本数
}
export interface QuintileGroup {
  quantile: number // 1=最低值组 … 5=最高值组（每日横截面分位）
  count: number
  mean_ret: number // 该组平均未来收益（原始小数）
}
export interface SingleFactorTestResult {
  id: string
  name: string
  source: string
  expression: string
  coverage: number | null // 因子值非空比例
  nonzero_ratio: number | null // 非零比例
  is_binary: boolean // 是否 0/1 二值信号
  grouping: 'binary' | 'quantile' | null // 触发分组方式
  quintile_ret: QuintileGroup[] | null // 5组分位平均收益（连续因子），识别U型/倒U型
  trigger: FactorTestGroupStats | null // 触发组（>0.5）未来N日收益
  not_trigger: FactorTestGroupStats | null // 未触发组（<=0.5）
  diff: number | null // 触发均值 - 未触发均值
  p_value: number | null // Mann-Whitney U p 值
  daily_diff: number | null // 按日配对检验：逐日差值均值（与 diff 同口径）
  daily_t: number | null // 按日差值序列单样本 t 统计量
  daily_win: number | null // 日差值>0 的交易日占比（胜率，0-1）
  daily_n: number // 参与配对的交易日数
  ic: number | null
  rank_ic: number | null
  icir: number | null
  rank_icir: number | null
  n_obs: number
  error: string | null
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

// ---------- 数据 ----------

export async function listDataSources(): Promise<DataSourceInfo> {
  const { data } = await http.get<DataSourceInfo>('/data-sources')
  return data
}

export async function listInstruments(market = 'all', source = 'qlib') {
  const { data } = await http.get<string[]>('/data/instruments', {
    params: { market, source },
  })
  return data
}

export async function getDailyBars(
  instrument: string,
  startDate: string,
  endDate: string,
  source = 'qlib',
) {
  const { data } = await http.get('/data/daily-bars', {
    params: { instrument, start_date: startDate, end_date: endDate, source },
  })
  return data
}
