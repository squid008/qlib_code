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

export default function NavChart({ nav }: { nav?: NavPoint[] | null }) {
  if (!nav || nav.length === 0) {
    return (
      <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
        <h2 className="text-lg font-semibold mb-4">净值曲线</h2>
        <p className="text-slate-400 text-sm">暂无净值数据</p>
      </section>
    )
  }

  // Y 轴不从 0 开始：从「策略净值与基准的历史最小值中较小者」×0.9 开始
  let yMin = 1.0
  for (const n of nav) {
    if (typeof n.value === 'number') yMin = Math.min(yMin, n.value)
    if (typeof n.benchmark === 'number') yMin = Math.min(yMin, n.benchmark)
  }
  const yDomainMin = yMin * 0.9

  return (
    <section id="nav-chart" className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <h2 className="text-lg font-semibold mb-4">收益曲线</h2>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={nav} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" tick={{ fontSize: 12 }} />
            <YAxis
              tick={{ fontSize: 12 }}
              domain={[yDomainMin, 'auto']}
              allowDataOverflow={false}
            />
            <Tooltip />
            <Legend />
            <Line
              type="monotone"
              dataKey="value"
              name="策略净值"
              stroke="#dc2626"
              strokeWidth={3}
              dot={false}
            />
            {nav.some((n) => n.benchmark !== undefined) && (
              <Line
                type="monotone"
                dataKey="benchmark"
                name="基准"
                stroke="#2563eb"
                strokeWidth={1.5}
                dot={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
