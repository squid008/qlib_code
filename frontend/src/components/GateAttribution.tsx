import { useEffect, useMemo, useState } from 'react'
import { getBacktestSnapshot } from '../api'
import type { BacktestRequest, GateAttrExtra, GateCompose, ComposeSegmentInfo } from '../types'

interface Props {
  taskId: string
  /** 已保存的公式（把 gate 的附加因子映射回"你起的名字 + 你保存的公式原文"） */
  formulas?: { id: string; name: string; text?: string; expression: string }[]
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
 * Meta-Gate 因子归因表（v1.19.72/73）。
 *
 * 回答「我勾了几个风控因子，机器觉得哪个更有用？」—— 数据来自产物 `compose.json`（每段落盘）：
 *   · **分裂增益占比（gain）** = 门控模型在它身上累计用掉了多少分裂增益 ⇒ "模型自己觉得它有用"的程度
 *   · **ΔAUC（逐个单独试）** = 把它**单单**加进基线再训一个门控模型，验证集 AUC 提升多少
 *     ⇒ 比 gain 更硬的证据（gain 会被内容相近的公式互相分走）
 *   · **有效段数** = ΔAUC > 0 的段占比 ⇒ 衡量"稳定有效"而不是"某一段侥幸"
 */
export default function GateAttribution({ taskId, formulas = [], live = false }: Props) {
  const [compose, setCompose] = useState<GateCompose | null>(null)
  // 该次回测的完整参数：拿提交时的附加因子顺序（与产物里的 extra_feature_i 一一对应）精确映射回公式，
  // 也用来判断"有没有开 Meta-Gate / 有没有归因数据"（没数据时必须说清原因，别静默不显示）
  const [params, setParams] = useState<BacktestRequest | null>(null)
  const [err, setErr] = useState('')
  const [showSegs, setShowSegs] = useState(false)

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    setCompose(null)
    setParams(null)
    setErr('')
    const load = () => {
      getBacktestSnapshot(taskId)
        .then((snap) => {
          if (!alive) return
          const c = (snap.compose as GateCompose | null) ?? null
          setCompose(c)
          setParams((snap.params as BacktestRequest | null) ?? null)
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
  /** 提交时的附加因子表达式（顺序 = 产物里的 extra_feature_i） */
  const submitted: string[] = useMemo(
    () => params?.meta_gate_opts?.extra_features || [],
    [params],
  )

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

  const note = useMemo(() => {
    for (const n of segNames) {
      const t = ((compose?.segments || {})[n] as ComposeSegmentInfo)?.gate?.attribution?.note
      if (t && t.includes('上限')) return t
    }
    return ''
  }, [compose, segNames])

  const attrErr = useMemo(() => {
    for (const n of segNames) {
      const e = ((compose?.segments || {})[n] as ComposeSegmentInfo)?.gate?.attribution?.error
      if (e) return String(e)
    }
    return ''
  }, [compose, segNames])

  /** 把序号映射回"你保存的那条公式"（优先用提交时的完整表达式比对，最稳） */
  const formulaOf = (i: number, expr: string) => {
    const full = submitted[i] || expr
    return (
      formulas.find((f) => f.expression === full) ||
      formulas.find((f) => !!full && !!f.expression && full.startsWith(f.expression.slice(0, 40))) ||
      (expr ? formulas.find((f) => !!f.expression && expr.startsWith(f.expression.slice(0, 40))) : undefined)
    )
  }
  /** 主标签 = 公式名（找不到就说明是"不在公式库里的临时公式"） */
  const labelOf = (i: number, expr: string) =>
    formulaOf(i, expr)?.name || `未在公式库中找到的因子 #${i + 1}`
  /** 灰色小字 = **你保存的公式原文**（不是编译后的 qlib 表达式） */
  const textOf = (i: number, expr: string) => formulaOf(i, expr)?.text || ''

  if (err) return null

  // ---- 没有归因数据时**必须说清原因**（用户 2026-09-16 被"静默不显示"坑到：他以为表里那两行是他的因子）----
  const gateOn = !!params?.meta_gate
  if (!gateOn || submitted.length === 0) return null   // 这次回测根本没开 Meta-Gate ⇒ 与归因无关，不占版面
  if (!compose || segNames.length === 0) {
    return (
      <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6 text-sm">
        <h2 className="text-lg font-semibold mb-2">Meta-Gate 因子归因</h2>
        <p className="text-amber-600">
          {live
            ? '这次回测还在跑：归因是每段算完之后写入的，跑完第 1 段后这里就会出表（无需刷新）。'
            : '这次回测没有归因数据：它早于 v1.19.72（归因功能上线），产物里没有 compose.json ⇒ 重跑一次就有。'}
        </p>
        <p className="mt-1 text-xs text-slate-400">
          这次提交时勾了 {submitted.length} 条附加因子；归因会给出每条因子「逐个单独试」的 AUC 提升排名。
          {!live && '（归因不会追补历史产物 —— 因为 gate 模型当时没有落盘。）'}
        </p>
      </section>
    )
  }
  if (agg.length === 0) {
    return (
      <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6 text-sm">
        <h2 className="text-lg font-semibold mb-2">Meta-Gate 因子归因</h2>
        <p className="text-slate-500">
          这次回测没有做归因（提交时把「因子归因」选成了「关」）⇒ 只记录了各因子的分裂增益占比，
          没有"逐个单独试"的 AUC 提升。想看到排名，下次提交时把「因子归因」保持「每段都做」即可。
        </p>
      </section>
    )
  }

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
  const bestWin = best ? best.deltaAucs.filter((d) => d > 0).length : 0

  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <h2 className="text-lg font-semibold">
          Meta-Gate 因子归因
          <span className="ml-2 text-sm font-normal text-slate-500">
            我勾的几个风控因子，机器觉得哪个更有用 · 共 {segNames.length} 段 · 数据来自本次提交的{' '}
            {submitted.length} 条附加因子
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

      {/* 结论行 */}
      <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4 text-sm space-y-1">
        <div>
          <span className="text-slate-500">门控模型自身的区分度（AUC，各段均值）：</span>
          不用这些因子 <b>{fmt(mean(baseAucs), 4)}</b>
          <span className="mx-1">→</span>
          全用上 <b>{fmt(mean(fullAucs), 4)}</b>
          <span className="ml-2 text-slate-500">
            （整体只提升 <b>{fmt(mean(fullAucs) - mean(baseAucs), 4)}</b>）
          </span>
        </div>
        <div>
          <span className="text-slate-500">门控的判断主要来自：</span>
          主模型打分（primary_p）占 <b>{(mean(primShares) * 100).toFixed(0)}%</b>
          <span className="ml-2 text-slate-500">的分裂增益（其余为模型特征与这些附加因子）</span>
        </div>
        {best && (
          <div className="text-[13px]">
            <span className="text-slate-500">提升最大的因子：</span>
            <b className="text-emerald-600">{labelOf(best.i, best.expr)}</b>
            <span className="ml-2">
              平均 <b>{best.meanDelta >= 0 ? '+' : ''}{fmt(best.meanDelta)}</b> AUC，
              <b>{best.trained}</b> 段里 <b>{bestWin}</b> 段为正
            </span>
            {bestWin * 2 <= best.trained && (
              <span className="ml-2 text-amber-600">
                （不到一半的段为正 ⇒ 即便排第一也不稳，别据此剔除其它因子）
              </span>
            )}
          </div>
        )}
      </div>

      {/* 排名表 */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500 border-b">
              <th className="py-1.5 pr-3">#</th>
              <th className="py-1.5 pr-3">附加因子（你勾选的）</th>
              <th
                className="py-1.5 pr-3 text-right"
                title="分裂增益占比：门控模型在它身上累计用掉了多少分裂增益。含义是「模型自己觉得它有用」，但会被内容相近的公式互相分走，只作参考。"
              >
                分裂增益占比
              </th>
              <th
                className="py-1.5 pr-3 text-right"
                title="逐个单独试：把它单单加进基线（主特征+主模型打分）再训一个门控模型，验证集 AUC 提升多少。更硬、更可比的证据。"
              >
                单独试的 AUC 提升
              </th>
              <th className="py-1.5 pr-3 text-right" title="ΔAUC 为正的段数 / 参与测试的段数">
                有效段数
              </th>
              <th className="py-1.5 pr-3">结论</th>
            </tr>
          </thead>
          <tbody>
            {agg.map((r, idx) => {
              const strong = r.winRate >= 0.7 && r.meanDelta > 0
              const weak = r.winRate <= 0.3 || r.meanDelta <= 0
              const userText = textOf(r.i, r.expr)
              return (
                <tr key={r.i} className="border-b border-slate-100 dark:border-slate-700">
                  <td className="py-1.5 pr-3 text-slate-400 align-top">{idx + 1}</td>
                  <td className="py-1.5 pr-3 align-top">
                    <div className="font-medium">{labelOf(r.i, r.expr)}</div>
                    {userText ? (
                      <div
                        className="mt-0.5 text-[10px] text-slate-400 font-mono break-all"
                        title={userText}
                      >
                        {userText.length > 90 ? `${userText.slice(0, 90)}…` : userText}
                      </div>
                    ) : (
                      <div
                        className="mt-0.5 text-[10px] text-slate-400"
                        title={`提交时的表达式（qlib 编译后）：${(submitted[r.i] || r.expr || '').slice(0, 400)}`}
                      >
                        这条公式不在你当前保存的公式库里（可能已删除，或是临时公式）⇒ 显示不出名字与原文
                      </div>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 text-right align-top">{(r.gainShare * 100).toFixed(1)}%</td>
                  <td
                    className={`py-1.5 pr-3 text-right align-top font-medium ${
                      r.meanDelta > 0 ? 'text-emerald-600' : r.meanDelta < 0 ? 'text-red-500' : ''
                    }`}
                  >
                    {r.meanDelta >= 0 ? '+' : ''}
                    {fmt(r.meanDelta)}
                  </td>
                  <td className="py-1.5 pr-3 text-right align-top">
                    {r.deltaAucs.filter((d) => d > 0).length}/{r.trained}
                  </td>
                  <td className="py-1.5 pr-3 align-top">
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
                <th className="py-1.5 pr-3 text-right">门控 AUC</th>
                <th className="py-1.5 pr-3 text-right">不用这些因子</th>
                <th className="py-1.5 pr-3 text-right">训练样本</th>
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
                          {row?.delta_auc == null
                            ? '—'
                            : `${row.delta_auc >= 0 ? '+' : ''}${fmt(row.delta_auc)}`}
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

      {/* 怎么读这张表（人话版） */}
      <div className="mt-4 rounded-lg bg-slate-50 dark:bg-slate-900 p-3 text-[11px] text-slate-500 leading-relaxed space-y-1">
        <div className="font-medium text-slate-600 dark:text-slate-300">怎么读这张表</div>
        <div>
          <b>分裂增益（gain）</b>：门控模型是一棵棵树，每次在某条公式上"分岔"就会用掉一些增益。
          累计下来就是这条公式的分裂增益占比 —— 相当于<b>模型自己觉得它有用</b>的程度。
          ⚠ 它会被<b>内容相近的公式互相分走</b>（谁先被选中谁占便宜），所以只能当参考。
        </div>
        <div>
          <b>单独试的 AUC 提升（ΔAUC）</b>：更硬的证据 —— 把这条公式<b>单独一个</b>加进基线
          （主特征 + 主模型打分），重新训一个门控模型，看验证集 AUC 提升多少。
          因为每次只多它一个，所以几条公式之间<b>可以直接比</b>：提升为正才算真的有用。
        </div>
        <div>
          <b>有效段数</b>：滚动回测每段独立训练，所以同一公式在不同段可能有用/没用。
          "3/5 段为正"比"5 段里平均 +0.001"更值得信任 —— <b>要看稳定性，不要只看平均数</b>。
        </div>
      </div>
      {note && <p className="mt-1 text-[11px] text-amber-600">{note}</p>}
      {attrErr && <p className="mt-1 text-[11px] text-red-500">归因未完成：{attrErr}</p>}
    </section>
  )
}
