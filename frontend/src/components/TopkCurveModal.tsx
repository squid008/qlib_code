import { useMemo, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { SingleFactorTestResult, TopKCurveItem } from '../api'

/**
 * 连续因子「持仓期收益曲线」弹窗（v1.18.45）。
 *
 * 数据全部来自单因子测试结果（**打开即秒开，不提交任务**）：
 *   · `topk_curves`       —— 明细曲线：items[0] = **超额收益最强的分位组**（逐日等分），
 *                            其余为固定 K 档（TopK，可点选切换 ⇒ 纯前端切换、零重算）
 *   · `quantile_curves`   —— 十分位累计收益曲线（10 条 + 多空）
 *   · `topk_sensitivity`  —— 各 K 的换手 / 三档年化成本 / 成本吞噬比例
 *
 * ⚠ 口径（页面必须显著标注）：成本为**往返合计**费率的**简化估算**，未考虑涨跌停、停牌、
 * 流动性冲击；曲线是 `LABEL`（h 期前视）逐日累加、**不是净值**；多空**不可实现**（A 股空头
 * 收益拿不到），仅作有效性参考。
 */
interface Props {
  open: boolean
  onClose: () => void
  name: string
  row: SingleFactorTestResult | null
}

/** 点数上限：1211 个点 × 11 条线对 Recharts 偏重，按等间隔抽样（累计曲线形状不变）。 */
const MAX_POINTS = 360

const pct = (v: number | null | undefined, nd = 2) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

/** 明细档显示名：分位组标「最强分位组 Q几」；固定 K 档标「固定 K=…」（默认档加注）。 */
function itemLabel(it: TopKCurveItem, defaultK: number | null) {
  if (it.kind === 'decile') return `最强分位组 Q${it.quantile}`
  const isDefault = defaultK != null && it.k === defaultK
  return `固定 K=${it.k}${isDefault ? '（默认 10% 档）' : ''}`
}

/** 等间隔抽样 + 保留末点。 */
function stride(n: number) {
  return Math.max(1, Math.ceil(n / MAX_POINTS))
}

export default function TopkCurveModal({ open, onClose, name, row }: Props) {
  const [sel, setSel] = useState(0)
  const [hidden, setHidden] = useState<Record<string, boolean>>({})
  const toggleSeries = (key?: string | number) => {
    if (typeof key !== 'string' || !key) return
    setHidden((h) => ({ ...h, [key]: !h[key] }))
  }

  const tc = row?.topk_curves ?? null
  const qc = row?.quantile_curves ?? null
  const sens = row?.topk_sensitivity ?? null
  const items = tc?.items ?? []
  const idx = Math.min(sel, Math.max(0, items.length - 1))
  const item = items.length ? items[idx] : null

  // 图 1：选中档的三档累计曲线（0 / 0.004 / 0.008，均按调仓期扣费）
  const detailData = useMemo(() => {
    if (!tc || !item) return []
    const st = stride(tc.dates.length)
    const out: Record<string, number | string>[] = []
    for (let i = 0; i < tc.dates.length; i += st) {
      out.push({
        d: tc.dates[i],
        c0: item.curves['0.0000']?.[i] ?? null,
        c4: item.curves['0.0040']?.[i] ?? null,
        c8: item.curves['0.0080']?.[i] ?? null,
      })
    }
    const last = tc.dates.length - 1
    if (last >= 0 && (out.length === 0 || out[out.length - 1].d !== tc.dates[last])) {
      out.push({
        d: tc.dates[last],
        c0: item.curves['0.0000']?.[last] ?? null,
        c4: item.curves['0.0040']?.[last] ?? null,
        c8: item.curves['0.0080']?.[last] ?? null,
      })
    }
    return out
  }, [tc, item])

  // 图 2：十分位累计收益曲线（10 条 + 多空）
  const decileData = useMemo(() => {
    if (!qc || !qc.groups?.length) return []
    const st = stride(qc.dates.length)
    const out: Record<string, number | string>[] = []
    for (let i = 0; i < qc.dates.length; i += st) {
      const p: Record<string, number | string> = { d: qc.dates[i] }
      for (const g of qc.groups) p[`q${g.quantile}`] = g.cum[i] ?? null
      p.ls = qc.long_short.cum[i] ?? null
      out.push(p)
    }
    const last = qc.dates.length - 1
    if (last >= 0 && out.length && out[out.length - 1].d !== qc.dates[last]) {
      const p: Record<string, number | string> = { d: qc.dates[last] }
      for (const g of qc.groups) p[`q${g.quantile}`] = g.cum[last] ?? null
      p.ls = qc.long_short.cum[last] ?? null
      out.push(p)
    }
    return out
  }, [qc])

  const DECILE_COLORS = [
    '#dc2626', '#ea580c', '#d97706', '#65a30d', '#059669',
    '#0891b2', '#2563eb', '#7c3aed', '#c026d3', '#0f766e',
  ]

  if (!open) return null

  const empty = !tc && !qc

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-slate-800 rounded-lg shadow-xl w-[1040px] max-w-full max-h-[92vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-slate-700">
          <div>
            <span className="font-semibold text-slate-700 dark:text-slate-200">
              持仓期收益曲线（含三档成本）
            </span>
            <span className="text-slate-400 ml-2 text-xs">
              {name}
              {tc ? `　调仓期 ${tc.rebalance_period} 交易日　${tc.n} 个交易日` : ''}
              {qc ? `　分位档：最强 = Q${qc.best_quantile}` : ''}
            </span>
          </div>
          <button onClick={onClose} className="px-2 py-1 text-xs rounded border">
            关闭
          </button>
        </div>

        {/* 口径 / 免责（必须显著标注） */}
        <div className="mx-4 mt-3 px-3 py-2 rounded bg-amber-50 dark:bg-amber-900/20 text-[11px] text-amber-700 dark:text-amber-300 leading-relaxed">
          <b>简化估算，读图前请先看口径</b>：① 费率是<b>往返（买+卖）合计</b>，按<b>调仓期</b>扣、
          只扣<b>实际调仓</b>的股票（首期建仓不计费）；② <b>未考虑涨跌停、停牌、流动性冲击</b>；
          ③ 曲线是 <code>LABEL</code>（未来 h 日收益）逐日累加，<b>不是净值</b>，量级不可当收益率读；
          ④ 默认档 = <b>超额收益最强的分位组</b>（逐日等分，只数与固定 K 档略有差异）；
          ⑤ <b>多空不可实现</b>（A 股空头收益拿不到），仅作有效性参考。
        </div>

        {empty ? (
          <div className="px-4 py-6 text-xs text-slate-500">
            该行没有曲线数据
            {row?.topk_curves_error ? `：${row.topk_curves_error}` : '（0/1 稀疏信号走「事件研究」，无此曲线）'}
          </div>
        ) : (
          <div className="px-4 py-3">
            {/* ---- 图 1：明细档（默认 = 最强分位组）---- */}
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                持仓期累计收益（三档成本 · 同一组合）
              </span>
              <span className="text-[11px] text-slate-400">切换组合（纯前端，零重算）：</span>
              {items.map((it, i) => (
                <button
                  key={`${it.kind}-${it.k}-${i}`}
                  onClick={() => setSel(i)}
                  className={`px-1.5 py-0.5 rounded border text-[11px] ${
                    i === idx
                      ? 'border-sky-500 text-sky-600 bg-sky-50 dark:bg-sky-900/30'
                      : 'border-slate-300 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700/40'
                  }`}
                >
                  {itemLabel(it, sens?.default_k ?? null)}
                </button>
              ))}
            </div>
            <div className="text-[11px] text-slate-400 mb-1">
              {item?.kind === 'decile'
                ? `每日取因子最强/最弱端的 1/${qc?.n_groups ?? 10}（等分）⇒ 持仓约 ${item.k} 只，只数逐日变化；换手分母 = 当期在手只数`
                : `固定持仓 ${item?.k} 只（按名次取，与分位组口径略有差异）`}
            </div>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={detailData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="d" tick={{ fontSize: 10 }} minTickGap={48} />
                <YAxis tick={{ fontSize: 10 }} width={52} />
                <Tooltip formatter={(v: number | string) => (typeof v === 'number' ? v.toFixed(4) : v)} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
                <Line type="monotone" dataKey="c0" name="无成本" stroke="#0f766e" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="c4" name="往返 0.004" stroke="#0284c7" dot={false} strokeWidth={1.6} />
                <Line type="monotone" dataKey="c8" name="往返 0.008" stroke="#b45309" dot={false} strokeWidth={1.6} />
              </LineChart>
            </ResponsiveContainer>

            {/* ---- 图 2：十分位累计收益曲线 ---- */}
            {qc && (
              <>
                <div className="flex items-center gap-2 mt-4 mb-1">
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                    十分位累计收益曲线（1 = 最低值组 … {qc.n_groups} = 最高值组）
                  </span>
                  <span className="text-[11px] text-slate-400">
                    最强档 = Q{qc.best_quantile}（点图例可显隐曲线；多空不可实现，仅作参考）
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={250}>
                  <LineChart data={decileData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="d" tick={{ fontSize: 10 }} minTickGap={48} />
                    <YAxis tick={{ fontSize: 10 }} width={52} />
                    <Tooltip formatter={(v: number | string) => (typeof v === 'number' ? v.toFixed(4) : v)} />
                    <Legend
                      wrapperStyle={{ fontSize: 10, cursor: 'pointer' }}
                      onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                    />
                    <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
                    {qc.groups.map((g) => (
                      <Line
                        key={g.quantile}
                        type="monotone"
                        dataKey={`q${g.quantile}`}
                        name={`Q${g.quantile}${g.quantile === qc.best_quantile ? '(最强)' : ''}`}
                        stroke={DECILE_COLORS[(g.quantile - 1) % DECILE_COLORS.length]}
                        dot={false}
                        strokeWidth={g.quantile === qc.best_quantile ? 2 : 1}
                        hide={!!hidden[`q${g.quantile}`]}
                      />
                    ))}
                    <Line
                      type="monotone"
                      dataKey="ls"
                      name={`多空 Q${qc.long_short.quantile[0]}−Q${qc.long_short.quantile[1]}（不可实现）`}
                      stroke="#111827"
                      dot={false}
                      strokeWidth={2}
                      strokeDasharray="6 3"
                      hide={!!hidden.ls}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </>
            )}

            {/* ---- K 敏感度表 ---- */}
            {sens && (
              <>
                <div className="mt-4 mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">
                  成本敏感度：不同持仓只数 K（换手 / 年化成本 / 吞噬比例）
                </div>
                <table className="text-[11px] w-full border-t border-slate-200 dark:border-slate-700">
                  <thead>
                    <tr className="text-slate-400">
                      <th className="text-left px-1 py-1 font-normal">K（只数）</th>
                      <th className="text-right px-1 font-normal">每期换手</th>
                      <th className="text-right px-1 font-normal">年化成本@0.004</th>
                      <th className="text-right px-1 font-normal">年化成本@0.008</th>
                      <th className="text-right px-1 font-normal">成本吞噬@0.004</th>
                      <th className="text-right px-1 font-normal">每期毛收益</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sens.rows.map((r) => (
                      <tr
                        key={r.k}
                        className={
                          r.k === sens.default_k
                            ? 'bg-sky-50 dark:bg-sky-900/20 text-slate-700 dark:text-slate-200'
                            : 'text-slate-600 dark:text-slate-300'
                        }
                      >
                        <td className="px-1 py-0.5">
                          {r.k}
                          {r.k === sens.default_k ? '（默认 10%）' : ''}
                        </td>
                        <td className="text-right px-1">{pct(r.avg_turnover)}</td>
                        <td className="text-right px-1">{pct(r.ann_cost['0.0040'])}</td>
                        <td className="text-right px-1">{pct(r.ann_cost['0.0080'])}</td>
                        <td className="text-right px-1">{pct(r.cost_eaten['0.0040'], 1)}</td>
                        <td className="text-right px-1">{pct(r.gross_per_period)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="mt-1 text-[11px] text-slate-400 leading-relaxed">
                  {sens.note}　年化按 {sens.trading_days} 交易日；K 与明细曲线的固定档一一对应。
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
