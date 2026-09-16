import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

import {
  getSignalTestOptions,
  parseSignalCsv,
  runSignalTest,
  type SignalTestOptions,
  type SignalTestParseResult,
  type SignalTestRequest,
  type SignalTestRunResult,
} from '../api'
import { isValidAt, isReverseValidAt, pairStabilityOf } from './verdictRules'
import ZoomableLineChart from './ZoomableLineChart'

/**
 * 交易信号测试面板（v1.19.38 起；v1.19.46 起支持 **xlsx/xls + 单元格内多只 + 拖拽替换**）。
 *
 * 同事的二次平台公式会用 qlib 没有的算子 ⇒ 不逐个加算子，而是**直接给买入信号文件**：
 *   ① 信号清单（`日期,标的` / 同事的 Excel 表）⇒ 事件研究 + 等权持有回测；
 *   ② 聚宽成交明细 ⇒ 只出净值（A 模拟它的成本 / B 我的 cost / C 精确 / +基准）；
 *   ③ 聚宽《收益概述》⇒ 只画官方净值，或叠加到 ② 上做校准。
 *
 * 图表口径（用户 2026-09-15 要求「把事件研究里的那两张图搞过来，弄成一样的」）：
 *   **两套口径严格分开**（与 `EventStudyModal` 同款）——
 *   ① 日配对：触发组 ↔ 基准（可相减 ⇒ 下方超额曲线）② 事件级中位数：中位 ↔ 基准中位；
 *   均值/分位（p25/p75）无同口径基准 ⇒ **默认隐藏**，点击图例可开启。
 *   锚点判定复用 `verdictRules.ts`（紫=有效、红=有效反向），与单因子测试**同一份规则**。
 *   **所有图的图例都可点击隐藏/复现**（`hidden` + `toggleSeries`）。
 */

const FILL_LABEL: Record<string, string> = {
  t1_open: '次日开盘价',
  t_close: '信号日收盘价',
  t1_close: '次日收盘价',
}

const ALLOC_LABEL: Record<string, string> = {
  event_even: '事件驱动再平衡（有信号/到期就调平）',
  cash_even: '现金等分（不主动再平衡）',
  // v1.19.76：按批次分片（有"持仓周期/批次"列时自动出现）——每批固定份额、批内等权、只动刷新的票
  batch_even: '按批次分片（每批固定份额·批内等权·只动刷新的票）',
}

const NAV_LABEL: Record<string, string> = {
  benchmark: '基准',
  nav_sim_fee: 'A 模拟它的成本',
  nav_my_cost: 'B 我的成本',
  nav_exact: 'C 精确（成交价+手续费）',
  nav_exact_min: 'C′ 现金非负下界口径',
  nav_official: '官方净值（聚宽）',
  nav_official_bench: '官方基准（聚宽）',
}
const NAV_COLOR: Record<string, string> = {
  benchmark: '#94a3b8',
  nav_sim_fee: '#0ea5e9',
  nav_my_cost: '#f59e0b',
  nav_exact: '#10b981',
  nav_exact_min: '#a855f7',
  nav_official: '#e11d48',
  nav_official_bench: '#9ca3af',
}

/** 解析诊断/统计的**中文标签**（用户要求：别把 rows_total 这些键名直接糊在界面上）。 */
const STAT_LABEL: Record<string, string> = {
  rows_total: '源表行数', rows_valid: '有效信号', dropped: '丢弃行', dup_dropped: '重复行(计为份数)',
  dup_merged_rows: '重复行(计为份数)', weight_sum: '份数合计', bucket_count: '批次数', buckets: '批次',
  index_rows: '指数行(已忽略)', buy_signals: '买入信号', sell_signals: '卖出信号',
  stocks: '股票数', days: '信号天数', date_min: '起始日', date_max: '结束日',
  max_signals_per_day: '单日最多信号', signals_per_day_avg: '日均信号数',
  multi_code_cells: '含多只的单元格', had_header: '带表头', filename: '文件名',
  cancel_rows: '撤单行', intraday_orders: '日中委托(用收盘价近似)',
  buy_trades: '买入笔数', sell_trades: '卖出笔数', turnover: '成交总额', fee_total: '手续费合计',
  fee_rate_buy: '买入费率(反推)', fee_rate_sell: '卖出费率(反推)', stamp_tax_est: '其中印花税(反推)',
  first_day: '首个交易日', first_day_buy_amount: '首日买入额', first_day_buy_trades: '首日买入笔数',
  suggest_capital: '建议初始资金', min_capital: '现金非负下界', cum_flow_end: '期末累计现金流',
  open_positions: '期末未平仓数', fill_open: '开盘价成交笔数', fill_close: '收盘价成交笔数',
  nav_end: '期末考试净值', total_return: '总收益', max_drawdown: '最大回撤',
  bench_nav_end: '官方基准期末', excess_end_pct: '期末超额(%)',
  buy_total: '买入合计', sell_total: '卖出合计',
}
/** 显示成"金额"（千分位）的键 */
const MONEY_KEYS = new Set(['turnover', 'fee_total', 'first_day_buy_amount', 'suggest_capital',
  'min_capital', 'cum_flow_end', 'buy_total', 'sell_total'])
/** 显示成"百分比"的键（后端给的是小数） */
const RATE_KEYS = new Set(['fee_rate_buy', 'fee_rate_sell', 'stamp_tax_est', 'total_return', 'max_drawdown'])

