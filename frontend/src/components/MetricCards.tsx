import type { BacktestResult } from '../types'

function fmt(v: number | null | undefined, suffix = '%', digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return `${(v * 100).toFixed(digits)}${suffix}`
}

export default function MetricCards({ result }: { result: BacktestResult }) {
  const cards = [
    { label: '年化收益', value: fmt(result.annualized_return) },
    { label: '超额收益', value: fmt(result.annualized_excess_return) },
    { label: '夏普比率', value: fmt(result.sharpe, '', 2) },
    { label: '最大回撤', value: fmt(result.max_drawdown) },
    { label: '胜率', value: fmt(result.win_rate) },
    { label: '基准收益', value: fmt(result.benchmark_return) },
    { label: '累计收益', value: fmt(result.total_return) },
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
