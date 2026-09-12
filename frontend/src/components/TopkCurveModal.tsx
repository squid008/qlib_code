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
 * 流动性冲击；曲线默认**复利**（v1.18.47 起：每期收益滚入本金 = 实盘满仓复投，可切「算术累加」）、
 * **近似净值但不是逐日盯市净值**；多空**不可实现**（A 股空头收益拿不到），仅作有效性参考。
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

/** 有符号百分数：原始小数 → `+134.22%` / `-3.10%`；空值给 `-`。 */
const signedPct = (v: number | null | undefined, nd = 2) =>
  v == null || !Number.isFinite(v) ? '-' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(nd)}%`

/** 累计值 → **净值（从 1 开始）**；缺失保持 null（前端断线）。
 *  后端按口径给「复利累乘」（`curves_compound`/`cum_compound`，**默认**）或「算术累加」
 *  （`curves`/`cum`）的累计值（都是「净值 − 1」的形式），+1 只是为了读起来是净值口径。 */
const nav = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? null : 1 + v

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

/** 曲线口径：`compound` = **复利**（默认；每期收益滚入本金 = 实盘满仓复投）；
 *  `arith` = 算术累加（各期收益直接相加，"平均每期赚多少"的视角）。
 *  ⚠ 两口径不可混比：`log(1+复利) ≈ Σr − ½Σr²` ⇒ 强策略复利显著**高于**算术（实测 csi1000
 *  负市值对数 5 年：净值 4.38 vs 2.67），弱/无效策略复利**低于**算术（波动损耗）。 */
type Basis = 'compound' | 'arith'

export default function TopkCurveModal({ open, onClose, name, row }: Props) {
  const [sel, setSel] = useState(0)
  const [bench, setBench] = useState('')
  const [hidden, setHidden] = useState<Record<string, boolean>>({})
  // 口径（v1.18.47）：**默认复利**（= 实盘）；后端旧版无 curves_compound 时自动回退算术
  const [basis, setBasis] = useState<Basis>('compound')
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
  // 可切换基准：多候选一次下发 ⇒ 这里纯切换、零重算；未选中时用后端给的默认（按股票池映射）
  const benchList = tc?.benchmarks?.items ?? []
  const benchCode =
    benchList.find((b) => b.code === bench)?.code ??
    benchList.find((b) => b.code === tc?.benchmarks?.default)?.code ??
    benchList[0]?.code ??
    ''
  const benchItem = benchList.find((b) => b.code === benchCode) ?? null
  // 固定 K 档（由表单「明细 K」决定，弹窗内只能切换、不能改 ⇒ 提示回表单重跑）
  const fixedKs = items.filter((it) => it.kind === 'topk').map((it) => it.k)

  // 图 1：选中档的三档累计曲线（0 / 0.004 / 0.008，均按调仓期扣费）+ 选中基准
  // 口径（basis）：复利取 `curves_compound` / `cum_compound`；后端旧版缺该字段时自动回退算术
  const detailData = useMemo(() => {
    if (!tc || !item) return []
    const cv = basis === 'compound' ? (item.curves_compound ?? item.curves) : item.curves
    const bv =
      basis === 'compound' ? (benchItem?.cum_compound ?? benchItem?.cum) : benchItem?.cum
    const st = stride(tc.dates.length)
    const pick = (i: number) => ({
      d: tc.dates[i],
      c0: nav(cv['0.0000']?.[i]),
      c4: nav(cv['0.0040']?.[i]),
      c8: nav(cv['0.0080']?.[i]),
      b: nav(bv?.[i]),
    })
    const out: Record<string, number | string | null>[] = []
    for (let i = 0; i < tc.dates.length; i += st) out.push(pick(i))
    const last = tc.dates.length - 1
    if (last >= 0 && (out.length === 0 || out[out.length - 1].d !== tc.dates[last])) {
      out.push(pick(last))
    }
    return out
  }, [tc, item, benchItem, basis])

  /** 取序列末尾的有效值（跳过尾部的 null/缺失）。 */
  const lastOf = (arr: (number | null)[] | undefined) => {
    if (!arr) return null
    for (let i = arr.length - 1; i >= 0; i--) {
      if (arr[i] != null && Number.isFinite(arr[i] as number)) return arr[i] as number
    }
    return null
  }
  // 区间累计超额：**必须同口径**（同一调仓日、同持有 h 日、同 basis）
  //   · 算术口径：差值 `组合 − 基准`（可直接相减）
  //   · 复利口径：**净值比** `(1+组合)/(1+基准) − 1`（两条复利净值曲线相减没有可解释的实盘含义）
  const _pv = lastOf(
    basis === 'compound'
      ? (item?.curves_compound?.['0.0000'] ?? item?.curves['0.0000'])
      : item?.curves['0.0000'],
  )
  const _bv = lastOf(
    basis === 'compound' ? (benchItem?.cum_compound ?? benchItem?.cum) : benchItem?.cum,
  )
  const excess =
    _pv == null || _bv == null
      ? null
      : basis === 'compound'
        ? _bv <= -1
          ? null
          : (1 + _pv) / (1 + _bv) - 1
        : _pv - _bv

  // 图 2：十分位累计收益曲线（10 条 + 多空）
  const decileData = useMemo(() => {
    if (!qc || !qc.groups?.length) return []
    const st = stride(qc.dates.length)
    const cumOf = (g: { cum: number[]; cum_compound?: number[] }) =>
      basis === 'compound' ? (g.cum_compound ?? g.cum) : g.cum
    const out: Record<string, number | string | null>[] = []
    for (let i = 0; i < qc.dates.length; i += st) {
      const p: Record<string, number | string | null> = { d: qc.dates[i] }
      for (const g of qc.groups) p[`q${g.quantile}`] = nav(cumOf(g)[i])
      // ⚠ 多空 = 最强−最弱 的**差值曲线**（不是净值）⇒ 不 +1，走右轴单独看（起点 0）；
      //   只有**算术口径**下相减才有可解释含义 ⇒ 复利模式不画（置 null）
      p.ls = basis === 'arith' ? (qc.long_short.cum[i] ?? null) : null
      out.push(p)
    }
    const last = qc.dates.length - 1
    if (last >= 0 && out.length && out[out.length - 1].d !== qc.dates[last]) {
      const p: Record<string, number | string | null> = { d: qc.dates[last] }
      for (const g of qc.groups) p[`q${g.quantile}`] = nav(cumOf(g)[last])
      p.ls = basis === 'arith' ? (qc.long_short.cum[last] ?? null) : null
      out.push(p)
    }
    return out
  }, [qc, basis])

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
              {tc
                ? `　调仓期 ${tc.rebalance_period} 交易日（默认 = 预测周期${
                    tc.horizon ? ` h=${tc.horizon}` : ''
                  }）　${tc.n} 次调仓`
                : ''}
              {qc ? `　分位档：最强 Q${qc.best_quantile} / 最弱 Q${qc.worst_quantile}` : ''}
            </span>
          </div>
          <button onClick={onClose} className="px-2 py-1 text-xs rounded border">
            关闭
          </button>
        </div>

        {/* 口径切换（v1.18.47）：**默认复利** = 实盘满仓复投；算术累加 = "平均每期赚多少"的视角 */}
        <div className="mx-4 mt-3 flex items-center gap-2 flex-wrap text-[11px]">
          <span className="text-slate-500">曲线口径：</span>
          <button
            onClick={() => setBasis('compound')}
            title="复利（默认）：每期收益滚入本金 ⇒ Π[(1+r)(1−费率×换手)]−1 = 实盘满仓复投口径"
            className={`px-2 py-0.5 rounded border ${
              basis === 'compound'
                ? 'border-sky-500 text-sky-600 bg-sky-50 dark:bg-sky-900/30'
                : 'border-slate-300 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700/40'
            }`}
          >
            复利（实盘）
          </button>
          <button
            onClick={() => setBasis('arith')}
            title="算术累加：各期收益直接相加（不滚入本金）；「平均每期赚多少」的统计视角，非实盘净值"
            className={`px-2 py-0.5 rounded border ${
              basis === 'arith'
                ? 'border-sky-500 text-sky-600 bg-sky-50 dark:bg-sky-900/30'
                : 'border-slate-300 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700/40'
            }`}
          >
            算术累加
          </button>
          <span className="text-slate-400">
            ⚠ 两口径不可混比：强策略复利显著高于算术（长期可达数十~上百个百分点），弱/无效策略反之
          </span>
        </div>

        {/* 口径 / 免责（必须显著标注） */}
        <div className="mx-4 mt-2 px-3 py-2 rounded bg-amber-50 dark:bg-amber-900/20 text-[11px] text-amber-700 dark:text-amber-300 leading-relaxed">
          <b>简化估算，读图前请先看口径</b>：① 费率是<b>往返（买+卖）合计</b>，按<b>调仓期</b>扣、
          只扣<b>实际调仓</b>的股票（首期建仓不计费）；② <b>未考虑涨跌停、停牌、流动性冲击</b>；
          ③ 曲线 = 每期持有组合的收益（调仓日取信号、T+1 买入、持有到下一个调仓日；调仓期默认 =
          预测周期 h）—— 当前口径：<b>{basis === 'compound'
            ? '复利（每期收益滚入本金 = 实盘满仓复投）'
            : '算术累加（各期收益直接相加，非实盘净值）'}</b>，
          <b>以 1 为起点</b>、<b>近似净值但不是逐日盯市净值</b>；
          ④ 默认档 = <b>超额收益最强的分位组</b>（逐期等分，只数与固定 K 档略有差异）；
          ⑤ <b>多空 = 最强组 − 最弱组</b>，A 股空头收益拿不到 ⇒ <b>不可实现</b>，仅作有效性参考
          （复利口径下两条净值相减无可解释含义 ⇒ <b>仅在算术口径显示</b>）；
          ⑥ <b>基准与组合同口径</b>：指数在<b>同一调仓日</b>的 T+1 → T+h+1 收益、按当前口径累计
          （价格指数、<b>不含分红</b>）⇒ 同口径可直接比超额。
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
                持仓期净值曲线（三档成本 · 同一组合 · <b>起点 = 1</b> ·{' '}
                <b className="text-sky-600">{basis === 'compound' ? '复利' : '算术累加'}</b>）
              </span>
              <span className="text-[11px] text-slate-400">切换组合（纯前端，零重算）：</span>
              <span className="text-[11px] text-slate-400 ml-1">｜基准：</span>
              {benchList.map((b) => (
                <button
                  key={b.code}
                  onClick={() => setBench(b.code)}
                  title={`${b.code}（同口径：指数在同一调仓期的持有 h 日收益、按当前口径累计；价格指数不含分红）`}
                  className={`px-1.5 py-0.5 rounded border text-[11px] ${
                    b.code === benchCode
                      ? 'border-slate-500 text-slate-700 bg-slate-100 dark:bg-slate-700'
                      : 'border-slate-300 text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700/40'
                  }`}
                >
                  {b.name}
                </button>
              ))}
              {!benchList.length && (
                <span className="text-[11px] text-slate-400">（本行未取到基准行情）</span>
              )}
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
              {tc ? `每 ${tc.rebalance_period} 个交易日调仓一次（调仓日取信号、T+1 买入、持有到下一个调仓日）；` : ''}
              {item?.kind === 'decile'
                ? `每日取因子最强端的 1/${qc?.n_groups ?? 10}（等分）⇒ 持仓约 ${item.k} 只，只数逐期变化；换手分母 = 当期在手只数`
                : `固定持仓 ${item?.k} 只（按名次取，与分位组口径略有差异）`}
              {'；'}
              {fixedKs.length > 0
                ? `固定 K 档（${fixedKs.join(' / ')} 只）来自表单「固定 K 档」预设 —— 改 K 需回表单重跑（每个额外 K 约 +0.1~0.2s）`
                : '想要更多固定 K 档？在表单「固定 K 档」里勾选（1/2/3/5/10/20/50/100/10%/20%）后重跑'}
              {'；点图例可显隐曲线'}
            </div>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={detailData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="d" tick={{ fontSize: 10 }} minTickGap={48} />
                <YAxis tick={{ fontSize: 10 }} width={52} domain={['auto', 'auto']} />
                <Tooltip formatter={(v: number | string) => (typeof v === 'number' ? v.toFixed(4) : v)} />
                <Legend
                  wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
                  onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                />
                {/* 净值口径 ⇒ 参考线在 1（起点） */}
                <ReferenceLine y={1} stroke="#94a3b8" strokeDasharray="3 3" />
                <Line
                  type="monotone"
                  dataKey="c0"
                  name="无成本"
                  stroke="#0f766e"
                  dot={false}
                  strokeWidth={2}
                  hide={!!hidden.c0}
                />
                <Line
                  type="monotone"
                  dataKey="c4"
                  name="往返 0.004"
                  stroke="#0284c7"
                  dot={false}
                  strokeWidth={1.6}
                  hide={!!hidden.c4}
                />
                <Line
                  type="monotone"
                  dataKey="c8"
                  name="往返 0.008"
                  stroke="#b45309"
                  dot={false}
                  strokeWidth={1.6}
                  hide={!!hidden.c8}
                />
                {benchItem && (
                  <Line
                    type="monotone"
                    dataKey="b"
                    name={`基准 ${benchItem.name}`}
                    stroke="#475569"
                    strokeDasharray="6 3"
                    dot={false}
                    strokeWidth={1.6}
                    hide={!!hidden.b}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
            {benchItem && (
              <div className="text-[11px] text-slate-500 mt-1">
                区间累计（{basis === 'compound' ? '复利' : '算术'}）：组合（无成本）
                <b>{signedPct(_pv)}</b>
                　基准 {benchItem.name} <b>{signedPct(_bv)}</b>
                　⇒ 超额{basis === 'compound' ? '（净值比）' : ''}{' '}
                <b className={excess != null && excess >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                  {signedPct(excess)}
                </b>
                {_bv == null ? '（基准尾部数据不足，已断线）' : ''}
              </div>
            )}

            {/* ---- 图 2：十分位累计收益曲线 ---- */}
            {qc && (
              <>
                <div className="flex items-center gap-2 mt-4 mb-1">
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                    十分位净值曲线（<b>起点 = 1</b>；1 = 最低值组 … {qc.n_groups} = 最高值组）
                  </span>
                  <span className="text-[11px] text-slate-400">
                    最强 Q{qc.best_quantile} / 最弱 Q{qc.worst_quantile}；
                    {basis === 'arith' ? (
                      <>
                        <b>右轴 = 多空</b>（最强−最弱，起点 0；不可实现，仅作参考）
                      </>
                    ) : (
                      <b>复利口径下多空（差值）无可解释含义 ⇒ 已隐藏</b>
                    )}
                    ；点图例可显隐曲线
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={250}>
                  <LineChart data={decileData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="d" tick={{ fontSize: 10 }} minTickGap={48} />
                    <YAxis tick={{ fontSize: 10 }} width={52} domain={['auto', 'auto']} />
                    {/* 多空是**差值**曲线（不是净值）⇒ 单独一根右轴，避免把净值曲线压扁；
                        复利口径下多空不显示 ⇒ 右轴一并隐藏（否则留一条空轴） */}
                    <YAxis
                      yAxisId="r"
                      orientation="right"
                      tick={{ fontSize: 10 }}
                      width={52}
                      domain={['auto', 'auto']}
                      hide={basis !== 'arith'}
                    />
                    <Tooltip formatter={(v: number | string) => (typeof v === 'number' ? v.toFixed(4) : v)} />
                    <Legend
                      wrapperStyle={{ fontSize: 10, cursor: 'pointer' }}
                      onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                    />
                    <ReferenceLine y={1} stroke="#94a3b8" strokeDasharray="3 3" />
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
                      yAxisId="r"
                      type="monotone"
                      dataKey="ls"
                      name={`多空 Q${qc.long_short.quantile[0]}−Q${qc.long_short.quantile[1]}（最强−最弱·右轴·不可实现）`}
                      stroke="#111827"
                      dot={false}
                      strokeWidth={2}
                      strokeDasharray="6 3"
                      hide={basis !== 'arith' || !!hidden.ls}
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