function pct(v: number | null | undefined, digits = 2): string {
  return v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(digits)}%`
}

function statValue(k: string, v: unknown): string {
  if (v == null) return '-'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'number') {
    if (MONEY_KEYS.has(k)) return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    if (RATE_KEYS.has(k)) return `${(v * 100).toFixed(4)}%`
    return Number.isInteger(v) ? String(v) : v.toFixed(2)
  }
  return String(v)
}

function allocLabel(mode: string): string {
  if (ALLOC_LABEL[mode]) return ALLOC_LABEL[mode]
  const m = /^event_even_band(.+)$/.exec(mode)
  if (m) return `事件等权 · 死区 ${m[1].replace('p', '.')}%（自动对比档）`
  return mode
}

function navLabel(k: string): string {
  return NAV_LABEL[k] ?? allocLabel(k)
}
function navColor(k: string, i: number): string {
  return NAV_COLOR[k] ?? ['#0ea5e9', '#f59e0b', '#10b981', '#ef4444'][i % 4]
}
/** 悬停说明（用户 2026-09-15 追问"C 精确是怎么重建净值的？换策略（比如高分红股）误差会不会很大"）
 *  —— 放 `title` 里**不占版面**，也避免再往卡片里堆长句（那段长句正是他要删的）。 */
const NAV_TITLE: Record<string, string> = {
  nav_official: '聚宽《收益概述》里的官方净值（策略收益），用来给重建结果做校准。',
  nav_official_bench: '聚宽《收益概述》里的官方基准累计收益（默认隐藏，避免和"基准"下拉互相压扁）。',
  nav_sim_fee: '模拟它的成本：成交时点与股数按它的流水，价格换成我们的后复权价，费率用它反推的佣金+印花税。',
  nav_my_cost: '同上，费率换成本界面设定的 cost（往返合计）；A 与 B 的差就是费率档次的影响。'
    + 'A/B 的绝对值有 ±5% 量级口径不确定性（价格水平换算所致），只宜比较相对高低。',
  nav_exact: '精确重建：把成交明细当流水逐笔重放 —— 股数、成交价、手续费全部用 CSV 里的实际值'
    + '（成交价按委托时间取当日开盘价或收盘价），逐日按我们的收盘价盯市，所以价格水平差异被消掉了。'
    + '注意：成交明细只记买卖，不含现金分红入账（也含送转带来的股数变化由后续成交体现），'
    + '因此高分红/高送转策略会系统性偏低，缺口量级≈分红；它检验的是"流水是否记得全"，不代表策略本身好坏。',
  nav_exact_min: '用"现金非负下界"资金跑同一套流水（用于定位资金口径的影响）。',
  benchmark: '你选的指数基准（后复权口径）。',
}
const NAV_FALLBACK_TITLE = '净值曲线（起点归一为 1，可比）'

function navDash(k: string): string | undefined {
  if (k === 'nav_exact_min' || k === 'nav_official_bench') return '5 3'
  if (k === 'benchmark' || k === 'baseline' || k === 'baseline_median') return '4 4'
  return undefined
}

/** 事件研究逐行数据（图表与表格共用；字段与 `EventStudyModal` 对齐）。 */
interface EventRow {
  k: number
  n: number
  mean: number | null
  median: number | null
  p25: number | null
  p75: number | null
  win: number | null
  trigger_pair: number | null
  baseline: number | null
  baseline_median: number | null
  excess: number | null
  excess_median: number | null
  t: number | null
  dWin: number | null
  valid: boolean
  validRev: boolean
}

function bufToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let bin = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CHUNK)))
  }
  return btoa(bin)
}

const ACCEPT_EXT = ['.csv', '.txt', '.xlsx', '.xls']

/**
 * 锚点：**只在判定有效的持有日画点**（紫 `#7c3aed` = 有效、**红 `#dc2626` = 有效反向**），其余点不画。
 *
 * ⚠⚠ 签名必须与 `EventStudyModal.validAnchorDot` **完全一致**：`(field, color, revColor)`。
 *   2026-09-15 的教训：我照抄了弹窗的**调用写法**（`anchorDot('trigger_pair', '#7c3aed')`），
 *   却写了另一个签名 `anchorDot(color, revColor)` ⇒ 第一个参数（dataKey 字符串）被当成**颜色**、
 *   第二个（紫）被当成**反向色** ⇒ 实际渲染成 `fill="trigger_pair"`（无效色）+ **反向点紫色**
 *   —— 用户当场发现"有效反向锚点还是紫色的，没有改成红色"。
 *   修法：把签名对齐弹窗（`field` 参与取值、`revColor` 默认红），而不是去改一堆调用点。
 */
function anchorDot(field: string, color: string, revColor = '#dc2626') {
  return (p: any) => {
    const v = p.payload?.[field]
    const rev = p.payload?.validRev === true
    if ((p.payload?.valid === true || rev) && typeof v === 'number' && v != null) {
      return (
        <circle
          key={p.key ?? `a-${p.payload?.k ?? ''}`}
          cx={p.cx}
          cy={p.cy}
          r={3.5}
          fill={rev ? revColor : color}
          stroke="#fff"
          strokeWidth={1}
        />
      )
    }
    return <g key={p.key ?? `a-${p.payload?.k ?? ''}`} />
  }
}

