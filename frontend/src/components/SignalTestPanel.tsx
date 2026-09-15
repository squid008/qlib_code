import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
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

/**
 * 交易信号测试面板（v1.19.38）。
 *
 * 同事的二次平台公式会用 qlib 没有的算子 ⇒ 不逐个加算子，而是**直接给买入信号 CSV**：
 *   ① 信号清单模式（`日期,标的`）⇒ 事件研究（逐 k 曲线 + 有效/有效反向锚点）+ 等权持有回测；
 *   ② 聚宽成交明细模式 ⇒ 只出净值：A 模拟它反推的费率 / B 我设定的 cost / C 精确成交价+手续费 / +基准。
 *
 * 与「单因子测试」的关系：**零耦合**（后端独立包 `app/signals`），但复用同一套判定规则
 * `verdictRules.ts` ⇒ 锚点口径与单因子测试逐位一致，不会出现"两个页面同一信号判定不同"。
 *
 * ⚠ 文件按**字节**（base64）传后端：实测用户 CSV 是 **GBK**，前端若按 UTF-8 读文本会乱码
 *   （代码是 ASCII 还能靠"按位置认列"兜住，但表头会废掉、诊断会误导）。
 */

const FILL_LABEL: Record<string, string> = {
  t1_open: '次日开盘价',
  t_close: '信号日收盘价',
  t1_close: '次日收盘价',
}

