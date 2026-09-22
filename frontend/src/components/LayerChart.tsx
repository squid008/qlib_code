import { useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  ReferenceLine,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import type { LayerPoint, LayerReturns } from '../types'

const GROUP_COLORS = [
  '#dc2626', // Group1 红
  '#f59e0b', // Group2 橙
  '#2563eb', // Group3 蓝
  '#7c3aed', // Group4 紫
  '#16a34a', // Group5 绿
  '#0f766e', // long-short 深青
]

// 所有可显示的曲线 key（含基准）。默认全显。
const ALL_LINES = ['Group1', 'Group2', 'Group3', 'Group4', 'Group5', 'long_short',
  'universe', 'benchmark'] as const
type LineKey = (typeof ALL_LINES)[number]

const LINE_META: Record<LineKey, { name: string; color: string; dashed?: boolean }> = {
  Group1: { name: 'Group1', color: GROUP_COLORS[0] },
  Group2: { name: 'Group2', color: GROUP_COLORS[1] },
  Group3: { name: 'Group3', color: GROUP_COLORS[2] },
  Group4: { name: 'Group4', color: GROUP_COLORS[3] },
  Group5: { name: 'Group5', color: GROUP_COLORS[4] },
  long_short: { name: '多空(1-5)', color: GROUP_COLORS[5], dashed: true },
  // ★ v1.20.43：**池内等权**（全样本等权收益，与 Group1 **同源** ✓）——
  //   用户 2026-09-21 遇到的"回测跑赢、分层跑不过"，根因是**等权组合 vs 市值加权指数**
  //   的口径差 ✗（2021-01 抱团行情：沪深300 +2.85%，而全 A 等权 ≈ −5.8% ✗）
  //   ⇒ 画出"分子分母同源"的等权基准线 ✓，"模型到底赢没赢"一眼可辨 ✓。
  universe: { name: '池内等权', color: '#0891b2', dashed: true },
  benchmark: { name: '基准', color: '#475569', dashed: true },
}

function SegmentTabs({
  segments,
  active,
  onChange,
}: {
  segments: { label: string; isMerged?: boolean }[]
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
          } ${s.isMerged ? 'font-bold' : ''}`}
        >
          {s.label}
        </button>
      ))}
    </div>
  )
}

export default function LayerChart({ data }: { data?: LayerReturns | null }) {
  const [active, setActive] = useState('')
  // 每条曲线的显隐状态（点击图例切换）
  const [hidden, setHidden] = useState<Record<string, boolean>>({})

  if (!data || (!data.segments?.length && !data.merged)) {
    return (
      <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
        <h2 className="text-lg font-semibold mb-4">分层回测（5组）</h2>
        <p className="text-slate-400 text-sm">暂无分层数据</p>
      </section>
    )
  }

  // 构建可选列表：段1..段N + 汇总（放最后）
  const options: { label: string; isMerged?: boolean }[] = []
  if (data.segments?.length) {
    options.push(...data.segments.map((s) => ({ label: s.segment })))
  }
  if (data.merged?.groups?.length) {
    options.push({ label: '汇总', isMerged: true })
  }

  // 当前激活的分段（默认第一个）
  const effective = options.some((o) => o.label === active) ? active : options[0]?.label ?? ''

  // 找出当前选中的 groups 与是否含基准
  const curSeg =
    (data.segments || []).find((s) => s.segment === effective) ||
    (effective === '汇总' ? data.merged : undefined)
  const groups = curSeg?.groups || []
  const hasBench = groups.some((g) => g.benchmark !== undefined && g.benchmark !== null)
  // ★ v1.20.43：池内等权线是否存在（⚠ 旧落盘数据无 `universe` 列 ⇒ 自动不显示 ✓ 向后兼容 ✓）
  const hasUniverse = groups.some((g) => g.universe !== undefined && g.universe !== null)

  // 图例点击：隐藏/回显某条曲线
  const toggleLine = (key: string) => {
    setHidden((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  // 用于 Legend 的 payload（点击回调）
  const legendPayload = ALL_LINES.filter((k) => {
    if (k === 'benchmark') return hasBench
    if (k === 'universe') return hasUniverse
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

  // v1.19.11：最前面补一行「**起点 = 0**」，把 0 显式画出来（用户反馈：各分组都是 ~0.01 起步、
  // 看不出从 0 开始）。原因：后端 `groups[].Group1..5` 的**首值 = 「第一期结束时」的累计收益**
  // （如 +1.87%），并非 0；而基准列后端本就是 0 起步（`pct_change().fillna(0)`）⇒ 之前只有基准
  // 视觉上"从 0 开始"。做法与「因子研究」页图 2 完全一致（那一页同样在最前面补一行全 0 的「起点」）；
  // X 轴是**类别轴** ⇒ 用 `'起点'` 文本做标签，避免与首个真实日期重复。
  const chartData: LayerPoint[] = groups.length
    ? [
        {
          date: '起点',
          Group1: 0,
          Group2: 0,
          Group3: 0,
          Group4: 0,
          Group5: 0,
          long_short: 0,
          long_average: 0,
          ...(hasUniverse ? { universe: 0 } : {}),
          ...(hasBench ? { benchmark: 0 } : {}),
        },
        ...groups,
      ]
    : groups

  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <h2 className="text-lg font-semibold">分层回测（5组）</h2>
      </div>
      <SegmentTabs
        segments={options}
        active={effective}
        onChange={(label) => {
          setActive(label)
          // 切换分段时重置曲线显隐，避免基准等因历史点击而一直隐藏
          setHidden({})
        }}
      />

      {groups.length === 0 ? (
        <p className="text-slate-400 text-sm">该分段暂无分层数据</p>
      ) : (
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 320 }}>
            <LineChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} domain={['auto', 'auto']} />
              {/* v1.19.8：数据是**累计收益（0 起）**（后端 v1.19.5 起为 `Π(1+r)−1`）——
                  补一条 0 基准线，否则"从 0 开始"在图上不明显（用户反馈分组图看不出 0 基准）。 */}
              <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
              <Tooltip />
              {/* ★ v1.20.52：图例可点隐藏曲线 ⇒ 鼠标变**手型** ✓（与 NavChart 一致 ✓） */}
              <Legend
                onClick={handleLegendClick}
                payload={legendPayload}
                wrapperStyle={{ cursor: 'pointer' }}
              />
              {[1, 2, 3, 4, 5].map((i) => {
                const key = `Group${i}` as LineKey
                return (
                  <Line
                    key={key}
                    type="monotone"
                    dataKey={key}
                    name={LINE_META[key].name}
                    stroke={LINE_META[key].color}
                    strokeWidth={i === 1 ? 2.5 : 1.5}
                    dot={false}
                    hide={hidden[key]}
                  />
                )
              })}
              <Line
                type="monotone"
                dataKey="long_short"
                name={LINE_META.long_short.name}
                stroke={LINE_META.long_short.color}
                strokeWidth={2}
                strokeDasharray="4 2"
                dot={false}
                hide={hidden.long_short}
              />
              {/* ★ v1.20.43：**池内等权**基准线（与 Group1 同源 ⇒ 可直接比 ✓） */}
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
                  strokeWidth={2}
                  strokeDasharray="4 2"
                  dot={false}
                  hide={hidden.benchmark}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
