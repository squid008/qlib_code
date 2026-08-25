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
  has_params: boolean
  has_result: boolean
  has_meta: boolean
  images: Record<string, string>
  segments: string[]
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

export async function getBacktestResult(taskId: string): Promise<BacktestResult> {
  const { data } = await http.get<BacktestResult>(`/backtest/${taskId}/result`)
  return data
}

// ---------- 因子库 ----------

export async function getFactorCatalog(dataset = 'Alpha158'): Promise<FactorCatalog> {
  const { data } = await http.get<FactorCatalog>('/factors/catalog', {
    params: { dataset },
  })
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
