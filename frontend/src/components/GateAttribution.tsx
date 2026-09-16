import { useEffect, useMemo, useState } from 'react'
import { getBacktestSnapshot } from '../api'
import type { GateAttrExtra, GateCompose, ComposeSegmentInfo } from '../types'

interface Props {
  taskId: string
  /** 当前保存的公式列表（把 extra_feature_i 映射回"你勾的那条公式的名字"） */
  formulas?: { id: string; name: string; expression: string }[]
  /** 该任务是否仍在运行：运行中且还没拿到归因 ⇒ 每 20s 重试（归因是**每段**写的，跑完才完整） */
  live?: boolean
}

/** 单段的归因汇总（用于交叉对比）。 */
interface Agg {
  i: number
  expr: string
  gainShare: number
  deltaAucs: number[] // 各段 ΔAUC
  meanDelta: number
  winRate: number // ΔAUC > 0 的段占比
  trained: number // 成功算出 ΔAUC 的段数
}

function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0
}

const fmt = (v: number | null | undefined, digits = 4) =>
  v == null || Number.isNaN(v) ? '—' : v.toFixed(digits)

/**
 * Meta-Gate 因子归因表（v1.19.72）。
 *
 * 回答用户 2026-09-16 的问题：「不知道机器最后觉得用哪个风控因子更好？」
 * 数据来自产物 `compose.json`（后端每段落盘）：
 *   · gain 占比 = 树在它上面花了多少分裂增益（会被共线性分走，只作参考）
 *   · **ΔAUC（单因子 ablation）= 单独把它给 gate 相对基线的 valid_auc 提升** ⇒ 最可比的排名
 *   · 段胜率 = ΔAUC > 0 的段占比 ⇒ 衡量"稳定有效"而非"某段侥幸"
 */
