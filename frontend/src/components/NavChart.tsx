import { useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import type { LayerReturns, NavPoint } from '../types'

// 标签顺序：策略净值 → 超额 → 池内等权 → 基准
const ALL_LINES = ['value', 'excess', 'universe', 'benchmark'] as const
type LineKey = (typeof ALL_LINES)[number]

const LINE_META: Record<LineKey, { name: string; color: string }> = {
  value: { name: '策略净值', color: '#dc2626' },
  excess: { name: '超额', color: '#eab308' }, // 黄色
  // ★ v1.20.51：**池内等权**（全池等权、与分层图 `Group1` **同源** ✓）——
  //   与 `LayerChart` 的「池内等权」**同名同色**（青色虚线 ✓），两张图一眼对应 ✓。
  //   ⚠ 口径：全池等权、日频、**毛收益**（无成本 / 无涨跌停停牌约束 ✓）⇒ 它是"**选股地板**" ✓，
  //     不是"市场基准" ✗（市场基准仍是市值加权的『基准』那条 ✓）。
  //     用法：组合在它**上面**才算真赚到选股/交易的钱 ✓（v1.20.43 加这条就是为此 ✓）。
  universe: { name: '池内等权', color: '#0891b2' },
  benchmark: { name: '基准', color: '#2563eb' },
}

/** 段标签（"段9"/"seg9"）→ 数字段号 ✓（解不出 ⇒ 极大值 ⇒ 排最后 ✓）。 */
const segNo = (label?: string | null): number => {
  const n = Number(String(label ?? '').replace(/[^0-9]/g, ''))
  return Number.isFinite(n) && n > 0 ? n : 10 ** 9
}

/**
 * ★ v1.20.51 把「池内等权」拼成与净值**同轴**的一条连续曲线。
 *
 * 数据形状（⚠ 关键）：`layer_returns.segments[i].groups[*].universe` 是**段内累计收益**
 * —— **段首重置为 0** ✓（2026-09-22 实测铁证：`seg2` 首行 `benchmark=0.0000` ✓）
 * ⇒ 所以必须**跨段链式拼接** ✗（不能当成日收益连乘 ✓，也不能直接用各段末值画 ✓）：
 *
 *     全局值(t ∈ 段 s) = ∏_{k<s}(1 + 该段末累计_k) × (1 + 累计_{s,t})
 *
 * 再按日期对齐到 `nav`（取 ≤ 该日期的最近一个点 ⇒ 前向填充 ✓）。
 * ⚠ 旧产物（v1.20.43 之前）没有 `universe` 列 ⇒ 返回 `null` ⇒ 该线**自动不画** ✓（向后兼容 ✓）。
 */
const buildUniverseSeries = (
  nav: NavPoint[],
  lr?: LayerReturns | null,
): (number | null)[] | null => {
  const segs = ((lr?.segments as { segment?: string; groups?: unknown[] }[] | null) || []).filter(
    (s) => s && Array.isArray(s.groups) && s.groups.length > 0,
  )
  if (segs.length === 0) return null
  const ordered = [...segs].sort((a, b) => segNo(a.segment) - segNo(b.segment))
  const pts: { date: string; value: number }[] = []
  let base = 1
  for (const s of ordered) {
    const gs = (s.groups as { date: string; universe?: number | null }[]).filter(
      (g) => typeof g.universe === 'number',
    )
    if (gs.length === 0) continue
    for (const g of gs) pts.push({ date: g.date, value: base * (1 + (g.universe as number)) })
    base *= 1 + ((gs[gs.length - 1].universe as number) || 0)
  }
  if (pts.length === 0) return null
  pts.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
  let j = 0
  let cur: number | null = null
  return nav.map((n) => {
    while (j < pts.length && pts[j].date <= n.date) {
      cur = pts[j].value
      j++
    }
    return cur
  })
}

// 日期字符串 → 本地时间戳（避免 UTC 时区偏移导致日期错位）
const toTs = (d: string) => new Date(`${d}T00:00:00`).getTime()
const toDateStr = (ts: number) => {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export default function NavChart({
  nav,
  endDate,
  layerReturns,
}: {
  nav?: NavPoint[] | null
  // 回测参数设定的结束日期。对"未跑完"的回测，把 X 轴右界延伸到该日期，
  // 右侧留白 = 尚未跑到的区间，一眼看出进度；不传/已跑完时保持原分类轴。
  endDate?: string | null
  // ★ v1.20.51：分层数据（含「池内等权」列 `universe`）⇒ 在收益曲线上画**池内等权**地板线 ✓
  layerReturns?: LayerReturns | null
}) {
  // legend 点击隐藏/显示曲线（默认全显）
  const [hidden, setHidden] = useState<Record<string, boolean>>({})
  const toggleLine = (key: string) => {
    setHidden((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  if (!nav || nav.length === 0) {
    return (
      <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
        <h2 className="text-lg font-semibold mb-4">净值曲线</h2>
        <p className="text-slate-400 text-sm">暂无净值数据</p>
      </section>
    )
  }

  // v1.19.11：**起点对齐** —— 策略净值与基准各自按**首个有效点锚定为 1.0**（两条曲线必然同起点）。
  // 背景：后端 v1.16.1 起已对基准归一（`normalize_benchmark_curve`），但
  //   ① **运行中（partial）视图**此前落盘的是**未归一**的原始曲线 —— 基准首点含"窗口首日相对前收"
  //      的段外收益（实测 +1.9% ⇒ 基准从 1.019 起，而净值从 0.9997 起）⇒ 图上两线错开（用户反馈）；
  //   ② v1.16.1 **之前**生成的旧 `result.json` 基准同样未归一（历史结果里基准首点为 0.987/1.019…）。
  // 展示层再兜一层（**幂等**：已归一时除以 1 无变化）⇒ 任何来源的数据都同起点 1.0。
  const firstPositive = (key: 'value' | 'benchmark') => {
    for (const n of nav) {
      const v = n[key]
      if (typeof v === 'number' && v > 0) return v
    }
    return null
  }
  const hasBench = nav.some((n) => n.benchmark !== undefined && n.benchmark !== null)
  const v0 = firstPositive('value')
  const b0 = firstPositive('benchmark')
  // ★ v1.20.51：池内等权（同轴 ✓、跨段链式拼接 ✓）；旧产物无该列 ⇒ null ⇒ 不画 ✓
  const uniSeries = buildUniverseSeries(nav, layerReturns)
  const hasUniverse = !!uniSeries && uniSeries.some((v) => typeof v === 'number')
  const u0 = uniSeries ? uniSeries.find((v) => typeof v === 'number' && v > 0) ?? null : null
  // 给每点加 excess = value / benchmark（超额净值倍数，从 1 起步），
  // 用于画"超额曲线"——直观展示策略相对基准的累计优势。
  // ⚠ 分子分母同除各自首点 ⇒ 与锚定前**比值完全相同**，超额曲线不受影响。
  const data = nav.map((n, i) => {
    const value = typeof n.value === 'number' && v0 ? n.value / v0 : n.value
    const benchmark = typeof n.benchmark === 'number' && b0 ? n.benchmark / b0 : n.benchmark
    const rawU = uniSeries ? uniSeries[i] : null
    return {
      ...n,
      value,
      benchmark,
      // 与策略净值/基准同一处理：各自锚定为 1.0 起步 ⇒ 三条线同起点可比 ✓
      universe: typeof rawU === 'number' && u0 ? rawU / u0 : undefined,
      ts: toTs(n.date),
      excess:
        hasBench && typeof value === 'number' && typeof benchmark === 'number' && benchmark > 0
          ? value / benchmark
          : undefined,
    }
  })

  // 未跑完（数据最后一天早于参数结束日）→ 切真实时间轴并延伸右界到 endDate
  const lastDate = data[data.length - 1]?.date
  const extendX = !!endDate && !!lastDate && endDate > lastDate && data.length > 1

  // Y 轴不从 0 开始：从「策略净值/基准/超额的最小值」×0.9 开始
  // excess 是超额倍数（从 1 起步），所以 yMin 至少 1
  let yMin = 1.0
  for (const n of data) {
    if (typeof n.value === 'number') yMin = Math.min(yMin, n.value)
    if (typeof n.benchmark === 'number') yMin = Math.min(yMin, n.benchmark)
    if (typeof n.excess === 'number') yMin = Math.min(yMin, n.excess)
    if (typeof n.universe === 'number') yMin = Math.min(yMin, n.universe)
  }
  const yDomainMin = yMin * 0.9

  // 自定义 Legend payload：固定顺序 [策略净值, 超额, 池内等权, 基准]，缺数据的自动隐藏
  const legendPayload = ALL_LINES.filter((k) => {
    if (k === 'benchmark') return hasBench
    if (k === 'excess') return hasBench // 超额曲线依赖基准
    if (k === 'universe') return hasUniverse // ★ v1.20.51：旧产物无 `universe` 列 ⇒ 不显示 ✓
    return true
  }).map((k) => ({
    value: LINE_META[k].name,
    type: 'line' as const,
    id: k,
    color: LINE_META[k].color,
    inactive: hidden[k],
  }))

  const handleLegendClick = (o: { id?: string }) => {
    if (o.id) toggleLine(o.id)
  }

  return (
    <section id="nav-chart" className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <h2 className="text-lg font-semibold mb-4">收益曲线</h2>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 288 }}>
          <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            {extendX && endDate ? (
              // 未跑完：真实日历时间轴，右界延伸到参数结束日，右侧留白 = 剩余区间
              <XAxis
                dataKey="ts"
                type="number"
                scale="time"
                domain={[data[0].ts, toTs(endDate)]}
                tick={{ fontSize: 12 }}
                tickFormatter={(v: unknown) => toDateStr(Number(v))}
                allowDataOverflow
              />
            ) : (
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
            )}
            <YAxis
              tick={{ fontSize: 12 }}
              domain={[yDomainMin, 'auto']}
              allowDataOverflow={false}
            />
            <Tooltip
              labelFormatter={(label: unknown) =>
                typeof label === 'number' ? toDateStr(label) : String(label)
              }
            />
            <Legend onClick={handleLegendClick} payload={legendPayload} />
            <Line
              type="monotone"
              dataKey="value"
              name={LINE_META.value.name}
              stroke={LINE_META.value.color}
              strokeWidth={3}
              dot={false}
              hide={hidden.value}
            />
            {hasBench && (
              <Line
                type="monotone"
                dataKey="excess"
                name={LINE_META.excess.name}
                stroke={LINE_META.excess.color}
                strokeWidth={1.5}
                dot={false}
                hide={hidden.excess}
              />
            )}
            {/* ★ v1.20.51：**池内等权**地板线（与分层图同名同色 ⇒ 青色虚线 ✓）
                ⚠ 口径：全池等权、日频、毛收益（无成本/无涨跌停停牌约束）⇒ 组合在它上面才算真赚到 ✓ */}
            {hasUniverse && (
              <Line
                type="monotone"
                dataKey="universe"
                name={LINE_META.universe.name}
                stroke={LINE_META.universe.color}
                strokeWidth={2}
                strokeDasharray="6 3"
                dot={false}
                hide={hidden.universe}
              />
            )}
            {hasBench && (
              <Line
                type="monotone"
                dataKey="benchmark"
                name={LINE_META.benchmark.name}
                stroke={LINE_META.benchmark.color}
                strokeWidth={1.5}
                dot={false}
                hide={hidden.benchmark}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
