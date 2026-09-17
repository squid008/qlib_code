import { useMemo } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

/**
 * 事件收益**分布图**（v1.19.81，用户 2026-09-17）。
 *
 * 为什么换掉"概率分档表"：分档是**有损压缩** —— 5 个数丢掉了整个形状，而且还得先把档位定死
 * （>10%？>20%？）才知道该看什么。直方图 + 均值/中位数/尾部标注一次给全：
 *   · **形状**：单峰还是双峰、哪一侧尾巴更长（右尾长 = "赚大亏小"；两侧对称 = 纯波动）；
 *   · **位置**：中位数（典型一次触发赚多少）vs 均值（被少数暴涨事件拉高了多少）；
 *   · **尾部**：p01/p05/p95/p99 与极值直接写在下方（不用再选档位）。
 *
 * 分箱由后端给（`dist_edges` + 每 k 的 `counts`）：中间 2% 一档、两侧渐稀、端桶夹住极值
 * ⇒ 各桶计数之和恒等于样本数（不丢样本）。
 */
interface Props {
  /** 分箱边界（长度 = 桶数 + 1） */
  edges?: number[] | null
  /** 各桶计数（长度 = 桶数） */
  counts?: number[] | null
  /** 持有期 k（标题/tooltip 文案用） */
  k: number | null
  /** 同一 k 的分布统计（用于参考线与尾部数字） */
  stat?: {
    n?: number | null
    mean?: number | null
    median?: number | null
    win?: number | null
    p01?: number | null
    p05?: number | null
    p95?: number | null
    p99?: number | null
    min?: number | null
    max?: number | null
  } | null
}

const pctS = (v?: number | null, nd = 2) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

/** 参考线要挂在**已有类目**上：找出 v 落进的那个桶的标签 */
function binLabelOf(rows: { lo: number; hi: number; label: string }[], v?: number | null) {
  if (v == null || !Number.isFinite(v)) return undefined
  const i = rows.findIndex((r) => v >= r.lo && v <= r.hi)
  return i >= 0 ? rows[i].label : undefined
}

export default function EventDistChart({ edges, counts, k, stat }: Props) {
  const rows = useMemo(() => {
    if (!edges || !counts || edges.length < 2 || counts.length !== edges.length - 1) return []
    const total = counts.reduce((a, b) => a + b, 0) || 1
    return counts.map((c, i) => {
      const lo = edges[i]
      const hi = edges[i + 1]
      return {
        i,
        lo,
        hi,
        c,
        p: (c / total) * 100,
        label: `${(lo * 100).toFixed(0)}~${(hi * 100).toFixed(0)}%`,
      }
    })
  }, [edges, counts])

  if (rows.length === 0) {
    return (
      <div className="text-slate-400 text-xs py-6 text-center">
        （该结果没有分布数据：由旧版本后端生成 —— 点「重新计算」即可补上）
      </div>
    )
  }

  const zero = binLabelOf(rows, 0)
  const med = binLabelOf(rows, stat?.median)
  const mean = binLabelOf(rows, stat?.mean)
  const p05 = binLabelOf(rows, stat?.p05)
  const p95 = binLabelOf(rows, stat?.p95)
  const peak = Math.max(...rows.map((r) => r.p), 1)

  return (
    <div>
      <ResponsiveContainer width="100%" height={190}>
        {/* barCategoryGap=0 ⇒ 桶与桶贴合，才像直方图而不是柱状图 */}
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 2 }} barCategoryGap={0}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={4} />
          <YAxis
            tick={{ fontSize: 10 }}
            width={44}
            unit="%"
            domain={[0, Math.ceil(peak * 1.18)]}
            allowDecimals={false}
          />
          <Tooltip
            formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(2)}%` : v)}
            labelFormatter={(l) => `持有 ${k ?? '-'} 日、收益落在 ${l} 的事件占比`}
          />
          <Bar dataKey="p" name="事件占比" maxBarSize={20}>
            {rows.map((r) => (
              <Cell
                key={r.i}
                fill={r.hi <= 0 ? '#ef4444' : r.lo >= 0 ? '#0ea5e9' : '#94a3b8'}
              />
            ))}
          </Bar>
          {zero && <ReferenceLine x={zero} stroke="#64748b" strokeWidth={1} />}
          {med && (
            <ReferenceLine
              x={med}
              stroke="#0284c7"
              strokeWidth={2}
              label={{ value: '中位数', position: 'insideTopRight', fontSize: 10, fill: '#0284c7' }}
            />
          )}
          {mean && (
            <ReferenceLine
              x={mean}
              stroke="#dc2626"
              strokeWidth={2}
              strokeDasharray="5 3"
              label={{ value: '均值', position: 'insideTopLeft', fontSize: 10, fill: '#dc2626' }}
            />
          )}
          {p95 && (
            <ReferenceLine
              x={p95}
              stroke="#f59e0b"
              strokeWidth={1}
              strokeDasharray="2 2"
              label={{ value: 'p95', position: 'top', fontSize: 10, fill: '#f59e0b' }}
            />
          )}
          {p05 && (
            <ReferenceLine
              x={p05}
              stroke="#f59e0b"
              strokeWidth={1}
              strokeDasharray="2 2"
              label={{ value: 'p05', position: 'top', fontSize: 10, fill: '#f59e0b' }}
            />
          )}
        </BarChart>
      </ResponsiveContainer>
      <div className="text-slate-500 mt-1">
        均值 <span className="text-red-500 dark:text-red-400 font-medium">{pctS(stat?.mean)}</span>
        　中位数 <span className="text-sky-600 dark:text-sky-400 font-medium">{pctS(stat?.median)}</span>
        　胜率(&gt;0) {pctS(stat?.win, 1)}　最小 {pctS(stat?.min, 1)}　最大 {pctS(stat?.max, 1)}
      </div>
      <div className="text-slate-500">
        左尾 p01 / p05 ={' '}
        <span className="text-red-500 dark:text-red-400">
          {pctS(stat?.p01)} / {pctS(stat?.p05)}
        </span>
        　右尾 p95 / p99 ={' '}
        <span className="text-sky-600 dark:text-sky-400">
          {pctS(stat?.p95)} / {pctS(stat?.p99)}
        </span>
        <span className="text-slate-400">
          　（右尾明显比左尾长 ⇒ 赚大亏小；两侧差不多 ⇒ 纯波动，均值好看也没用）
        </span>
      </div>
    </div>
  )
}
