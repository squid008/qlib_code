import axios from 'axios'
import type {
  BacktestRequest,
  BacktestTask,
  TaskIdResponse,
  DataSourceInfo,
  ModelArtifacts,
  BacktestResult,
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

export async function deleteBacktest(taskId: string): Promise<{ status: string }> {
  const { data } = await http.delete<{ status: string }>(`/backtest/${taskId}`)
  return data
}

export async function getBacktestResult(taskId: string): Promise<BacktestResult> {
  const { data } = await http.get<BacktestResult>(`/backtest/${taskId}/result`)
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
