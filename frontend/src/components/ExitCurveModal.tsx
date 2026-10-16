import { useMemo } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { PeriodRollNav, SingleFactorTestResult } from '../api'

/**
 * **离场曲线**弹窗（v2.8.0，用户 2026-10-15 ✓）。
 *
 * 用户原话：「我期望的是把 3 组回测都在列表里呈现出来，因子列显示：X、X+入场价止损、
 * X+首次入场止损，然后三个事件研究按钮，我点进去**分别展示各自的净值曲线**」
 * ⇒ 这个弹窗就是那个"分别展示"：
 *   · `ei = k` ⇒ 只画「对照」+ 第 k 条离场（两条 ✓，一眼看差在哪 ✓）；
 *   · `ei = null` ⇒ 对照 + 全部离场一起画 ✓（从基础行点进来时用 ✓）。
 *
 * ⚠ 两条曲线**同源**（同一份名单/价格面板/引擎 ✓）⇒ 差值才解释得清 ✓；
 *   别拿净值相除当结论 ✗（持仓结构不同 ⇒ 混了再平衡溢价 ✓）。
 * ⚠ 口径有两种，弹窗里会标出来 ✓：
 *   · `period_roll` = 连续因子的「每期全仓滚动」（调仓日按当期名单等权重建 ✓）；
 *   · `signal` = 0/1 信号的「信号驱动（事件）」（出信号买入、持有预测周期、离场提前平仓 ✓，
 *     与「交易信号测试」同一个引擎 ✓）。
 */
export interface ExitCurveModalProps {
  row: SingleFactorTestResult
  /** 强调第几条离场（`null` = 对照 + 全部 ✓） */
  ei: number | null
  name: string
  onClose: () => void
}

const EXIT_COLORS = ['#7c3aed', '#059669', '#d97706', '#2563eb', '#db2777', '#0891b2']

const pct1 = (v: unknown) =>
  typeof v === 'number' && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '-'

