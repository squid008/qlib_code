import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { cancelEventStudy, createEventStudy, getEventStudyProgress } from '../api'
import type { EventStudyRequest, EventStudyResult } from '../api'

interface Props {
  open: boolean
  onClose: () => void
  factorName: string
  /** 参数快照（由单因子测试面板当前设置构造；open 时提交一次） */
  req: EventStudyRequest | null
  /** 单因子测试顺带算出的结果（v1.18.7）：有值时直接展示，不再提交任务（秒开） */
  data?: EventStudyResult | null
}

type EstStatus = 'idle' | 'running' | 'success' | 'failed' | 'cancelled'

// 原始小数 → 百分数展示（收益类字段均为小数）
const pct = (v: number | null | undefined, nd = 3) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

const num = (v: number | null | undefined, nd = 1) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

export default function EventStudyModal({ open, onClose, factorName, req, data }: Props) {
  const [status, setStatus] = useState<EstStatus>('idle')
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<EventStudyResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [maxK, setMaxK] = useState(40)

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

  // 图表数据：curve → {k, mean, median, p25, p75}（百分数）
  const chartData = useMemo(() => {
    if (!result || !result.curve) return []
    return result.curve.map((c) => ({
      k: c.k,
      mean: c.mean == null ? null : c.mean * 100,
      median: c.median == null ? null : c.median * 100,
      p25: c.p25 == null ? null : c.p25 * 100,
      p75: c.p75 == null ? null : c.p75 * 100,
    }))
  }, [result])

  // 抽查若干持有期用于概率表
  const probRows = useMemo(() => {
    if (!result || !result.prob) return []
    const want = [1, 3, 5, 10, 20, 40, 60]
    return result.prob.filter((p) => want.includes(p.k)).slice(0, 7)
  }, [result])

  const lastPoint =
    result && result.curve && result.curve.length > 0
      ? result.curve[result.curve.length - 1]
      : null

  const verdictHint = useMemo(() => {
    if (!result || !lastPoint) return null
    const win = lastPoint.win ?? 0
    const med = lastPoint.median ?? 0
    if (win >= 0.45 && win <= 0.55 && Math.abs(med) < 0.005) {
      return {
        tone: 'warn' as const,
        text: '胜率约 50%、中位数≈0：无方向优势，均值几乎全部来自少数尾部事件（典型"彩票型"分布），不可作为稳定 alpha。',
      }
    }
    if (med > 0.01 && win > 0.55) {
      return { tone: 'good' as const, text: '中位数为正且胜率明显高于 50%：存在可复制的正向事件效应。' }
    }
    if (med < -0.005) {
      return { tone: 'bad' as const, text: '中位数为负：多数触发事件是亏的，均值靠少数大赢家——需谨慎。' }
    }
    return { tone: 'plain' as const, text: '中位数与胜率处于临界区间，建议结合概率表与明细复核。' }
  }, [result, lastPoint])

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
            <label className="text-xs text-slate-500">
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
            </label>
            <button
              onClick={() => void start(maxK)}
              disabled={status === 'running'}
              className="px-2 py-1 text-xs rounded bg-sky-600 text-white disabled:opacity-40"
            >
              重新计算
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
            <div className="grid grid-cols-5 gap-2 mb-3">
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">触发事件数</div>
                <div className="text-base font-semibold">{result.n_events}</div>
                <div className="text-slate-400">剔除前 {result.n_raw}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">持有 {lastPoint?.k ?? '-'} 日中位数</div>
                <div className="text-base font-semibold">{pct(lastPoint?.median)}</div>
                <div className="text-slate-400">均值 {pct(lastPoint?.mean)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">胜率（{lastPoint?.k ?? '-'}日）</div>
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
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={chartData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                  <XAxis dataKey="k" tick={{ fontSize: 11 }} label={{ value: 'k (交易日)', position: 'insideBottomRight', offset: -2, fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={45} />
                  <Tooltip
                    formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(3)}%` : v)}
                    labelFormatter={(l) => `持有 ${l} 个交易日`}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line type="monotone" dataKey="p75" name="p75" stroke="#cbd5e1" dot={false} strokeWidth={1} />
                  <Line type="monotone" dataKey="p25" name="p25" stroke="#cbd5e1" dot={false} strokeWidth={1} />
                  <Line type="monotone" dataKey="median" name="中位数" stroke="#0284c7" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="mean" name="均值" stroke="#dc2626" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>

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
                    {(result.top_events ?? []).slice(0, 8).map((e, i) => (
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
                {(result.worst_events ?? []).map((e, i) => (
                  <span key={`${e.code}:${e.dt}:${i}`} className="text-slate-500">
                    {e.code} <span className="text-slate-400">{e.dt}</span>{' '}
                    <span className="text-red-500">{pct(e.ret, 2)}</span>
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
