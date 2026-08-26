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
  ReferenceLine,
} from 'recharts'
import type { ICAnalysis, ICSegment } from '../types'

function SegmentTabs({
  segments,
  active,
  onChange,
}: {
  segments: { label: string }[]
  active: string
  onChange: (label: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-2 mb-3">
      {segments.map((s) => (
        <button
          key={s.label}
          onClick={() => onChange(s.label)}
          className={`px-3 py-1 rounded text-xs border ${
            active === s.label
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white dark:bg-slate-700 border-slate-300 dark:border-slate-600'
          }`}
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}

function StatBadges({ seg }: { seg: ICSegment }) {
  return (
    <div className="flex flex-wrap gap-3 text-xs mb-3">
      {seg.mean_ic !== undefined && (
        <span className="px-2 py-1 rounded bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-200">
          平均IC: {seg.mean_ic.toFixed(4)}
        </span>
      )}
      {seg.icir !== undefined && seg.icir !== null && (
        <span className="px-2 py-1 rounded bg-teal-100 text-teal-700 dark:bg-teal-900 dark:text-teal-200">
          ICIR: {seg.icir.toFixed(3)}
        </span>
      )}
      {seg.mean_rank_ic !== undefined && seg.mean_rank_ic !== null && (
        <span className="px-2 py-1 rounded bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200">
          RankIC: {seg.mean_rank_ic.toFixed(4)}
        </span>
      )}
      {seg.rank_icir !== undefined && seg.rank_icir !== null && (
        <span className="px-2 py-1 rounded bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200">
          RankICIR: {seg.rank_icir.toFixed(3)}
        </span>
      )}
    </div>
  )
}

export default function ICChart({ data }: { data?: ICAnalysis | null }) {
  const [tab, setTab] = useState<'train' | 'test'>('test')
  const [active, setActive] = useState('')

  if (!data || (!data.train?.length && !data.test?.length)) {
    return (
      <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
        <h2 className="text-lg font-semibold mb-4">训练集 / 测试集 IC</h2>
        <p className="text-slate-400 text-sm">暂无 IC 数据</p>
      </section>
    )
  }

  const list = tab === 'test' ? data.test || [] : data.train || []

  // 测试集追加"汇总"
  const options: { label: string }[] = list.map((s) => ({ label: s.segment }))
  if (tab === 'test' && data.merged_test?.points?.length) {
    options.push({ label: '汇总' })
  }

  const effective = options.some((o) => o.label === active) ? active : options[0]?.label ?? ''

  let seg: ICSegment | undefined
  if (effective === '汇总' && tab === 'test') {
    seg = data.merged_test ?? undefined
  } else {
    seg = list.find((s) => s.segment === effective)
  }

  // 计算平均IC标线（基于当前展示曲线的全部点）
  const points = seg?.points || []
  let icMean: number | null = null
  if (points.length) {
    const ics = points.map((p) => p.ic).filter((v) => v !== undefined && v !== null)
    icMean = ics.length ? ics.reduce((a, b) => a + b, 0) / ics.length : null
  }

  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <h2 className="text-lg font-semibold">训练集 / 测试集 IC</h2>
        <span className="text-xs text-slate-400">
          IC 为每日横截面 Pearson 相关；平均IC+ICIR 衡量信号稳定度，IR&gt;0.5 相对稳健
        </span>
      </div>

      {/* 训练/测试切换 */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => {
            setTab('test')
            setActive('')
          }}
          className={`px-3 py-1 rounded text-xs border ${
            tab === 'test'
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white dark:bg-slate-700 border-slate-300 dark:border-slate-600'
          }`}
        >
          测试集 IC
        </button>
        <button
          onClick={() => {
            setTab('train')
            setActive('')
          }}
          className={`px-3 py-1 rounded text-xs border ${
            tab === 'train'
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white dark:bg-slate-700 border-slate-300 dark:border-slate-600'
          }`}
        >
          训练集 IC
        </button>
      </div>

      <SegmentTabs segments={options} active={effective} onChange={setActive} />

      {/* 训练集 IC 是样本内指标，存在过拟合虚高，需提示用户勿高估模型真实预测力 */}
      {tab === 'train' && (
        <p className="text-xs mb-3 text-amber-600 dark:text-amber-400">
          ⚠ 训练集 IC 为样本内指标，通常显著高于样本外（过拟合虚高），不代表真实预测力，请以测试集 IC 为准。
        </p>
      )}

      {!seg || points.length === 0 ? (
        <p className="text-slate-400 text-sm">该分段暂无 IC 数据</p>
      ) : (
        <>
          <StatBadges seg={seg} />
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={points} margin={{ top: 15, right: 20, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                {/* 固定 Y 轴 domain：训练集/测试集 IC 在同一尺度下可直观对比 */}
                <YAxis tick={{ fontSize: 12 }} domain={[-0.5, 0.5]} />
                <Tooltip />
                <Legend />
                <ReferenceLine y={0} stroke="#94a3b8" />
                {icMean !== null && (
                  <ReferenceLine y={icMean} stroke="#dc2626" strokeDasharray="4 2" label={{ value: 'avg', position: 'right', fill: '#dc2626', fontSize: 11 }} />
                )}
                <Line
                  type="monotone"
                  dataKey="ic"
                  name="IC"
                  stroke="#2563eb"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="rank_ic"
                  name="RankIC"
                  stroke="#f59e0b"
                  strokeWidth={1.5}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </section>
  )
}
