import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { cancelEventStudy, createEventStudy, getEventStudyProgress } from '../api'
import type { EventStudyRequest, EventStudyResult } from '../api'
import { excessThresholdOf, pairStabilityOf } from './verdictRules'

interface Props {
  open: boolean
  onClose: () => void
  factorName: string
  /** 参数快照（由单因子测试面板当前设置构造；open 时提交一次） */
  req: EventStudyRequest | null
  /** 单因子测试顺带算出的结果（v1.18.7）：有值时直接展示，不再提交任务（秒开） */
  data?: EventStudyResult | null
  /**
   * 该行的日配对稳定性字段（t / 胜率，v1.18.32）：与表格「结论」列共用同一套门槛
   * （超额门槛 max(0.5%, 0.025%×k) + 稳定性 |t|≥2 或 日胜率≥55%），避免两处口径漂移。
   */
  pair?: { t?: number | null; win?: number | null } | null
}

type EstStatus = 'idle' | 'running' | 'success' | 'failed' | 'cancelled'

// 原始小数 → 百分数展示（收益类字段均为小数）
const pct = (v: number | null | undefined, nd = 3) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

const num = (v: number | null | undefined, nd = 1) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

export default function EventStudyModal({ open, onClose, factorName, req, data, pair }: Props) {
  const [status, setStatus] = useState<EstStatus>('idle')
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<EventStudyResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [maxK, setMaxK] = useState(40)
  // 曲线显隐（点击图例切换）。key = recharts 的 dataKey；两张图共用一份状态（键名不重叠）。
  //
  // 默认隐藏 mean / p25 / p75（v1.18.21）：主图只留**成对可比较**的线 ——
  //   ① 日配对：trigger_pair ↔ baseline（可相减 → 差值即下方超额曲线）
  //   ② 事件级中位数：median ↔ baseline_median
  // `mean`（事件级均值）是**该口径下的孤立线**（没有同口径基准），留在主图里极易被
  // 误用来减 `baseline`（日配对口径）—— 本项目已发生过一次（用户据 6.326% − 3.067%
  // 得出不存在的"+3.259% 超额"）。其诊断价值（与 median 的差距 = 是否「彩票型」）
  // 由弹窗的 upside 表与概率表承担，故默认收起、点击图例可随时开启。
  const [hidden, setHidden] = useState<Record<string, boolean>>({
    mean: true,
    p25: true,
    p75: true,
  })
  const toggleSeries = useCallback((key?: string | number) => {
    if (typeof key !== 'string' || !key) return
    setHidden((h) => ({ ...h, [key]: !h[key] }))
  }, [])

  const taskRef = useRef<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const reqRef = useRef<EventStudyRequest | null>(null)
  reqRef.current = req
  const dataRef = useRef<EventStudyResult | null>(null)
  dataRef.current = data ?? null

  const stopPoll = useCallback(() => {
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const start = useCallback(
    async (mk: number) => {
      const r = reqRef.current
      if (!r) return
      stopPoll()
      setStatus('running')
      setProgress(0)
      setMessage('提交中...')
      setResult(null)
      setError(null)
      try {
        const { task_id } = await createEventStudy({ ...r, max_k: mk })
        taskRef.current = task_id
        timerRef.current = window.setInterval(async () => {
          try {
            const p = await getEventStudyProgress(task_id)
            setProgress(p.progress)
            setMessage(p.message)
            if (p.status === 'success') {
              setResult(p.result ?? null)
              setStatus('success')
              stopPoll()
            } else if (p.status === 'failed') {
              setError(p.error || p.message || '事件研究失败')
              setStatus('failed')
              stopPoll()
            } else if (p.status === 'cancelled') {
              setStatus('cancelled')
              stopPoll()
            }
          } catch {
            /* 单次轮询失败忽略，下个周期重试 */
          }
        }, 1500)
      } catch (e: unknown) {
        const detail =
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(e)
        setError(detail)
        setStatus('failed')
      }
    },
    [stopPoll],
  )

  // open 时：优先直接展示单因子测试顺带算出的结果（秒开）；否则提交独立任务兜底
  useEffect(() => {
    if (open) {
      const d = dataRef.current
      if (d) {
        setResult(d)
        setStatus('success')
        setError(null)
        setMaxK(d.params?.max_k ?? 40)
        stopPoll()
        taskRef.current = null
      } else if (reqRef.current) {
        setMaxK(40)
        void start(40)
      }
    }
    if (!open) {
      stopPoll()
      taskRef.current = null
    }
    return () => stopPoll()
    // 仅在 open 切换时触发（req/data 每次渲染都是新对象，不能进依赖）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const onCancel = async () => {
    if (!taskRef.current) return
    try {
      await cancelEventStudy(taskRef.current)
    } catch {
      /* 忽略 */
    }
  }

  // 后端一次就算出 1..computedMaxK 的全部持有期（单因子测试顺带算的是 40 期），
  // 因此把「最长持有」调小（40 → 10）只是**截断展示**，触发样本完全相同、无需重算；
  // 只有调到超过 computedMaxK 时才需要真正重算（后端没有更长的期数）。
  const computedMaxK = result?.params?.max_k ?? result?.ks?.length ?? 0
  const needRecompute = result != null && maxK > computedMaxK
  const viewCurve = useMemo(
    () => (result?.curve ?? []).filter((c) => c.k <= maxK),
    [result, maxK],
  )

  // 图表数据：curve → {k, mean, median, p25, p75} + 触发组/基准各两条线
  //
  // ⚠ 口径必须分开看（本项目曾因此误读）：同一持有期 k 有**两套互不可比的均值口径**。
  //   · 事件级（每个事件 1 票）：curve[].mean / curve[].median（触发组）、baseline_median
  //     —— 743 个事件等权平均；极少数暴涨事件会主导均值。
  //   · 日配对（每个配对日 1 票）：trigger_pair（触发组）、baseline
  //     —— 先按触发日取截面均值，再对各配对日平均；等价于"每天等权"。
  //   **只有 trigger_pair 与 baseline 是同口径，可以相减**（= excess）；
  //   **mean 与 baseline 不同口径，相减没有意义**（curve.mean 是事件级）。
  //   trigger_pair 来自后端 baseline.trigger_pair（v1.18.8 起返回）。
  const chartData = useMemo(() => {
    const bl = result?.baseline
    const baseMap = new Map<number, number | null>()
    const baseMedMap = new Map<number, number | null>()
    const trigPairMap = new Map<number, number | null>()
    if (bl && bl.ks) {
      bl.ks.forEach((k, i) => {
        baseMap.set(k, bl.baseline?.[i] ?? null)
        baseMedMap.set(k, bl.baseline_median?.[i] ?? null)
        trigPairMap.set(k, bl.trigger_pair?.[i] ?? null)
      })
    }
    return viewCurve.map((c) => {
      const b = baseMap.get(c.k)
      const bm = baseMedMap.get(c.k)
      const tp = trigPairMap.get(c.k)
      return {
        k: c.k,
        mean: c.mean == null ? null : c.mean * 100,
        median: c.median == null ? null : c.median * 100,
        p25: c.p25 == null ? null : c.p25 * 100,
        p75: c.p75 == null ? null : c.p75 * 100,
        // 日配对口径（与 baseline 同口径，可相减）
        trigger_pair: tp == null ? null : tp * 100,
        baseline: b == null ? null : b * 100,
        // 事件级口径（与 median 同口径）
        baseline_median: bm == null ? null : bm * 100,
      }
    })
  }, [viewCurve, result])

  // 超额曲线（触发组 − 基准）：均值口径（日配对）+ 中位数口径（事件级）两条线
  const excessChart = useMemo(() => {
    const bl = result?.baseline
    if (!bl || !bl.ks) return []
    return bl.ks
      .map((k, i) => {
        const v = bl.excess?.[i]
        const vm = bl.excess_median?.[i]
        return {
          k,
          excess: v == null ? null : v * 100,
          excess_median: vm == null ? null : vm * 100,
        }
      })
      .filter((d) => d.k <= maxK)
  }, [result, maxK])

  // 后端是否给出了中位数口径（旧结果没有该字段时不画这条线）
  const hasExcessMedian = useMemo(
    () => excessChart.some((d) => d.excess_median != null),
    [excessChart],
  )

  // 末点超额（摘要卡片用；依赖 viewCurve 以避免引用后面才定义的 lastPoint）
  const excessAtLast = useMemo(() => {
    const bl = result?.baseline
    if (!bl || !bl.ks || viewCurve.length === 0) return null
    const i = bl.ks.indexOf(viewCurve[viewCurve.length - 1].k)
    return i >= 0 ? (bl.excess?.[i] ?? null) : null
  }, [result, viewCurve])

  // 末点「中位数超额」：中位数口径下，典型一次触发的超额（均值超额易被暴涨事件主导）
  const excessMedianAtLast = useMemo(() => {
    const bl = result?.baseline
    if (!bl || !bl.ks || !bl.excess_median || viewCurve.length === 0) return null
    const i = bl.ks.indexOf(viewCurve[viewCurve.length - 1].k)
    return i >= 0 ? (bl.excess_median?.[i] ?? null) : null
  }, [result, viewCurve])

  // 抽查若干持有期用于概率表（同样按 maxK 截断，随「最长持有」联动）
  const probRows = useMemo(() => {
    const want = [1, 3, 5, 10, 20, 40, 60]
    return (result?.prob ?? [])
      .filter((p) => p.k <= maxK && want.includes(p.k))
      .slice(0, 7)
  }, [result, maxK])

  const lastPoint = viewCurve.length > 0 ? viewCurve[viewCurve.length - 1] : null

  // Top / 最差事件：取「逐 k 榜单」里当前 k 的那份，保证与表头「持有 k 日收益」口径一致。
  // 旧结果（无 top_by_k）回退到顶层字段（= max_k 期口径）。
  // 之所以逐 k 取：① 固定用 max_k 期会与表头写明的 k 不符（滑到 2 日却显示 40 日收益）；
  // ② 未持满 max_k 期但在更短 k 上有效的事件（如区间末端贴近数据末尾的触发）会漏掉。
  const topEvents = useMemo(() => {
    const k = lastPoint?.k
    const byK = result?.top_by_k
    if (k != null && byK && byK[String(k)]) return byK[String(k)]
    return result?.top_events ?? []
  }, [result, lastPoint])

  const worstEvents = useMemo(() => {
    const k = lastPoint?.k
    const byK = result?.worst_by_k
    if (k != null && byK && byK[String(k)]) return byK[String(k)]
    return result?.worst_events ?? []
  }, [result, lastPoint])

  const verdictHint = useMemo(() => {
    if (!result || !lastPoint) return null
    const win = lastPoint.win ?? 0
    const med = lastPoint.median ?? 0
    // 当前 k 的日配对超额（与判定同口径）；无基准时为 null（不加此条件）
    const bl = result.baseline
    const bi = bl && bl.ks ? bl.ks.indexOf(lastPoint.k) : -1
    const ex = bi >= 0 ? (bl?.excess?.[bi] ?? null) : null
    const pct = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(3)}%`
    // v1.18.32：与表格「结论」列共用门槛 —— 超额按持有期缩放（max(0.5%, 0.025%×k)）+ 日配对稳定性
    const thr = excessThresholdOf(lastPoint.k)
    const thrTxt = `${(thr * 100).toFixed(3)}%`
    const st = pairStabilityOf({ t: pair?.t, win: pair?.win })
    const scope = `（本提示按当前「最长持有 ${lastPoint.k ?? '?'} 日」口径；表格结论按该行「周期」列口径）`
    if (win >= 0.45 && win <= 0.55 && Math.abs(med) < 0.005) {
      return {
        tone: 'warn' as const,
        text:
          '绝对收益胜率约 50%、中位数≈0：无方向优势，均值几乎全部来自少数尾部事件（典型"彩票型"分布），不可作为稳定 alpha。' +
          scope,
      }
    }
    // 中位数与胜率都好看，但未达超额门槛 / 日配对不稳定 → 不能算"正向事件效应"
    if (med > 0 && win > 0.55 && ((ex != null && ex < thr) || !st.ok)) {
      const bad: string[] = []
      if (ex != null && ex < thr) {
        bad.push(
          `日配对超额 ${pct(ex)} 未达门槛 ${thrTxt}（门槛 = max(0.5%, 0.025%×持有期)，0.5% 约合覆盖一次往返交易成本）`,
        )
      }
      if (!st.ok) {
        bad.push(
          st.missing
            ? '日配对稳定性无法验证（配对日不足 2）'
            : `日配对不稳定（|HAC t| ${st.tVal == null ? '-' : Math.abs(st.tVal).toFixed(2)} <2 且 日胜率 ${
                st.winVal == null ? '-' : `${(st.winVal * 100).toFixed(2)}%`
              } <55%）`,
        )
      }
      return {
        tone: 'warn' as const,
        text: `中位数为正、绝对收益胜率高于 50%，但${bad.join('；')} —— 收益不可交易 / 不可复现，判定为「待观察」。${scope}`,
      }
    }
    if (med > 0.01 && win > 0.55) {
      return {
        tone: 'good' as const,
        text: `中位数为正、绝对收益胜率明显高于 50%，且日配对超额 ≥${thrTxt}、日配对稳定：存在可复制的正向事件效应。${scope}`,
      }
    }
    if (med < -0.005) {
      return { tone: 'bad' as const, text: `中位数为负：多数触发事件是亏的，均值靠少数大赢家——需谨慎。${scope}` }
    }
    return {
      tone: 'plain' as const,
      text: `中位数与绝对收益胜率均处于临界区间，建议结合概率表与明细复核。${scope}`,
    }
  }, [result, lastPoint, pair])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-slate-800 rounded-lg shadow-xl w-[980px] max-w-full max-h-[92vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-slate-700">
          <div>
            <span className="font-semibold text-slate-700 dark:text-slate-200">事件研究</span>
            <span className="text-slate-400 ml-2 text-xs">
              {factorName}
              {result && result.params
                ? ` · ${result.params.universe} · ${result.params.start_date}~${result.params.end_date} · ${result.params.price_adjust}`
                : ''}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label
              className="text-xs text-slate-500"
              title={`显示到第几个持有交易日（后端已算 ${computedMaxK} 期）；不超过它时只切换展示、无需重算`}
            >
              最长持有
              <input
                type="number"
                min={1}
                max={120}
                value={maxK}
                onChange={(e) => setMaxK(Number(e.target.value) || 40)}
                className="ml-1 w-16 px-1 py-0.5 border rounded text-xs dark:bg-slate-700 dark:border-slate-600"
              />
              日
              {computedMaxK > 0 && (
                <span className="ml-1 text-slate-400">(已算 {computedMaxK} 期)</span>
              )}
            </label>
            <button
              onClick={() => void start(maxK)}
              disabled={status === 'running' || !needRecompute}
              title={
                needRecompute
                  ? `重算到 ${maxK} 个交易日（超过后端已算的 ${computedMaxK} 期）`
                  : `后端已算到 ${computedMaxK} 个交易日，${maxK} 期直接切换展示即可，无需重算`
              }
              className="px-2 py-1 text-xs rounded bg-sky-600 text-white disabled:opacity-40"
            >
              {needRecompute ? `重算至 ${maxK} 期` : '重新计算'}
            </button>
            {status === 'running' && (
              <button
                onClick={() => void onCancel()}
                className="px-2 py-1 text-xs rounded border border-red-400 text-red-500"
              >
                取消
              </button>
            )}
            <button onClick={onClose} className="px-2 py-1 text-xs rounded border">
              关闭
            </button>
          </div>
        </div>

        {/* 运行中 */}
        {status === 'running' && (
          <div className="px-4 py-6 text-sm">
            <div className="flex items-center gap-3">
              <div className="flex-1 h-2 bg-slate-100 dark:bg-slate-700 rounded overflow-hidden">
                <div
                  className="h-full bg-sky-500 transition-all"
                  style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
                />
              </div>
              <span className="text-slate-500 w-12 text-right">{progress.toFixed(0)}%</span>
            </div>
            <div className="mt-2 text-slate-500 text-xs">{message}</div>
          </div>
        )}

        {/* 失败 / 取消 */}
        {(status === 'failed' || status === 'cancelled') && (
          <div className="px-4 py-6 text-sm">
            <div className="text-red-500">{error || '任务已取消'}</div>
            <div className="text-slate-400 text-xs mt-1">
              提示：事件研究仅适用于 0/1 二值信号；若该因子在区间内从未触发也会无结果。
            </div>
          </div>
        )}

        {/* 结果 */}
        {status === 'success' && result && result.curve && result.upside && (
          <div className="px-4 py-3 text-xs">
            {/* 摘要卡片 */}
            <div className="grid grid-cols-6 gap-2 mb-3">
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">触发事件数</div>
                <div className="text-base font-semibold">{result.n_events}</div>
                <div className="text-slate-400">
                  剔除前 {result.n_raw}
                  {result.n_short != null && result.n_short > 0 && (
                    <>
                      　·　
                      <span
                        className="text-amber-600 dark:text-amber-400 cursor-help"
                        title={
                          `在 ${result.max_k ?? computedMaxK} 期口径下可统计 ${result.n_aligned ?? '-'} 个；` +
                          `另有 ${result.n_short} 个因信号日贴近数据末尾（未平仓）未计入该期` +
                          (result.n_unaligned ? `，${result.n_unaligned} 个完全无法对齐` : '') +
                          `。各期的有效样本数见曲线 hover；明细见页面底部「未纳入统计的触发（数据不足）」`
                        }
                      >
                        {result.n_short} 个未平仓
                      </span>
                    </>
                  )}
                </div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">持有 {lastPoint?.k ?? '-'} 日中位数</div>
                <div className="text-base font-semibold">{pct(lastPoint?.median)}</div>
                <div className="text-slate-400">均值 {pct(lastPoint?.mean)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">绝对收益胜率（{lastPoint?.k ?? '-'}日）</div>
                <div className="text-base font-semibold">{num(lastPoint?.win, 1)}</div>
                <div className="text-slate-400">p25 {pct(lastPoint?.p25)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">曾达 +50%（期内）</div>
                <div className="text-base font-semibold">{num(result.upside.reach50, 1)}</div>
                <div className="text-slate-400">+20% {num(result.upside.reach20, 1)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">理想最高点卖出中位数</div>
                <div className="text-base font-semibold">{pct(result.upside.median)}</div>
                <div className="text-slate-400">翻倍 {num(result.upside.reach100, 1)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">
                  k={viewCurve.length ? viewCurve[viewCurve.length - 1].k : '-'} 超额
                </div>
                <div className="text-base font-semibold">
                  {excessAtLast == null ? '-' : `${(excessAtLast * 100).toFixed(3)}%`}
                </div>
                <div className="text-slate-400">
                  触发 − 未触发（日均值口径）
                  {excessMedianAtLast != null && (
                    <>　·　中位数 {(excessMedianAtLast * 100).toFixed(3)}%</>
                  )}
                </div>
              </div>
            </div>

            {/* 结论提示 */}
            {verdictHint && (
              <div
                className={`mb-3 rounded px-3 py-2 ${
                  verdictHint.tone === 'warn'
                    ? 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
                    : verdictHint.tone === 'good'
                      ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                      : verdictHint.tone === 'bad'
                        ? 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300'
                        : 'bg-slate-50 text-slate-600 dark:bg-slate-700/40 dark:text-slate-300'
                }`}
              >
                {verdictHint.text}
              </div>
            )}

            {/* 曲线 */}
            <div className="border border-slate-200 dark:border-slate-700 rounded p-2 mb-3">
              <div className="text-slate-500 mb-1">
                持有期收益曲线（T+1 收盘买入，持有 k 个交易日；单位 %）
                <span className="text-amber-600 dark:text-amber-400 ml-2">
                  ⚠ 两套口径不可混算。默认只显示两对<b>同口径</b>曲线：
                  ① 「触发组(日配对)」↔「基准·未触发组(日配对)」（每个配对日 1 票，可相减 → 差值见下方超额曲线）；
                  ② 「中位数(事件级)」↔「基准中位数·未触发组(事件级)」（每个事件 1 票）。
                  「均值(事件级)」与 p25/p75 无同口径基准，已默认隐藏，点击图例可开启——<b>请勿用它减「基准(日配对)」</b>。
                </span>
              </div>
              {/* 触发集中度：配对日数 vs 事件数。两者相差越大，说明触发在时间上越集中，
                  事件级均值越容易被少数「触发密集日」抬高（而日配对口径不受其影响）——
                  这正是「事件级均值为正、日配对超额为负」这类现象的判读依据。 */}
              {result?.baseline?.n_pair_days != null && (
                <div className="text-slate-500 mb-1">
                  配对日 <span className="font-semibold text-slate-700 dark:text-slate-200">{result.baseline.n_pair_days}</span> 天
                  {' / '}
                  事件 <span className="font-semibold text-slate-700 dark:text-slate-200">{result.n_events}</span> 个
                  {result.baseline.n_pair_days > 0 && (
                    <>
                      {'　·　平均每个配对日触发 '}
                      <span className="font-semibold text-slate-700 dark:text-slate-200">
                        {(result.n_events / result.baseline.n_pair_days).toFixed(1)}
                      </span>
                      {' 个'}
                    </>
                  )}
                  {(result.n_events / Math.max(1, result.baseline.n_pair_days)) >= 3 && (
                    <span className="text-amber-600 dark:text-amber-400 ml-2">
                      ⚠ 触发高度集中 —— 事件级均值易被少数密集触发日抬高，请以「日配对」超额为准
                    </span>
                  )}
                </div>
              )}
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={chartData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                  <XAxis dataKey="k" tick={{ fontSize: 11 }} label={{ value: 'k (交易日)', position: 'insideBottomRight', offset: -2, fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={45} />
                  <Tooltip
                    formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(3)}%` : v)}
                    labelFormatter={(l) => `持有 ${l} 个交易日`}
                  />
                  <Legend
                    wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
                    onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                  />
                  {/* 成对口径（每组两条同口径、可互比）：
                      ① 日配对：trigger_pair ↔ baseline —— 可相减，差值即下方「超额曲线」
                      ② 事件级中位数：median ↔ baseline_median
                      默认隐藏的 mean / p25 / p75 见 hidden 初始值处的说明。 */}
                  <Line type="monotone" dataKey="trigger_pair" name="触发组(日配对)" stroke="#7c3aed" dot={false} strokeWidth={2} hide={!!hidden.trigger_pair} />
                  <Line type="monotone" dataKey="baseline" name="基准·未触发组(日配对)" stroke="#94a3b8" dot={false} strokeWidth={1.5} strokeDasharray="4 4" hide={!!hidden.baseline} />
                  <Line type="monotone" dataKey="median" name="中位数(事件级)" stroke="#0284c7" dot={false} strokeWidth={2} hide={!!hidden.median} />
                  <Line type="monotone" dataKey="baseline_median" name="基准中位数·未触发组(事件级)" stroke="#0e7490" dot={false} strokeWidth={1.5} strokeDasharray="2 2" hide={!!hidden.baseline_median} />
                  <Line type="monotone" dataKey="p75" name="p75(事件级·默认隐藏)" stroke="#cbd5e1" dot={false} strokeWidth={1} hide={!!hidden.p75} />
                  <Line type="monotone" dataKey="p25" name="p25(事件级·默认隐藏)" stroke="#cbd5e1" dot={false} strokeWidth={1} hide={!!hidden.p25} />
                  <Line type="monotone" dataKey="mean" name="均值(事件级·无同口径基准)" stroke="#dc2626" dot={false} strokeWidth={2} strokeDasharray="6 2" hide={!!hidden.mean} />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* 超额曲线：触发组 − 基准（未触发组）。两条线口径不同、不可相加：
                - 均值：日配对口径（先按日截面均值、再对配对日平均）→ 易被少数暴涨事件主导
                - 中位数：事件级口径（全部样本收益的中位数）→ 刻画「典型一次触发」的超额
                点击图例可单独隐藏/显示。 */}
            {excessChart.length > 0 && (
              <div className="border border-slate-200 dark:border-slate-700 rounded p-2 mb-3">
                <div className="text-slate-500 mb-1">
                  超额曲线（触发组 − 基准·未触发组；单位 %）
                  <span className="text-slate-400 ml-2">
                    均值＝日配对口径（易被少数暴涨事件主导）；中位数＝事件级口径（典型一次触发的超额）
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={170}>
                  <LineChart data={excessChart} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="k" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} width={45} />
                    <Tooltip
                      formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(3)}%` : v)}
                      labelFormatter={(l) => `持有 ${l} 个交易日`}
                    />
                    <Legend
                      wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
                      onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                    />
                    <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
                    <Line
                      type="monotone"
                      dataKey="excess"
                      name="超额(均值·日配对)"
                      stroke="#7c3aed"
                      dot={false}
                      strokeWidth={2}
                      hide={!!hidden.excess}
                    />
                    {hasExcessMedian && (
                      <Line
                        type="monotone"
                        dataKey="excess_median"
                        name="超额(中位数·事件级)"
                        stroke="#0d9488"
                        dot={false}
                        strokeWidth={2}
                        strokeDasharray="5 3"
                        hide={!!hidden.excess_median}
                      />
                    )}
                  </LineChart>
                </ResponsiveContainer>
                {hasExcessMedian && excessAtLast != null && excessMedianAtLast != null && (
                  <div className="text-slate-400 mt-1">
                    差值（k={viewCurve.length ? viewCurve[viewCurve.length - 1].k : '-'}）：均值超额{' '}
                    {(excessAtLast * 100).toFixed(3)}%　中位数超额{' '}
                    {(excessMedianAtLast * 100).toFixed(3)}%　
                    {excessAtLast - excessMedianAtLast > 0.01
                      ? '→ 均值明显高于中位数，超额主要来自少数暴涨事件'
                      : '→ 两者接近，超额较普遍而非仅靠尾部'}
                  </div>
                )}
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              {/* 概率表 */}
              <div className="border border-slate-200 dark:border-slate-700 rounded p-2">
                <div className="text-slate-500 mb-1">概率：触发后拿到目标收益的事件占比</div>
                <table className="w-full">
                  <thead>
                    <tr className="text-slate-400">
                      <th className="text-right font-normal">k</th>
                      <th className="text-right font-normal">&gt;0</th>
                      <th className="text-right font-normal">&gt;10%</th>
                      <th className="text-right font-normal">&gt;20%</th>
                      <th className="text-right font-normal">&gt;50%</th>
                      <th className="text-right font-normal">&gt;100%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {probRows.map((p) => (
                      <tr key={p.k} className="border-t border-slate-100 dark:border-slate-700">
                        <td className="text-right">{p.k}</td>
                        <td className="text-right">{num(p.gt0, 1)}</td>
                        <td className="text-right">{num(p.gt10, 1)}</td>
                        <td className="text-right">{num(p.gt20, 1)}</td>
                        <td className="text-right">{num(p.gt50, 1)}</td>
                        <td className="text-right">{num(p.gt100, 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="text-slate-400 mt-1">
                  （T+1 收盘买入、持有 k 日；已剔除 T 涨停 / T+1 涨停 / T+1 停牌样本）
                  {result.n_short != null && result.n_short > 0 && (
                    <span className="text-amber-600 dark:text-amber-400">
                      {' '}
                      另有 {result.n_short} 个触发在 {result.max_k ?? computedMaxK} 期上尚未平仓（数据不足），未计入对应 k
                    </span>
                  )}
                </div>
              </div>

              {/* Top 事件 */}
              <div className="border border-slate-200 dark:border-slate-700 rounded p-2">
                <div className="text-slate-500 mb-1">贡献最大的事件（持有 {lastPoint?.k ?? '-'} 日收益）</div>
                <table className="w-full">
                  <thead>
                    <tr className="text-slate-400">
                      <th className="text-left font-normal">代码</th>
                      <th className="text-left font-normal">信号日</th>
                      <th className="text-right font-normal">{lastPoint?.k ?? '-'}日</th>
                      <th className="text-right font-normal">期内最高</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topEvents.slice(0, 8).map((e, i) => (
                      <tr key={`${e.code}:${e.dt}:${i}`} className="border-t border-slate-100 dark:border-slate-700">
                        <td>{e.code}</td>
                        <td>{e.dt}</td>
                        <td className="text-right">{pct(e.ret, 2)}</td>
                        <td className="text-right">{pct(e.max_ret, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* 最差事件 */}
            <div className="mt-3 border border-slate-200 dark:border-slate-700 rounded p-2">
              <div className="text-slate-500 mb-1">最差的事件（持有 {lastPoint?.k ?? '-'} 日收益）</div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {worstEvents.map((e, i) => (
                  <span key={`${e.code}:${e.dt}:${i}`} className="text-slate-500">
                    {e.code} <span className="text-slate-400">{e.dt}</span>{' '}
                    <span className="text-red-500">{pct(e.ret, 2)}</span>
                  </span>
                ))}
              </div>
            </div>

            {/* 数据不足（未平仓 / 无法对齐）的事件：此前被静默丢弃，界面上看不出来 */}
            {(result.n_short != null && result.n_short > 0) ||
            (result.n_unaligned != null && result.n_unaligned > 0) ? (
              <div className="mt-3 border border-amber-300 dark:border-amber-700/60 rounded p-2 bg-amber-50/50 dark:bg-amber-900/10">
                <div className="text-amber-700 dark:text-amber-400 mb-1">
                  未纳入统计的触发（数据不足）
                  {result.n_short != null && result.n_short > 0 && (
                    <>
                      　·　<span className="font-semibold">{result.n_short}</span> 个触发未平仓
                      （信号日距数据末尾不足 {result.max_k ?? computedMaxK} 个交易日，无法持满 {result.max_k ?? computedMaxK} 日）
                    </>
                  )}
                  {result.n_unaligned != null && result.n_unaligned > 0 && (
                    <>
                      　·　<span className="font-semibold">{result.n_unaligned}</span> 个无法对齐
                      （信号日或 T+1 缺行情：连买入价都取不到）
                    </>
                  )}
                </div>
                <div className="text-slate-500 mb-1">
                  这些事件不会出现在上面的曲线与「贡献最大 / 最差事件」里（对应 k 的样本数会相应变少）。
                  若数量偏多，说明 <span className="font-semibold">评估区间末端已接近数据末尾</span>，
                  建议把结束日期提前 {result.max_k ?? computedMaxK} 个交易日以上。
                </div>
                {(result.short_events ?? []).length > 0 && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1 max-h-24 overflow-auto">
                    {(result.short_events ?? []).slice(0, 30).map((e, i) => (
                      <span key={`${e.code}:${e.dt}:${i}`} className="text-slate-500">
                        {e.code} <span className="text-slate-400">{e.dt}</span>{' '}
                        <span className="text-amber-600 dark:text-amber-400">已得 {e.n_valid_k} 期</span>
                      </span>
                    ))}
                    {(result.short_events ?? []).length > 30 && (
                      <span className="text-slate-400">
                        …另有 {(result.short_events ?? []).length - 30} 个
                      </span>
                    )}
                  </div>
                )}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