const ALLOC_LABEL: Record<string, string> = {
  event_even: '事件驱动再平衡（有信号/到期就调平）',
  cash_even: '现金等分（不主动再平衡）',
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

/** 净值列名 → 中文标签 / 颜色（两条曲线图共用，避免两处口径漂移）。 */
function navLabel(k: string): string {
  return NAV_LABEL[k] ?? allocLabel(k)
}
function navColor(k: string, i: number): string {
  return NAV_COLOR[k] ?? ['#0ea5e9', '#f59e0b', '#10b981', '#ef4444'][i % 4]
}
function navDash(k: string): string | undefined {
  if (k === 'nav_exact_min' || k === 'nav_official_bench') return '5 3'
  if (k === 'benchmark') return '4 3'
  return undefined
}

/** 资金方案列名 → 中文标签（`event_even_band5` 这类"同方案不同死区"的对比档也认）。 */
function allocLabel(mode: string): string {
  if (ALLOC_LABEL[mode]) return ALLOC_LABEL[mode]
  const m = /^event_even_band(.+)$/.exec(mode)
  if (m) return `事件等权 · 死区 ${m[1].replace('p', '.')}%（自动对比档）`
  if (mode === 'nav_exact_min') return 'C′ 现金非负下界口径'
  return mode
}

/** 事件研究逐 k 行（图表与表格共用；锚点由 `verdictRules` 判定，与单因子测试同源）。 */
interface EventRow {
  k: number
  n: number
  mean: number | null
  median: number | null
  win: number | null
  excess: number | null
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

const pct = (v: number | null | undefined, digits = 2): string =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(digits)}%`

function anchorDot(color: string, revColor: string) {
  return (props: any) => {
    const { cx, cy, payload, index } = props
    if (cx == null || cy == null) return <g key={`e${index}`} />
    if (payload?.valid) {
      return <circle key={`v${index}`} cx={cx} cy={cy} r={5} fill={color} stroke="#fff" strokeWidth={1} />
    }
    if (payload?.validRev) {
      return <circle key={`r${index}`} cx={cx} cy={cy} r={5} fill={revColor} stroke="#fff" strokeWidth={1} />
    }
    return <circle key={`n${index}`} cx={cx} cy={cy} r={1.6} fill="#94a3b8" />
  }
}

interface Props {
  defaultCapital?: number
  defaultBenchmark?: string
}

export default function SignalTestPanel({ defaultCapital = 1e9, defaultBenchmark = 'SH000300' }: Props) {
  const [opts, setOpts] = useState<SignalTestOptions | null>(null)
  const [fileName, setFileName] = useState('')
  const [b64, setB64] = useState('')
  // （可选）聚宽《收益概述》：把官方净值叠加上图做校准
  const [perfName, setPerfName] = useState('')
  const [perfB64, setPerfB64] = useState('')
  const [parsed, setParsed] = useState<SignalTestParseResult | null>(null)
  const [parsing, setParsing] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<SignalTestRunResult | null>(null)
  const [error, setError] = useState('')
  const [showIssues, setShowIssues] = useState(false)

  // 参数
  const [horizon, setHorizon] = useState(60)
  const [fill, setFill] = useState('t1_open')
  const [cost, setCost] = useState(0.004)
  const [capital, setCapital] = useState(defaultCapital)
  const [pool, setPool] = useState('all')
  const [benchmark, setBenchmark] = useState(defaultBenchmark)
  const [strictLimit, setStrictLimit] = useState(true)
  const [rebalBand, setRebalBand] = useState(0)
  const [jqCapital, setJqCapital] = useState<number | ''>('')
  const [navFocus, setNavFocus] = useState<string>('')

  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    getSignalTestOptions()
      .then(setOpts)
      .catch(() => setOpts(null))
  }, [])

  const onPickFile = useCallback(async (f: File) => {
    setFileName(f.name)
    setResult(null)
    setParsed(null)
    setError('')
    try {
      const buf = await f.arrayBuffer()
      const b = bufToBase64(buf)
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

  const run = useCallback(async () => {
    if (!b64) {
      setError('请先选择 CSV 文件')
      return
    }
    setRunning(true)
    setError('')
    setResult(null)
    try {
      const req: SignalTestRequest = {
        content_b64: b64,
        filename: fileName,
        perf_b64: perfB64 || undefined,
        cost,
        benchmark,
        horizon,
        fill,
        capital,
        strict_limit: strictLimit,
        rebal_band: rebalBand,
        pool,
        jq_capital: typeof jqCapital === 'number' ? jqCapital : undefined,
      }
      const r = await runSignalTest(req)
      setResult(r)
      const cols = r.backtest?.nav_columns ?? []
      setNavFocus(r.backtest?.alloc_default ?? cols[2] ?? '')
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? (e instanceof Error ? e.message : String(e)))
    } finally {
      setRunning(false)
    }
  }, [b64, fileName, perfB64, cost, benchmark, horizon, fill, capital, strictLimit, rebalBand, pool, jqCapital])

  // ---------------- 事件研究：逐 k 表 + 锚点 ----------------
  const eventRows = useMemo<EventRow[]>(() => {
    const es = result?.event
    if (!es) return []
    const base = es.baseline ?? null
    const rows: EventRow[] = (es.curve ?? []).map((c: any) => {
      const k = c.k as number
      const ex: number | null = base?.excess?.[k - 1] ?? null
      const t: number | null = base?.t_hac?.[k - 1] ?? null
      const dWin: number | null = base?.win?.[k - 1] ?? null
      const stable = pairStabilityOf({ t, win: dWin })
      const stableRev = pairStabilityOf({ t, win: dWin }, true)
      const valid = isValidAt(k, c.median, c.win, ex, stable.ok)
      const validRev = isReverseValidAt(k, c.median, c.win, ex, stableRev.ok)
      return {
        k,
        n: c.n,
        mean: c.mean,
        median: c.median,
        win: c.win,
        excess: ex,
        t,
        dWin,
        valid,
        validRev,
      }
    })
    return rows
  }, [result])

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

  return (
    <section className="border rounded-lg p-4 space-y-4 bg-white dark:bg-slate-900">
      <header className="flex flex-wrap items-center gap-3">
        <h3 className="font-semibold">交易信号测试</h3>
        <span className="text-xs text-slate-500">
          外部买入信号 CSV（同事的「日期,标的」清单 / 聚宽成交明细）⇒ 事件研究 + 等权持有回测
        </span>
        {result && (
          <span className="text-xs text-emerald-600">
            总耗时 {(result.elapsed ?? 0).toFixed(2)}s
            {result.timings &&
              `（解析 ${result.timings.parse}s / 取价 ${result.timings.prices ?? '-'}s / 事件 ${
                result.timings.event ?? '-'
              }s / 回测 ${result.timings.backtest ?? result.timings.replay ?? '-'}s）`}
          </span>
        )}
      </header>

      {/* 文件选择 */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col">
          <span className="text-sm text-slate-500">信号文件（CSV，GBK/UTF-8 都可以）</span>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.txt"
            className="mt-1 text-sm"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void onPickFile(f)
            }}
          />
        </label>
        {fileName && (
          <span className="text-xs text-slate-500">
            {fileName}
            {parsed && (
              <>
                ｜格式{' '}
                <b>
                  {parsed.mode === 'jq_trades'
                    ? '聚宽成交明细'
                    : parsed.mode === 'jq_perf'
                      ? '聚宽收益概述'
                      : '信号清单'}
                </b>
                ｜编码 {parsed.encoding}
                {parseStats.had_header === false && '（无表头，按位置识别）'}
              </>
            )}
          </span>
        )}
        <label className="flex flex-col">
          <span className="text-sm text-slate-500">（可选）聚宽《收益概述》result_1.csv</span>
          <input
            type="file"
            accept=".csv,.txt"
            className="mt-1 text-sm"
            onChange={async (e) => {
              const f = e.target.files?.[0]
              if (!f) return
              setPerfName(f.name)
              try {
                setPerfB64(bufToBase64(await f.arrayBuffer()))
              } catch {
                setPerfB64('')
              }
            }}
          />
          <span className="text-[10px] text-slate-400 mt-1">
            有它就把**官方净值**叠加上图做校准（逐日买卖金额也会与成交明细对账）
          </span>
        </label>
        {perfName && <span className="text-xs text-slate-500">已附：{perfName}</span>}
        {parsing && <span className="text-xs text-sky-600">解析中…</span>}
      </div>

      {/* 参数 */}
      <div className="flex flex-wrap items-end gap-3">
        {parsed?.mode !== 'jq_trades' && (
          <>
            <label className="flex flex-col">
              <span className="text-sm text-slate-500">预测周期 N（天）</span>
              <input
                type="number"
                min={1}
                max={250}
                className="mt-1 w-24 border rounded px-2 py-1"
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
              />
              <span className="text-[10px] text-slate-400 mt-1">事件研究 1..N + 持有 N 个交易日</span>
            </label>
            <label className="flex flex-col">
              <span className="text-sm text-slate-500">成交时点</span>
              <select
                className="mt-1 border rounded px-2 py-1"
                value={fill}
                onChange={(e) => setFill(e.target.value)}
              >
                {(opts?.fills ?? Object.keys(FILL_LABEL).map((k) => ({ key: k, name: FILL_LABEL[k] }))).map(
                  (f) => (
                    <option key={f.key} value={f.key}>
                      {f.name}
                    </option>
                  ),
                )}
              </select>
            </label>
            <label className="flex flex-col">
              <span className="text-sm text-slate-500">基准池（未触发组）</span>
              <select
                className="mt-1 border rounded px-2 py-1"
                value={pool}
                onChange={(e) => setPool(e.target.value)}
              >
                {(opts?.pools ?? [{ key: 'all', name: '全A' }]).map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col">
              <span className="text-sm text-slate-500">初始资金</span>
              <input
                type="number"
                className="mt-1 w-32 border rounded px-2 py-1"
                value={capital}
                onChange={(e) => setCapital(Number(e.target.value))}
              />
              <span className="text-[10px] text-slate-400 mt-1">默认 10 亿：1000 只等权也买得起</span>
            </label>
            <label className="flex flex-col">
              <span className="text-sm text-slate-500">再平衡死区</span>
              <input
                type="number"
                step={0.01}
                min={0}
                max={0.5}
                className="mt-1 w-24 border rounded px-2 py-1"
                value={rebalBand}
                onChange={(e) => setRebalBand(Number(e.target.value))}
              />
              <span className="text-[10px] text-slate-400 mt-1">0=每次事件完全调平（费用高）</span>
            </label>
          </>
        )}
        <label className="flex flex-col">
          <span className="text-sm text-slate-500">成本（往返合计）</span>
          <input
            type="number"
            step={0.0005}
            min={0}
            className="mt-1 w-24 border rounded px-2 py-1"
            value={cost}
            onChange={(e) => setCost(Number(e.target.value))}
          />
          <span className="text-[10px] text-slate-400 mt-1">默认 0.004（与连续信号同义）</span>
        </label>
        <label className="flex flex-col">
          <span className="text-sm text-slate-500">基准</span>
          <select
            className="mt-1 border rounded px-2 py-1"
            value={benchmark}
            onChange={(e) => setBenchmark(e.target.value)}
          >
            {(opts?.benchmarks ?? [{ code: 'SH000300', name: '沪深300' }]).map((b) => (
              <option key={b.code} value={b.code}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        {parsed?.mode !== 'jq_trades' && (
          <label className="flex items-center gap-1 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={strictLimit}
              onChange={(e) => setStrictLimit(e.target.checked)}
            />
            严格涨跌停/停牌（买不进=放弃，卖不出=顺延）
          </label>
        )}
        {parsed?.mode === 'jq_trades' && (
          <label className="flex flex-col">
            <span className="text-sm text-slate-500">聚宽初始资金</span>
            <input
              type="number"
              className="mt-1 w-32 border rounded px-2 py-1"
              value={jqCapital}
              onChange={(e) => setJqCapital(e.target.value === '' ? '' : Number(e.target.value))}
            />
            <span className="text-[10px] text-slate-400 mt-1">
              默认=首日买入总额；偏小会让 C 曲线等效加杠杆
            </span>
          </label>
        )}
        <button
          type="button"
          onClick={() => void run()}
          disabled={!b64 || running}
          className="px-4 py-1.5 rounded text-sm bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-40"
        >
          {running ? '跑测试中…' : '开始测试'}
        </button>
      </div>

      {error && <div className="text-sm text-red-600">{error}</div>}

      {/* 解析诊断 */}
      {parsed && (
        <div className="border rounded p-3 text-xs space-y-1 bg-slate-50 dark:bg-slate-800">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(parseStats)
              .filter(([k]) => !['warnings', 'filename'].includes(k))
              .map(([k, v]) => (
                <span key={k} className="text-slate-600 dark:text-slate-300">
                  <span className="text-slate-400">{k}:</span>{' '}
                  {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                </span>
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
              <button
                type="button"
                className="text-sky-600 underline"
                onClick={() => setShowIssues((s) => !s)}
              >
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
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      ) : null}

      {/* ---------------- 模式①：信号清单 ---------------- */}
      {bt && (
        <div className="space-y-4">
          <div className="text-xs text-slate-500">
            信号 {String(result?.signal?.n_buy ?? '-')} 条（买入）｜{String(result?.signal?.n_stocks ?? '-')} 只
            ｜区间 {String((result?.signal?.span as any)?.start)} ~ {String((result?.signal?.span as any)?.end)}
            ｜成交时点 <b>{FILL_LABEL[bt.fill] ?? bt.fill}</b>｜成本 {bt.cost}｜资金{' '}
            {(bt.capital ?? 0).toExponential(2)}
          </div>

          {/* 事件研究：逐 k 曲线（紫=有效，红=有效反向） */}
          {eventRows.length > 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div>
                <div className="text-sm font-medium mb-1">
                  逐 k 曲线：触发组中位数（紫点=有效，红点=有效反向）
                </div>
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={eventRows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="k" tick={{ fontSize: 11 }} />
                    <YAxis tickFormatter={(v) => `${(v * 100).toFixed(1)}%`} tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(v: any) => pct(Number(v))} labelFormatter={(k) => `k=${k}`} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="median"
                      name="触发组中位"
                      stroke="#2563eb"
                      dot={anchorDot('#7c3aed', '#dc2626')}
                      strokeWidth={2}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div>
                <div className="text-sm font-medium mb-1">逐 k 曲线：日配对超额（触发 − 未触发）</div>
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={eventRows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="k" tick={{ fontSize: 11 }} />
                    <YAxis tickFormatter={(v) => `${(v * 100).toFixed(1)}%`} tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(v: any) => pct(Number(v))} labelFormatter={(k) => `k=${k}`} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="excess"
                      name="日配对超额"
                      stroke="#0891b2"
                      dot={anchorDot('#7c3aed', '#dc2626')}
                      strokeWidth={2}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {eventRows.length > 0 && (
            <div className="overflow-auto max-h-72 border rounded">
              <table className="min-w-full text-xs">
                <thead className="bg-slate-100 dark:bg-slate-800 sticky top-0">
                  <tr>
                    {['k', 'n', '均值', '中位', '胜率', '超额(日配对)', 'HAC t', '日胜率', '结论'].map((h) => (
                      <th key={h} className="px-2 py-1 text-left font-medium">
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
                        <td className="px-2 py-0.5">{pct(r.excess)}</td>
                        <td className="px-2 py-0.5">{r.t == null ? '-' : r.t.toFixed(2)}</td>
                        <td className="px-2 py-0.5">{pct(r.dWin, 1)}</td>
                        <td className="px-2 py-0.5">
                          {r.valid && <span className="text-violet-700 font-medium">有效✓</span>}
                          {r.validRev && <span className="text-red-600 font-medium">有效(反向)✓</span>}
                          {!r.valid && !r.validRev && <span className="text-slate-400">-</span>}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 净值 + 基准 */}
          <div>
            <div className="flex flex-wrap items-center gap-3 mb-1">
              <span className="text-sm font-medium">净值曲线（两种资金方案 + 基准）</span>
              <select
                className="text-xs border rounded px-2 py-1"
                value={navFocus}
                onChange={(e) => setNavFocus(e.target.value)}
              >
                {navKeys.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={navData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                <Tooltip formatter={(v: any) => (v == null ? '-' : Number(v).toFixed(4))} />
                <Legend />
                {navKeys.map((k, i) => (
                  <Line
                    key={k}
                    type="monotone"
                    dataKey={k}
                    name={navLabel(k)}
                    stroke={navColor(k, i)}
                    strokeWidth={k === navFocus ? 2.4 : 1.3}
                    strokeDasharray={navDash(k)}
                    dot={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* 统计 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(bt.stats).map(([mode, st]: [string, any]) => (
              <div key={mode} className="border rounded p-3 text-xs space-y-1">
                <div className="font-medium">
                  {allocLabel(mode)}
                  {st.rebal_band ? `（死区 ${(st.rebal_band * 100).toFixed(1)}%）` : ''}
                  {mode === bt.alloc_default && <span className="text-emerald-600"> · 默认</span>}
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                  <span>期末净值</span>
                  <b>{st.final_nav}</b>
                  <span>总收益</span>
                  <b>{pct(st.total_return)}</b>
                  <span>年化 / 最大回撤</span>
                  <b>
                    {pct(st.perf?.annual_return)} / {pct(st.perf?.max_drawdown)}
                  </b>
                  <span>成交</span>
                  <b>
                    {st.trades} 笔（买 {st.buys}/卖 {st.sells}）
                  </b>
                  <span>成本合计</span>
                  <b>
                    {(st.fees_paid / 1e4).toFixed(0)} 万（本金 {pct(st.fee_ratio_of_capital)}）
                  </b>
                  <span>刷新持有 / 卖出顺延</span>
                  <b>
                    {st.hold_refreshes} / {st.sell_deferrals}
                  </b>
                  <span>被拒（涨停/停牌/资金）</span>
                  <b>
                    {st.rejects_limit_up} / {st.rejects_suspended} / {st.rejects_no_cash}
                  </b>
                  <span>平均持有 / 已实现胜率</span>
                  <b>
                    {st.avg_hold_days} 日 / {pct(st.win_rate, 1)}
                  </b>
                  <span>期末持仓 / 现金最低</span>
                  <b>
                    {st.open_positions_end} 只 / {Number(st.min_cash).toFixed(0)}
                  </b>
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
                {bt.rejects.slice(0, 200).map((r, i) => (
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

      {/* ---------------- 模式③：只上传《收益概述》⇒ 只看官方净值 ---------------- */}
      {result?.mode === 'jq_perf' && (
        <div className="space-y-3">
          <div className="text-xs text-slate-500">
            只上传了《收益概述》⇒ 仅展示**官方净值**（含官方基准），不做重建。想校准重建净值请同时上传成交明细。
          </div>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={navData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
              <Tooltip formatter={(v: any) => (v == null ? '-' : Number(v).toFixed(4))} />
              <Legend />
              {navKeys.map((k, i) => (
                <Line
                  key={k}
                  type="monotone"
                  dataKey={k}
                  name={navLabel(k)}
                  stroke={navColor(k, i)}
                  strokeWidth={2}
                  strokeDasharray={navDash(k)}
                  dot={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            {[
              ['期末净值', String((result.official as any)?.stats?.nav_end ?? '-')],
              ['总收益', pct((result.official as any)?.stats?.total_return)],
              ['最大回撤', pct((result.official as any)?.stats?.max_drawdown)],
              ['官方基准期末', String((result.official as any)?.stats?.bench_nav_end ?? '-')],
              ['超额收益(%)', String((result.official as any)?.stats?.excess_end_pct ?? '-')],
              ['区间', `${(result.official as any)?.stats?.date_min ?? '-'} ~ ${(result.official as any)?.stats?.date_max ?? '-'}`],
              ['买入合计', Number((result.official as any)?.stats?.buy_total ?? 0).toLocaleString()],
              ['卖出合计', Number((result.official as any)?.stats?.sell_total ?? 0).toLocaleString()],
            ].map(([k, v]) => (
              <div key={k} className="border rounded p-2">
                <div className="text-slate-500">{k}</div>
                <div className="font-medium">{v}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---------------- 模式②：聚宽成交明细 ---------------- */}
      {replay && result?.mode === 'jq_trades' && (
        <div className="space-y-4">
          {/* 官方《收益概述》校准（可选上传）：官方净值 vs 我方重建 + 逐日金额对账 */}
          {result?.official && (result.official as any).calib && (
            <div className="border rounded p-3 text-xs space-y-1 bg-emerald-50 dark:bg-emerald-950/30">
              <div className="font-medium">
                官方《收益概述》校准（截至 {(result.official as any).calib.as_of ?? '-'}，两条曲线同一天比较）
              </div>
              <div>
                官方期末净值 <b>{String((result.official as any).calib.official_final ?? '-')}</b>｜
                我方 C（成交价+实际手续费）<b>{String((result.official as any).calib.rebuilt_final)}</b>｜
                偏差 <b>{pct((result.official as any).calib.diff_final, 3)}</b>｜最大偏差{' '}
                {String((result.official as any).calib.max_abs_diff ?? '-')}
              </div>
              <div>
                逐日买卖金额一致{' '}
                <b>
                  {(result.official as any).calib.matched_days}/
                  {(result.official as any).calib.matched_days +
                    (result.official as any).calib.mismatched_days}
                </b>{' '}
                天 ⇒{' '}
                {(result.official as any).calib.mismatched_days === 0
                  ? '成交明细完整 ✓ 重建可信'
                  : '⚠ 有对不上的日子，明细可能不完整'}
              </div>
              <div>
                官方最大回撤 {pct((result.official as any).stats?.max_drawdown)}｜官方基准期末{' '}
                {String((result.official as any).stats?.bench_nav_end ?? '-')}（可作为"基准"下拉的参考选择）
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            <div className="border rounded p-3 space-y-1">
              <div className="font-medium">费率反推（从它的手续费列）</div>
              <div>买入 {pct(result?.fees?.rate_buy, 4)}</div>
              <div>卖出 {pct(result?.fees?.rate_sell, 4)}</div>
              <div>其中印花税 ≈ {pct(result?.fees?.stamp_tax_est, 4)}</div>
            </div>
            <div className="border rounded p-3 space-y-1">
              <div className="font-medium">初始资金</div>
              <div>采用 {Number(result?.capital?.used ?? 0).toLocaleString()} 元</div>
              <div>首日买入总额 {Number(result?.capital?.suggest ?? 0).toLocaleString()}</div>
              <div>现金非负下界 {Number(result?.capital?.min_viable ?? 0).toLocaleString()}</div>
              {result?.capital?.first_negative && (
                <div className="text-amber-700">
                  首次现金转负 {(result.capital.first_negative as any).date}（
                  {(result.capital.first_negative as any).side}
                  {(result.capital.first_negative as any).code}）
                  ⇒ 流水疑缺现金流
                </div>
              )}
              {result?.capital?.interval && (
                <div className="text-slate-600">
                  C 真值区间：期末净值{' '}
                  <b>{String((result.capital.interval as any).low?.final_nav ?? '-')}</b> ~{' '}
                  <b>{String((result.capital.interval as any).high?.final_nav ?? '-')}</b>
                  （下界 ~ 本金口径）
                </div>
              )}
            </div>
            <div className="border rounded p-3 space-y-1">
              <div className="font-medium">口径一致性体检</div>
              <div>
                我们的价/它的价 中位 {String(replay.diag?.price_ratio?.median ?? '-')}（漂移{' '}
                {String(replay.diag?.price_ratio_max_drift ?? '-')}）
              </div>
              <div className={Number(replay.diag?.price_ratio_max_drift ?? 0) > 0.05 ? 'text-amber-700' : ''}>
                {String(replay.diag?.price_consistency ?? '')}
              </div>
              <div>期末未平仓 {String(replay.diag?.open_positions_end ?? '-')} 只</div>
            </div>
          </div>

          {Object.values(replay.diag?.negative_cash_bars ?? {}).some((v: any) => Number(v) > 0) && (
            <div className="text-xs text-amber-700">
              ⚠ 有 K 线现金为负 ⇒ 资金假设偏小（等效加杠杆、收益被高估）：
              {Object.entries(replay.diag?.negative_cash_bars ?? {}).map(([k, v]) => (
                <span key={k} className="mr-3">
                  {k}: {String(v)} 根
                </span>
              ))}
            </div>
          )}

          <div>
            <div className="text-sm font-medium mb-1">
              净值对比：A 模拟它的成本 / B 我的成本 / C 它的成交价+手续费（精确）/ 基准
            </div>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={navData} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                <Tooltip formatter={(v: any) => (v == null ? '-' : Number(v).toFixed(4))} />
                <Legend />
                {navKeys.map((k, i) => (
                  <Line
                    key={k}
                    type="monotone"
                    dataKey={k}
                    name={navLabel(k)}
                    stroke={navColor(k, i)}
                    strokeWidth={k === 'benchmark' || k === 'nav_official_bench' ? 1.4 : 2}
                    strokeDasharray={navDash(k)}
                    dot={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            {Object.entries(replay.stats).map(([k, st]: [string, any]) => (
              <div key={k} className="border rounded p-3 space-y-1">
                <div className="font-medium">
                  {k === 'nav_sim_fee'
                    ? 'A 模拟它的成本'
                    : k === 'nav_my_cost'
                      ? 'B 我的成本（cost）'
                      : 'C 精确（成交价+手续费）'}
                </div>
                <div>期末净值 {st.final_nav}</div>
                <div>年化 {pct(st.perf?.annual_return)}｜回撤 {pct(st.perf?.max_drawdown)}</div>
                <div>费用 {Number(st.fees ?? 0).toLocaleString()} 元</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
