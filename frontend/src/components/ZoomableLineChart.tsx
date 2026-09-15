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

/**
 * 可**滚轮缩放 + 拖动平移**的净值曲线图（v1.19.49）。
 *
 * 用户 2026-09-15：「净值对比图要可以鼠标滚轮缩放拖动，20 年曲线很长，我可能要看局部，
 * 缩放到局部的时候曲线要自适应图表高度，不能还是扁的」。
 *
 * 实现要点：
 *  · 用「可见索引窗口 `win=[a,b]`」做缩放（只把切片交给 Recharts）—— 比 `Brush` 更可控，
 *    且能**按可见切片重算 Y 轴范围**（这就是"自适应图表高度"的关键：不重算的话
 *    局部波动在全局 scale 下会压成一条扁线）。
 *  · 滚轮以**光标位置为锚点**缩放（放大时鼠标下那个点不动，符合直觉）；
 *  · 按住拖动平移；双击或点「重置缩放」回到全区间；
 *  · ⚠ `wheel` 必须用**原生非 passive 监听**（React 的 `onWheel` 在现代 React 里是 passive，
 *    `preventDefault()` 无效 ⇒ 页面会跟着一起滚）。
 *  · 每次 `data` 变化（换文件/换参数）**自动重置**窗口，避免停在上一轮的区间上。
 */

interface Props {
  /** 每行要有横轴字段（默认 `date`）+ 各曲线字段 */
  data: Record<string, any>[]
  /** 要画的曲线字段（顺序 = 图例顺序） */
  keys: string[]
  labelOf: (k: string) => string
  colorOf: (k: string, i: number) => string
  dashOf?: (k: string) => string | undefined
  /** 高亮加粗的那条（用户在下拉里选的） */
  focus?: string
  hidden?: Record<string, boolean>
  onToggle?: (k?: string | number) => void
  height?: number
  xKey?: string
  format?: (v: any) => string
  /** 至少保留多少个点（防止缩到没有） */
  minSpan?: number
  /** 右上角"区间收益 / 最大回撤"要算哪条曲线（不给就不显示该角标） */
  statKey?: string
}

/**
 * Y 轴"好看"的刻度步长阶梯（优先整数/0.5/0.25/0.1…）。
 *
 * 用户 2026-09-15：「缩放的时候纵坐标轴搞成整数吧？或者 0.5 这样，不然都是小数看着很费劲」。
 * ⚠ 缩到很窄的区间时（比如净值只在 1.02~1.06）**必须允许小数**（否则只剩 1 个刻度、反而看不懂），
 *   所以做法是"从粗到细挑第一个能给出 2~8 个刻度的档位"，而不是硬取整。
 */
const NICE_STEPS = [
  0.001, 0.002, 0.005, 0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25, 0.5,
  1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000,
]

function niceTicks(lo: number, hi: number, target = 6): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return []
  const rough = (hi - lo) / Math.max(2, target)
  const step = NICE_STEPS.find((s) => s >= rough) ?? rough
  const start = Math.ceil(lo / step - 1e-9) * step
  const out: number[] = []
  for (let i = 0; i < 24; i += 1) {
    const v = start + i * step
    if (v > hi + 1e-9) break
    out.push(Number(v.toFixed(6)))
  }
  return out
}

function tickText(v: number): string {
  if (Math.abs(v) >= 1000) return v.toFixed(0)
  return String(Number(v.toFixed(3)))
}