export default function ExitCurveModal({ row, ei, name, onClose }: ExitCurveModalProps) {
  const series = useMemo(() => {
    const cmp = row.period_roll_items?.[0] ?? row.period_roll ?? null
    const out: {
      key: string; label: string; color: string; mode: string
      nav: (number | null)[]; dates: string[]
      perf: PeriodRollNav['perf']; stats: Record<string, number | undefined>
    }[] = []
    const push = (key: string, label: string, color: string, one: PeriodRollNav | null) => {
      if (!one || one.error) return
      out.push({ key, label, color, mode: String(one.mode || 'period_roll'),
                 nav: one.nav ?? [], dates: one.dates ?? [],
                 perf: one.perf ?? null,
                 stats: (one.stats ?? {}) as Record<string, number | undefined> })
    }
    push('cmp', '（对照：不离场）', '#94a3b8', cmp)
    const groups = row.exit_rolls ?? []
    groups.forEach((g, gi) => {
      if (ei != null && gi !== ei) return
      const one = ((g?.items ?? [])[0] ?? g?.first) || null
      const nm = g?.exit?.name || g?.exit?.id || `离场${gi + 1}`
      push(`ex${gi}`, `含离场「${nm}」`, EXIT_COLORS[gi % EXIT_COLORS.length], one)
    })
    return out
  }, [row, ei])

  // 各条曲线的日期轴可能不同（调仓日采样 vs 逐日 ✓）⇒ 取**并集**再逐条填值 ✓
  const data = useMemo(() => {
    const all = Array.from(new Set(series.flatMap((s) => s.dates))).sort()
    return all.map((d) => {
      const rec: Record<string, number | string | null> = { d }
      for (const s of series) {
        const i = s.dates.indexOf(d)
        const v = i >= 0 ? s.nav[i] : null
        rec[s.key] = typeof v === 'number' && Number.isFinite(v) ? v : null
      }
      return rec
    })
  }, [series])

  const modeOf = (s: { mode: string }) =>
    s.mode === 'signal' ? '信号驱动（事件）' : '每期全仓滚动'

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 overflow-y-auto"
         onClick={onClose}>
      <div className="bg-white dark:bg-slate-900 rounded shadow-lg w-full max-w-4xl mt-8"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-3 py-2 border-b dark:border-slate-700">
          <div className="text-sm font-semibold">
            离场曲线：{row.name}
            {name ? <span className="text-amber-600">　+{name}</span> : null}
            <span className="ml-2 text-slate-400 text-[11px]">
              {row.horizon != null ? `周期 ${row.horizon} 天` : ''}
              {series[1] ? `　｜　口径：${modeOf(series[1])}` : ''}
            </span>
          </div>
          <button type="button" onClick={onClose}
                  className="px-2 py-0.5 rounded border text-[11px] text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
            关闭
          </button>
        </div>

        <div className="p-3">
          {/* 指标对比（对照 + 每条离场 ✓）：Δ 才是这条离场的净效果 ✓ */}
          <div className="overflow-x-auto mb-2">
            <table className="w-full text-center text-[11px] border-collapse">
              <thead>
                <tr className="text-slate-400">
                  <th className="font-normal text-left px-1">曲线</th>
                  <th className="font-normal">年化</th>
                  <th className="font-normal">最大回撤</th>
                  <th className="font-normal">Δ年化</th>
                  <th className="font-normal">Δ回撤</th>
                  <th className="font-normal">离场卖出</th>
                  <th className="font-normal">顺延</th>
                  <th className="font-normal">被名单覆盖</th>
                  <th className="font-normal">持币空转</th>
                </tr>
              </thead>
              <tbody>
                {series.map((s, i) => {
                  const base = series[0]?.perf ?? null
                  const dAnn = (s.perf?.annual_return ?? 0) - (base?.annual_return ?? 0)
                  const dDd = (s.perf?.max_drawdown ?? 0) - (base?.max_drawdown ?? 0)
                  const st = (s.stats ?? {}) as Record<string, number | undefined>
                  return (
                    <tr key={s.key} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="text-left px-1" style={{ color: s.color }}>
                        {s.label}
                        <span className="ml-1 text-slate-400">
                          {s.mode === 'signal' ? '（信号驱动）' : ''}
                        </span>
                      </td>
                      <td><b>{pct1(s.perf?.annual_return)}</b></td>
                      <td><b>{pct1(s.perf?.max_drawdown)}</b></td>
                      <td>
                        {i === 0 ? '—' : (
                          <b className={dAnn >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                            {dAnn >= 0 ? '+' : ''}{(dAnn * 100).toFixed(1)}pp
                          </b>
                        )}
                      </td>
                      <td>
                        {i === 0 ? '—' : (
                          <b className={dDd >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                            {dDd >= 0 ? '+' : ''}{(dDd * 100).toFixed(1)}pp
                          </b>
                        )}
                      </td>
                      <td>{st.exit_sells ?? (i === 0 ? '—' : '-')}</td>
                      <td>{st.exit_deferrals ?? (i === 0 ? '—' : '-')}</td>
                      <td>{st.exit_overridden ?? (i === 0 ? '—' : '-')}</td>
                      <td>{st.exit_ignored ?? (i === 0 ? '—' : '-')}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {data.length > 1 ? (
            <div className="bg-white">
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={data} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                  <XAxis dataKey="d" tick={{ fontSize: 10 }} minTickGap={48} />
                  <YAxis tick={{ fontSize: 10 }} width={56} domain={['auto', 'auto']} />
                  <Tooltip formatter={(v: number | string) => (typeof v === 'number' ? v.toFixed(4) : v)} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {series.map((s, i) => (
                    <Line key={s.key} type="monotone" dataKey={s.key} name={s.label}
                          stroke={s.color} dot={false}
                          strokeWidth={i === 0 ? 1.6 : 2}
                          strokeDasharray={i === 0 ? '6 3' : undefined} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="text-[11px] text-amber-600">
              这条没有可画的净值（可能该变体回测失败，或该行没有对照曲线）—— 详情看结果行上的错误提示 ✓
            </p>
          )}

          <p className="mt-2 text-[11px] text-slate-400 leading-relaxed">
            两条曲线**同源**（同一份名单 / 同一份价格面板 / 同一个引擎）⇒ 差值才解释得清；<b>别拿净值相除当结论</b>。
            口径：连续因子 = 「每期全仓滚动」（调仓日按当期名单等权重建、期中只受离场影响）；
            0/1 信号 = 「信号驱动（事件）」（出信号买入、持有「周期」天、离场信号提前平仓，与「交易信号测试」同一个引擎）。
          </p>
        </div>
      </div>
    </div>
  )
}