export default function SignalTestPanel() {
  const [opts, setOpts] = useState<SignalTestOptions | null>(null)
  const [fileName, setFileName] = useState('')
  const [b64, setB64] = useState('')
  const [perfName, setPerfName] = useState('')
  const [perfB64, setPerfB64] = useState('')
  const [parsed, setParsed] = useState<SignalTestParseResult | null>(null)
  const [parsing, setParsing] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<SignalTestRunResult | null>(null)
  const [error, setError] = useState('')
  const [showIssues, setShowIssues] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [perfDragOver, setPerfDragOver] = useState(false)

  // 参数
  const [horizon, setHorizon] = useState(60)
  /** 净值曲线自己的持有周期（v1.19.62，默认跟随上面「预测周期」）。
   *
   *  用户 2026-09-15：「交易信号测试里是不是也要搞成输入不同周期出来不同净值曲线的模式？」
   *  ⇒ 净值区加一个周期选择器：改它**只重跑回测**（`backtest_only` 跳过事件研究那十几秒），
   *     事件研究那两张 k 图**保持不动** —— 它们算的是面板里的「预测周期」，与净值周期是两件事，
   *     所以界面上必须显式标出"净值按 X 天 / 事件研究按 Y 天"（口径分叉要写在明处）。 */
  const [navHold, setNavHold] = useState(60)
  const [navBusy, setNavBusy] = useState(false)
  const [navInfo, setNavInfo] = useState('')
  const [fill, setFill] = useState('t1_open')
  const [cost, setCost] = useState(0.004)
  const [capital, setCapital] = useState(1e9)
  const [pool, setPool] = useState('all')
  const [benchmark, setBenchmark] = useState('SH000300')
  const [strictLimit, setStrictLimit] = useState(true)
  const [rebalBand, setRebalBand] = useState(0)
  const [jqCapital, setJqCapital] = useState<number | ''>('')
  const [navFocus, setNavFocus] = useState('')

  // 曲线显隐（点击图例切换）。k 图与净值图共用一份（键名不重叠）。
  // 默认隐藏：均值/分位（无同口径基准，见事件研究弹窗说明）+ **官方基准**（用户 2026-09-15：
  // 「官方基准聚宽默认不显示」——它和"基准"下拉是两条不同的东西，一起画会互相压扁）
  const [hidden, setHidden] = useState<Record<string, boolean>>({
    mean: true, p25: true, p75: true, nav_official_bench: true,
  })
  const toggleSeries = useCallback((key?: string | number) => {
    if (typeof key !== 'string' || !key) return
    setHidden((h) => ({ ...h, [key]: !h[key] }))
  }, [])

  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    getSignalTestOptions().then(setOpts).catch(() => setOpts(null))
  }, [])

  /** 选中主信号文件（**替换**语义：新文件顶掉旧文件、旧结果与旧诊断）。 */
  const onPickFile = useCallback(async (f: File) => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase()
    if (!ACCEPT_EXT.includes(ext)) {
      setError(`不支持的文件类型「${ext}」—— 请给 CSV / TXT / XLSX / XLS`)
      return
    }
    setFileName(f.name)
    // ⚠ 用户要求「拖进去，之前的文件就顶掉，不然会冲突」：连结果、诊断、附带的《收益概述》一起清
    setResult(null)
    setParsed(null)
    setError('')
    setPerfName('')
    setPerfB64('')
    try {
      const b = bufToBase64(await f.arrayBuffer())
      setB64(b)
      setParsing(true)
      const p = await parseSignalCsv({ content_b64: b, filename: f.name })
      setParsed(p)
      if (p.mode === 'jq_trades' && p.stats) {
        const sug = p.stats.suggest_capital as number | undefined
        if (sug) setJqCapital(sug)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setParsing(false)
    }
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const f = e.dataTransfer.files?.[0]
      if (f) void onPickFile(f)
    },
    [onPickFile],
  )

  /** ② 可选的《收益概述》：同样支持**拖拽**（用户 2026-09-15：「我怎么不能拖CSV进去？鼠标变成叉」
   *  —— 因为它原来只是个普通容器，没挂 dragover/drop 处理，浏览器就用"禁止"光标 ✗）。 */
  const pickPerf = useCallback(async (f: File) => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase()
    if (!ACCEPT_EXT.includes(ext)) {
      setError(`《收益概述》不支持的文件类型「${ext}」—— 请给 CSV / TXT / XLSX / XLS`)
      return
    }
    setPerfName(f.name)
    try {
      setPerfB64(bufToBase64(await f.arrayBuffer()))
    } catch {
      setPerfB64('')
    }
  }, [])

  const onDropPerf = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setPerfDragOver(false)
      const f = e.dataTransfer.files?.[0]
      if (f) void pickPerf(f)
    },
    [pickPerf],
  )

  /** 组装请求体 —— `run`（整套）与「净值周期」重算（只回测）**共用一份**，避免两处参数走偏。 */
  const buildReq = useCallback(
    (extra?: Partial<SignalTestRequest>): SignalTestRequest => ({
      content_b64: b64,
      filename: fileName,
      perf_b64: perfB64 || undefined,
      cost, benchmark, horizon, fill, capital,
      strict_limit: strictLimit,
      rebal_band: rebalBand,
      pool,
      jq_capital: typeof jqCapital === 'number' ? jqCapital : undefined,
      ...extra,
    }),
    [b64, fileName, perfB64, cost, benchmark, horizon, fill, capital, strictLimit,
      rebalBand, pool, jqCapital],
  )

  const run = useCallback(async () => {
    if (!b64) {
      setError('请先选择（或拖入）信号文件')
      return
    }
    setRunning(true)
    setError('')
    setResult(null)
    setNavInfo('')
    try {
      const r = await runSignalTest(buildReq())
      setResult(r)
      const cols = r.backtest?.nav_columns ?? []
      setNavFocus(r.backtest?.alloc_default ?? cols[2] ?? '')
      // 净值周期跟随本次运行的「预测周期」（后端回的 diag.hold_days 是真正用上的那个）
      setNavHold(Number(r.backtest?.diag?.hold_days) || horizon)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? (e instanceof Error ? e.message : String(e)))
    } finally {
      setRunning(false)
    }
  }, [b64, buildReq, horizon])

  const appliedHold = Number((result?.backtest as { diag?: { hold_days?: number } } | undefined)
    ?.diag?.hold_days) || null
  /** 净值周期切换只在"有回测且周期真的会改变净值"的模式下出现（聚宽流水模式的净值与持有周期无关） */
  const canNavHold = !!result?.backtest && parsed?.mode !== 'jq_trades' && parsed?.mode !== 'jq_perf'

  /**
   * **只重跑回测**（净值曲线的持有周期切换，v1.19.62）。
   *
   * ⚠ 只替换 `result.backtest`（净值 / 统计 / 成交 / 被拒资金 全部随之更新）；
   *   `event`（事件研究那两张 k 图与锚点）与解析诊断**原样保留** —— 它们算的是运行时填的
   *   「预测周期」，不该被净值周期改动（界面上另有 amber 提示写明两者不同）。
   */
  const recalcNav = useCallback(
    async (k: number) => {
      const kk = Math.max(1, Math.min(250, Math.round(Number(k) || 0)))
      if (!b64 || !kk) return
      setNavBusy(true)
      setNavInfo('')
      const t0 = performance.now()
      try {
        const r = await runSignalTest(buildReq({ horizon: kk, backtest_only: true }))
        if (!r.backtest) throw new Error('后端未返回回测结果')
        setResult((prev) => (prev ? { ...prev, backtest: r.backtest } : r))
        setNavHold(kk)
        setNavInfo(
          `已按 ${kk} 天重算净值（用时 ${((performance.now() - t0) / 1000).toFixed(1)}s；` +
            `只重跑回测，事件研究仍是 ${horizon} 天口径）`,
        )
      } catch (e: any) {
        setNavInfo(
          '重算失败：' + (e?.response?.data?.detail ?? (e instanceof Error ? e.message : String(e))),
        )
      } finally {
        setNavBusy(false)
      }
    },
    [b64, buildReq, horizon],
  )

  // ---------------- 事件研究：逐 k 数据（与 EventStudyModal 完全同构） ----------------
  const eventRows = useMemo<EventRow[]>(() => {
    const es = result?.event as any
    if (!es) return []
    const bl = es.baseline ?? null
    // ⚠ 按 `baseline.ks` 定位（与事件研究弹窗同口径），不要假设 ks 一定是 [1..N]（`arr[k-1]` 会错位）
    const idx = new Map<number, number>()
    if (Array.isArray(bl?.ks)) bl.ks.forEach((kk: number, i: number) => idx.set(Number(kk), i))
    const at = (arr: any, k: number) => {
      if (!arr) return null
      const i = idx.size ? idx.get(k) : k - 1
      const v = i == null ? null : arr[i]
      return v == null || !Number.isFinite(Number(v)) ? null : Number(v)
    }
    const rows: EventRow[] = (es.curve ?? []).map((c: any) => {
      const k = Number(c.k)
      const ex = at(bl?.excess, k)
      const t = at(bl?.t_hac, k)
      const dWin = at(bl?.win, k)
      const stable = pairStabilityOf({ t, win: dWin })
      const stableRev = pairStabilityOf({ t, win: dWin }, true)
      return {
        k,
        n: Number(c.n ?? 0),
        mean: c.mean ?? null,
        median: c.median ?? null,
        p25: c.p25 ?? null,
        p75: c.p75 ?? null,
        win: c.win ?? null,
        trigger_pair: at(bl?.trigger_pair, k),
        baseline: at(bl?.baseline, k),
        baseline_median: at(bl?.baseline_median, k),
        excess: ex,
        excess_median: at(bl?.excess_median, k),
        t, dWin,
        valid: isValidAt(k, c.median ?? null, c.win ?? null, ex, stable.ok),
        validRev: isReverseValidAt(k, c.median ?? null, c.win ?? null, ex, stableRev.ok),
      }
    })
    return rows
  }, [result])

  /** Recharts 用的百分比数据（×100，与事件研究弹窗一致）。 */
  const curveChart = useMemo(
    () => eventRows.map((r) => ({
      ...r,
      mean: r.mean == null ? null : r.mean * 100,
      median: r.median == null ? null : r.median * 100,
      p25: r.p25 == null ? null : r.p25 * 100,
      p75: r.p75 == null ? null : r.p75 * 100,
      trigger_pair: r.trigger_pair == null ? null : r.trigger_pair * 100,
      baseline: r.baseline == null ? null : r.baseline * 100,
      baseline_median: r.baseline_median == null ? null : r.baseline_median * 100,
    })),
    [eventRows],
  )
  const excessChart = useMemo(
    () => eventRows.map((r) => ({
      k: r.k,
      excess: r.excess == null ? null : r.excess * 100,
      excess_median: r.excess_median == null ? null : r.excess_median * 100,
      valid: r.valid,
      validRev: r.validRev,
    })),
    [eventRows],
  )
  const validKind = useMemo(() => {
    const m = new Map<number, 'good' | 'reverse' | 'none'>()
    eventRows.forEach((r) => m.set(r.k, r.valid ? 'good' : r.validRev ? 'reverse' : 'none'))
    return m
  }, [eventRows])

  const navData = useMemo(() => {
    const nav = result?.backtest?.nav ?? result?.replay?.nav ?? []
    return nav.map((r) => {
      const o: Record<string, any> = { date: r.date }
      Object.keys(r).forEach((k) => {
        if (k !== 'date') o[k] = typeof r[k] === 'number' ? (r[k] as number) : null
      })
      return o
    })
  }, [result])

  const navKeys = useMemo(() => {
    const cols = result?.backtest?.nav_columns
    if (cols?.length) return cols
    const first = navData[0]
    return first ? Object.keys(first).filter((k) => k !== 'date') : []
  }, [result, navData])

  const parseStats = (parsed?.stats ?? {}) as Record<string, any>
  const bt = result?.backtest ?? null
  const replay = result?.replay ?? null
  const official = result?.official as any

  const tooltipLabel = (l: any) => {
    const kind = validKind.get(Number(l))
    if (kind === 'good') {
      return (
        <span>
          持有 {l} 个交易日 ·{' '}
          <span className="text-emerald-700 dark:text-emerald-300 font-semibold">有效 ✓</span>
        </span>
      )
    }
    if (kind === 'reverse') {
      return (
        <span>
          持有 {l} 个交易日 ·{' '}
          <span className="text-red-600 dark:text-red-400 font-semibold">反向有效 ⟲</span>
        </span>
      )
    }
    return <span>持有 {l} 个交易日 · 待观察</span>
  }

  const field = 'flex flex-col gap-1'
  const input = 'w-full border rounded px-2 py-1.5 text-sm'
  const card = 'border rounded-lg p-3 text-xs space-y-1'

  return (
    <section className="border rounded-lg p-4 space-y-4 bg-white dark:bg-slate-900">
      <header className="flex flex-wrap items-baseline gap-3">
        <h3 className="font-semibold">交易信号测试</h3>
        <span className="text-xs text-slate-500">
          外部买入信号（CSV / Excel 表 / 聚宽成交明细 / 聚宽《收益概述》）⇒ 事件研究 + 等权持有回测
        </span>
        {result && (
          <span className="text-xs text-emerald-600">
            总耗时 {(result.elapsed ?? 0).toFixed(2)}s
            {result.timings &&
              `（解析 ${result.timings.parse}s / 取价 ${result.timings.prices ?? '-'}s / 事件 ${
                result.timings.event ?? '-'
              }s${result.timings.event_cached ? '·缓存命中' : ''} / 回测 ${
                result.timings.backtest ?? result.timings.replay ?? '-'
              }s）`}
          </span>
        )}
      </header>

      {/* ---------- 上传区：拖拽 + 选择（拖入新文件=替换） ---------- */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={`rounded-lg border-2 border-dashed p-3 flex flex-col gap-2 transition-colors ${
            dragOver
              ? 'border-sky-500 bg-sky-50 dark:bg-sky-950/30'
              : 'border-slate-300 dark:border-slate-600'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-slate-600 dark:text-slate-300">
              ① 信号文件：拖进来，或
            </span>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="text-xs border rounded px-2 py-1 text-sky-600 hover:bg-sky-50 dark:text-sky-400"
            >
              选择文件
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.txt,.xlsx,.xls"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void onPickFile(f)
              }}
            />
          </div>
          <div className="text-[11px] text-slate-400">
            支持 CSV / TXT / XLSX / XLS（GBK 或 UTF-8 都行）；拖入新文件会替换当前文件与结果
          </div>
          {fileName && (
            <div className="text-xs text-slate-600 dark:text-slate-300 flex flex-wrap items-center gap-2">
              <span className="font-medium">{fileName}</span>
              {parsed && (
                <span className="text-slate-400">
                  格式：
                  <b className="text-slate-600 dark:text-slate-300">
                    {parsed.mode === 'jq_trades'
                      ? '聚宽成交明细'
                      : parsed.mode === 'jq_perf'
                        ? '聚宽收益概述'
                        : '信号清单'}
                  </b>
                  ｜来源：{parsed.encoding === 'excel' ? 'Excel 表格' : `文本(${parsed.encoding})`}
                  {parseStats.had_header === false && '｜无表头（按位置识别）'}
                </span>
              )}
              {parsing && <span className="text-sky-600">解析中…</span>}
              {b64 && (
                <button
                  type="button"
                  className="text-slate-400 underline"
                  onClick={() => {
                    setFileName('')
                    setB64('')
                    setParsed(null)
                    setResult(null)
                  }}
                >
                  清空
                </button>
              )}
            </div>
          )}
        </div>

        <div
          onDragOver={(e) => {
            e.preventDefault()
            setPerfDragOver(true)
          }}
          onDragLeave={() => setPerfDragOver(false)}
          onDrop={onDropPerf}
          className={`rounded-lg border-2 border-dashed p-3 flex flex-col gap-2 transition-colors ${
            perfDragOver
              ? 'border-sky-500 bg-sky-50 dark:bg-sky-950/30'
              : 'border-slate-300 dark:border-slate-600'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-slate-600 dark:text-slate-300">
              ②（可选）聚宽《收益概述》：拖进来，或
            </span>
            <label className="text-xs border rounded px-2 py-1 text-sky-600 hover:bg-sky-50 dark:text-sky-400 cursor-pointer">
              选择文件
              <input
                type="file"
                accept=".csv,.txt,.xlsx,.xls"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) void pickPerf(f)
                }}
              />
            </label>
          </div>
          <div className="text-[11px] text-slate-400">
            有它就把官方净值叠加上图做校准，并按逐日买卖金额核对成交明细是否完整
          </div>
          {perfName && (
            <div className="text-xs text-slate-600 dark:text-slate-300 flex items-center gap-2">
              <span className="font-medium">{perfName}</span>
              <button
                type="button"
                className="text-slate-400 underline"
                onClick={() => {
                  setPerfName('')
                  setPerfB64('')
                }}
              >
                清空
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ---------- 参数（统一 grid ⇒ 对齐） ---------- */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 items-end">
        {parsed?.mode !== 'jq_trades' && parsed?.mode !== 'jq_perf' && (
          <>
            <label className={field}>
              <span className="text-xs text-slate-500">预测周期 N（天）</span>
              <input
                type="number"
                min={1}
                max={250}
                className={input}
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
              />
            </label>
            <label className={field}>
              <span className="text-xs text-slate-500">成交时点</span>
              <select className={input} value={fill} onChange={(e) => setFill(e.target.value)}>
                {(opts?.fills ?? Object.keys(FILL_LABEL).map((k) => ({ key: k, name: FILL_LABEL[k] }))).map((f) => (
                  <option key={f.key} value={f.key}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={field}>
              <span className="text-xs text-slate-500">基准池（未触发组）</span>
              <select className={input} value={pool} onChange={(e) => setPool(e.target.value)}>
                {(opts?.pools ?? [{ key: 'all', name: '全A' }]).map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={field}>
              <span className="text-xs text-slate-500">初始资金</span>
              <input
                type="number"
                className={input}
                value={capital}
                onChange={(e) => setCapital(Number(e.target.value))}
              />
            </label>
            <label className={field}>
              <span className="text-xs text-slate-500">再平衡死区（0=完全调平）</span>
              <input
                type="number"
                step={0.01}
                min={0}
                max={0.5}
                className={input}
                value={rebalBand}
                onChange={(e) => setRebalBand(Number(e.target.value))}
              />
            </label>
          </>
        )}
        {parsed?.mode === 'jq_trades' && (
          <label className={field}>
            <span className="text-xs text-slate-500">聚宽初始资金</span>
            <input
              type="number"
              className={input}
              value={jqCapital}
              onChange={(e) => setJqCapital(e.target.value === '' ? '' : Number(e.target.value))}
            />
          </label>
        )}
        <label className={field}>
          <span className="text-xs text-slate-500">成本（往返合计）</span>
          <input
            type="number"
            step={0.0005}
            min={0}
            className={input}
            value={cost}
            onChange={(e) => setCost(Number(e.target.value))}
          />
        </label>
        <label className={field}>
          <span className="text-xs text-slate-500">基准</span>
          <select className={input} value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>
            {(opts?.benchmarks ?? [{ code: 'SH000300', name: '沪深300' }]).map((b) => (
              <option key={b.code} value={b.code}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        {parsed?.mode !== 'jq_trades' && parsed?.mode !== 'jq_perf' && (
          <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300 pb-2">
            <input
              type="checkbox"
              checked={strictLimit}
              onChange={(e) => setStrictLimit(e.target.checked)}
            />
            严格涨跌停/停牌
          </label>
        )}
        <div className="flex items-end">
          <button
            type="button"
            onClick={() => void run()}
            disabled={!b64 || running}
            className="w-full px-4 py-2 rounded text-sm bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-40"
          >
            {running ? '跑测试中…' : '开始测试'}
          </button>
        </div>
      </div>

      {error && <div className="text-sm text-red-600">{error}</div>}

      {/* ---------- 解析诊断（中文标签） ---------- */}
      {parsed && (
        <div className="border rounded-lg p-3 text-xs space-y-2 bg-slate-50 dark:bg-slate-800">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-x-4 gap-y-1">
            {Object.entries(parseStats)
              .filter(([k]) => !['warnings', 'filename'].includes(k))
              .map(([k, v]) => (
                <div key={k} className="flex justify-between gap-2">
                  <span className="text-slate-500">{STAT_LABEL[k] ?? k}</span>
                  <span className="font-medium text-slate-700 dark:text-slate-200">
                    {statValue(k, v)}
                  </span>
                </div>
              ))}
          </div>
          {parseStats.warnings && Object.keys(parseStats.warnings).length > 0 && (
            <div className="text-amber-700">
              未识别/歧义：
              {Object.entries(parseStats.warnings as Record<string, number>).map(([k, v]) => (
                <span key={k} className="mr-3">
                  {k} × {v}
                </span>
              ))}
            </div>
          )}
          {parsed.issues?.length > 0 && (
            <div>
              <button type="button" className="text-sky-600 underline" onClick={() => setShowIssues((s) => !s)}>
                {showIssues ? '收起' : '展开'}丢弃/忽略明细（{parsed.issues.length} 条）
              </button>
              {showIssues && (
                <div className="mt-1 max-h-40 overflow-auto font-mono">
                  {parsed.issues.slice(0, 200).map((it, i) => (
                    <div key={i}>
                      第 {it.row} 行：{it.reason}｜{it.raw}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {result?.warnings?.length ? (
        <div className="text-xs text-amber-700 space-y-1">
          {result.warnings.map((w, i) => (
            <div key={i}>{w}</div>
          ))}
        </div>
      ) : null}

      {/* ---------------- 模式①：信号清单 ---------------- */}
      {bt && (
        <div className="space-y-4">
          <div className="text-xs text-slate-500">
            信号 {String((result?.signal as any)?.n_buy ?? '-')} 条（买入）｜
            {String((result?.signal as any)?.n_stocks ?? '-')} 只｜区间{' '}
            {String((result?.signal as any)?.span?.start)} ~ {String((result?.signal as any)?.span?.end)}
            ｜成交时点 <b>{FILL_LABEL[bt.fill] ?? bt.fill}</b>｜成本 {bt.cost}｜资金{' '}
            {(bt.capital ?? 0).toExponential(2)}
          </div>

          {/* 两张事件研究图（与「事件研究」弹窗同款：成对口径 + 锚点 + 可点图例） */}
          {curveChart.length > 0 && (
            <>
              <div className="border rounded-lg p-3">
                <div className="text-xs text-slate-500 mb-1">
                  持有期收益曲线（事件研究 · 单位 %）
                  <span className="text-violet-600 dark:text-violet-400 ml-2">● 紫点 = 判定「有效」的持有日</span>
                  <span className="text-red-600 dark:text-red-400 ml-2">● 红点 = 判定「反向有效」的持有日</span>
                  <span className="text-slate-400 ml-2">
                    默认只显示两对<b>同口径</b>曲线：① 触发组(日配对) ↔ 基准·未触发组(日配对)；
                    ② 中位数(事件级) ↔ 基准中位数(事件级)。均值与 p25/p75 无同口径基准 ⇒ 默认隐藏，
                    <b>点击图例可开启</b>（请勿用它们减「基准(日配对)」）。
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={curveChart} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis
                      dataKey="k"
                      tick={{ fontSize: 11 }}
                      label={{ value: 'k (交易日)', position: 'insideBottomRight', offset: -2, fontSize: 11 }}
                    />
                    <YAxis tick={{ fontSize: 11 }} width={50} />
                    <Tooltip
                      formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(3)}%` : v)}
                      labelFormatter={tooltipLabel}
                    />
                    <Legend
                      wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
                      onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                    />
                    <Line type="monotone" dataKey="trigger_pair" name="触发组(日配对)" stroke="#7c3aed"
                      dot={anchorDot('trigger_pair', '#7c3aed')} strokeWidth={2} hide={!!hidden.trigger_pair} />
                    <Line type="monotone" dataKey="baseline" name="基准·未触发组(日配对)" stroke="#94a3b8"
                      dot={false} strokeWidth={1.5} strokeDasharray="4 4" hide={!!hidden.baseline} />
                    <Line type="monotone" dataKey="median" name="中位数(事件级)" stroke="#0284c7"
                      dot={false} strokeWidth={2} hide={!!hidden.median} />
                    <Line type="monotone" dataKey="baseline_median" name="基准中位数·未触发组(事件级)"
                      stroke="#0e7490" dot={false} strokeWidth={1.5} strokeDasharray="2 2"
                      hide={!!hidden.baseline_median} />
                    <Line type="monotone" dataKey="p75" name="p75(事件级·默认隐藏)" stroke="#cbd5e1"
                      dot={false} strokeWidth={1} hide={!!hidden.p75} />
                    <Line type="monotone" dataKey="p25" name="p25(事件级·默认隐藏)" stroke="#cbd5e1"
                      dot={false} strokeWidth={1} hide={!!hidden.p25} />
                    <Line type="monotone" dataKey="mean" name="均值(事件级·无同口径基准)" stroke="#dc2626"
                      dot={false} strokeWidth={2} strokeDasharray="6 2" hide={!!hidden.mean} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {excessChart.length > 0 && (
                <div className="border rounded-lg p-3">
                  <div className="text-xs text-slate-500 mb-1">
                    超额曲线（触发组 − 基准·未触发组；单位 %）
                    <span className="text-violet-600 dark:text-violet-400 ml-2">● 紫点 = 有效</span>
                    <span className="text-red-600 dark:text-red-400 ml-2">● 红点 = 反向有效</span>
                    <span className="text-slate-400 ml-2">
                      均值＝日配对口径（易被少数暴涨事件主导）；中位数＝事件级口径（典型一次触发的超额）
                    </span>
                  </div>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={excessChart} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="k" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} width={50} />
                      <Tooltip
                        formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(3)}%` : v)}
                        labelFormatter={tooltipLabel}
                      />
                      <Legend
                        wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
                        onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                      />
                      <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
                      <Line type="monotone" dataKey="excess" name="超额(均值·日配对)" stroke="#7c3aed"
                        dot={anchorDot('excess', '#7c3aed')} strokeWidth={2} hide={!!hidden.excess} />
                      <Line type="monotone" dataKey="excess_median" name="超额(中位数·事件级)" stroke="#0e7490"
                        dot={false} strokeWidth={1.5} strokeDasharray="2 2" hide={!!hidden.excess_median} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </>
          )}

          {/* 净值（两种资金方案 + 基准）
              v1.19.49：① 位置移到逐 k 明细表**上面**（用户 2026-09-15）；
              ② 换成可滚轮缩放/拖动平移的 `ZoomableLineChart`，且缩放后 **Y 轴按可见区间重算**
                 （不重算的话 20 年曲线缩到局部会被全局 scale 压成一条扁线）。 */}
          <div className="border rounded-lg p-3">
            <div className="flex flex-wrap items-center gap-3 mb-1">
              <span className="text-sm font-medium">
                净值曲线（{appliedHold ?? horizon} 天持有 · 两种资金方案 + 基准）
              </span>
              <select
                className="text-xs border rounded px-2 py-1"
                value={navFocus}
                onChange={(e) => setNavFocus(e.target.value)}
              >
                {navKeys.map((k) => (
                  <option key={k} value={k}>
                    {navLabel(k)}
                  </option>
                ))}
              </select>
              <span className="text-[11px] text-slate-400">点击图例可隐藏/显示任意曲线</span>
            </div>
            {/* 净值周期切换（v1.19.62，用户 2026-09-15）：
                ⚠ 走 `backtest_only` ⇒ **只重跑回测**（跳过事件研究那十几秒，约 2~3s）；
                  事件研究那张 k 图与锚点**保持不动**（按运行时填的「预测周期」）——
                  两者不一致时下面给 amber 提示，避免把两个口径看混。 */}
            {canNavHold && (
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <span className="text-xs text-slate-500">净值持有周期</span>
                <input
                  type="number"
                  min={1}
                  max={250}
                  className="w-16 text-xs border rounded px-2 py-1"
                  value={navHold}
                  onChange={(e) => setNavHold(Number(e.target.value))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void recalcNav(navHold)
                  }}
                />
                <button
                  type="button"
                  disabled={navBusy || !b64}
                  onClick={() => void recalcNav(navHold)}
                  className="px-2.5 py-1 rounded text-xs bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-40"
                >
                  {navBusy ? '重算中…' : '按此周期重算净值'}
                </button>
                <span className="text-[11px] text-slate-400">
                  只重跑回测（跳过事件研究），约 2~3 秒
                </span>
                {appliedHold != null && appliedHold !== horizon && (
                  <span className="text-[11px] text-amber-600">
                    净值按 {appliedHold} 天 · 上方事件研究（k 图/锚点）仍按 {horizon} 天
                  </span>
                )}
                {navInfo && (
                  <span className="text-[11px] text-slate-500 basis-full">{navInfo}</span>
                )}
              </div>
            )}
            <ZoomableLineChart
              data={navData}
              keys={navKeys}
              labelOf={navLabel}
              colorOf={navColor}
              dashOf={navDash}
              focus={navFocus}
              hidden={hidden}
              onToggle={toggleSeries}
              statKey={navFocus || navKeys[0]}
              height={300}
            />
          </div>

          {/* 逐 k 明细表 */}
          {eventRows.length > 0 && (
            <div className="overflow-auto max-h-72 border rounded-lg">
              <table className="min-w-full text-xs">
                <thead className="bg-slate-100 dark:bg-slate-800 sticky top-0">
                  <tr>
                    {['k', 'n', '均值', '中位', '胜率', '触发组(日配对)', '基准(日配对)', '超额(日配对)',
                      'HAC t', '日胜率', '结论'].map((h) => (
                      <th key={h} className="px-2 py-1 text-left font-medium whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {eventRows
                    .filter((r) => r.k <= Math.min(horizon, 60) || r.k % 5 === 0)
                    .map((r) => (
                      <tr key={r.k} className="border-t">
                        <td className="px-2 py-0.5">{r.k}</td>
                        <td className="px-2 py-0.5">{r.n}</td>
                        <td className="px-2 py-0.5">{pct(r.mean)}</td>
                        <td className="px-2 py-0.5">{pct(r.median)}</td>
                        <td className="px-2 py-0.5">{pct(r.win, 1)}</td>
                        <td className="px-2 py-0.5">{pct(r.trigger_pair)}</td>
                        <td className="px-2 py-0.5">{pct(r.baseline)}</td>
                        <td className="px-2 py-0.5">{pct(r.excess)}</td>
                        <td className="px-2 py-0.5">{r.t == null ? '-' : r.t.toFixed(2)}</td>
                        <td className="px-2 py-0.5">{pct(r.dWin, 1)}</td>
                        <td className="px-2 py-0.5 whitespace-nowrap">
                          {r.valid ? (
                            <span className="text-violet-700 font-medium">有效✓</span>
                          ) : r.validRev ? (
                            <span className="text-red-600 font-medium">有效(反向)✓</span>
                          ) : (
                            <span className="text-slate-400">-</span>
                          )}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 统计卡（两方案） */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(bt.stats).map(([mode, st]: [string, any]) => (
              <div key={mode} className={card}>
                <div className="font-medium">
                  {allocLabel(mode)}
                  {st.rebal_band ? `（死区 ${(st.rebal_band * 100).toFixed(1)}%）` : ''}
                  {mode === bt.alloc_default && <span className="text-emerald-600"> · 默认</span>}
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                  {[
                    ['期末净值', String(st.final_nav)],
                    ['总收益', pct(st.total_return)],
                    ['年化 / 最大回撤', `${pct(st.perf?.annual_return)} / ${pct(st.perf?.max_drawdown)}`],
                    ['成交笔数', `${st.trades}（买 ${st.buys}/卖 ${st.sells}）`],
                    ['成本合计', `${(st.fees_paid / 1e4).toFixed(0)} 万（本金 ${pct(st.fee_ratio_of_capital)}）`],
                    ['刷新持有 / 卖出顺延', `${st.hold_refreshes} / ${st.sell_deferrals}`],
                    ['被拒（涨停/停牌/资金）', `${st.rejects_limit_up} / ${st.rejects_suspended} / ${st.rejects_no_cash}`],
                    ['平均持有 / 已实现胜率', `${st.avg_hold_days} 日 / ${pct(st.win_rate, 1)}`],
                    ['期末持仓 / 现金最低', `${st.open_positions_end} 只 / ${Number(st.min_cash).toFixed(0)}`],
                  ].map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-2">
                      <span className="text-slate-500">{k}</span>
                      <span className="font-medium text-slate-700 dark:text-slate-200 text-right">{v}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {bt.rejects?.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-slate-500">
                被拒/顺延明细（{bt.rejects.length} 条，最多显示 200）
              </summary>
              <div className="mt-1 max-h-40 overflow-auto font-mono">
                {bt.rejects.slice(0, 200).map((r: any, i: number) => (
                  <div key={i}>
                    {r.date} {r.code} {r.text}
                    {r.signal_date ? `（信号日 ${r.signal_date}）` : ''}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {/* ---------------- 模式③：只上传《收益概述》 ---------------- */}
      {result?.mode === 'jq_perf' && (
        <div className="space-y-3">
          <div className="text-xs text-slate-500">
            只上传了《收益概述》⇒ 仅展示官方净值（含官方基准），不做重建。想校准重建净值请同时上传成交明细。
          </div>
          <div className="border rounded-lg p-3">
            <ZoomableLineChart
              data={navData}
              keys={navKeys}
              labelOf={navLabel}
              colorOf={navColor}
              dashOf={navDash}
              hidden={hidden}
              onToggle={toggleSeries}
              statKey={navKeys[0]}
              height={320}
            />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            {[
              ['期末考试净值', String(official?.stats?.nav_end ?? '-')],
              ['总收益', pct(official?.stats?.total_return)],
              ['最大回撤', pct(official?.stats?.max_drawdown)],
              ['官方基准期末', String(official?.stats?.bench_nav_end ?? '-')],
              ['期末超额(%)', String(official?.stats?.excess_end_pct ?? '-')],
              ['区间', `${official?.stats?.date_min ?? '-'} ~ ${official?.stats?.date_max ?? '-'}`],
              ['买入合计', Number(official?.stats?.buy_total ?? 0).toLocaleString()],
              ['卖出合计', Number(official?.stats?.sell_total ?? 0).toLocaleString()],
            ].map(([k, v]) => (
              <div key={k} className="border rounded-lg p-2 flex flex-col gap-0.5">
                <span className="text-slate-500">{k}</span>
                <span className="font-medium">{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---------------- 模式②：聚宽成交明细 ---------------- */}
      {replay && result?.mode === 'jq_trades' && (
        <div className="space-y-4">
          {official?.calib && (
            <div className="border rounded-lg p-3 text-xs space-y-1 bg-emerald-50 dark:bg-emerald-950/30">
              <div className="font-medium">
                官方《收益概述》校准（截至 {official.calib.as_of ?? '-'}，两条曲线同一天比较）
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1">
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">官方期末净值</span>
                  <span className="font-medium">{String(official.calib.official_final ?? '-')}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">我方 C（精确）</span>
                  <span className="font-medium">{String(official.calib.rebuilt_final)}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">偏差</span>
                  <span className="font-medium">{pct(official.calib.diff_final, 2)}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">逐日金额一致</span>
                  <span className="font-medium">
                    {official.calib.matched_days}/
                    {official.calib.matched_days + official.calib.mismatched_days} 天
                  </span>
                </div>
              </div>
              <div className="text-slate-500">
                {official.calib.mismatched_days === 0
                  ? '成交明细完整，重建可信'
                  : '有对不上的日子，明细可能不完整'}
                ｜官方最大回撤 {pct(official.stats?.max_drawdown)}｜官方基准期末{' '}
                {String(official.stats?.bench_nav_end ?? '-')}（可据此选「基准」下拉）
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            <div className={card}>
              <div className="font-medium" title="从 CSV 的手续费列按买卖成交额反推：佣金（含滑点）+ 印花税">
                费率反推（从它的手续费列）
              </div>
              <div className="flex justify-between"><span className="text-slate-500">买入</span><span>{pct(result?.fees?.rate_buy, 4)}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">卖出</span><span>{pct(result?.fees?.rate_sell, 4)}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">其中印花税(反推)</span><span>{pct(result?.fees?.stamp_tax_est, 4)}</span></div>
            </div>
            <div className={card}>
              <div className="font-medium">初始资金</div>
              <div className="flex justify-between"><span className="text-slate-500">采用</span><span>{Number(result?.capital?.used ?? 0).toLocaleString()}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">首日买入总额</span><span>{Number(result?.capital?.suggest ?? 0).toLocaleString()}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">现金非负下界</span><span>{Number(result?.capital?.min_viable ?? 0).toLocaleString()}</span></div>
              {result?.capital?.first_negative && (
                <div className="text-amber-700">
                  首次现金转负 {(result.capital.first_negative as any).date}（
                  {(result.capital.first_negative as any).side}
                  {(result.capital.first_negative as any).code}）
                </div>
              )}
              {result?.capital?.interval && (
                <div className="text-slate-600">
                  C 真值区间：期末净值{' '}
                  <b>{String((result.capital.interval as any).low?.final_nav ?? '-')}</b> ~{' '}
                  <b>{String((result.capital.interval as any).high?.final_nav ?? '-')}</b>
                </div>
              )}
            </div>
            <div className={card}>
              <div className="font-medium">口径一致性体检</div>
              <div className="flex justify-between">
                <span className="text-slate-500">我们的价 / 它的价（中位）</span>
                <span>{String(replay.diag?.price_ratio?.median ?? '-')}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">比值漂移</span>
                <span>{String(replay.diag?.price_ratio_max_drift ?? '-')}</span>
              </div>
              <div className={Number(replay.diag?.price_ratio_max_drift ?? 0) > 0.05 ? 'text-amber-700' : ''}>
                {String(replay.diag?.price_consistency ?? '')}
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">期末未平仓</span>
                <span>{String(replay.diag?.open_positions_end ?? '-')} 只</span>
              </div>

            </div>
          </div>

          <div className="border rounded-lg p-3">
            <div className="text-sm font-medium mb-1">
              净值对比：A 模拟它的成本 / B 我的成本 / C 它的成交价+手续费 / 基准
              <span className="text-[11px] text-slate-400 ml-2">点击图例可隐藏/显示任意曲线</span>
            </div>
            <ZoomableLineChart
              data={navData}
              keys={navKeys}
              labelOf={navLabel}
              colorOf={navColor}
              dashOf={navDash}
              hidden={hidden}
              onToggle={toggleSeries}
              statKey={navKeys.includes('nav_exact') ? 'nav_exact' : navKeys[0]}
              height={320}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            {Object.entries(replay.stats).map(([k, st]: [string, any]) => (
              <div key={k} className={card}>
                <div className="font-medium" title={NAV_TITLE[k] ?? NAV_FALLBACK_TITLE}>
                  {navLabel(k)}
                </div>
                <div className="flex justify-between"><span className="text-slate-500">期末净值</span><span className="font-medium">{st.final_nav}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">年化</span><span>{pct(st.perf?.annual_return)}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">最大回撤</span><span>{pct(st.perf?.max_drawdown)}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">费用</span><span>{Number(st.fees ?? 0).toLocaleString()}</span></div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
