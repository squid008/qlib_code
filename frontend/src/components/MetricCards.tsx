import type { BacktestResult } from '../types'

function fmt(v: number | null | undefined, suffix = '%', digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return `${(v * 100).toFixed(digits)}${suffix}`
}

// 夏普比率是无量纲比率（非百分比），直接显示真实值，不做 ×100
function fmtRatio(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return v.toFixed(digits)
}

// 从 nav 计算"超额最大回撤"：超额曲线（value/benchmark）从峰值回撤的最大幅度（负值）
function calcExcessMaxDrawdown(nav?: BacktestResult['nav']): number | null {
  if (!nav || nav.length === 0) return null
  let peak = -Infinity
  let maxDd = 0
  for (const n of nav) {
    if (typeof n.value !== 'number' || typeof n.benchmark !== 'number' || n.benchmark <= 0) {
      continue
    }
    const excess = n.value / n.benchmark
    if (excess > peak) peak = excess
    if (peak > 0) {
      const dd = (excess - peak) / peak
      if (dd < maxDd) maxDd = dd
    }
  }
  return maxDd < 0 ? maxDd : null
}

export default function MetricCards({ result }: { result: BacktestResult }) {
  const excessMaxDd = calcExcessMaxDrawdown(result.nav)

  const cards = [
    { label: '年化收益', value: fmt(result.annualized_return) },
    { label: '超额收益', value: fmt(result.annualized_excess_return) },
    { label: '夏普比率', value: fmtRatio(result.sharpe, 2) },
    { label: '最大回撤', value: fmt(result.max_drawdown) },
    { label: '胜率', value: fmt(result.win_rate) },
    { label: '基准收益', value: fmt(result.benchmark_return) },
    { label: '累计收益', value: fmt(result.total_return) },
    { label: '超额最大回撤', value: fmt(excessMaxDd) },
  ]

  return (
    <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {cards.map((c) => (
        <div key={c.label} className="bg-white dark:bg-slate-800 rounded-xl shadow p-4">
          <div className="text-sm text-slate-500">{c.label}</div>
          <div className="text-2xl font-bold text-blue-600 mt-1">{c.value}</div>
        </div>
      ))}
    </section>
  )
}
