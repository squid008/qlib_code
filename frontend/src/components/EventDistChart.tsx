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
 * 事件收益**分布图**（v1.19.81；v1.19.82 按用户反馈改版）。
 *
 * 为什么换掉"概率分档表"：分档是**有损压缩** —— 5 个数丢掉整个形状，而且还得先把档位定死
 * （>10%？>20%？）才知道该看什么。直方图 + 均值/中位数/尾部标注一次给全：
 *   · **形状**：单峰还是双峰、哪一侧尾巴更长；
 *   · **位置**：中位数（典型一次触发赚多少）vs 均值（被少数暴涨事件拉高了多少）；
 *   · **尾部**：p01/p05/p25/p75/p95/p99 与极值。
 *
 * v1.19.82 修（用户截图：参考线文字贴在图顶被裁掉、中位数与均值靠近时互相压住）：
 *   把统计值**全部挪到图外一行**（颜色标记与参考线同色 ⇒ 仍能对上哪条），
 *   图内只保留线本身 ⇒ 既不裁切、也不重叠；该行整体**居中**，各项之间用 flex 间距隔开
 *   （避免"百分比符号与汉字贴在一起"）。
 */
interface Props {
  /** 分箱边界（长度 = 桶数 + 1） */
  edges?: number[] | null
  /** 各桶计数（长度 = 桶数） */
  counts?: number[] | null
  /** 持有期 k（标题/tooltip 文案用） */
  k: number | null
  /** 同一 k 的分布统计（参考线 + 图外数值行） */
  stat?: {
    n?: number | null
    mean?: number | null
    median?: number | null
    win?: number | null
    p01?: number | null
    p05?: number | null
    p25?: number | null
    p75?: number | null
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

// 参考线配色（图内线 + 图外数值行的色标**必须一致**，否则对不上是哪条）
const C_MED = '#0284c7' // 中位数：实线
const C_MEAN = '#dc2626' // 均值：虚线
const C_Q = '#94a3b8' // p25 / p75：浅虚线
const C_TAIL = '#f59e0b' // p05 / p95：琥珀虚线

export default function EventDistChart({ edges, counts, k, stat }: Props) {
  const rows = useMemo(() => {
    if (!edges || !counts || edges.length < 2 || counts.length !== edges.length - 1) return []
    const total = counts.reduce((a, b) => a + b, 0) || 1
    return counts.map((c, i) => {
      const lo = edges[i]
      const hi = edges[i + 1]
      return { i, lo, hi, c, p: (c / total) * 100, label: `${(lo * 100).toFixed(0)}~${(hi * 100).toFixed(0)}%` }
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
  const p25 = binLabelOf(rows, stat?.p25)
  const p75 = binLabelOf(rows, stat?.p75)
  const p95 = binLabelOf(rows, stat?.p95)
  const peak = Math.max(...rows.map((r) => r.p), 1)

  // 图外数值行：位置（中位数/均值）→ 分位（p25/p75/p05/p95/p01/p99）→ 极值 → 样本
  const items: { name: string; v?: number | null; c: string; nd?: number }[] = [
    { name: '中位数', v: stat?.median, c: C_MED },
    { name: '均值', v: stat?.mean, c: C_MEAN },
    { name: 'p75', v: stat?.p75, c: C_Q },
    { name: 'p25', v: stat?.p25, c: C_Q },
    { name: 'p95', v: stat?.p95, c: C_TAIL },
    { name: 'p05', v: stat?.p05, c: C_TAIL },
    { name: 'p99', v: stat?.p99, c: C_TAIL, nd: 1 },
    { name: 'p01', v: stat?.p01, c: C_TAIL, nd: 1 },
    { name: '胜率(>0)', v: stat?.win, c: '#64748b', nd: 1 },
    { name: '最小', v: stat?.min, c: '#dc2626', nd: 1 },
    { name: '最大', v: stat?.max, c: '#0284c7', nd: 1 },
    { name: '样本', v: null, c: '#64748b' },
  ]

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
              <Cell key={r.i} fill={r.hi <= 0 ? '#ef4444' : r.lo >= 0 ? '#0ea5e9' : '#94a3b8'} />
            ))}
          </Bar>
          {/* 参考线只画线、**不写字**：文字统一放图外一行（贴图顶会被裁掉、彼此也会压住） */}
          {zero && <ReferenceLine x={zero} stroke="#64748b" strokeWidth={1} />}
          {p25 && <ReferenceLine x={p25} stroke={C_Q} strokeWidth={1} strokeDasharray="3 3" />}
          {p75 && <ReferenceLine x={p75} stroke={C_Q} strokeWidth={1} strokeDasharray="3 3" />}
          {p05 && <ReferenceLine x={p05} stroke={C_TAIL} strokeWidth={1} strokeDasharray="2 2" />}
          {p95 && <ReferenceLine x={p95} stroke={C_TAIL} strokeWidth={1} strokeDasharray="2 2" />}
          {med && <ReferenceLine x={med} stroke={C_MED} strokeWidth={2} />}
          {mean && <ReferenceLine x={mean} stroke={C_MEAN} strokeWidth={2} strokeDasharray="5 3" />}
        </BarChart>
      </ResponsiveContainer>

      {/* 统计值：一项一块、居中、flex 间距隔开（不会出现"%最小"这类黏连） */}
      <div className="flex flex-wrap justify-center items-center gap-x-4 gap-y-1 mt-1 text-slate-600 dark:text-slate-300">
        {items.map((it) => (
          <span key={it.name} className="whitespace-nowrap">
            <span style={{ color: it.c }}>▍</span> {it.name} ={' '}
            <span className="font-medium" style={{ color: it.c }}>
              {it.name === '样本' ? (stat?.n ?? '-') : pctS(it.v, it.nd ?? 2)}
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}
