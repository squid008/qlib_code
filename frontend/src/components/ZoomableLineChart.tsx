import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
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
  /** 起点对齐的"锚定曲线"（默认取 `statKey`；`anchor` 模式下所有曲线都除以它的区间起点值） */
  anchorKey?: string
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

/** 起点对齐的三种口径（用户 2026-09-15 问「缩放后基准曲线能不能对齐到净值起点、以 C 为准、
 *  其它成本曲线要不要也对齐、科不科学」，故三种都做成可选，默认最常用的一种）。 */
const ALIGN_MODES = [
  ['each', '各自对齐（每条曲线的区间起点 = 1.0）'],
  ['anchor', '锚定主曲线起点（都除以它的区间起点值）'],
  ['none', '不对齐（显示原始净值）'],
] as const
type AlignMode = (typeof ALIGN_MODES)[number][0]

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
  anchorKey,
}: Props) {
  const n = data.length
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [win, setWin] = useState<[number, number]>([0, Math.max(0, n - 1)])
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ x: number; a: number; b: number } | null>(null)
  const rafRef = useRef<number | null>(null)
  const pendingRef = useRef<{ fx: number; dir: number } | null>(null)
  const [align, setAlign] = useState<AlignMode>('each')
  /** 鼠标所指的数据点索引（`null` = 不在图上 ⇒ 读数列显示**最右端**的值）。
   *
   * ⚠ v1.19.55：不再用 Recharts 的 `<Tooltip>` —— 它那块浮层太大、正好挡住曲线
   *   （用户：「鼠标移动的时候那个显示面板太大了，都挡住后面曲线了」）⇒ 改成**图例下方的读数列**
   *   （标签在上、数值在下，3 位小数；起点对齐时附原始值）。索引由鼠标 X 自己算，不依赖 Recharts 内部状态。 */
  const [hover, setHover] = useState<number | null>(null)

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
    const rect = wrapRef.current?.getBoundingClientRect()
    const span = win[1] - win[0] + 1              // 可见切片长度（此处还读不到下方的 `view`）
    // ① 读数列：把鼠标 X 映射成可见切片里的下标（左轴 56px + 右边距 12px 要扣掉）
    if (rect && span > 1) {
      const fx = (e.clientX - rect.left - 56) / Math.max(1, rect.width - 56 - 12)
      if (fx < -0.02 || fx > 1.02) {
        setHover(null)
      } else {
        setHover(Math.round(Math.min(1, Math.max(0, fx)) * (span - 1)))
      }
    }
    // ② 拖动平移
    const d = dragRef.current
    if (!d) return
    const w = Math.max(1, (rect?.width ?? 600) - 56)
    const shift = Math.round(((e.clientX - d.x) / w) * (d.b - d.a))
    const [na, nb] = clampWin(d.a - shift, d.b - shift)
    setWin([na, nb])
  }
  const endDrag = () => {
    dragRef.current = null
    setDragging(false)
  }

  /** 起点对齐（用户 2026-09-15）。
   *
   *  **为什么科学**：这是"区间归一 / rebase"，只是把每条曲线**除以各自（或锚定曲线）在区间起点
   *  的那个值** —— 一个**线性可逆缩放**，不创造也不销毁信息：曲线之间的比值只差一个**常数**，
   *  相对形状、排序、最大回撤**全都不变**（最大回撤本来就是尺度无关的）。所以它不会造假，
   *  只是把刻度从"绝对净值"换成"区间内相对起点"。
   *  **为什么要防误读**：归一后两条线都从 1.0 出发，会**掩盖起点本身的水平差**（例如 C 8.4992
   *  vs 官方 8.6762 的 2% 差是分红/流水口径造成的、不在区间内发生）⇒ 所以：
   *  ① tooltip 同时给出**原始值**；② 保留"不对齐"档让人随时回看真实水平。
   *  **性能**：O(可见点数 × 曲线数)，和角标同量级（实测 ~10 µs），可忽略。 */
  const anchorKeyOf = anchorKey ?? statKey
  /** ⚠ 只在**缩放后**才归一（用户 2026-09-15：「最开始时展示全部区间，所有曲线都是 1.0 开始的，
   *  选各自对齐曲线不应该变」）。他说的其实不成立 —— 各列**首行本来就不是 1.0**
   *  （A/B = 1 − 首日费；C 0.9057 与聚宽官方 0.9085 是**首日熔断暴跌 −9%** 的真实结果），
   *  所以"按首值归一"在全区间会整体缩放。既然如此，就按他的预期来：
   *  **全区间一律显示原始净值（三档切换完全不变）**，缩放后才按所选口径重锚。 */
  const zoomed = win[0] > 0 || win[1] < n - 1
  /** 只在**数据首点被裁掉**时才重锚（`win[0] > 0`）：
   *  · 全区间 / 缩放到含起点的那段 ⇒ 显示原始净值（与角标的"自建仓起 1.0"口径一致）；
   *  · 首点被裁掉 ⇒ 才按所选口径重锚（这就是"起点对齐"要解决的问题）。 */
  const rebasing = win[0] > 0 && align !== 'none'
  const view = useMemo(() => {
    const slice = data.slice(win[0], win[1] + 1)
    if (!rebasing || slice.length < 2) return slice
    const firstOf = (k: string): number | null => {
      for (const row of slice) {
        const v = row[k]
        if (typeof v === 'number' && Number.isFinite(v) && v !== 0) return v
      }
      return null
    }
    const bases: Record<string, number | null> = {}
    for (const k of keys) bases[k] = firstOf(k)
    const shared = align === 'anchor' && anchorKeyOf ? firstOf(anchorKeyOf) : null
    return slice.map((row) => {
      const o: Record<string, any> = { ...row, __raw: {} as Record<string, unknown> }
      for (const k of keys) {
        const v = row[k]
        o.__raw[k] = v
        // ⚠ `shared` 取不到（锚定曲线在可见区间内没有有限值）时**退回各自起点**，而不是整条曲线变 null
        const den = align === 'anchor' ? (shared ?? bases[k]) : bases[k]
        o[k] = typeof v === 'number' && Number.isFinite(v) && den ? v / den : null
      }
      return o
    })
  }, [data, win, keys, align, anchorKeyOf])

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

  /** 左上角角标：**当前可见区间**的区间收益 / 最大回撤（跟随缩放变动）。
   *
   * ⚠⚠ 起点口径（2026-09-15 用户抓出的 bug）：**净值序列的 1.0 是"建仓前"**，
   *   不是**首行值** —— 首行已经含着首日盈亏（本例 2016-01-04 熔断日 −9%）。
   *   用首行做分母会把累计收益算高：C 8.4992/0.9057−1 = 838% ✗，正确是 8.4992−1 = **749.9%** ✓。
   *   **可用聚宽文件自证**：它末行 `策略收益 = 767.62%`，而 `nav = 8.6762` ⇒ 基点确实是 1.0 ✓。
   *   ⇒ 规则：窗口**从数据首点开始**时基点取 **1.0**；缩放后（首点被裁掉）取**窗口首值**。
   *
   * 性能：只对**可见切片**做一次 O(点数) 扫描 ⇒ 微秒级；真正的成本是 Recharts 重绘本身。
   * 滚轮已用 rAF 合并 ⇒ 每帧最多重算一次。
   */
  const stat = useMemo(() => {
    if (!statKey) return null
    const vals: number[] = []
    for (const row of view) {
      const v = row[statKey]
      if (typeof v === 'number' && Number.isFinite(v)) vals.push(v)
    }
    if (vals.length < 2) return null
    // 原点 = 建仓前的 1.0（窗口只要还包含数据首点就用它）；首点被裁掉 ⇒ 退化为"窗口内相对收益"
    const fromStart = win[0] === 0
    const base = fromStart ? 1.0 : vals[0]
    let peak = base                       // peak 初值 = base ⇒ 首日跌幅也计入最大回撤 ✓
    let mdd = 0
    for (const v of vals) {
      if (v > peak) peak = v
      const dd = v / peak - 1
      if (dd < mdd) mdd = dd
    }
    return {
      ret: vals[vals.length - 1] / base - 1,
      mdd,
      n: vals.length,
      fromStart,
      from: String(view[0]?.[xKey] ?? ''),
      to: String(view[view.length - 1]?.[xKey] ?? ''),
    }
  }, [view, statKey, xKey, win])

  /** 安全的悬停下标：缩放/平移后 `hover` 可能落在切片之外 ⇒ 退回"最右端"（别显示空值）。 */
  const hIdx = hover != null && hover < view.length ? hover : null
  const hRow = hIdx != null ? view[hIdx] : view[view.length - 1]

  /** 读数列的数值格式（用户要求：**三位小数**就够）。 */
  const fmt = format ?? ((v: any) => (v == null || !Number.isFinite(Number(v)) ? '-' : Number(v).toFixed(3)))

  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400 mb-1">
        {/* ⚠ v1.20.32：删掉「滚轮缩放 · 按住拖动平移 · 鼠标移动时下方读数（点标签可隐藏/显示）」
            这行操作提示（交互已足够直观，且下方"显示第 x~y 点 / 共 n 点"本身就在说明 ✓）。 */}
        <span>
          显示第 {win[0] + 1}~{win[1] + 1} 点 / 共 {n} 点
          {view.length >= 2 && (
            <>
              （{String(view[0]?.[xKey])} ~ {String(view[view.length - 1]?.[xKey])}）
            </>
          )}
        </span>
        <label className="flex items-center gap-1">
          缩放后起点对齐
          <select
            className="border border-slate-300 dark:border-slate-600 rounded px-1 py-0.5 text-[11px]"
            value={align}
            onChange={(e) => setAlign(e.target.value as AlignMode)}
          >
            {ALIGN_MODES.map(([v, txt]) => (
              <option key={v} value={v}>
                {v === 'anchor' && anchorKeyOf ? `锚定「${labelOf(anchorKeyOf)}」起点` : txt}
              </option>
            ))}
          </select>
        </label>
        {/* 重置缩放紧挨「缩放后起点对齐」右边（v1.19.58 起的版式）。
            ⚠ v1.19.67 我误当成"1c5e20c 之前的左边位置"滚过头了，v1.19.68 挪回这里。 */}
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
        /* ⚠ `select-none` + 拖动时 `preventDefault`：否则拖动会选中坐标轴/日期文字（用户反馈）；
           另加 `relative` ⇒ 角标以**图表区域**为定位父级（否则会盖住上方控件行） */
        className="relative select-none"
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
            {/* 移动锚点（用户 2026-09-15：「移动的锚点还是要加的，视觉效果好一点，不然鼠标移动
                都不知道锚点在哪里」）：一条**竖向指示线** + 每条曲线在该 x 上的**圆点**。
                ⚠ 用自己的 `hover` 下标画（不依赖 Recharts 的 activeDot —— 我们已不用 <Tooltip>，
                  它的内部 active 索引不一定更新）。 */}
            {hIdx != null && view[hIdx] && (
              <>
                <ReferenceLine x={view[hIdx][xKey]} stroke="#94a3b8" strokeDasharray="3 3" />
                {keys.map((k, i) => {
                  if (hidden?.[k]) return null
                  const v = view[hIdx]?.[k]
                  return typeof v === 'number' && Number.isFinite(v) ? (
                    <ReferenceDot
                      key={`dot-${k}`}
                      x={view[hIdx][xKey]}
                      y={v}
                      r={3.5}
                      fill={colorOf(k, i)}
                      stroke="#fff"
                      strokeWidth={1}
                      isFront
                    />
                  ) : null
                })}
              </>
            )}
          </LineChart>
        </ResponsiveContainer>
        {/* 角标：**当前可见区间**的区间收益 / 最大回撤（跟随缩放实时变动）。
            ⚠ 挂在**图表区域内部**定位（不是整个组件上）：原先挂外层 + `top-7`，
            而顶部控件行（"缩放后起点对齐"那排）比 28px 高 ⇒ 被卡片盖住（用户 2026-09-15 报：
            "这个图有个BUG，挡住了"）。放进图表区后无论控件行多高都不会重叠。
            位置取左上角（好策略净值右边高，放右边会挡），`left-16` 避开 56px 的 Y 轴刻度。
            红涨绿跌（A 股习惯）；`pointer-events-none` ⇒ 不挡拖动/缩放。 */}
        {stat && (
          <div className="pointer-events-none absolute left-16 top-2 z-10 rounded border border-slate-200 bg-white/90 px-2 py-1 text-[11px] leading-snug shadow-sm dark:border-slate-700 dark:bg-slate-900/90">
            <div className="text-slate-500">
              {statKey ? labelOf(statKey) : ''}（{stat.fromStart ? '自建仓起' : '当前区间'}）
            </div>
            <div>
              {stat.fromStart ? '累计收益' : '区间收益'}{' '}
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

      {/* 读数列（取代原来那个盖住曲线的 tooltip）：**上排是标签、下排全是可变值**，
          整行**居中**放在图下方（用户 2026-09-15：要"底下都是可变值"、"整体挪到图的下方中央"）；
          点标签 = 隐藏/显示该曲线。鼠标不在图上时显示**最右端**的值（即"当前净值"）。 */}
      <div className="mt-1 flex flex-wrap items-start justify-center gap-x-6 gap-y-1 text-[11px] select-none">
        <div className="flex flex-col items-start leading-tight text-left text-slate-500">
          <span className="pl-3">{hIdx != null ? '鼠标所指' : '最右端'}</span>
          <span className="tabular-nums text-slate-600 dark:text-slate-300 pl-3">
            {String(hRow?.[xKey] ?? '')}
          </span>
        </div>
        {keys.map((k, i) => {
          const row = hRow
          const v = row?.[k]
          const raw = (row as any)?.__raw?.[k]
          const dim = !!hidden?.[k]
          return (
            <button
              key={k}
              type="button"
              title={`${labelOf(k)}：点一下隐藏/显示`}
              onClick={() => onToggle?.(k)}
              className={`flex flex-col items-start leading-tight text-left ${dim ? 'opacity-40' : ''}`}
            >
              <span className="flex items-center gap-1" style={{ color: colorOf(k, i) }}>
                <span className="inline-block h-2 w-2 rounded-sm" style={{ background: colorOf(k, i) }} />
                <span className={dim ? 'line-through' : ''}>{labelOf(k)}</span>
              </span>
              <span className="tabular-nums text-slate-600 dark:text-slate-300 pl-3">
                {fmt(v)}
                {rebasing && typeof raw === 'number' && Number.isFinite(raw)
                  ? `（原始 ${raw.toFixed(3)}）`
                  : ''}
              </span>
            </button>
          )
        })}
      </div>

    </div>
  )
}