export default function ZoomableLineChart({
  data,
  keys,
  labelOf,
  colorOf,
  dashOf,
  focus,
  hidden,
  onToggle,
  height = 320,
  xKey = 'date',
  format,
  minSpan = 6,
  statKey,
}: Props) {
  const n = data.length
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [win, setWin] = useState<[number, number]>([0, Math.max(0, n - 1)])
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ x: number; a: number; b: number } | null>(null)
  const rafRef = useRef<number | null>(null)
  const pendingRef = useRef<{ fx: number; dir: number } | null>(null)

  // 数据换了（新结果/换文件）⇒ 重置到全区间（否则会停在上一轮的缩放窗口）
  const sig = `${n}|${String(data[0]?.[xKey] ?? '')}|${String(data[n - 1]?.[xKey] ?? '')}`
  useEffect(() => {
    setWin([0, Math.max(0, n - 1)])
  }, [sig, n])

  const clampWin = useCallback(
    (a: number, b: number): [number, number] => {
      if (n === 0) return [0, 0]
      let span = Math.max(Math.min(minSpan, n), b - a)
      span = Math.min(span, Math.max(1, n - 1))
      let na = Math.max(0, Math.min(n - 1 - span, Math.round(a)))
      if (na < 0) na = 0
      return [na, na + span]
    },
    [n, minSpan],
  )

  // 滚轮缩放（原生非 passive 监听：React 的 onWheel 拦不住默认滚动）
  useEffect(() => {
    const el = wrapRef.current
    if (!el || n < minSpan * 2) return
    // ⚠ 滚轮事件可能一帧来很多次（触摸板尤其）：用 `requestAnimationFrame` **合并到每帧一次**，
    //   否则每个事件都触发一次 Recharts 重渲染 ⇒ 拖手感变卡（也比"节流 100ms"更顺滑）。
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault()
      const rect = el.getBoundingClientRect()
      pendingRef.current = {
        fx: Math.min(1, Math.max(0, (ev.clientX - rect.left) / Math.max(1, rect.width - 56))),
        dir: ev.deltaY < 0 ? -1 : 1,
      }
      if (rafRef.current != null) return
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        const p = pendingRef.current
        if (!p) return
        setWin(([a, b]) => {
          const span = b - a
          const want = Math.round(span * (p.dir < 0 ? 0.78 : 1.28))
          const newSpan = Math.max(minSpan, Math.min(n - 1, want))
          if (newSpan === span) return [a, b]
          const anchor = a + p.fx * span
          return clampWin(anchor - p.fx * newSpan, anchor - p.fx * newSpan + newSpan)
        })
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      el.removeEventListener('wheel', onWheel)
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [n, minSpan, clampWin])

  const onMouseDown = (e: React.MouseEvent) => {
    if (n === 0) return
    // ⚠ 必须 preventDefault：否则拖动时会把坐标轴文字/日期文字**选成蓝色高亮**
    //   （用户 2026-09-15："底下的日期文字和纵坐标轴容易被选中"）+ 容器样式再配 `select-none`。
    e.preventDefault()
    dragRef.current = { x: e.clientX, a: win[0], b: win[1] }
    setDragging(true)
  }
  const onMouseMove = (e: React.MouseEvent) => {
    const d = dragRef.current
    if (!d) return
    const rect = wrapRef.current?.getBoundingClientRect()
    const w = Math.max(1, (rect?.width ?? 600) - 56)
    const shift = Math.round(((e.clientX - d.x) / w) * (d.b - d.a))
    const [na, nb] = clampWin(d.a - shift, d.b - shift)
    setWin([na, nb])
  }
  const endDrag = () => {
    dragRef.current = null
    setDragging(false)
  }

  const view = useMemo(() => data.slice(win[0], win[1] + 1), [data, win])

  /** Y 轴范围按**可见切片**重算（"缩放到局部要自适应高度"），刻度用"好看"的步长。 */
  const { yDomain, yTicks } = useMemo(() => {
    let lo = Infinity
    let hi = -Infinity
    for (const row of view) {
      for (const k of keys) {
        if (hidden?.[k]) continue
        const v = row[k]
        if (typeof v === 'number' && Number.isFinite(v)) {
          if (v < lo) lo = v
          if (v > hi) hi = v
        }
      }
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
      return { yDomain: ['auto', 'auto'] as any, yTicks: undefined as number[] | undefined }
    }
    if (hi === lo) {
      const d = Math.abs(hi) * 0.02 || 0.01
      return { yDomain: [lo - d, hi + d] as any, yTicks: undefined }
    }
    const pad = (hi - lo) * 0.06
    return { yDomain: [lo - pad, hi + pad] as any, yTicks: niceTicks(lo, hi) }
  }, [view, keys, hidden])

  /** 右上角角标：**当前可见区间**的区间收益 / 最大回撤（跟随缩放变动）。
   *
   * 性能：这里只对**可见切片**做一次 O(点数) 扫描（最多几千点）⇒ 微秒级；
   * 真正花时间的是 Recharts 重绘本身，与这个角标无关。滚轮已用 rAF 合并 ⇒ 每帧最多重算一次。
   */
  const stat = useMemo(() => {
    if (!statKey) return null
    const vals: number[] = []
    for (const row of view) {
      const v = row[statKey]
      if (typeof v === 'number' && Number.isFinite(v)) vals.push(v)
    }
    if (vals.length < 2) return null
    let peak = vals[0]
    let mdd = 0
    for (const v of vals) {
      if (v > peak) peak = v
      const dd = v / peak - 1
      if (dd < mdd) mdd = dd
    }
    return {
      ret: vals[vals.length - 1] / vals[0] - 1,
      mdd,
      n: vals.length,
      from: String(view[0]?.[xKey] ?? ''),
      to: String(view[view.length - 1]?.[xKey] ?? ''),
    }
  }, [view, statKey, xKey])

  const zoomed = win[0] > 0 || win[1] < n - 1
  const fmt = format ?? ((v: any) => (v == null ? '-' : Number(v).toFixed(4)))

  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400 mb-1">
        <span>滚轮缩放 · 按住拖动平移</span>
        <span>
          显示第 {win[0] + 1}~{win[1] + 1} 点 / 共 {n} 点
          {view.length >= 2 && (
            <>
              （{String(view[0]?.[xKey])} ~ {String(view[view.length - 1]?.[xKey])}）
            </>
          )}
        </span>
        {zoomed && (
          <button
            type="button"
            className="px-2 py-0.5 rounded border border-slate-300 dark:border-slate-600 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
            onClick={() => setWin([0, Math.max(0, n - 1)])}
          >
            重置缩放
          </button>
        )}
      </div>
      <div
        ref={wrapRef}
        /* ⚠ `select-none` + 拖动时 `preventDefault`：否则拖动会选中坐标轴/日期文字（用户反馈） */
        className="select-none"
        style={{ cursor: dragging ? 'grabbing' : 'grab' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        onDragStart={(e) => e.preventDefault()}
        onDoubleClick={() => setWin([0, Math.max(0, n - 1)])}
      >
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={view} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={xKey} tick={{ fontSize: 10 }} minTickGap={40} />
            <YAxis
              tick={{ fontSize: 11 }}
              domain={yDomain}
              ticks={yTicks}
              tickFormatter={(v: number) => tickText(v)}
              allowDataOverflow
              width={56}
            />
            <Tooltip formatter={fmt} />
            <Legend
              wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
              onClick={(d) => onToggle?.((d as { dataKey?: string }).dataKey)}
            />
            {keys.map((k, i) => (
              <Line
                key={k}
                type="monotone"
                dataKey={k}
                name={labelOf(k)}
                stroke={colorOf(k, i)}
                strokeWidth={k === focus ? 2.4 : 1.3}
                strokeDasharray={dashOf?.(k)}
                dot={false}
                hide={!!hidden?.[k]}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {/* 右上角角标：**当前可见区间**的区间收益 / 最大回撤（跟随缩放实时变动）。
          · 红涨绿跌（A 股习惯）；`pointer-events-none` ⇒ 不会挡住拖动/缩放。
          · 计算只扫可见切片（微秒级），不是性能瓶颈；滚轮已用 rAF 合并到每帧一次。 */}
      {stat && (
        <div className="pointer-events-none absolute right-3 top-7 z-10 rounded border border-slate-200 bg-white/90 px-2 py-1 text-[11px] leading-snug shadow-sm dark:border-slate-700 dark:bg-slate-900/90">
          <div className="text-slate-500">{statKey ? labelOf(statKey) : ''}（当前区间）</div>
          <div>
            区间收益{' '}
            <b className={stat.ret >= 0 ? 'text-red-600' : 'text-emerald-600'}>
              {`${stat.ret >= 0 ? '+' : ''}${(stat.ret * 100).toFixed(2)}%`}
            </b>
          </div>
          <div>
            最大回撤 <b className="text-slate-700 dark:text-slate-200">{(stat.mdd * 100).toFixed(2)}%</b>
          </div>
          <div className="text-slate-400">
            {stat.from} ~ {stat.to}（{stat.n} 点）
          </div>
        </div>
      )}
    </div>
  )
}