export default function GateAttribution({ taskId, formulas = [], live = false }: Props) {
  const [compose, setCompose] = useState<GateCompose | null>(null)
  const [err, setErr] = useState('')
  const [showSegs, setShowSegs] = useState(false)

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    setCompose(null)
    setErr('')
    const load = () => {
      getBacktestSnapshot(taskId)
        .then((snap) => {
          if (!alive) return
          const c = (snap.compose as GateCompose | null) ?? null
          setCompose(c)
          // 运行中且还没归因 ⇒ 继续等（每段才写一条，跑完才完整）
          if (live && !c) timer = window.setTimeout(load, 20000)
        })
        .catch((e: unknown) => {
          if (alive) setErr(e instanceof Error ? e.message : String(e))
        })
    }
    load()
    return () => {
      alive = false
      if (timer) window.clearTimeout(timer)
    }
  }, [taskId, live])

  const segNames = useMemo(() => Object.keys(compose?.segments || {}), [compose])

  const agg = useMemo<Agg[]>(() => {
    const byIdx = new Map<number, Agg>()
    for (const name of segNames) {
      const info = (compose?.segments || {})[name] as ComposeSegmentInfo
      const extras: GateAttrExtra[] = info?.gate?.attribution?.extras || []
      for (const r of extras) {
        const cur =
          byIdx.get(r.i) || {
            i: r.i,
            expr: r.expr || '',
            gainShare: 0,
            deltaAucs: [],
            meanDelta: 0,
            winRate: 0,
            trained: 0,
          }
        cur.gainShare = r.gain_share ?? cur.gainShare
        if (r.expr) cur.expr = r.expr
        if (r.delta_auc != null && !Number.isNaN(r.delta_auc)) cur.deltaAucs.push(r.delta_auc)
        cur.trained = cur.deltaAucs.length
        cur.meanDelta = mean(cur.deltaAucs)
        cur.winRate = cur.trained ? cur.deltaAucs.filter((d) => d > 0).length / cur.trained : 0
        byIdx.set(r.i, cur)
      }
    }
    return [...byIdx.values()].sort((a, b) => b.meanDelta - a.meanDelta)
  }, [compose, segNames])

  /** 归因说明（只在"因子数超上限、被截断归因"时才展示，避免与表尾说明重复） */
  const note = useMemo(() => {
    for (const n of segNames) {
      const t = ((compose?.segments || {})[n] as ComposeSegmentInfo)?.gate?.attribution?.note
      if (t && t.includes('上限')) return t
    }
    return ''
  }, [compose, segNames])

  /** 归因失败信息（基线 gate 训练失败等）——有就让用户看到，别静默 */
  const attrErr = useMemo(() => {
    for (const n of segNames) {
      const e = ((compose?.segments || {})[n] as ComposeSegmentInfo)?.gate?.attribution?.error
      if (e) return String(e)
    }
    return ''
  }, [compose, segNames])

  /** 把序号映射回"你勾的公式名"（产物里的顺序 = 提交时的 extra_features 顺序） */
  const labelOf = (i: number, expr: string) => {
    const hit = formulas.find((f) => expr && expr.startsWith(f.expression.slice(0, 40)))
    return hit ? hit.name : `因子 #${i + 1}`
  }

  if (err) return null
  // 只在"真的有 gate 归因"时才渲染（compose.json 也可能只含 overlay/硬规则）
  if (!compose || segNames.length === 0 || agg.length === 0) return null

  const fullAucs = segNames
    .map((n) => ((compose.segments || {})[n] as ComposeSegmentInfo)?.gate?.attribution?.full_auc)
    .filter((v): v is number => typeof v === 'number')
  const baseAucs = segNames
    .map(
      (n) => ((compose.segments || {})[n] as ComposeSegmentInfo)?.gate?.attribution?.primary_only_auc,
    )
    .filter((v): v is number => typeof v === 'number')
  const primShares = segNames
    .map(
      (n) =>
        ((compose.segments || {})[n] as ComposeSegmentInfo)?.gate?.attribution?.primary_p_gain_share,
    )
    .filter((v): v is number => typeof v === 'number')

  const best = agg[0]

  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <h2 className="text-lg font-semibold">
          Meta-Gate 因子归因
          <span className="ml-2 text-sm font-normal text-slate-500">
            共 {segNames.length} 段 · 每段独立训练 gate 并做单因子 ablation
          </span>
        </h2>
        <button
          type="button"
          onClick={() => setShowSegs(!showSegs)}
          className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
        >
          {showSegs ? '只看汇总' : '查看各段明细'}
        </button>
      </div>

      {/* 结论行：整体价值 + 最强因子 */}
      <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4 text-sm space-y-1">
        <div>
          <span className="text-slate-500">gate 自身 AUC：</span>
          基线（不用附加因子）<b>{fmt(mean(baseAucs), 4)}</b>
          <span className="mx-1">→</span>
          全用上 <b>{fmt(mean(fullAucs), 4)}</b>
          <span className="ml-2 text-slate-500">
            （这批附加因子整体提升 <b>{fmt(mean(fullAucs) - mean(baseAucs), 4)}</b>，各段均值）
          </span>
        </div>
        <div>
          <span className="text-slate-500">gate 的增益来源：</span>
          主模型分（primary_p）占 <b>{(mean(primShares) * 100).toFixed(0)}%</b>
          （其余为特征与附加因子）
        </div>
        {best && (
          <div className="text-[13px]">
            <span className="text-slate-500">机器最看重的附加因子：</span>
            <b className="text-emerald-600">{labelOf(best.i, best.expr)}</b>
            <span className="ml-2">
              ΔAUC 均值 <b>{best.meanDelta >= 0 ? '+' : ''}{fmt(best.meanDelta)}</b>
              ，<b>{best.trained}</b> 段中 <b>{best.deltaAucs.filter((d) => d > 0).length}</b> 段为正
            </span>
          </div>
        )}
      </div>

      {/* 排名表 */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500 border-b">
              <th className="py-1.5 pr-3">#</th>
              <th className="py-1.5 pr-3">附加因子</th>
              <th className="py-1.5 pr-3 text-right" title="LightGBM 分裂增益占比（会被共线性分走，仅供参考）">
                gain 占比
              </th>
              <th className="py-1.5 pr-3 text-right" title="单独把它加进基线后 gate 的 valid_auc 提升">
                ΔAUC 均值
              </th>
              <th className="py-1.5 pr-3 text-right">段胜率</th>
              <th className="py-1.5 pr-3">结论</th>
            </tr>
          </thead>
          <tbody>
            {agg.map((r, idx) => {
              const strong = r.winRate >= 0.7 && r.meanDelta > 0
              const weak = r.winRate <= 0.3 || r.meanDelta <= 0
              return (
                <tr key={r.i} className="border-b border-slate-100 dark:border-slate-700">
                  <td className="py-1.5 pr-3 text-slate-400">{idx + 1}</td>
                  <td className="py-1.5 pr-3">
                    <span className="font-medium">{labelOf(r.i, r.expr)}</span>
                    <span className="ml-2 text-[10px] text-slate-400 font-mono">
                      {r.expr ? `${r.expr.slice(0, 46)}…` : `extra_feature_${r.i}`}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right">{(r.gainShare * 100).toFixed(1)}%</td>
                  <td
                    className={`py-1.5 pr-3 text-right font-medium ${
                      r.meanDelta > 0 ? 'text-emerald-600' : r.meanDelta < 0 ? 'text-red-500' : ''
                    }`}
                  >
                    {r.meanDelta >= 0 ? '+' : ''}
                    {fmt(r.meanDelta)}
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {r.deltaAucs.filter((d) => d > 0).length}/{r.trained}（
                    {(r.winRate * 100).toFixed(0)}%）
                  </td>
                  <td className="py-1.5 pr-3">
                    {strong ? (
                      <span className="text-emerald-600">稳定有效</span>
                    ) : weak ? (
                      <span className="text-slate-400">基本无贡献</span>
                    ) : (
                      <span className="text-amber-600">不稳定</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* 各段明细 */}
      {showSegs && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-slate-500 border-b">
                <th className="py-1.5 pr-3">段</th>
                <th className="py-1.5 pr-3 text-right">gate AUC</th>
                <th className="py-1.5 pr-3 text-right">基线 AUC</th>
                <th className="py-1.5 pr-3 text-right">样本数</th>
                {agg.map((r) => (
                  <th key={r.i} className="py-1.5 pr-3 text-right">
                    {labelOf(r.i, r.expr)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {segNames.map((name) => {
                const g = ((compose.segments || {})[name] as ComposeSegmentInfo)?.gate
                const at = g?.attribution
                return (
                  <tr key={name} className="border-b border-slate-100 dark:border-slate-700">
                    <td className="py-1.5 pr-3">{name}</td>
                    <td className="py-1.5 pr-3 text-right">{fmt(g?.valid_auc, 4)}</td>
                    <td className="py-1.5 pr-3 text-right">{fmt(at?.primary_only_auc, 4)}</td>
                    <td className="py-1.5 pr-3 text-right">{g?.n ?? '—'}</td>
                    {agg.map((r) => {
                      const row = (at?.extras || []).find((x) => x.i === r.i)
                      return (
                        <td key={r.i} className="py-1.5 pr-3 text-right">
                          {row?.delta_auc == null ? '—' : `${row.delta_auc >= 0 ? '+' : ''}${fmt(row.delta_auc)}`}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-[11px] text-slate-400">
        ΔAUC = 只留「主特征 + 主分」作基线、单独加上该因子后 gate 的 valid_auc 提升 ⇒
        比 gain 更可比（gain 会被相似公式互相分走）。段胜率越高越可靠：某段正、某段负说明它对市场状态敏感。
      </p>
      {note && <p className="mt-1 text-[11px] text-amber-600">{note}</p>}
      {attrErr && <p className="mt-1 text-[11px] text-red-500">归因未完成：{attrErr}</p>}
    </section>
  )
}
