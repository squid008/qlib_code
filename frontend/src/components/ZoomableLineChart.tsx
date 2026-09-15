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
}: Props) {
  const n = data.length
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [win, setWin] = useState<[number, number]>([0, Math.max(0, n - 1)])
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ x: number; a: number; b: number } | null>(null)

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
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault()
      const rect = el.getBoundingClientRect()
      const fx = Math.min(1, Math.max(0, (ev.clientX - rect.left) / Math.max(1, rect.width - 56)))
      setWin(([a, b]) => {
        const span = b - a
        const want = Math.round(span * (ev.deltaY < 0 ? 0.78 : 1.28))
        const newSpan = Math.max(minSpan, Math.min(n - 1, want))
        if (newSpan === span) return [a, b]
        const anchor = a + fx * span
        return clampWin(anchor - fx * newSpan, anchor - fx * newSpan + newSpan)
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [n, minSpan, clampWin])

  const onMouseDown = (e: React.MouseEvent) => {
    if (n === 0) return
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

  /** Y 轴范围按**可见切片**重算（"缩放到局部要自适应高度"）。 */
  const yDomain = useMemo(() => {
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
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return ['auto', 'auto'] as any
    if (hi === lo) {
      const d = Math.abs(hi) * 0.02 || 0.01
      return [lo - d, hi + d] as any
    }
    const pad = (hi - lo) * 0.06
    return [lo - pad, hi + pad] as any
  }, [view, keys, hidden])

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
        style={{ cursor: dragging ? 'grabbing' : 'grab' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        onDoubleClick={() => setWin([0, Math.max(0, n - 1)])}
      >
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={view} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={xKey} tick={{ fontSize: 10 }} minTickGap={40} />
            <YAxis tick={{ fontSize: 11 }} domain={yDomain} allowDataOverflow width={56} />
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
    </div>
  )
}
