// 与后端 Pydantic 模型对应的 TypeScript 类型

export interface BacktestRequest {
  universe: string
  instruments?: string[] | null
  start_date: string
  end_date: string
  model: string
  model_params?: Record<string, string | number | null> | null
  topk: number
  n_days_hold: number
  label_horizon: number
  layer_rebalance: number
  n_days_learn: number
  data_source: string
  data_source_provider_uri?: string | null
  feature: string
  selected_features?: string[] | null
  custom_formulas?: string[] | null
  bins: number
  // 交易成本与成交设置
  deal_price: string
  price_adjust?: string // 复权方式：none/forward/backward（缺省=不复权）
  open_cost: number
  close_cost: number
  min_cost: number
  impact_cost: number
  volume_threshold?: number | null
  limit_threshold?: number | null
  trade_unit?: number | null
  // 训练/测试划分（滚动训练）
  split_mode: string
  train_win: number
  train_unit: string
  test_win: number
  test_unit: string
  step_win?: number | null
  step_unit?: string | null
  initial_capital: number
  load_model_task_id?: string | null
  resume_task_id?: string | null
}

export interface NavPoint {
  date: string
  value: number
  benchmark?: number
}

// 分层回测（5组）数据
export interface LayerPoint {
  date: string
  Group1: number
  Group2: number
  Group3: number
  Group4: number
  Group5: number
  long_short: number
  long_average: number
  benchmark?: number | null
}

export interface LayerSegment {
  segment: string // "段1" / "汇总"
  groups: LayerPoint[]
  benchmark?: string | null
}

export interface LayerReturns {
  segments?: LayerSegment[] | null
  merged?: LayerSegment | null
}

// IC 分析数据
export interface ICPoint {
  date: string
  ic: number
  rank_ic?: number | null
}

export interface ICSegment {
  segment: string
  points: ICPoint[]
  mean_ic: number
  icir?: number | null
  mean_rank_ic?: number | null
  rank_icir?: number | null
}

export interface ICAnalysis {
  train?: ICSegment[] | null
  test?: ICSegment[] | null
  merged_test?: ICSegment | null
  merged_train?: ICSegment | null
}

export interface ModelArtifacts {
  model_info?: {
    model?: string
    feature?: string
    topk?: number
    num_trees?: number
    segment?: string
  } | null
  feature_names?: string[] | null
  params?: Record<string, string | number | boolean | null> | null
  linear?: {
    formula?: string
    intercept?: number
    weights?: number[]
    feature_weights?: { feature: string; weight: number }[] | null
  } | null
  model_file?: string | null
  feature_importance?: { feature: string; importance: number }[] | null
  // 滚动训练时返回：每段的模型交付物
  segments?: ModelArtifacts[] | null
}

// 因子库目录（特征集）
export interface FactorField {
  name: string
  expression: string
  category: string
  description: string
}
export interface FactorGroup {
  group: string
  fields: FactorField[]
}
export interface FactorCatalog {
  dataset: string
  total: number
  groups: FactorGroup[]
  flat: FactorField[]
}

export interface TradeRecord {
  date: string
  instrument: string
  direction: number // 1=买入, -1=卖出
  amount?: number | null
  deal_price?: number | null
  trade_value?: number | null
  trade_cost?: number | null
  ffr?: number | null
}

export interface BacktestResult {
  annualized_return?: number | null
  annualized_excess_return?: number | null
  sharpe?: number | null
  max_drawdown?: number | null
  win_rate?: number | null
  benchmark_return?: number | null
  total_return?: number | null
  report_df?: Record<string, number | null> | null
  nav?: NavPoint[] | null
  trades?: TradeRecord[] | null
  layer_returns?: LayerReturns | null
  ic_analysis?: ICAnalysis | null
}

export interface BacktestTask {
  task_id: string
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled' | 'cancelling'
  progress: number
  message: string
  created_at: string
  display_name?: string | null
  result?: BacktestResult | null
  // 滚动训练运行中：已跑段的部分结果（净值/分层/IC），供中途查看
  partial_result?: BacktestPartialResult | null
}

export interface BacktestPartialResult {
  segments_done: number
  segments_total: number
  nav?: NavPoint[] | null
  // 回测参数设定的结束日期（未跑完时，曲线 X 轴右端延伸到此，直观显示进度）
  end_date?: string | null
  layer_returns?: LayerReturns | null
  ic_analysis?: ICAnalysis | null
}

export interface TaskIdResponse {
  task_id: string
}

export interface DataSourceInfo {
  [name: string]: {
    daily: boolean
    minute: boolean
    financial: boolean
    industry: boolean
    index_constituent: boolean
  }
}
