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
import type { NavPoint } from '../types'

// 标签顺序：策略净值 → 超额 → 基准
const ALL_LINES = ['value', 'excess', 'benchmark'] as const
type LineKey = (typeof ALL_LINES)[number]

const LINE_META: Record<LineKey, { name: string; color: string }> = {
  value: { name: '策略净值', color: '#dc2626' },
  excess: { name: '超额', color: '#eab308' }, // 黄色
  benchmark: { name: '基准', color: '#2563eb' },
}

export default function NavChart({ nav }: { nav?: NavPoint[] | null }) {
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

  // 给每点加 excess = value / benchmark（超额净值倍数，从 1 起步），
  // 用于画"超额曲线"——直观展示策略相对基准的累计优势
  const hasBench = nav.some((n) => n.benchmark !== undefined && n.benchmark !== null)
  const data = nav.map((n) => ({
    ...n,
    excess:
      hasBench && typeof n.value === 'number' && typeof n.benchmark === 'number' && n.benchmark > 0
        ? n.value / n.benchmark
        : undefined,
  }))

  // Y 轴不从 0 开始：从「策略净值/基准/超额的最小值」×0.9 开始
  // excess 是超额倍数（从 1 起步），所以 yMin 至少 1
  let yMin = 1.0
  for (const n of data) {
    if (typeof n.value === 'number') yMin = Math.min(yMin, n.value)
    if (typeof n.benchmark === 'number') yMin = Math.min(yMin, n.benchmark)
    if (typeof n.excess === 'number') yMin = Math.min(yMin, n.excess)
  }
  const yDomainMin = yMin * 0.9

  // 自定义 Legend payload：固定顺序 [策略净值, 超额, 基准]，超额可在没基准时自动隐藏
  const legendPayload = ALL_LINES.filter((k) => {
    if (k === 'benchmark') return hasBench
    if (k === 'excess') return hasBench // 超额曲线依赖基准
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
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" tick={{ fontSize: 12 }} />
            <YAxis
              tick={{ fontSize: 12 }}
              domain={[yDomainMin, 'auto']}
              allowDataOverflow={false}
            />
            <Tooltip />
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
