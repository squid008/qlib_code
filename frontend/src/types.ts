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
  data_source: string
  data_source_provider_uri?: string | null
  feature: string
  selected_features?: string[] | null
  custom_formulas?: string[] | null
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
  // 日截面剔除（调仓当日状态判定，无未来函数；与单因子测试同口径）
  exclude_st?: boolean // 剔除 ST/*ST/退市整理
  exclude_stock_gem?: boolean // 剔除创业板（SZ30）
  exclude_stock_kcb?: boolean // 剔除科创板（SH688）
  // 信号后处理（v1.14；v1.16 起 single 与滚动 custom 均可，默认关/空=不启用）
  meta_gate?: boolean
  meta_gate_opts?: {
    scope?: string
    ydef?: string
    reject_ratio?: number | null
    extra_features?: string[] | null // qlib 表达式（gate 附加输入特征，如多个 01 触发公式）
    /** 归因范围（v1.19.72）：'all'（默认，每段都做单因子 ablation）| 'seg1'（只第一段）| 'off'/false（关）。
     *  ⚠ 端到端实测代价：1 个 gate 训练约 4.5~7.6s ⇒ 每段 ablation（k+1 个）约 +20~30s ⇒ 36 段约 +12~18 分钟。 */
    attribution?: boolean | string | null
  } | null
  trigger_overlay_opts?: {
    enabled?: boolean
    formula?: string
    topk?: number
    weight?: number
  } | null
  hard_filters?: {
    min_mktcap_bn?: number | null // 市值下限（亿元）
    max_mktcap_bn?: number | null // 市值上限（亿元）
    min_price?: number | null // 真实股价下限（元）
  } | null
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

// ---- 信号合成归因（v1.19.72，产物 compose.json）----
// 用户 2026-09-16：「我勾了 Meta-Gate 风控，训练产物里没有这块结果？不知道机器最后觉得用哪个
// 风控因子更好？」⇒ 后端每段把 gate 的 gain 占比与「单因子 ablation」的 valid_auc 落盘，前端出表。
export interface GateAttrExtra {
  i: number // 附加因子序号（= 勾选顺序 ⇒ 可映射回 params.meta_gate_opts.extra_features[i]）
  gain: number // LightGBM gain（分裂增益累计）
  gain_share: number // 在这批附加因子内的 gain 占比
  expr: string // 表达式开头（截断，便于人眼辨认）
  auc: number | null // 单因子 ablation：基线 + 该因子 的 gate valid_auc
  delta_auc: number | null // 相对基线（不含任何附加因子）的提升 ⇒ 最可比的"哪个更好"
  error?: string
}

export interface GateAttribution {
  primary_p_gain_share?: number // 主模型分（primary_p）在总 gain 里的占比
  extras_gain_share?: number // 这批附加因子整体在总 gain 里的占比
  primary_only_auc?: number // 基线：只有主特征 + 主分
  full_auc?: number // 全部附加因子都用上
  full_delta_auc?: number // = full_auc - primary_only_auc ⇒ 这批因子整体值不值
  extras?: GateAttrExtra[]
  error?: string
  note?: string
}

export interface ComposeSegmentInfo {
  gate?: {
    reject_ratio?: number
    n?: number
    valid_auc?: number
    extras?: number
    attribution?: GateAttribution
  } | null
  overlay?: { n?: number; valid_auc?: number } | null
  hard?: string | null
}

export interface GateCompose {
  segments?: Record<string, ComposeSegmentInfo>
  updated_at?: string
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
