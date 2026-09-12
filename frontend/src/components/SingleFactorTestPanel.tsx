import { Component, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import DateInput, { type DateInputHandle, isValidDateStr } from './DateInput'
import {
  createSingleFactorTest,
  getSingleFactorTestProgress,
  cancelSingleFactorTest,
  getSingleFactorTestTasks,
  clearSingleFactorTest,
  getFactorCatalog,
} from '../api'
import type {
  CustomFormula,
  EventStudyRequest,
  EventStudyResult,
  SingleFactorTestResult,
} from '../api'
import type { FactorCatalog, FactorField } from '../types'
import EventStudyModal from './EventStudyModal'
import TopkCurveModal from './TopkCurveModal'
import { excessThresholdOf, pairStabilityOf, type PairStability } from './verdictRules'

interface SingleFactorTestPanelProps {
  customFormulas: CustomFormula[]
  defaultUniverse: string
  defaultStartDate: string
  defaultEndDate: string
  defaultLabelHorizon: number
  // 任务提交/结束/取消时触发，让 App 立即刷新"并发: x/y"显示（即时 +1/-1，不等 3 秒轮询）
  onCapacityChange?: () => void
}

type TestResult = SingleFactorTestResult

// 结论判定（与表格"结论"列同源，导出复用，避免两处口径漂移）：
// 按渲染列的判定顺序给出 kind；布尔字段供表格样式分支使用。
type VerdictKind = 'conflicting' | 'good' | 'goodReverse' | 'lottery' | 'timeConcentrated' | 'watch'
const VERDICT_LABEL: Record<VerdictKind, string> = {
  conflicting: '方向矛盾',
  good: '有效✓',
  goodReverse: '有效(反向)✓',
  lottery: '彩票型',
  timeConcentrated: '时间集中',
  watch: '待观察',
}
interface VerdictStats {
  significant: boolean
  goodBase: boolean
  conflicting: boolean
  goodReverseBase: boolean
  dT: number | null
  stable: boolean
  good: boolean
  goodReverse: boolean
  kind: VerdictKind
  // 判「待观察」时的具体原因（供结论列悬停）。存在的意义：判定用未舍入的原始值，
  // 而界面部分列只显示 1~2 位小数，会出现"胜率显示 55.0% 却不通过 >=55%"的困惑
  // （例：win=0.549694 → 显示 55.0%，但 0.549694 < 0.55 → 判待观察）。
  watchReason?: string
}
/** 弹窗的错误边界：渲染异常时显示错误信息，而不是让整个页面白屏（事件研究 / 持仓曲线共用）。 */
class EsErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { err: Error | null }
> {
  state: { err: Error | null } = { err: null }

  static getDerivedStateFromError(err: Error) {
    return { err }
  }

  render() {
    if (this.state.err) {
      return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white dark:bg-slate-800 rounded-lg shadow-xl p-4 max-w-[640px] text-xs">
            <div className="text-red-500 font-semibold mb-1">
              {this.props.label ?? '事件研究弹窗'}渲染出错
            </div>
            <div className="text-slate-500 break-all">{String(this.state.err)}</div>
            <div className="text-slate-400 mt-2">
              页面其余部分未受影响；按 F5 刷新可重置。请把上面的错误信息反馈给开发者。
            </div>
            <button
              className="mt-3 px-2 py-1 rounded border"
              onClick={() => this.setState({ err: null })}
            >
              重试
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

/** 事件研究在该行「周期」上的取值点（无对应周期时退化为最长持有期）。 */
function esPointOf(r: TestResult) {
  const es = r.event_study
  if (!es || !es.curve || es.curve.length === 0) return null
  const k = r.horizon
  if (k != null) {
    const hit = es.curve.find((c) => c.k === k)
    if (hit) return hit
  }
  return es.curve[es.curve.length - 1]
}

/**
 * 取「当前判定持有期 k」对应的**日配对均值超额**（= 触发组 − 未触发组，同口径）。
 *
 * 为何需要它：中位数为正只说明"典型一次触发赚钱"，但若**跑不赢同期未触发组**，
 * 那等于"随便买也比它强"，信号没有实用价值 → 故 v1.18.20 起把 `excess > 0` 纳入
 * 「有效」的必要条件（反向则为 `excess < 0`）。
 *
 * 口径选择：用 `baseline.excess`（均值·日配对）而非 `excess_median`（中位数·事件级）——
 * 前者直接对应"整体收益是否跑赢未触发组"，与用户直觉一致；后者作为参考在弹窗中可见。
 * 无 `baseline`（旧结果 / 基准计算失败）时返回 null → 不加此门槛但明确标注。
 */
function esExcessOf(r: TestResult, k?: number | null): number | null {
  const bl = r.event_study?.baseline
  if (!bl || !bl.ks || !bl.excess) return null
  const i = k == null ? bl.ks.length - 1 : bl.ks.indexOf(k)
  if (i < 0 || i >= bl.excess.length) return null
  const v = bl.excess[i]
  return v == null ? null : v
}

/** 「待观察」的原因文案（0/1 信号走事件研究路径）：逐条对比门槛，指出差在哪一项。 */
function watchReasonOfBinary(
  pt: NonNullable<ReturnType<typeof esPointOf>>,
  ex: number | null,
  exThr: number,
  st: PairStability,
) {
  const med = pt.median ?? 0
  const win = pt.win ?? 0
  const pct = (v: number, d = 3) => `${(v * 100).toFixed(d)}%`
  const medOk = med >= 0.005
  const winOk = win >= 0.55
  const exOk = ex == null || ex >= exThr
  const sgn = (v: number) => `${v >= 0 ? '+' : ''}${pct(v)}`
  const medTxt = medOk
    ? `中位数 ${pct(med)} ≥0.50% ✓`
    : `中位数 ${pct(med)} <0.50%（差 ${pct(0.005 - med)}）✗`
  const winTxt = winOk
    ? `绝对收益胜率 ${pct(win, 2)} ≥55% ✓`
    : `绝对收益胜率 ${pct(win, 2)} <55%（差 ${pct(0.55 - win, 2)}）✗`
  const exTxt =
    ex == null
      ? '超额 无法计算（无基准，未参与判定）—'
      : exOk
        ? `超额 ${sgn(ex)} ≥${pct(exThr)} ✓`
        : `超额 ${sgn(ex)} <${pct(exThr)}（差 ${pct(exThr - ex)}）✗`
  const stTxt = st.missing
    ? '日配对稳定性 无法验证（配对日不足 2）✗'
    : st.ok
      ? st.tOk
        ? `日配对稳定 |HAC t| ${Math.abs(st.tVal as number).toFixed(2)} ≥2 ✓`
        : `日配对稳定 日胜率 ${pct(st.winVal as number, 2)} ≥55% ✓`
      : `日配对不稳定 |HAC t| ${st.tVal == null ? '-' : Math.abs(st.tVal).toFixed(2)} <2 且 日胜率 ${st.winVal == null ? '-' : pct(st.winVal, 2)} <55% ✗`
  const why = [
    !medOk ? '中位数未达标' : '',
    !winOk ? '绝对收益胜率未达标' : '',
    ex != null && !exOk ? `超额未达门槛（${pct(ex)} <${pct(exThr)}）` : '',
    !st.ok ? '日配对未通过稳定性检验' : '',
  ].filter(Boolean).join('、') || '未达任何有效判定条件'
  return [
    `结论：待观察（${why}）`,
    `事件研究（持有 ${pt.k ?? '?'} 交易日，n=${pt.n ?? '?'}）：${medTxt}；${winTxt}；${exTxt}；${stTxt}`,
    `「有效✓」需同时满足：① 中位数 ≥0.50%；② 绝对收益胜率 ≥55%；③ 超额 ≥ 门槛（max(0.5%, 0.025%×持有期)，本次持有 ${pt.k ?? '?'} 日 → ${pct(exThr)}）；④ 日配对稳定（|HAC t| ≥2 或 日胜率 ≥55%）。`,
    '「彩票型」需 |中位数| <1% 且绝对收益胜率 45%~55% 且均值 >max(0.50%, 中位数×3)。',
    '注：判定使用未舍入的原始值（上表绝对收益胜率已显示到 0.01%）。点右侧「事件研究」看各持有期明细与超额曲线。',
  ].join('\n')
}

/** 「待观察」的原因文案（连续因子 / 无事件研究结果时）。 */
function watchReasonOfContinuous(r: TestResult, significant: boolean, stable: boolean) {
  const pct = (v: number, d = 3) => `${(v * 100).toFixed(d)}%`
  const lines = ['结论：待观察']
  if (r.error) {
    lines.push(`测试异常：${r.error}`)
  } else {
    const bad: string[] = []
    if (r.diff === null || r.diff === undefined) bad.push('无触发/未触发差值')
    else if (r.diff <= 0) bad.push(`差值 ${pct(r.diff)} ≤0`)
    if (r.is_binary && !significant) bad.push('p 值 ≥0.05（两组差异不显著）')
    if (!stable) bad.push('日配对检验不显著（|HAC t| <2 或胜率未偏离 50%）')
    lines.push(bad.length ? `未通过：${bad.join('；')}` : '未达任何有效判定条件')
  }
  lines.push('连续因子判定需同时满足：差值 >0 且显著、日配对检验稳定。')
  return lines.join('\n')
}

function verdictOf(r: TestResult): VerdictStats {
  const significant = r.p_value !== null && r.p_value < 0.05
  const goodBase =
    r.error == null && r.diff !== null && r.diff > 0 && (!r.is_binary || significant)
  // 方向矛盾：IC 与触发收益差方向相反且 ICIR 稳定（|ICIR|>=0.05，即×100后>=5）
  const conflicting =
    r.error == null &&
    r.ic !== null &&
    r.icir !== null &&
    Math.abs(r.icir) >= 0.05 &&
    r.diff !== null &&
    ((r.ic < 0 && r.diff > 0) || (r.ic > 0 && r.diff < 0))
  // 反向有效：连续因子高分位组收益显著更低（diff<0），且 IC/ICIR 稳定为负（方向一致）
  const goodReverseBase =
    r.error == null &&
    !r.is_binary &&
    significant &&
    r.ic !== null &&
    r.ic < 0 &&
    r.icir !== null &&
    Math.abs(r.icir) >= 0.05 &&
    r.diff !== null &&
    r.diff < 0
  const dT = r.daily_t_hac ?? r.daily_t
  const stable =
    dT === null ||
    dT === undefined ||
    (Math.abs(dT) >= 2 &&
      ((r.diff ?? 0) >= 0 ? (r.daily_win ?? 0) >= 0.5 : (r.daily_win ?? 0) <= 0.5))
  let good = goodBase && stable
  let goodReverse = goodReverseBase && stable
  let kind: VerdictKind = 'watch'

  // ---- 0/1 信号：结论以「事件研究」为准（v1.18.7）----
  // 稀疏信号每天可能只有 1 只票触发 → 按日配对检验退化成单票收益序列，其 t 值/胜率
  // 不可信（实测 CCCMA250 全 A 每日触发只数中位数 = 1，Top20 天贡献日差净和的 100.1%，
  // 剔除后剩余日均 -0.002%）。改用事件研究的「中位数 + 胜率」判定，并单独识别
  // 「彩票型」：中位数≈0、胜率≈50%，但均值显著更高（收益靠极少数连板/妖股事件撑起）。
  const pt = r.is_binary ? esPointOf(r) : null
  if (pt) {
    const med = pt.median ?? 0
    const win = pt.win ?? 0
    const mean = pt.mean ?? 0
    // 超额（触发组 − 未触发组·日配对均值，与判定同一持有期 k）与门槛
    const ex = esExcessOf(r, pt.k)
    const exThr = excessThresholdOf(pt.k)
    // v1.18.20 引入「超额 >0」；v1.18.32 收紧为「超额 ≥ 门槛(k)」并追加「日配对稳定」：
    // 实测出现过 0.081% 这种噪声级超额（连一次往返成本 0.2~0.35% 都覆盖不了）被判有效。
    // 门槛 = max(0.5%, 0.025%×k)：0.5% 覆盖一次往返成本、0.025%/日 ≈ 年化 6% 机会成本。
    // 稳定性：|HAC t| ≥2 或 日胜率 ≥55%（两者皆缺 → 不通过，宁严勿松）。
    // ex == null（无基准/旧结果）时第③条不启用（向后兼容），第④条仍要求。
    const st = pairStabilityOf({ t: r.daily_t_hac ?? r.daily_t, win: r.daily_win }, false)
    const stRev = pairStabilityOf({ t: r.daily_t_hac ?? r.daily_t, win: r.daily_win }, true)
    const exOk = ex == null || ex >= exThr
    const exRevOk = ex == null || ex <= -exThr
    const esGood = med >= 0.005 && win >= 0.55 && exOk && st.ok
    const esReverse = med <= -0.005 && win <= 0.45 && exRevOk && stRev.ok
    const lottery =
      Math.abs(med) < 0.01 && win >= 0.45 && win <= 0.55 && mean > Math.max(0.005, med * 3)
    good = esGood
    goodReverse = esReverse
    if (conflicting && !esGood) kind = 'conflicting'
    else if (esGood) kind = 'good'
    else if (esReverse) kind = 'goodReverse'
    else if (lottery) kind = 'lottery'
    else kind = 'watch'
    return {
      significant, goodBase, conflicting, goodReverseBase, dT, stable, good, goodReverse, kind,
      watchReason: kind === 'watch' ? watchReasonOfBinary(pt, ex, exThr, st) : undefined,
    }
  }

  if (conflicting) kind = 'conflicting'
  else if (good) kind = 'good'
  else if (goodReverse) kind = 'goodReverse'
  else if ((goodBase || goodReverseBase) && !stable) kind = 'timeConcentrated'
  else kind = 'watch'
  return {
    significant, goodBase, conflicting, goodReverseBase, dT, stable, good, goodReverse, kind,
    watchReason: kind === 'watch' ? watchReasonOfContinuous(r, significant, stable) : undefined,
  }
}

// 导出工作表1"因子指标"表头（列口径与界面表格一致：覆盖率/收益/差值/胜率/Q组 = ×100 百分数，IC/RankIC/ICIR/t = 原始小数）
// Q 组收益：仅连续因子有（日截面分位收益）；**列数由实际分位组数决定**（v1.18.45 起后端默认
// 十分位 10 组，见请求参数 quantiles），至少保留旧的 Q1~Q5 列宽。"触发组日均/未触发组日均"：仅 0/1 信号有
// （对应界面"分位收益"列的双柱，即触发组 vs 未触发组的逐日截面收益均值）。两类分组口径不同故不混填 Q 列，
// 避免"触发组与最低值组同列"的误读。
const exportHeaders = (qCols: number) => [
  '因子', '来源', '周期(天)', '公式', '覆盖率(%)', '信号', '触发数', '触发收益(%)', '未触发数', '未触发收益(%)',
  '差值(%)', '日差值(%)', 't(HAC)', '相对收益胜率(%)', '配对日数', 'p值',
  ...Array.from({ length: qCols }, (_, i) => `Q${i + 1}收益(%)`),
  '触发组日均(%)', '未触发组日均(%)',
  'IC', 'RankIC', 'ICIR', '结论', '事件中位数(%)',
]
// 导出工作表2"因子与公式"表头
const EXPORT_FORMULA_HEADERS = ['因子', '来源', '公式（用户保存原文 / 目录表达式）']

/** 解析「明细 K」输入（持仓期曲线要额外算出明细的 K 档，v1.18.45）：
 *  逗号/顿号/空格分隔；`50` = 50 只（绝对只数）；`0.2` 或 `20%` = 日均有效只数的 20%。
 *  留空 = 只算默认档（最强分位组 + 10% 固定档，**零额外开销**）。 */
function parseDetailKs(text: string): { ks: number[]; error?: string } {
  const s = text.trim()
  if (!s) return { ks: [] }
  const ks: number[] = []
  for (const raw of s.split(/[,，、\s]+/).filter(Boolean)) {
    const isPct = raw.endsWith('%')
    const v = Number(isPct ? raw.slice(0, -1) : raw)
    if (!Number.isFinite(v) || v <= 0) {
      return { ks: [], error: `明细 K 非法：${raw}（示例：50 只填 50；百分比填 0.2 或 20%）` }
    }
    ks.push(isPct ? v / 100 : v)
  }
  if (ks.length > 7) return { ks: [], error: '明细 K 最多 7 个（默认档另算，后端上限 8 个）' }
  return { ks }
}

// 解析批量预测周期输入：单值 / 逗号枚举(1,2,3,5) / range 区间(1:5:20 = 起点:步长:终点，含终点)。
// 值范围 1~250（后端同样校验兜底）。返回去重后的周期列表，非法时带中文错误。
function parseHorizons(text: string): { horizons: number[]; error?: string } {
  const s = text
    .trim()
    .replace(/[，;；\s]+/g, ',')
    .replace(/,+/g, ',')
    .replace(/^,|,$/g, '')
  if (!s) return { horizons: [] }
  const out: number[] = []
  for (const part of s.split(',')) {
    if (!part) continue
    const m = part.match(/^(\d+)(?::(\d+)(?::(\d+))?)?$/)
    if (!m) return { horizons: [], error: `无法识别"${part}"：支持单个数字、逗号枚举(如 1,2,5)或区间(如 1:5:20)` }
    const a = Number(m[1])
    if (m[2] == null) {
      out.push(a)
    } else {
      // a:b → a..b（步长默认 1）；a:b:c → a..c（含终点，b 为步长）
      const step = m[3] == null ? 1 : Number(m[2])
      const b = m[3] == null ? Number(m[2]) : Number(m[3])
      if (step <= 0) return { horizons: [], error: `步长必须为正整数：${step}` }
      if (a > b) return { horizons: [], error: `区间起点 ${a} 不能大于终点 ${b}` }
      for (let v = a; v <= b; v += step) out.push(v)
    }
  }
  const bad = out.find((v) => v < 1 || v > 250)
  if (bad) return { horizons: [], error: `预测周期需在 1~250 之间：${bad}` }
  return { horizons: [...new Set(out)] } // 去重保序
}

interface SourceGroup {
  source: 'custom' | 'alpha158' | 'alpha360'
  label: string
  items: { key: string; id: string; name: string; expression: string; original?: string }[]
  loaded?: boolean
}

// 单因子测试面板：勾选自定义公式 / Alpha158 / Alpha360 的因子，逐个快速诊断
// （覆盖率 / 是否0-1信号 / 触发vs未触发收益对比 / IC / RankIC / ICIR）
export default function SingleFactorTestPanel({
  customFormulas,
  defaultUniverse,
  defaultStartDate,
  defaultEndDate,
  defaultLabelHorizon,
  onCapacityChange,
}: SingleFactorTestPanelProps) {
  const [universe, setUniverse] = useState(defaultUniverse)
  const [startDate, setStartDate] = useState(defaultStartDate)
  const [endDate, setEndDate] = useState(defaultEndDate)
  // 预测周期：支持单值 / 逗号枚举(1,2,3,5) / range 区间(1:5:20 → 1,6,11,16，含终点)
  const [labelHorizonText, setLabelHorizonText] = useState<string>(
    String(Number.isFinite(defaultLabelHorizon) ? defaultLabelHorizon : 2),
  )
  // 日期三段输入：开始日期填完整后自动跳到结束日期年份
  const startDateRef = useRef<DateInputHandle>(null)
  const endDateRef = useRef<DateInputHandle>(null)
  // 复权方式：none/forward/backward（与回测一致，默认前复权；前/后复权在比率类因子与收益率上数学等价）
  const [priceAdjust, setPriceAdjust] = useState('forward')
  // 触发组剔除开关：信号日(T)涨停 / 成交日(T+1)涨停 / 成交日停牌（默认全开，保持原行为 + 新增成交日口径）
  const [excludeLimitUpSignal, setExcludeLimitUpSignal] = useState(true)
  const [excludeLimitUpTrade, setExcludeLimitUpTrade] = useState(true)
  const [excludeSuspended, setExcludeSuspended] = useState(true)
  // 日截面剔除：ST(T+1) / 创业板 / 科创板（均只用当日已发布状态，无未来函数）。
  // ST 默认开：ST/*ST/退市整理期个股的收益分布与正常股差异极大，评估触发型信号时
  // 应默认排除；创业板/科创板仍默认关。缺 is_st 标签时后端会明确报错提示取消勾选。
  const [excludeStT1, setExcludeStT1] = useState(true)
  const [excludeGem, setExcludeGem] = useState(false)
  const [excludeKcb, setExcludeKcb] = useState(false)
  // 信号停牌行语义：勾选=SR删行（益盟/通达信"无停牌行"，与回测特征一致，默认）；
  // 取消=停牌日保留 NaN 占位（qlib 官方/聚宽 notebook 口径，对账时用）
  const [suspendRemove, setSuspendRemove] = useState(true)
  // 价格整分：真实价按分取整参与因子计算（仅不复权生效，默认开；与益盟/聚宽整分口径对齐）
  const [priceRound, setPriceRound] = useState(true)
  // 预热缓冲（交易日，v1.18.6）：特征加载多前移 N 个交易日，长回看/动态窗口公式
  // （DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套）在区间首日即有收敛值；0=关闭（旧口径）
  const [warmupDaysText, setWarmupDaysText] = useState('250')
  // 持仓期曲线的「明细 K」（v1.18.45）：留空 = 只算默认档（最强分位组 + 10% 固定档），零额外开销；
  // 每个额外 K 约 +0.1~0.2s（后端要为该 K 再取一次当期名单 + 算三档成本）。
  // 填了之后弹窗里可在这些 K 之间**纯前端切换**，不用重跑。
  const [detailKText, setDetailKText] = useState('')

  // 事件研究弹窗：0/1 稀疏信号的"触发事件收益分布"。
  // 动机：稀疏信号按日配对检验在触发日只有 1 只票时会退化成单票收益序列，均值/显著性
  // 不可信；事件研究以"每次触发"为样本单位，给出概率/赔率的真实画像。
  const [estOpen, setEstOpen] = useState(false)
  const [estReq, setEstReq] = useState<EventStudyRequest | null>(null)
  const [estData, setEstData] = useState<EventStudyResult | null>(null)
  const [estName, setEstName] = useState('')
  // 该行的日配对稳定性字段（t/胜率）：供弹窗与表格共用同一套判定门槛（v1.18.32）
  const [estPair, setEstPair] = useState<{ t?: number | null; win?: number | null } | null>(null)

  // 持仓期收益曲线弹窗（v1.18.45，连续因子）：曲线/敏感度数据**随本次测试结果一并返回**
  // ⇒ 打开即看（零重算、纯前端切换 K）；0/1 稀疏信号走「事件研究」那套。
  const [curveOpen, setCurveOpen] = useState(false)
  const [curveRow, setCurveRow] = useState<TestResult | null>(null)
  const [curveName, setCurveName] = useState('')
  const openCurve = (r: TestResult) => {
    setCurveRow(r)
    setCurveName(r.name)
    setCurveOpen(true)
  }

  // 按当前面板参数为某一行结果发起事件研究（参数与单因子测试保持一致）
  const openEventStudy = (r: TestResult) => {
    const wn = Math.floor(Number(warmupDaysText))
    const wu =
      warmupDaysText.trim() === '' || !Number.isFinite(wn) ? undefined : Math.max(0, wn)
    setEstData(r.event_study ?? null)
    setEstName(r.name)
    setEstPair({ t: r.daily_t_hac ?? r.daily_t, win: r.daily_win })
    setEstReq({
      universe,
      start_date: startDate,
      end_date: endDate,
      factor: {
        id: r.id,
        name: r.name,
        expression: r.expression,
        source: r.source,
        source_formula: r.source_formula,
      },
      exclude_limit_up_signal: excludeLimitUpSignal,
      exclude_limit_up_trade: excludeLimitUpTrade,
      exclude_suspended: excludeSuspended,
      exclude_st_t1: excludeStT1,
      exclude_stock_gem: excludeGem,
      exclude_stock_kcb: excludeKcb,
      price_adjust: priceAdjust,
      price_round: priceRound,
      suspend_remove: suspendRemove,
      freeze_suspended_price: true,
      warmup_days: wu,
    })
    setEstOpen(true)
  }

  // 三个因子来源
  const [groups, setGroups] = useState<SourceGroup[]>([
    {
      source: 'custom',
      label: '自定义公式',
      items: customFormulas.map((f) => ({
        key: `custom:${f.id}`,
        id: f.id,
        name: f.name,
        expression: f.expression,
        original: f.text,
      })),
    },
    { source: 'alpha158', label: 'Alpha158', items: [] },
    { source: 'alpha360', label: 'Alpha360', items: [] },
  ])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [progressMsg, setProgressMsg] = useState('')
  const [results, setResults] = useState<TestResult[]>([])
  const [error, setError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [exporting, setExporting] = useState(false)
  const taskIdRef = useRef<string | null>(null)
  const pollTimerRef = useRef<number | null>(null)

  // 组件卸载时停止轮询
  useEffect(
    () => () => {
      if (pollTimerRef.current !== null) window.clearInterval(pollTimerRef.current)
    },
    [],
  )

  // 刷新页面后：若后台仍有 running 的单因子测试任务（上次取消未成功/未完成），
  // 恢复显示进度并继续轮询，避免任务"凭空消失"。
  useEffect(() => {
    let disposed = false
    ;(async () => {
      try {
        const res = await getSingleFactorTestTasks()
        const runningTask = res.tasks?.find((t) => t.status === 'running')
        if (!disposed && runningTask) {
          // 若上次已请求取消（cancel_requested），恢复后仍显示"取消中..."并禁用按钮，
          // 避免误以为还能再次取消；轮询拿到 cancelled 后自动结束。
          const cancelling = runningTask.cancel_requested
          setRunning(true)
          setCancelling(cancelling)
          setProgress(runningTask.progress ?? 1)
          setProgressMsg(cancelling ? '正在取消...' : runningTask.message || '恢复任务...')
          startPolling(runningTask.task_id)
        }
      } catch {
        // 后端暂不支持该接口时静默，不影响正常使用
      }
    })()
    return () => {
      disposed = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 自定义公式变化时同步
  useEffect(() => {
    setGroups((prev) =>
      prev.map((g) =>
        g.source === 'custom'
          ? {
              ...g,
              items: customFormulas.map((f) => ({
                key: `custom:${f.id}`,
                id: f.id,
                name: f.name,
                expression: f.expression,
                original: f.text,
              })),
            }
          : g,
      ),
    )
  }, [customFormulas])

  // 按需加载 Alpha158 / Alpha360 目录
  const loadCatalog = async (source: 'alpha158' | 'alpha360') => {
    if (groups.find((g) => g.source === source)?.loaded) return
    try {
      const c: FactorCatalog = await getFactorCatalog(source === 'alpha158' ? 'Alpha158' : 'Alpha360')
      setGroups((prev) =>
        prev.map((g) =>
          g.source === source
            ? {
                ...g,
                loaded: true,
                items: c.flat.map((f: FactorField) => ({
                  key: `${source}:${f.name}`,
                  id: f.name,
                  name: f.name,
                  expression: f.expression,
                })),
              }
            : g,
        ),
      )
    } catch {
      setError(`加载 ${source === 'alpha158' ? 'Alpha158' : 'Alpha360'} 目录失败`)
    }
  }

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggleAll = (g: SourceGroup, select: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const it of g.items) {
        if (select) next.add(it.key)
        else next.delete(it.key)
      }
      return next
    })
  }

  // 停止轮询并清空任务引用
  const stopPolling = () => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
    taskIdRef.current = null
  }

  // 开始轮询任务进度：每 700ms 刷新一次，直到成功/失败/取消
  const startPolling = (taskId: string) => {
    stopPolling()
    taskIdRef.current = taskId
    pollTimerRef.current = window.setInterval(async () => {
      try {
        const p = await getSingleFactorTestProgress(taskId)
        setProgress(p.progress)
        setProgressMsg(p.message)
        if (p.status === 'success') {
          stopPolling()
          setResults(p.result?.items ?? [])
          setRunning(false)
          setCancelling(false)
          // 任务结束，立即刷新并发显示（占用名额 -1）
          onCapacityChange?.()
        } else if (p.status === 'failed') {
          stopPolling()
          setError(`单因子测试失败：${p.error ?? p.message}`)
          setRunning(false)
          setCancelling(false)
          onCapacityChange?.()
        } else if (p.status === 'cancelled') {
          stopPolling()
          setProgressMsg('已取消')
          setRunning(false)
          setCancelling(false)
          onCapacityChange?.()
        }
      } catch (e: unknown) {
        // 轮询瞬间失败（网络抖动）不中断，下一次轮询继续
        const msg = e instanceof Error ? e.message : String(e)
        if (msg.includes('404')) {
          stopPolling()
          setError('单因子测试任务不存在或已过期')
          setRunning(false)
          setCancelling(false)
          onCapacityChange?.()
        }
      }
    }, 700)
  }

  const run = async () => {
    if (!startDate || !endDate) {
      setError('请填写测试区间')
      return
    }
    // 日期真实性校验：键盘可绕过三段输入的即时校验（如敲出「6 月 31 日」），
    // 必须在此拦下，否则后端会抛「day is out of range for month」。
    if (!isValidDateStr(startDate) || !isValidDateStr(endDate)) {
      setError('日期无效（如 6 月没有 31 日），请重新选择开始/结束日期')
      return
    }
    if (endDate < startDate) {
      setError('结束日期不能早于开始日期')
      return
    }
    const factors = groups.flatMap((g) =>
      g.items
        .filter((it) => selected.has(it.key))
        .map((it) => ({
          id: it.id,
          name: it.name,
          expression: it.expression,
          source: g.source,
          source_formula: it.original ?? it.expression, // 原文随请求走，结果直接回传展示
        })),
    )
    if (factors.length === 0) {
      setError('请至少勾选一个因子')
      return
    }
    // 解析预测周期（单值 / 逗号枚举 / range 区间），前端先校验，后端再兜底
    const parsed = parseHorizons(labelHorizonText)
    if (parsed.error) {
      setError(parsed.error)
      return
    }
    if (parsed.horizons.length === 0) {
      setError('请填写预测周期，如 2 或 1,2,5 或 1:5:20')
      return
    }
    // 预热缓冲（交易日）：空=后端默认（250）；非数字按默认处理，负数取 0，小数取整
    const warmupNum = Math.floor(Number(warmupDaysText))
    const warmup_days =
      warmupDaysText.trim() === '' || !Number.isFinite(warmupNum) ? undefined : Math.max(0, warmupNum)
    // 持仓期曲线的明细 K（留空 = 只算默认档）
    const parsedKs = parseDetailKs(detailKText)
    if (parsedKs.error) {
      setError(parsedKs.error)
      return
    }
    setRunning(true)
    setCancelling(false)
    setError('')
    setResults([])
    setProgress(1)
    setProgressMsg('提交任务...')
    try {
      const resp = await createSingleFactorTest({
        universe,
        start_date: startDate,
        end_date: endDate,
        label_horizon: parsed.horizons[0], // 兼容旧后端（单周期）
        label_horizons: parsed.horizons,
        factors,
        exclude_limit_up_signal: excludeLimitUpSignal,
        exclude_limit_up_trade: excludeLimitUpTrade,
        exclude_suspended: excludeSuspended,
        exclude_st_t1: excludeStT1,
        exclude_stock_gem: excludeGem,
        exclude_stock_kcb: excludeKcb,
        suspend_remove: suspendRemove,
        price_round: priceRound,
        price_adjust: priceAdjust,
        warmup_days,
        topk_list: parsedKs.ks.length ? parsedKs.ks : undefined,
      })
      const task_id = resp?.task_id
      if (!task_id) {
        // 后端仍是旧版（同步返回 items/total），无法轮询进度
        setError('后端接口未返回任务ID（后端版本过旧），请重启后端服务（start_backend.bat）后重试')
        setRunning(false)
        return
      }
      startPolling(task_id)
      // 立即刷新并发显示（留 200ms 给后端线程登记占用/排队，通常毫秒级完成）
      window.setTimeout(() => onCapacityChange?.(), 200)
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(
        detail
          ? `单因子测试失败：${String(detail)}`
          : `单因子测试失败：${e instanceof Error ? e.message : String(e)}`,
      )
      setRunning(false)
      setCancelling(false)
    }
  }

  const cancel = async () => {
    const taskId = taskIdRef.current
    if (!taskId) return
    setCancelling(true)
    setProgressMsg('正在取消...')
    try {
      const r = await cancelSingleFactorTest(taskId)
      if (!r.ok) {
        // 任务已结束，轮询会拿到最终状态
        setProgressMsg(r.message || '任务已结束')
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(`取消失败：${msg}`)
      setCancelling(false)
    }
  }

  // 结果行"编译前公式"：按 (source,id) 回查勾选项原文（自定义公式用用户写的 text；目录因子无原文则回退 qlib 表达式）
  const srcByKey = useMemo(() => {
    const m = new Map<string, string>()
    for (const g of groups)
      for (const it of g.items) if (it.original != null) m.set(`${g.source}:${it.id}`, it.original)
    return m
  }, [groups])

  // 清理结果（释放内存）：删除后端已完成的测试任务/结果，并清空本次展示。运行中任务不受影响。
  const handleClear = async () => {
    setClearing(true)
    try {
      await clearSingleFactorTest()
      setResults([])
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(`清理失败：${msg}`)
    } finally {
      setClearing(false)
    }
  }

  // 百分比口径：×100 显示（覆盖率 / 收益 / 差值）
  const fmt = (v: number | null | undefined, digits = 4, suffix = '') =>
    v === null || v === undefined || Number.isNaN(v) ? '-' : `${(v * 100).toFixed(digits)}${suffix}`
  // 原始小数口径：不加%，直接显示原始值（IC / RankIC / ICIR）
  const fmtRaw = (v: number | null | undefined, digits = 4) =>
    v === null || v === undefined || Number.isNaN(v) ? '-' : v.toFixed(digits)
  // p 值：极小值（<0.001）用科学计数法显示真实量级，toFixed(4) 会退化成 0.0000 失去信息
  const fmtP = (v: number | null | undefined) =>
    v === null || v === undefined || Number.isNaN(v)
      ? '-'
      : v < 1e-300
        ? '<1e-300*' // 双精度浮点下溢为 0，实际 p 极小（约 1e-300 以下）
        : v < 0.001
          ? `${v.toExponential(2)}${v < 0.05 ? '*' : ''}`
          : `${v.toFixed(4)}${v < 0.05 ? '*' : ''}`

  // 导出当前展示结果到 Excel（一个文件两个工作表，纯前端生成不占后端）：
  //   Sheet1 "因子指标"：与界面表格一致的指标（含 error 行，错误信息放"结论"列）
  //   Sheet2 "因子与公式"：因子名 + 用户保存公式/目录表达式（按 因子×来源 去重，仅首个周期行）
  // xlsx 用动态 import（点击才加载）：同事 pull 后未 npm install 时其余功能不受影响，导出给出明确提示；
  // 同时把 ~200KB 的 xlsx 移出首屏包，仅导出时按需下载该 chunk。
  const handleExport = async () => {
    if (results.length === 0) return
    setExporting(true)
    setError('')
    try {
      // CJS 模块的 namespace 兼容 default / 具名两种形态（不同打包/预构建环境下取 utils 都可靠）
      const mod: any = await import('xlsx')
      const XLSX = mod.default ?? mod
      // Q 列数 = 实际分位组数（默认十分位 10；至少 5 列，避免 0/1 信号行的列数与历史不一致）
      const qCols = Math.max(5, ...results.map((r) => r.quintile_ret?.length ?? 0))
      const headers = exportHeaders(qCols)
      const aoa: (string | number)[][] = [headers]
      for (const r of results) {
        const formula = r.source_formula || srcByKey.get(`${r.source}:${r.id}`) || r.expression || ''
        const horizon = r.horizon != null ? String(r.horizon) : '-'
        const dash = '-'
        if (r.error) {
          aoa.push([
            r.name, r.source, horizon, formula,
            ...Array(headers.length - 6).fill(dash),   // 前 4 列 + 末列"结论"放错误信息、再末列空
            `错误：${r.error}`,
          ])
          continue
        }
        const q = r.quintile_ret ?? []
        const qCells = Array.from({ length: qCols }, () => dash as string)
        for (const g of q) if (g.quantile >= 1 && g.quantile <= qCols) qCells[g.quantile - 1] = fmt(g.mean_ret, 3)
        // 0/1 信号的触发组/未触发组逐日截面收益均值（对应界面"分位收益"列双柱），连续因子无此分组故留空
        const dayPair = r.is_binary
          ? [fmt(r.daily_trig_mean, 3), fmt(r.daily_not_mean, 3)]
          : [dash, dash]
        const v = verdictOf(r)
        aoa.push([
          r.name,
          r.source,
          horizon,
          formula,
          fmt(r.coverage, 2),
          r.is_binary ? '0/1' : '连续',
          r.trigger?.count != null ? String(r.trigger.count) : dash,
          fmt(r.trigger?.mean_ret, 3),
          r.not_trigger?.count != null ? String(r.not_trigger.count) : dash,
          fmt(r.not_trigger?.mean_ret, 3),
          fmt(r.diff, 3),
          fmt(r.daily_diff, 3),
          fmtRaw(v.dT, 2),
          fmt(r.daily_win, 1),
          r.daily_n != null ? String(r.daily_n) : dash,
          r.p_value == null ? dash : fmtP(r.p_value),
          ...qCells,
          ...dayPair,
          fmtRaw(r.ic, 4),
          fmtRaw(r.rank_ic, 4),
          fmtRaw(r.icir, 3),
          VERDICT_LABEL[v.kind],
          (() => {
            const pt = esPointOf(r)
            return pt ? fmt(pt.median, 3) : dash
          })(),
        ])
      }
      const seen = new Set<string>()
      const fAoa: (string | number)[][] = [EXPORT_FORMULA_HEADERS]
      for (const r of results) {
        const key = `${r.source}:${r.id}`
        if (seen.has(key)) continue
        seen.add(key)
        fAoa.push([r.name, r.source, r.source_formula || srcByKey.get(key) || r.expression || ''])
      }
      const wb = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), '因子指标')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(fAoa), '因子与公式')
      const now = new Date()
      const pad = (n: number) => String(n).padStart(2, '0')
      XLSX.writeFile(
        wb,
        `单因子测试结果_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.xlsx`,
      )
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      // 动态 import 失败（同事未 npm install）时 e.message 为依赖解析错误，直接提示可操作步骤
      setError(`导出失败：${msg}。若提示缺少 xlsx，请在 frontend 目录执行 npm install 后重试`)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="mt-2 border rounded p-3 bg-slate-50 dark:bg-slate-900 text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-slate-600 dark:text-slate-300">单因子测试</span>
        <span className="text-slate-400">不训练模型，快速诊断因子预测力：稀疏 0/1 信号看"触发 vs 未触发"收益，连续因子看 IC</span>
      </div>

      {/* 参数行：5 参数 flex-1 铺开 + 开始测试按钮同一行。
          在面板 p-3 内边距内布局（与下方"剔除开关"文字对齐），按钮右边缘留出相同边距。 */}
      <div className="flex flex-wrap items-end gap-3 mb-3">
        <label className="flex-1 flex flex-col min-w-[90px]">
          <span className="text-slate-500 mb-1">股票池</span>
          <select className="border rounded px-2 py-1" value={universe} onChange={(e) => setUniverse(e.target.value)}>
            <option value="csi300">沪深300</option>
            <option value="csi500">中证500</option>
            <option value="csi800">中证800</option>
            <option value="csi1000">中证1000</option>
            <option value="all">全部A股</option>
          </select>
        </label>
        <label className="flex-1 flex flex-col min-w-[170px]">
          <span className="text-slate-500 mb-1">开始日期</span>
          <DateInput
            ref={startDateRef}
            className="mt-0.5 w-full"
            value={startDate}
            onChange={setStartDate}
            onComplete={() => endDateRef.current?.focusYear()}
          />
        </label>
        <label className="flex-1 flex flex-col min-w-[170px]">
          <span className="text-slate-500 mb-1">结束日期</span>
          <DateInput ref={endDateRef} className="mt-0.5 w-full" value={endDate} onChange={setEndDate} />
        </label>
        <label className="flex-1 flex flex-col min-w-[120px]">
          <span className="text-slate-500 mb-1">预测周期(天)</span>
          <input
            type="text"
            inputMode="numeric"
            placeholder="如 2 / 1,2,3,5 / 1:5:20"
            title="单个数字=测一个周期；逗号枚举=批量测多个；a:b:c=起点:步长:终点(含终点)，如 1:5:20 → 1,6,11,16"
            className="border rounded px-2 py-1"
            value={labelHorizonText}
            onChange={(e) => setLabelHorizonText(e.target.value)}
          />
        </label>
        <label className="flex-1 flex flex-col min-w-[90px]" title="前复权与后复权在比率类因子/收益率上数学等价（仅价格绝对值不同）">
          <span className="text-slate-500 mb-1">复权方式</span>
          <select
            className="border rounded px-2 py-1"
            value={priceAdjust}
            onChange={(e) => setPriceAdjust(e.target.value)}
          >
            <option value="none">不复权</option>
            <option value="forward">前复权</option>
            <option value="backward">后复权</option>
          </select>
        </label>
        {/* 按钮容器：宽度补偿面板 p-3+border 相对表单 p-6 的 13px×2 偏移，
            使按钮左端与上方表单第 4 列（"单因子测试/收起单因子测试"按钮列）对齐，
            右端保持在面板内容右缘不动。 */}
        <div className="flex items-end md:w-[calc((100%-3rem)/4-6.5px)]">
          {running ? (
            <button
              type="button"
              onClick={cancel}
              disabled={cancelling}
              className="w-full px-3 py-1.5 rounded bg-red-600 text-white text-xs disabled:opacity-50"
            >
              {cancelling ? '取消中...' : `取消 (${Math.round(progress)}%)`}
            </button>
          ) : (
            <button
              type="button"
              onClick={run}
              className="w-full px-3 py-1.5 rounded bg-blue-600 text-white text-xs"
            >
              开始测试
            </button>
          )}
        </div>
      </div>

      {/* 触发组剔除开关行：独立 flex（不参与上面 grid，避免撑高参数行——见 md/两地 git 工作流.md 布局备忘） */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-3">
        <span className="text-slate-500">剔除开关：</span>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="信号日(T)收盘后已知涨停，属选股过滤（涨停后追高风险），无前视"
        >
          <input
            type="checkbox"
            checked={excludeLimitUpSignal}
            onChange={(e) => setExcludeLimitUpSignal(e.target.checked)}
          />
          <span>信号日涨停(T)</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="成交日(T+1)为实际调仓买入日，涨停封板买不到，与回测 BoardAwareExchange 口径一致"
        >
          <input
            type="checkbox"
            checked={excludeLimitUpTrade}
            onChange={(e) => setExcludeLimitUpTrade(e.target.checked)}
          />
          <span>成交日涨停(T+1)</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="成交日(T+1)停牌/无行情，同样买不到"
        >
          <input
            type="checkbox"
            checked={excludeSuspended}
            onChange={(e) => setExcludeSuspended(e.target.checked)}
          />
          <span>成交日停牌</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="成交日(T+1)处于 ST/*ST/退市整理 的样本剔除（用 T+1 当日状态判定，日截面、无未来函数；涨停判定已直接使用交易所标签，覆盖 ST 5% 涨跌停）"
        >
          <input type="checkbox" checked={excludeStT1} onChange={(e) => setExcludeStT1(e.target.checked)} />
          <span>剔除ST(T+1)</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="剔除创业板（SZ30 号段，20% 涨跌幅）；板块归属恒定，无未来函数"
        >
          <input type="checkbox" checked={excludeGem} onChange={(e) => setExcludeGem(e.target.checked)} />
          <span>剔除创业板</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="剔除科创板（SH688，20% 涨跌幅）；板块归属恒定，无未来函数"
        >
          <input type="checkbox" checked={excludeKcb} onChange={(e) => setExcludeKcb(e.target.checked)} />
          <span>剔除科创板</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="信号计算的停牌行语义：勾选=删除停牌日（益盟/通达信『无停牌行』，默认）；取消=停牌日保留为 NaN（qlib 官方 / 聚宽 notebook 口径）。只影响因子信号计算，不影响上方样本剔除；与聚宽对账时取消勾选"
        >
          <input
            type="checkbox"
            checked={suspendRemove}
            onChange={(e) => setSuspendRemove(e.target.checked)}
          />
          <span>停牌删行</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="价格整分：真实价按分取整（ROUND 2 位）参与因子计算。A 股价格本为整分，可消除『后复权价÷复权因子』还原的浮点尾差在指标阈值（如 5/15）边界导致的触发误判，与益盟/聚宽（整分原始价）口径一致。仅『不复权』模式下生效"
        >
          <input
            type="checkbox"
            checked={priceRound}
            onChange={(e) => setPriceRound(e.target.checked)}
          />
          <span>价格整分</span>
        </label>
      </div>

      {/* 预热缓冲（v1.18.6）：长回看/动态窗口公式在评估区间首日的收敛保障 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-3">
        <label
          className="flex items-center gap-1"
          title="特征加载时多前移 N 个交易日（预热缓冲），使 DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套等『扩展天数无法静态推断』的长回看/动态窗口公式在区间首日即有收敛后的因子值。0=关闭（回到旧口径，与 qlib D.features 冷启动逐位对齐）；留空=后端默认 250 交易日（≈1年）。固定窗口公式（MA/REF 等）结果不受影响，出口仍只按评估区间统计。代价：加载耗时/内存随 N 近似线性增加"
        >
          <span className="text-slate-500">预热缓冲：</span>
          <input
            type="text"
            inputMode="numeric"
            className="border rounded px-2 py-0.5 w-16 text-center"
            value={warmupDaysText}
            onChange={(e) => setWarmupDaysText(e.target.value)}
          />
          <span className="text-slate-500">交易日（0=关闭，留空=默认250）</span>
        </label>
      </div>

      {/* 持仓期曲线「明细 K」（v1.18.45）：留空=只算默认档（最强分位组 + 10% 固定档），零额外开销；
          填了之后弹窗里可纯前端切换这些 K 档（不用重跑）。每个额外 K 约 +0.1~0.2s。 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-3">
        <label
          className="flex items-center gap-1"
          title="仅对连续因子有效：持仓期曲线弹窗里可切换的「固定 K 档」。留空 = 只算默认档（超额收益最强的分位组 + 10% 固定档），不增加耗时。支持逗号分隔：50 = 50 只；0.2 或 20% = 日均有效只数的 20%。每个额外 K 约 +0.1~0.2s（后端要重新取该 K 的当期名单并算三档成本）；K 敏感度汇总表（各 K 的换手/年化成本）不受此设置影响、始终全量计算"
        >
          <span className="text-slate-500">明细 K：</span>
          <input
            type="text"
            className="border rounded px-2 py-0.5 w-40 text-center"
            placeholder="留空=默认档"
            value={detailKText}
            onChange={(e) => setDetailKText(e.target.value)}
          />
          <span className="text-slate-500">（如 0.2,50；每个 +0.1~0.2s）</span>
        </label>
      </div>

      {/* 进度条 */}
      {running && (
        <div className="mb-3">
          <div className="h-2 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden">
            <div
              className="h-full rounded bg-blue-600 transition-all duration-300"
              style={{ width: `${Math.max(1, Math.min(100, progress))}%` }}
            />
          </div>
          <p className="mt-1 text-slate-500 text-[11px]">{progressMsg || `进度 ${Math.round(progress)}%`}</p>
        </div>
      )}

      {/* 因子勾选区（三个来源） */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
        {groups.map((g) => (
          <div key={g.source} className="border rounded p-2 bg-white dark:bg-slate-800">
            <div className="flex items-center justify-between mb-1">
              <button
                type="button"
                onClick={() => {
                  if (g.source !== 'custom') loadCatalog(g.source)
                }}
                className={`font-semibold ${
                  g.loaded || g.source === 'custom'
                    ? 'text-slate-600 dark:text-slate-300'
                    : 'text-blue-500 hover:underline'
                }`}
                title={g.source === 'custom' ? '' : '点击加载因子目录'}
              >
                {g.label}
              </button>
              <span className="text-slate-400">
                {g.items.filter((it) => selected.has(it.key)).length}/{g.items.length}
              </span>
            </div>
            {g.loaded || g.source === 'custom' ? (
              <>
                <div className="flex items-center gap-2 mb-1">
                  <button type="button" onClick={() => toggleAll(g, true)} className="text-blue-500 hover:underline">
                    全选
                  </button>
                  <span className="text-slate-300">|</span>
                  <button type="button" onClick={() => toggleAll(g, false)} className="text-blue-500 hover:underline">
                    清空
                  </button>
                </div>
                <div className="max-h-40 overflow-y-auto space-y-1 pr-1">
                  {g.items.map((it) => (
                    <label
                      key={it.key}
                      className={`flex items-center gap-1.5 cursor-pointer rounded px-1.5 py-0.5 border ${
                        selected.has(it.key)
                          ? 'bg-blue-600 text-white border-blue-600'
                          : 'border-slate-200 dark:border-slate-600 hover:border-blue-400'
                      }`}
                      title={it.original ?? it.expression}
                    >
                      <input
                        type="checkbox"
                        className="hidden"
                        checked={selected.has(it.key)}
                        onChange={() => toggle(it.key)}
                      />
                      <span className="truncate">{it.name}</span>
                    </label>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-slate-400 italic">点击标题加载 Alpha158 因子目录</p>
            )}
          </div>
        ))}
      </div>

      {error && <p className="mb-2 text-red-500 text-[11px] break-all">{error}</p>}

      {/* 取消提示 */}
      {!running && !error && progressMsg === '已取消' && (
        <p className="mb-2 text-amber-600 text-[11px]">测试已取消，未生成结果。</p>
      )}

      {/* 清理入口：展示保留到用户主动清理（收起/展开不丢结果）；清理后才清空展示并释放后端内存 */}
      {results.length > 0 && (
        <div className="flex items-center justify-end gap-2 mb-2">
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            title="导出当前展示结果到 Excel（.xlsx，纯前端生成）：工作表1「因子指标」含每行指标与结论；工作表2「因子与公式」为各因子与其保存公式"
            className="px-2 py-1 rounded border text-[11px] text-blue-600 border-blue-300 hover:bg-blue-50 dark:text-blue-400 dark:border-blue-700 dark:hover:bg-blue-950 disabled:opacity-50"
          >
            {exporting ? '导出中...' : '导出结果'}
          </button>
          <button
            type="button"
            onClick={handleClear}
            disabled={clearing}
            title="删除后端已完成的单因子测试任务与结果（释放进程内存），并清空下方本次展示；运行中的任务不受影响"
            className="px-2 py-1 rounded border text-[11px] text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50"
          >
            {clearing ? '清理中...' : '清理结果（释放内存）'}
          </button>
        </div>
      )}

      {/* 结果表格 */}
      {results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="text-slate-500 border-b">
                <th className="text-left py-1 pr-2">因子</th>
                <th className="text-right px-1">周期</th>
                <th className="text-right px-1">覆盖率</th>
                <th className="text-right px-1">信号</th>
                <th className="text-right px-1">触发数</th>
                <th className="text-right px-1">触发收益</th>
                <th className="text-right px-1">未触发数</th>
                <th className="text-right px-1">未触发收益</th>
                <th className="text-right px-1">差值</th>
                <th className="text-right px-1">p值</th>
                <th className="text-right px-1">分位收益</th>
                <th className="text-right px-1">IC</th>
                <th className="text-right px-1">RankIC</th>
                <th className="text-right px-1">ICIR</th>
                <th className="text-right pl-2">结论</th>
                <th
                  className="text-right px-1"
                  title="事件研究：持有 = 该行「周期」时的收益中位数（仅 0/1 信号有值；悬停可看均值/绝对收益胜率/样本数）"
                >
                  中位数
                </th>
                <th
                  className="text-center px-1"
                  title="因子研究（两块，按因子类型自动出现一个）：0/1 二值信号 → 事件研究（触发对齐 T=0 的收益分布）；连续因子 → 持仓期曲线（净值曲线 + 十分位曲线 + 成本敏感度表 + 可切换基准）"
                >
                  因子研究
                </th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                // 展示用的公式：优先用户原文（source_formula 随结果回传，最可靠）；
                // 没有原文时回退到本地按 (source,id) 查询；都没有才用 expression（兜底）。
                // 只在 hover 公式名时显示——因子列底下不再常驻公式行。
                const hoverFormula =
                  r.source_formula || srcByKey.get(`${r.source}:${r.id}`) || r.expression
                // 结论判定统一收敛到模块级 verdictOf（表格"结论"列与导出文件口径一致）
                // 渲染只需 dT（悬停提示）与结论类型；其余判定明细已在 verdictOf 内部消化
                const { dT, kind: verdictKind, watchReason } = verdictOf(r)
                const qr = r.quintile_ret ?? []
                const maxAbs = qr.length > 0 ? Math.max(...qr.map((g) => Math.abs(g.mean_ret))) : 0
                // 分位收益悬停：逐组展示日截面收益（一行一组），最后一行汇总配对日数。
                // 组数由后端 `quantiles` 参数决定（默认十分位 10 组），这里全部按实际组数渲染。
                const qn = qr.length
                const qrTip = qn
                  ? `每日横截面分${qn}组(1=最低值…${qn}=最高值)，日截面平均收益：\n` +
                    qr.map((g) => `第${g.quantile}组: ${(g.mean_ret * 100).toFixed(2)}%`).join('\n') +
                    `\n配对日数：${qr[0]?.n_days ?? '-'}`
                  : ''
                // 0/1 信号的双柱：信号组/非信号组的逐日截面收益均值（日均）
                const dTrig = r.daily_trig_mean ?? null
                const dNot = r.daily_not_mean ?? null
                const hasDaily = dTrig !== null && dNot !== null && Number.isFinite(dTrig) && Number.isFinite(dNot)
                const dMax = hasDaily ? Math.max(Math.abs(dTrig), Math.abs(dNot)) : 0
                // 悬停提示同时显示信号组/非信号组日截面均值 + 配对日差（不论鼠标放在哪个柱子上）
                // t 与"结论/时间集中"判定同口径：优先 HAC 稳健 t（修正自相关/异方差），
                // 旧结果无 HAC 字段则回退普通 t
                const dTipLabel = r.daily_t_hac != null ? 'HAC t' : 't'
                const dailyTip = hasDaily
                  ? `触发组：日截面均值 ${dTrig >= 0 ? '+' : ''}${(dTrig * 100).toFixed(3)}%　|　未触发组：日截面均值 ${dNot >= 0 ? '+' : ''}${(dNot * 100).toFixed(3)}%　|　配对日差：${r.daily_diff != null ? `${r.daily_diff >= 0 ? '+' : ''}${(r.daily_diff * 100).toFixed(3)}%` : '-'}（${dT != null ? `${dTipLabel}=${dT >= 0 ? '+' : ''}${dT.toFixed(1)}` : ''}）　|　配对日数：${r.daily_n ?? '-'}`
                  : ''
                // 差值单元格悬停：配对日 ≥2 给完整检验详情；仅 1 个配对日说明"不足统计推断"；
                // 无配对日（两组无同日样本）明示不计算——避免可见区显示误导性的 0。
                const pairTitle =
                  dT != null
                    ? (() => {
                        const band = r.daily_n > 0 ? 1.96 / Math.sqrt(r.daily_n) : 0
                        const s1 = r.daily_acf1 != null ? Math.abs(r.daily_acf1) > band : null
                        const s5 = r.daily_acf5 != null ? Math.abs(r.daily_acf5) > band : null
                        return [
                          `按日配对检验（${r.daily_n} 个交易日）：`,
                          `日差值均值 ${fmt(r.daily_diff, 3)}%`,
                          `HAC t=${fmtRaw(dT, 2)}（普通 t=${r.daily_t != null ? fmtRaw(r.daily_t, 2) : '-'}）`,
                          `相对收益胜率 ${fmt(r.daily_win, 2)}%（触发组日均 > 未触发组日配对的交易日占比）`,
                          `lag-1 自相关=${r.daily_acf1 ?? '-'}${s1 == null ? '' : s1 ? '（显著）' : '（不显著）'}`,
                          `lag-5 自相关=${r.daily_acf5 ?? '-'}${s5 == null ? '' : s5 ? '（显著）' : '（不显著）'}`,
                          `方差稳定性 后半/前半=${r.daily_var_ratio != null ? `${r.daily_var_ratio}x` : '-'}`,
                          `（HAC 修正自相关与异方差；lag 自相关置信带 ±${band.toFixed(3)}）`,
                        ].join('\n')
                      })()
                    : r.daily_diff != null
                      ? `按日配对检验：仅 ${r.daily_n} 个配对日，不足统计推断；日差 = 当日触发组−未触发组日截面均值差 ${fmt(r.daily_diff, 3)}%`
                      : '无配对日（触发组与非触发组无任何同日样本），不计算日配对差'
                return (
                  <tr key={`${r.id}:${r.horizon ?? '-'}`} className="border-b border-slate-100 dark:border-slate-700">
                    {r.error ? (
                      <td className="py-1 pr-2 text-red-500" colSpan={16}>
                        {r.name}{r.horizon ? `（周期 ${r.horizon} 天）` : ''}：{r.error}
                      </td>
                    ) : (
                      <>
                        <td className="py-1 pr-2" title={hoverFormula}>
                          <span className="font-semibold">{r.name}</span>
                          <span className="text-slate-400 ml-1">[{r.source}]</span>
                        </td>
                        <td className="text-right px-1 whitespace-nowrap text-slate-500">
                          {r.horizon ? `${r.horizon} 天` : '-'}
                        </td>
                        <td className="text-right px-1">{fmt(r.coverage, 2)}</td>
                        <td className="text-right px-1">
                          {r.is_binary ? (
                            <span className="text-emerald-600 font-semibold">0/1</span>
                          ) : (
                            <span className="text-slate-400" title="连续因子按分位数分组：触发 = 前 20% 高分位，未触发 = 后 20% 低分位">
                              连续
                            </span>
                          )}
                        </td>
                        <td className="text-right px-1">
                          <span
                            title={
                              (r.trigger?.limit_up_excluded_t ?? 0) +
                                (r.trigger?.limit_up_excluded_t1 ?? 0) +
                                (r.trigger?.suspended_excluded ?? 0) +
                                (r.trigger?.extra_excluded ?? 0) >
                              0
                                ? `触发组已剔除（数字为剔除后样本数）：信号日T涨停 ${r.trigger?.limit_up_excluded_t} · 成交日T+1涨停 ${r.trigger?.limit_up_excluded_t1} · 成交日停牌 ${r.trigger?.suspended_excluded}${(r.trigger?.extra_excluded ?? 0) > 0 ? ` · ST/创/科剔除 ${r.trigger?.extra_excluded}` : ''}`
                                : undefined
                            }
                          >
                            {r.trigger?.count ?? '-'}
                          </span>
                        </td>
                        <td className="text-right px-1">{fmt(r.trigger?.mean_ret, 3)}</td>
                        <td className="text-right px-1">
                          <span
                            title={
                              (r.not_trigger?.limit_up_excluded_t ?? 0) +
                                (r.not_trigger?.limit_up_excluded_t1 ?? 0) +
                                (r.not_trigger?.suspended_excluded ?? 0) +
                                (r.not_trigger?.extra_excluded ?? 0) >
                              0
                                ? `未触发组已剔除（与触发组同口径，数字为剔除后样本数）：信号日T涨停 ${r.not_trigger?.limit_up_excluded_t} · 成交日T+1涨停 ${r.not_trigger?.limit_up_excluded_t1} · 成交日停牌 ${r.not_trigger?.suspended_excluded}${(r.not_trigger?.extra_excluded ?? 0) > 0 ? ` · ST/创/科剔除 ${r.not_trigger?.extra_excluded}` : ''}`
                                : undefined
                            }
                          >
                            {r.not_trigger?.count ?? '-'}
                          </span>
                        </td>
                        <td className="text-right px-1">{fmt(r.not_trigger?.mean_ret, 3)}</td>
                        <td
                          className={`text-right px-1 font-semibold ${(r.diff ?? 0) >= 0 ? 'text-red-500' : 'text-emerald-600'}`}
                          title={pairTitle}
                        >
                          <div>{fmt(r.diff, 3)}</div>
                          {r.daily_diff != null && (
                            <div className="text-slate-400 font-mono text-[9px] font-normal whitespace-nowrap">
                              日{fmt(r.daily_diff, 3)} · t={dT != null ? fmtRaw(dT, 1) : '-'} · 日胜{r.daily_win != null ? fmt(r.daily_win, 1) : '-'}%
                            </div>
                          )}
                        </td>
                        <td className="text-right px-1">
                          {r.p_value === null ? '-' : fmtP(r.p_value)}
                        </td>
                        <td className="text-right px-1">
                          {r.is_binary && hasDaily && dMax > 0 ? (
                            // 0/1 信号：信号组 vs 非信号组的逐日截面收益均值双柱（悬停直接显示两组数值与配对日差）
                            <div className="inline-flex items-end gap-[3px] h-6 align-bottom">
                              <div
                                className={`w-[6px] ${dTrig >= 0 ? 'bg-emerald-500' : 'bg-red-400'}`}
                                style={{ height: `${Math.max(3, Math.round((Math.abs(dTrig) / dMax) * 20))}px` }}
                                title={dailyTip}
                              />
                              <div
                                className={`w-[6px] ${dNot >= 0 ? 'bg-emerald-500' : 'bg-red-400'}`}
                                style={{ height: `${Math.max(3, Math.round((Math.abs(dNot) / dMax) * 20))}px` }}
                                title={dailyTip}
                              />
                            </div>
                          ) : qr.length > 0 && maxAbs > 0 ? (
                            <div className="inline-flex items-end gap-[3px] h-6 align-bottom" title={qrTip}>
                              {qr.map((g) => {
                                const h = Math.max(3, Math.round((Math.abs(g.mean_ret) / maxAbs) * 20))
                                const c = g.mean_ret >= 0 ? 'bg-emerald-500' : 'bg-red-400'
                                return (
                                  <div
                                    key={g.quantile}
                                    className={`w-[6px] ${c}`}
                                    style={{ height: `${h}px` }}
                                    title={qrTip}
                                  />
                                )
                              })}
                            </div>
                          ) : (
                            '-'
                          )}
                        </td>
                        <td className="text-right px-1">{fmtRaw(r.ic, 4)}</td>
                        <td className="text-right px-1">{fmtRaw(r.rank_ic, 4)}</td>
                        <td className="text-right px-1">{fmtRaw(r.icir, 3)}</td>
                        <td className="text-right pl-2">
                          {verdictKind === 'conflicting' ? (
                            <span className="text-red-600 font-semibold" title="IC/ICIR 与触发收益差方向相反，信号可能由少数触发日主导，横截面方向相反">
                              方向矛盾
                            </span>
                          ) : verdictKind === 'good' ? (
                            <span
                              className="text-emerald-600 font-semibold"
                              title="0/1 信号按事件研究判定：中位数 ≥0.5%、绝对收益胜率 ≥55%，且超额（触发组 − 未触发组·日配对）>0 —— 三者需同时满足；连续因子按 IC/分位判定"
                            >
                              有效✓
                            </span>
                          ) : verdictKind === 'goodReverse' ? (
                            <span
                              className="text-emerald-600 font-semibold"
                              title="连续因子：高分位组收益显著更低且 IC/ICIR 稳定为负，因子值与未来收益负相关，需反向使用（因子值低时买入）"
                            >
                              有效(反向)✓
                            </span>
                          ) : verdictKind === 'lottery' ? (
                            <span
                              className="text-amber-600 font-semibold"
                              title="事件研究：中位数≈0、绝对收益胜率≈50%，但均值显著更高 —— 收益主要由极少数尾部事件（连板/妖股）撑起，多数触发只是白干，不可作为稳定 alpha。点右侧「事件研究」看分布明细"
                            >
                              彩票型
                            </span>
                          ) : verdictKind === 'timeConcentrated' ? (
                            <span
                              className="text-amber-600 font-semibold"
                              title="总差值方向显著但按日配对检验不显著（|t|&lt;2 或相对收益胜率≈50%）：差值主要由少数交易日驱动，逐日无稳定超额，慎用"
                            >
                              时间集中
                            </span>
                          ) : (
                            <span
                              className="text-slate-400 cursor-help underline decoration-dotted decoration-slate-300 underline-offset-2"
                              title={watchReason ?? '未达任何有效判定门槛'}
                            >
                              待观察
                            </span>
                          )}
                        </td>
                        <td className="text-right px-1">
                          {(() => {
                            const pt = esPointOf(r)
                            if (!pt) return <span className="text-slate-300">-</span>
                            const med = pt.median ?? 0
                            const cls =
                              med > 0.005
                                ? 'text-emerald-600'
                                : med < -0.005
                                  ? 'text-red-500'
                                  : 'text-slate-500'
                            return (
                              <span
                                className={cls}
                                title={`事件研究（持有 ${pt.k} 交易日，n=${pt.n}）：中位数 ${fmt(med, 3)}%｜均值 ${fmt(pt.mean, 3)}%｜绝对收益胜率 ${((pt.win ?? 0) * 100).toFixed(2)}%`}
                              >
                                {fmt(med, 3)}
                              </span>
                            )
                          })()}
                        </td>
                        {/* 因子研究：事件研究（0/1）与持仓曲线（连续）合成一列 —— 两者互斥，
                            按因子类型各出现一个；两个都没有时给一个说明性的短横 */}
                        <td className="text-center px-1">
                          <div className="flex items-center justify-center gap-1">
                            {r.is_binary && (
                              <button
                                onClick={() => openEventStudy(r)}
                                className="px-1.5 py-0.5 rounded border border-sky-400 text-sky-600 hover:bg-sky-50 dark:hover:bg-sky-900/30 text-[11px] whitespace-nowrap"
                                title="事件研究（0/1 信号）：把每次触发对齐到 T=0，统计 T+1 买入后 1~N 个交易日的收益分布（概率/赔率）；稀疏信号建议用它替代按日配对检验"
                              >
                                事件研究
                              </button>
                            )}
                            {(r.topk_curves || r.quantile_curves) && (
                              <button
                                onClick={() => openCurve(r)}
                                className="px-1.5 py-0.5 rounded border border-emerald-400 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 text-[11px] whitespace-nowrap"
                                title="持仓期曲线（连续因子）：**起点=1** 的净值曲线（默认 = 超额收益最强的分位组，含三档往返成本）+ 可切换基准 + 十分位净值曲线 + 成本敏感度表。固定 K 档由表单「明细 K」决定（改 K 需重跑）；组合/基准/图例显隐均为纯前端、零重算。⚠ 简化估算，未考虑涨跌停/停牌/流动性冲击，近似净值非逐日盯市"
                              >
                                持仓曲线
                              </button>
                            )}
                            {!r.is_binary && !(r.topk_curves || r.quantile_curves) && (
                              <span
                                className="text-slate-300"
                                title="该行没有可用的因子研究数据（连续因子看「结论」列的错误信息；0/1 信号走事件研究）"
                              >
                                -
                              </span>
                            )}
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="mt-1 text-slate-400">
            触发分组：0/1 信号为"因子值&gt;0.5"；连续因子按分位数分组（触发 = 前 20% 高分位，未触发 = 后 20% 低分位）。触发数为按剔除开关过滤后的数量（涨停/停牌判定统一为涨停价四舍五入口径，板块 10%/20%/30%）：信号日(T)涨停 = 选股过滤无前视；成交日(T+1)涨停与停牌 = 调仓日实际买不到，与回测一致。
            差值 = 触发均值 − 未触发均值（正数说明触发组未来收益更高）；收益按信号日收盘价买入、持有"周期"列对应天数计算（同因子不同周期逐行对比，可看持有期长短对预测力的影响）；p值* 表示 Mann-Whitney U 检验显著（&lt;0.05）。
            IC = 逐日横截面 Pearson 相关均值，ICIR = 平均IC/IC标准差。表中 IC/RankIC/ICIR 均为原始小数（不加%），稳定性阈值 |ICIR|≥0.05（即×100后≥5，日频口径，市值为例0.1以上即为稳定负向）按同一口径判定；覆盖率/收益/差值为 ×100 百分比。
            0/1 信号的「有效✓」需<b>同时</b>满足四条：① 事件研究中位数 ≥0.50%；② 绝对收益胜率 ≥55%；③ <b>日配对超额 ≥ 门槛</b>（门槛 = max(0.5%, 0.025%×持有期)：20 日即 0.50%、40 日 1.00%）—— 0.5% 约合覆盖一次往返交易成本，0.025%/日 约合年化 6% 机会成本，避免 0.08% 这类噪声级超额被判有效；④ <b>日配对稳定</b>（|HAC t| ≥2 或 日胜率 ≥55%）。第③④条用于排除「典型触发赚钱、但整体跑不赢什么都不选 / 逐日无稳定超额」的信号。无基准（旧结果或基准计算失败）时不启用第③条，但第④条仍要求。
            0/1 信号的分位收益列显示"信号组 vs 非信号组"的逐日截面收益均值双柱（每天先算各组平均未来收益，再对所有交易日取均值，防信号聚集虚高），悬停可查看两组数值与配对日差。
            分位收益：连续因子按每日横截面分位（<b>默认十分位 10 组</b>，可选 5 组=旧口径），每组为日截面平均收益（每天先算组内均值、再对所有参与交易日取平均，与 0/1 双柱同口径，避免少数日子集中主导），柱状图可识别非线性关系（单调、U型、倒U型），绿=正收益、红=负收益；勾选剔除开关时，分位样本先按剔除开关过滤再分组，悬停显示各组日截面收益与配对日数。
            若出现"方向矛盾"：diff 为正但 IC/ICIR 稳定为负，说明信号由少数触发日主导，逐日横截面方向相反，慎用。
            若出现"时间集中"：diff 方向显著但按日配对检验（日均差值 t 值 / 相对收益胜率）不显著，说明总差值被少数交易日拉高，逐日看并无稳定超额（典型如暴跌抄底类信号），慎用。结论判定与提示中的 t 均为 Newey-West HAC 稳健 t。
            有效(反向)：连续因子高分位组收益显著更低（diff&lt;0）、IC/ICIR 稳定为负且方向一致，说明因子与未来收益负相关，反向使用（因子值低时买入）有效，常见于市值、流动性等负向因子。
          </p>
        </div>
      )}

      {/* 事件研究弹窗：0/1 稀疏信号的触发事件收益分布（触发对齐 T=0）。
          ErrorBoundary 兜底：弹窗异常时显示错误信息而非整页白屏。 */}
      <EsErrorBoundary>
        <EventStudyModal
          open={estOpen}
          onClose={() => setEstOpen(false)}
          factorName={estName}
          req={estReq}
          data={estData}
          pair={estPair}
        />
      </EsErrorBoundary>

      {/* 持仓期收益曲线弹窗（连续因子）：数据已在结果里 ⇒ 秒开、纯前端切换 K。 */}
      <EsErrorBoundary label="持仓曲线弹窗">
        <TopkCurveModal
          open={curveOpen}
          onClose={() => setCurveOpen(false)}
          name={curveName}
          row={curveRow}
        />
      </EsErrorBoundary>
    </div>
  )
}
