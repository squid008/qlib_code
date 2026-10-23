import http from './http'
// ⚠ `EventStudyRequest.factor` 用的是 `SingleFactorTestItem`（仍定义在 `api.ts` 里 ✓）——
//   type-only 导入**不会**产生运行时循环 ✓（编译后整句消失 ✓）。
import type { SingleFactorTestItem } from '../api'

// ★ v2.11.58：从 `api.ts` 外提（原 L873–1054 ✓）—— 见 `api.ts` 头部说明。
// ---------- 事件研究（0/1 稀疏信号：触发事件对齐 T=0 的收益分布） ----------
// 用途：稀疏 0/1 信号的"按日配对检验"在触发日只有 1 只票时会退化成单票收益序列，
// 均值/显著性不可信；事件研究以"每次触发"为样本单位，给出概率/赔率的真实画像。

export interface EventStudyCurvePoint {
  k: number // 持有交易日
  n: number // 该期有效样本数
  mean: number | null // 平均收益（原始小数）
  median: number | null
  win: number | null // 胜率（>0 占比）
  p01?: number | null // 深左尾（v1.19.81：分布图要标尾部）
  p05?: number | null
  p10: number | null
  p25: number | null
  p75: number | null
  p90: number | null
  p95?: number | null
  p99?: number | null // 深右尾
  max: number | null
  min: number | null
  /** 该 k 的**直方图计数**（长度 = `EventStudyResult.dist_edges` 长度 - 1）——
   *  分布图用它画形状（v1.19.81）；旧结果无此字段。 */
  counts?: number[]
}
export interface EventStudyProbPoint {
  k: number
  gt0: number | null // 收益 >0 的事件占比
  gt10: number | null
  gt20: number | null
  gt50: number | null
  gt100: number | null
  // 亏损侧镜像（v1.19.80）：触发后拿到目标亏损的事件占比，档位与收益侧一一对应
  // ⇒ 两表并排 = "赚大亏小"还是"纯波动"一眼可见
  lt0: number | null // 收益 <0 的事件占比
  lt10: number | null
  lt20: number | null
  lt50: number | null
  /** <-100%：A 股个股结构性恒为 0（价格非负 ⇒ 多头最多亏 100%），保留只为与 gt100 对称 */
  lt100: number | null
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
  /** 期内最高（Top 榜用；= 到当前 k 期为止的最大值） */
  max_ret?: number | null
  /** 期内最低（亏损榜用，v1.19.80；与 max_ret 对称 ⇒ 也回答"最惨浮亏到哪"） */
  min_ret?: number | null
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
  // ── 逐 k 的日配对稳定性（v1.19.24；弹窗第④条「日配对稳定」随「最长持有 k」取用）──
  // ⚠ 旧结果（v1.19.24 之前算的）没有这三项 ⇒ 前端回退用"该行"的 daily_t_hac/daily_win。
  t_hac?: (number | null)[] // 每个 k：日配对差值序列的 Newey-West HAC t
  win?: (number | null)[] // 每个 k：日配对差值 > 0 的配对日占比
  n_pair_days_k?: number[] // 每个 k：有效配对日数（< 2 ⇒ 上一行两项为 null）
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
  /** 直方图分箱边界（全部 k 共用，v1.19.81）：中间 2% 一档、两侧渐稀、端桶夹住极值 */
  dist_edges?: number[]
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
  /** v1.20.36：服务端耗时明细（前端据此自适应「快 ⇒ 自动跟随 / 慢 ⇒ 手工按钮」，不展示数字也用它判定） */
  timings?: {
    total_s?: number
    cached?: boolean
    stages?: { p: number; msg: string; t: number }[]
    stages_s?: { msg: string; s: number }[]
    slowest?: { msg: string; s: number } | null
  } | null
  /** v1.20.36：是否命中**内容寻址磁盘缓存**（命中 ⇒ 秒回，必判为"快"） */
  cached?: boolean | null
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
  /**
   * ★ v2.1.65：**成交时点**（`t1_open` 次日开盘 / `t1_close` 次日收盘 / `t_close` 信号日收盘 ✓）。
   * 后端 `EventStudyRequest` 从 v2.1.52 起就有这个字段 ✓，**前端类型漏了** ✗ ⇒ 弹窗
   * 「重新计算」与净值曲线都只能拿到后端默认值 ⇒ 面板切口径**没有任何效果** ✗
   * （用户 2026-10-12："我怎么测下来两个数一模一样" ✓ 的同一类问题 ✓）。
   */
  fill?: string
  /**
   * ★★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径**（`legacy` 默认 / `open_only` ✓）——
   * 与 `fill` 同一套路：后端的事件研究本身不用它 ✗，但**弹窗里的净值曲线**（`/event-study/nav` ✓）
   * 要用 ✓ ⇒ 必须由面板一起带过来 ✓（否则"面板换了口径、曲线纹丝不动" ✗）。
   */
  limit_judge?: string
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
