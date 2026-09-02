import { useEffect, useRef, useState } from 'react'
import {
  createSingleFactorTest,
  getSingleFactorTestProgress,
  cancelSingleFactorTest,
  getSingleFactorTestTasks,
  getFactorCatalog,
} from '../api'
import type { CustomFormula, SingleFactorTestResult } from '../api'
import type { FactorCatalog, FactorField } from '../types'

interface SingleFactorTestPanelProps {
  customFormulas: CustomFormula[]
  defaultUniverse: string
  defaultStartDate: string
  defaultEndDate: string
  defaultLabelHorizon: number
  // 任务提交/结束/取消时触发，让 App 立即刷新"并发: x/y"显示（即时 +1/-1，不等 3 秒轮询）
  onCapacityChange?: () => void
}

type TestResult = SingleFactorTestResult

interface SourceGroup {
  source: 'custom' | 'alpha158' | 'alpha360'
  label: string
  items: { key: string; id: string; name: string; expression: string }[]
  loaded?: boolean
}

// 单因子测试面板：勾选自定义公式 / Alpha158 / Alpha360 的因子，逐个快速诊断
// （覆盖率 / 是否0-1信号 / 触发vs未触发收益对比 / IC / RankIC / ICIR）
export default function SingleFactorTestPanel({
  customFormulas,
  defaultUniverse,
  defaultStartDate,
  defaultEndDate,
  defaultLabelHorizon,
  onCapacityChange,
}: SingleFactorTestPanelProps) {
  const [universe, setUniverse] = useState(defaultUniverse)
  const [startDate, setStartDate] = useState(defaultStartDate)
  const [endDate, setEndDate] = useState(defaultEndDate)
  const [labelHorizon, setLabelHorizon] = useState<number>(
    Number.isFinite(defaultLabelHorizon) ? defaultLabelHorizon : 2,
  )
  // 复权方式：none/forward/backward（与回测一致，默认前复权；前/后复权在比率类因子与收益率上数学等价）
  const [priceAdjust, setPriceAdjust] = useState('forward')
  // 触发组剔除开关：信号日(T)涨停 / 成交日(T+1)涨停 / 成交日停牌（默认全开，保持原行为 + 新增成交日口径）
  const [excludeLimitUpSignal, setExcludeLimitUpSignal] = useState(true)
  const [excludeLimitUpTrade, setExcludeLimitUpTrade] = useState(true)
  const [excludeSuspended, setExcludeSuspended] = useState(true)

  // 三个因子来源
  const [groups, setGroups] = useState<SourceGroup[]>([
    {
      source: 'custom',
      label: '自定义公式',
      items: customFormulas.map((f) => ({
        key: `custom:${f.id}`,
        id: f.id,
        name: f.name,
        expression: f.expression,
      })),
    },
    { source: 'alpha158', label: 'Alpha158', items: [] },
    { source: 'alpha360', label: 'Alpha360', items: [] },
  ])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [progressMsg, setProgressMsg] = useState('')
  const [results, setResults] = useState<TestResult[]>([])
  const [error, setError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const taskIdRef = useRef<string | null>(null)
  const pollTimerRef = useRef<number | null>(null)

  // 组件卸载时停止轮询
  useEffect(
    () => () => {
      if (pollTimerRef.current !== null) window.clearInterval(pollTimerRef.current)
    },
    [],
  )

  // 刷新页面后：若后台仍有 running 的单因子测试任务（上次取消未成功/未完成），
  // 恢复显示进度并继续轮询，避免任务"凭空消失"。
  useEffect(() => {
    let disposed = false
    ;(async () => {
      try {
        const res = await getSingleFactorTestTasks()
        const runningTask = res.tasks?.find((t) => t.status === 'running')
        if (!disposed && runningTask) {
          // 若上次已请求取消（cancel_requested），恢复后仍显示"取消中..."并禁用按钮，
          // 避免误以为还能再次取消；轮询拿到 cancelled 后自动结束。
          const cancelling = runningTask.cancel_requested
          setRunning(true)
          setCancelling(cancelling)
          setProgress(runningTask.progress ?? 1)
          setProgressMsg(cancelling ? '正在取消...' : runningTask.message || '恢复任务...')
          startPolling(runningTask.task_id)
        }
      } catch {
        // 后端暂不支持该接口时静默，不影响正常使用
      }
    })()
    return () => {
      disposed = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 自定义公式变化时同步
  useEffect(() => {
    setGroups((prev) =>
      prev.map((g) =>
        g.source === 'custom'
          ? {
              ...g,
              items: customFormulas.map((f) => ({
                key: `custom:${f.id}`,
                id: f.id,
                name: f.name,
                expression: f.expression,
              })),
            }
          : g,
      ),
    )
  }, [customFormulas])

  // 按需加载 Alpha158 / Alpha360 目录
  const loadCatalog = async (source: 'alpha158' | 'alpha360') => {
    if (groups.find((g) => g.source === source)?.loaded) return
    try {
      const c: FactorCatalog = await getFactorCatalog(source === 'alpha158' ? 'Alpha158' : 'Alpha360')
      setGroups((prev) =>
        prev.map((g) =>
          g.source === source
            ? {
                ...g,
                loaded: true,
                items: c.flat.map((f: FactorField) => ({
                  key: `${source}:${f.name}`,
                  id: f.name,
                  name: f.name,
                  expression: f.expression,
                })),
              }
            : g,
        ),
      )
    } catch {
      setError(`加载 ${source === 'alpha158' ? 'Alpha158' : 'Alpha360'} 目录失败`)
    }
  }

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggleAll = (g: SourceGroup, select: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const it of g.items) {
        if (select) next.add(it.key)
        else next.delete(it.key)
      }
      return next
    })
  }

  // 停止轮询并清空任务引用
  const stopPolling = () => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
    taskIdRef.current = null
  }

  // 开始轮询任务进度：每 700ms 刷新一次，直到成功/失败/取消
  const startPolling = (taskId: string) => {
    stopPolling()
    taskIdRef.current = taskId
    pollTimerRef.current = window.setInterval(async () => {
      try {
        const p = await getSingleFactorTestProgress(taskId)
        setProgress(p.progress)
        setProgressMsg(p.message)
        if (p.status === 'success') {
          stopPolling()
          setResults(p.result?.items ?? [])
          setRunning(false)
          setCancelling(false)
          // 任务结束，立即刷新并发显示（占用名额 -1）
          onCapacityChange?.()
        } else if (p.status === 'failed') {
          stopPolling()
          setError(`单因子测试失败：${p.error ?? p.message}`)
          setRunning(false)
          setCancelling(false)
          onCapacityChange?.()
        } else if (p.status === 'cancelled') {
          stopPolling()
          setProgressMsg('已取消')
          setRunning(false)
          setCancelling(false)
          onCapacityChange?.()
        }
      } catch (e: unknown) {
        // 轮询瞬间失败（网络抖动）不中断，下一次轮询继续
        const msg = e instanceof Error ? e.message : String(e)
        if (msg.includes('404')) {
          stopPolling()
          setError('单因子测试任务不存在或已过期')
          setRunning(false)
          setCancelling(false)
          onCapacityChange?.()
        }
      }
    }, 700)
  }

  const run = async () => {
    if (!startDate || !endDate) {
      setError('请填写测试区间')
      return
    }
    if (endDate < startDate) {
      setError('结束日期不能早于开始日期')
      return
    }
    const factors = groups.flatMap((g) =>
      g.items
        .filter((it) => selected.has(it.key))
        .map((it) => ({ id: it.id, name: it.name, expression: it.expression, source: g.source })),
    )
    if (factors.length === 0) {
      setError('请至少勾选一个因子')
      return
    }
    setRunning(true)
    setCancelling(false)
    setError('')
    setResults([])
    setProgress(1)
    setProgressMsg('提交任务...')
    try {
      const resp = await createSingleFactorTest({
        universe,
        start_date: startDate,
        end_date: endDate,
        label_horizon: labelHorizon,
        factors,
        exclude_limit_up_signal: excludeLimitUpSignal,
        exclude_limit_up_trade: excludeLimitUpTrade,
        exclude_suspended: excludeSuspended,
        price_adjust: priceAdjust,
      })
      const task_id = resp?.task_id
      if (!task_id) {
        // 后端仍是旧版（同步返回 items/total），无法轮询进度
        setError('后端接口未返回任务ID（后端版本过旧），请重启后端服务（start_backend.bat）后重试')
        setRunning(false)
        return
      }
      startPolling(task_id)
      // 立即刷新并发显示（留 200ms 给后端线程登记占用/排队，通常毫秒级完成）
      window.setTimeout(() => onCapacityChange?.(), 200)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(`单因子测试失败：${msg}`)
      setRunning(false)
      setCancelling(false)
    }
  }

  const cancel = async () => {
    const taskId = taskIdRef.current
    if (!taskId) return
    setCancelling(true)
    setProgressMsg('正在取消...')
    try {
      const r = await cancelSingleFactorTest(taskId)
      if (!r.ok) {
        // 任务已结束，轮询会拿到最终状态
        setProgressMsg(r.message || '任务已结束')
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(`取消失败：${msg}`)
      setCancelling(false)
    }
  }

  // 百分比口径：×100 显示（覆盖率 / 收益 / 差值）
  const fmt = (v: number | null | undefined, digits = 4, suffix = '') =>
    v === null || v === undefined || Number.isNaN(v) ? '-' : `${(v * 100).toFixed(digits)}${suffix}`
  // 原始小数口径：不加%，直接显示原始值（IC / RankIC / ICIR）
  const fmtRaw = (v: number | null | undefined, digits = 4) =>
    v === null || v === undefined || Number.isNaN(v) ? '-' : v.toFixed(digits)
  // p 值：极小值（<0.001）用科学计数法显示真实量级，toFixed(4) 会退化成 0.0000 失去信息
  const fmtP = (v: number | null | undefined) =>
    v === null || v === undefined || Number.isNaN(v)
      ? '-'
      : v < 1e-300
        ? '<1e-300*' // 双精度浮点下溢为 0，实际 p 极小（约 1e-300 以下）
        : v < 0.001
          ? `${v.toExponential(2)}${v < 0.05 ? '*' : ''}`
          : `${v.toFixed(4)}${v < 0.05 ? '*' : ''}`

  return (
    <div className="mt-2 border rounded p-3 bg-slate-50 dark:bg-slate-900 text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-slate-600 dark:text-slate-300">单因子测试</span>
        <span className="text-slate-400">不训练模型，快速诊断因子预测力：稀疏 0/1 信号看"触发 vs 未触发"收益，连续因子看 IC</span>
      </div>

      {/* 参数行：5 参数 flex-1 铺开 + 开始测试按钮同一行。
          在面板 p-3 内边距内布局（与下方"剔除开关"文字对齐），按钮右边缘留出相同边距。 */}
      <div className="flex flex-wrap items-end gap-3 mb-3">
        <label className="flex-1 flex flex-col min-w-[90px]">
          <span className="text-slate-500 mb-1">股票池</span>
          <select className="border rounded px-2 py-1" value={universe} onChange={(e) => setUniverse(e.target.value)}>
            <option value="csi300">沪深300</option>
            <option value="csi500">中证500</option>
            <option value="csi800">中证800</option>
            <option value="csi1000">中证1000</option>
            <option value="all">全部A股</option>
          </select>
        </label>
        <label className="flex-1 flex flex-col min-w-[110px]">
          <span className="text-slate-500 mb-1">开始日期</span>
          <input type="date" className="border rounded px-2 py-1" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="flex-1 flex flex-col min-w-[110px]">
          <span className="text-slate-500 mb-1">结束日期</span>
          <input type="date" className="border rounded px-2 py-1" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <label className="flex-1 flex flex-col min-w-[90px]">
          <span className="text-slate-500 mb-1">预测周期(天)</span>
          <input
            type="number"
            min={1}
            className="border rounded px-2 py-1"
            value={Number.isFinite(labelHorizon) ? labelHorizon : 2}
            onChange={(e) => setLabelHorizon(Number(e.target.value))}
          />
        </label>
        <label className="flex-1 flex flex-col min-w-[90px]" title="前复权与后复权在比率类因子/收益率上数学等价（仅价格绝对值不同）">
          <span className="text-slate-500 mb-1">复权方式</span>
          <select
            className="border rounded px-2 py-1"
            value={priceAdjust}
            onChange={(e) => setPriceAdjust(e.target.value)}
          >
            <option value="none">不复权</option>
            <option value="forward">前复权</option>
            <option value="backward">后复权</option>
          </select>
        </label>
        <div className="flex items-end md:w-[calc((100%-3rem)/4)]">
          {running ? (
            <button
              type="button"
              onClick={cancel}
              disabled={cancelling}
              className="w-full px-3 py-1.5 rounded bg-red-600 text-white text-xs disabled:opacity-50"
            >
              {cancelling ? '取消中...' : `取消 (${Math.round(progress)}%)`}
            </button>
          ) : (
            <button
              type="button"
              onClick={run}
              className="w-full px-3 py-1.5 rounded bg-blue-600 text-white text-xs"
            >
              开始测试
            </button>
          )}
        </div>
      </div>

      {/* 触发组剔除开关行：独立 flex（不参与上面 grid，避免撑高参数行——见 md/两地 git 工作流.md 布局备忘） */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-3">
        <span className="text-slate-500">剔除开关：</span>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="信号日(T)收盘后已知涨停，属选股过滤（涨停后追高风险），无前视"
        >
          <input
            type="checkbox"
            checked={excludeLimitUpSignal}
            onChange={(e) => setExcludeLimitUpSignal(e.target.checked)}
          />
          <span>信号日涨停(T)</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="成交日(T+1)为实际调仓买入日，涨停封板买不到，与回测 BoardAwareExchange 口径一致"
        >
          <input
            type="checkbox"
            checked={excludeLimitUpTrade}
            onChange={(e) => setExcludeLimitUpTrade(e.target.checked)}
          />
          <span>成交日涨停(T+1)</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="成交日(T+1)停牌/无行情，同样买不到"
        >
          <input
            type="checkbox"
            checked={excludeSuspended}
            onChange={(e) => setExcludeSuspended(e.target.checked)}
          />
          <span>成交日停牌</span>
        </label>
      </div>

      {/* 进度条 */}
      {running && (
        <div className="mb-3">
          <div className="h-2 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden">
            <div
              className="h-full rounded bg-blue-600 transition-all duration-300"
              style={{ width: `${Math.max(1, Math.min(100, progress))}%` }}
            />
          </div>
          <p className="mt-1 text-slate-500 text-[11px]">{progressMsg || `进度 ${Math.round(progress)}%`}</p>
        </div>
      )}

      {/* 因子勾选区（三个来源） */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
        {groups.map((g) => (
          <div key={g.source} className="border rounded p-2 bg-white dark:bg-slate-800">
            <div className="flex items-center justify-between mb-1">
              <button
                type="button"
                onClick={() => {
                  if (g.source !== 'custom') loadCatalog(g.source)
                }}
                className={`font-semibold ${
                  g.loaded || g.source === 'custom'
                    ? 'text-slate-600 dark:text-slate-300'
                    : 'text-blue-500 hover:underline'
                }`}
                title={g.source === 'custom' ? '' : '点击加载因子目录'}
              >
                {g.label}
              </button>
              <span className="text-slate-400">
                {g.items.filter((it) => selected.has(it.key)).length}/{g.items.length}
              </span>
            </div>
            {g.loaded || g.source === 'custom' ? (
              <>
                <div className="flex items-center gap-2 mb-1">
                  <button type="button" onClick={() => toggleAll(g, true)} className="text-blue-500 hover:underline">
                    全选
                  </button>
                  <span className="text-slate-300">|</span>
                  <button type="button" onClick={() => toggleAll(g, false)} className="text-blue-500 hover:underline">
                    清空
                  </button>
                </div>
                <div className="max-h-40 overflow-y-auto space-y-1 pr-1">
                  {g.items.map((it) => (
                    <label
                      key={it.key}
                      className={`flex items-center gap-1.5 cursor-pointer rounded px-1.5 py-0.5 border ${
                        selected.has(it.key)
                          ? 'bg-blue-600 text-white border-blue-600'
                          : 'border-slate-200 dark:border-slate-600 hover:border-blue-400'
                      }`}
                      title={it.expression}
                    >
                      <input
                        type="checkbox"
                        className="hidden"
                        checked={selected.has(it.key)}
                        onChange={() => toggle(it.key)}
                      />
                      <span className="truncate">{it.name}</span>
                    </label>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-slate-400 italic">点击标题加载 Alpha158 因子目录</p>
            )}
          </div>
        ))}
      </div>

      {error && <p className="mb-2 text-red-500 text-[11px] break-all">{error}</p>}

      {/* 取消提示 */}
      {!running && !error && progressMsg === '已取消' && (
        <p className="mb-2 text-amber-600 text-[11px]">测试已取消，未生成结果。</p>
      )}

      {/* 结果表格 */}
      {results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="text-slate-500 border-b">
                <th className="text-left py-1 pr-2">因子</th>
                <th className="text-right px-1">覆盖率</th>
                <th className="text-right px-1">信号</th>
                <th className="text-right px-1">触发数</th>
                <th className="text-right px-1">触发收益</th>
                <th className="text-right px-1">未触发数</th>
                <th className="text-right px-1">未触发收益</th>
                <th className="text-right px-1">差值</th>
                <th className="text-right px-1">p值</th>
                <th className="text-right px-1">分位收益</th>
                <th className="text-right px-1">IC</th>
                <th className="text-right px-1">RankIC</th>
                <th className="text-right px-1">ICIR</th>
                <th className="text-right pl-2">结论</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                const significant = r.p_value !== null && r.p_value < 0.05
                const goodBase =
                  r.error == null && r.diff !== null && r.diff > 0 && (!r.is_binary || significant)
                // 方向矛盾：IC 与触发收益差方向相反且 ICIR 稳定（|ICIR|>=0.05，即×100后>=5）
                // 说明"触发后收益"由少数触发日主导，逐日横截面方向相反，不能仅凭 diff 下结论
                // （对连续因子同样成立，不限于 0/1 信号）
                const conflicting =
                  r.error == null &&
                  r.ic !== null &&
                  r.icir !== null &&
                  Math.abs(r.icir) >= 0.05 &&
                  r.diff !== null &&
                  ((r.ic < 0 && r.diff > 0) || (r.ic > 0 && r.diff < 0))
                // 反向有效：连续因子高分位组收益显著更低（diff<0），且 IC/ICIR 稳定为负（方向一致）→ 因子需反向使用（低值组买入）
                const goodReverseBase =
                  r.error == null &&
                  !r.is_binary &&
                  significant &&
                  r.ic !== null &&
                  r.ic < 0 &&
                  r.icir !== null &&
                  Math.abs(r.icir) >= 0.05 &&
                  r.diff !== null &&
                  r.diff < 0
                // 按日稳定性：|t|>=2 且胜率方向与 diff 一致。diff 是观测加权平均，若触发样本集中
                // 在少数暴涨/暴跌日，观测平均会被拉高而逐日并无稳定超额（如暴跌抄底类信号），
                // 此时 t≈0、胜率≈50%，不得判定为有效。t 优先用 HAC 稳健 t（修正自相关/异方差），
                // 旧结果无 HAC 字段则回退普通 t。
                const dT = r.daily_t_hac ?? r.daily_t
                const stable =
                  dT === null ||
                  dT === undefined ||
                  (Math.abs(dT) >= 2 &&
                    ((r.diff ?? 0) >= 0 ? (r.daily_win ?? 0) >= 0.5 : (r.daily_win ?? 0) <= 0.5))
                const good = goodBase && stable
                const goodReverse = goodReverseBase && stable
                const qr = r.quintile_ret ?? []
                const maxAbs = qr.length > 0 ? Math.max(...qr.map((g) => Math.abs(g.mean_ret))) : 0
                // 分位收益悬停：直接展示 5 组日截面收益（一行一组），便于横向比较形态
                const qrTip = qr.length
                  ? '每日横截面分5组(1=最低值…5=最高值)，日截面平均收益（一天一组样本，已按剔除开关过滤）：\n' +
                    qr.map((g) => `第${g.quantile}组: ${(g.mean_ret * 100).toFixed(2)}%（配对日数 ${g.n_days ?? '-'}）`).join('\n')
                  : ''
                // 0/1 信号的双柱：信号组/非信号组的逐日截面收益均值（日均）
                const dTrig = r.daily_trig_mean ?? null
                const dNot = r.daily_not_mean ?? null
                const hasDaily = dTrig !== null && dNot !== null && Number.isFinite(dTrig) && Number.isFinite(dNot)
                const dMax = hasDaily ? Math.max(Math.abs(dTrig), Math.abs(dNot)) : 0
                // 悬停提示同时显示信号组/非信号组日截面均值 + 配对日差（不论鼠标放在哪个柱子上）
                // t 与"结论/时间集中"判定同口径：优先 HAC 稳健 t（修正自相关/异方差），
                // 旧结果无 HAC 字段则回退普通 t
                const dTipLabel = r.daily_t_hac != null ? 'HAC t' : 't'
                const dailyTip = hasDaily
                  ? `触发组：日截面均值 ${dTrig >= 0 ? '+' : ''}${(dTrig * 100).toFixed(3)}%　|　未触发组：日截面均值 ${dNot >= 0 ? '+' : ''}${(dNot * 100).toFixed(3)}%　|　配对日差：${r.daily_diff != null ? `${r.daily_diff >= 0 ? '+' : ''}${(r.daily_diff * 100).toFixed(3)}%` : '-'}（${dT != null ? `${dTipLabel}=${dT >= 0 ? '+' : ''}${dT.toFixed(1)}` : ''}）　|　配对日数：${r.daily_n ?? '-'}`
                  : ''
                return (
                  <tr key={r.id} className="border-b border-slate-100 dark:border-slate-700">
                    {r.error ? (
                      <td className="py-1 pr-2 text-red-500" colSpan={14}>
                        {r.name}：{r.error}
                      </td>
                    ) : (
                      <>
                        <td className="py-1 pr-2">
                          <span className="font-semibold">{r.name}</span>
                          <span className="text-slate-400 ml-1">[{r.source}]</span>
                          <div className="text-slate-400 font-mono text-[9px] truncate max-w-[220px]" title={r.expression}>
                            {r.expression}
                          </div>
                        </td>
                        <td className="text-right px-1">{fmt(r.coverage, 2)}</td>
                        <td className="text-right px-1">
                          {r.is_binary ? (
                            <span className="text-emerald-600 font-semibold">0/1</span>
                          ) : (
                            <span className="text-slate-400" title="连续因子按分位数分组：触发 = 前 20% 高分位，未触发 = 后 20% 低分位">
                              连续
                            </span>
                          )}
                        </td>
                        <td className="text-right px-1">
                          <span
                            title={
                              (r.trigger?.limit_up_excluded_t ?? 0) +
                                (r.trigger?.limit_up_excluded_t1 ?? 0) +
                                (r.trigger?.suspended_excluded ?? 0) >
                              0
                                ? `触发组已剔除（数字为剔除后样本数）：信号日T涨停 ${r.trigger?.limit_up_excluded_t} · 成交日T+1涨停 ${r.trigger?.limit_up_excluded_t1} · 成交日停牌 ${r.trigger?.suspended_excluded}`
                                : undefined
                            }
                          >
                            {r.trigger?.count ?? '-'}
                          </span>
                        </td>
                        <td className="text-right px-1">{fmt(r.trigger?.mean_ret, 3)}</td>
                        <td className="text-right px-1">
                          <span
                            title={
                              (r.not_trigger?.limit_up_excluded_t ?? 0) +
                                (r.not_trigger?.limit_up_excluded_t1 ?? 0) +
                                (r.not_trigger?.suspended_excluded ?? 0) >
                              0
                                ? `未触发组已剔除（与触发组同口径，数字为剔除后样本数）：信号日T涨停 ${r.not_trigger?.limit_up_excluded_t} · 成交日T+1涨停 ${r.not_trigger?.limit_up_excluded_t1} · 成交日停牌 ${r.not_trigger?.suspended_excluded}`
                                : undefined
                            }
                          >
                            {r.not_trigger?.count ?? '-'}
                          </span>
                        </td>
                        <td className="text-right px-1">{fmt(r.not_trigger?.mean_ret, 3)}</td>
                        <td
                          className={`text-right px-1 font-semibold ${(r.diff ?? 0) >= 0 ? 'text-red-500' : 'text-emerald-600'}`}
                          title={
                            dT != null
                              ? (() => {
                                  const band = r.daily_n > 0 ? 1.96 / Math.sqrt(r.daily_n) : 0
                                  const s1 = r.daily_acf1 != null ? Math.abs(r.daily_acf1) > band : null
                                  const s5 = r.daily_acf5 != null ? Math.abs(r.daily_acf5) > band : null
                                  return [
                                    `按日配对检验（${r.daily_n} 个交易日）：`,
                                    `日差值均值 ${fmt(r.daily_diff, 3)}%`,
                                    `HAC t=${fmtRaw(dT, 2)}（普通 t=${r.daily_t != null ? fmtRaw(r.daily_t, 2) : '-'}）`,
                                    `胜率 ${fmt(r.daily_win, 1)}%`,
                                    `lag-1 自相关=${r.daily_acf1 ?? '-'}${s1 == null ? '' : s1 ? '（显著）' : '（不显著）'}`,
                                    `lag-5 自相关=${r.daily_acf5 ?? '-'}${s5 == null ? '' : s5 ? '（显著）' : '（不显著）'}`,
                                    `方差稳定性 后半/前半=${r.daily_var_ratio != null ? `${r.daily_var_ratio}x` : '-'}`,
                                    `（HAC 修正自相关与异方差；lag 自相关置信带 ±${band.toFixed(3)}）`,
                                  ].join('\n')
                                })()
                              : undefined
                          }
                        >
                          <div>{fmt(r.diff, 3)}</div>
                          {dT != null && (
                            <div className="text-slate-400 font-mono text-[9px] font-normal whitespace-nowrap">
                              日{fmt(r.daily_diff, 3)} · t={fmtRaw(dT, 1)} · 胜{fmt(r.daily_win, 1)}%
                            </div>
                          )}
                        </td>
                        <td className="text-right px-1">
                          {r.p_value === null ? '-' : fmtP(r.p_value)}
                        </td>
                        <td className="text-right px-1">
                          {r.is_binary && hasDaily && dMax > 0 ? (
                            // 0/1 信号：信号组 vs 非信号组的逐日截面收益均值双柱（悬停直接显示两组数值与配对日差）
                            <div className="inline-flex items-end gap-[3px] h-6 align-bottom">
                              <div
                                className={`w-[6px] ${dTrig >= 0 ? 'bg-emerald-500' : 'bg-red-400'}`}
                                style={{ height: `${Math.max(3, Math.round((Math.abs(dTrig) / dMax) * 20))}px` }}
                                title={dailyTip}
                              />
                              <div
                                className={`w-[6px] ${dNot >= 0 ? 'bg-emerald-500' : 'bg-red-400'}`}
                                style={{ height: `${Math.max(3, Math.round((Math.abs(dNot) / dMax) * 20))}px` }}
                                title={dailyTip}
                              />
                            </div>
                          ) : qr.length > 0 && maxAbs > 0 ? (
                            <div className="inline-flex items-end gap-[3px] h-6 align-bottom" title={qrTip}>
                              {qr.map((g) => {
                                const h = Math.max(3, Math.round((Math.abs(g.mean_ret) / maxAbs) * 20))
                                const c = g.mean_ret >= 0 ? 'bg-emerald-500' : 'bg-red-400'
                                return (
                                  <div
                                    key={g.quantile}
                                    className={`w-[6px] ${c}`}
                                    style={{ height: `${h}px` }}
                                    title={qrTip}
                                  />
                                )
                              })}
                            </div>
                          ) : (
                            '-'
                          )}
                        </td>
                        <td className="text-right px-1">{fmtRaw(r.ic, 4)}</td>
                        <td className="text-right px-1">{fmtRaw(r.rank_ic, 4)}</td>
                        <td className="text-right px-1">{fmtRaw(r.icir, 3)}</td>
                        <td className="text-right pl-2">
                          {conflicting ? (
                            <span className="text-orange-500 font-semibold" title="IC/ICIR 与触发收益差方向相反，信号可能由少数触发日主导，横截面方向相反">
                              方向矛盾
                            </span>
                          ) : good ? (
                            <span className="text-emerald-600 font-semibold">有效✓</span>
                          ) : goodReverse ? (
                            <span
                              className="text-emerald-600 font-semibold"
                              title="连续因子：高分位组收益显著更低且 IC/ICIR 稳定为负，因子值与未来收益负相关，需反向使用（因子值低时买入）"
                            >
                              有效(反向)✓
                            </span>
                          ) : (goodBase || goodReverseBase) && !stable ? (
                            <span
                              className="text-amber-600 font-semibold"
                              title="总差值方向显著但按日配对检验不显著（|t|&lt;2 或胜率≈50%）：差值主要由少数交易日驱动，逐日无稳定超额，慎用"
                            >
                              时间集中
                            </span>
                          ) : (
                            <span className="text-slate-400">待观察</span>
                          )}
                        </td>
                      </>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="mt-1 text-slate-400">
            触发分组：0/1 信号为"因子值&gt;0.5"；连续因子按分位数分组（触发 = 前 20% 高分位，未触发 = 后 20% 低分位）。触发数为按剔除开关过滤后的数量（涨停/停牌判定统一为涨停价四舍五入口径，板块 10%/20%/30%）：信号日(T)涨停 = 选股过滤无前视；成交日(T+1)涨停与停牌 = 调仓日实际买不到，与回测一致。
            差值 = 触发均值 − 未触发均值（正数说明触发组未来 {labelHorizon} 日收益更高）；收益按信号日收盘价买入持有 {labelHorizon} 个交易日计算；p值* 表示 Mann-Whitney U 检验显著（&lt;0.05）。
            IC = 逐日横截面 Pearson 相关均值，ICIR = 平均IC/IC标准差。表中 IC/RankIC/ICIR 均为原始小数（不加%），稳定性阈值 |ICIR|≥0.05（即×100后≥5，日频口径，市值为例0.1以上即为稳定负向）按同一口径判定；覆盖率/收益/差值为 ×100 百分比。
            0/1 信号的分位收益列显示"信号组 vs 非信号组"的逐日截面收益均值双柱（每天先算各组平均未来收益，再对所有交易日取均值，防信号聚集虚高），悬停可查看两组数值与配对日差。
            分位收益：连续因子按每日横截面分 5 组（1=最低值组…5=最高值组），每组为日截面平均收益（每天先算组内均值、再对所有参与交易日取平均，与 0/1 双柱同口径，避免少数日子集中主导），柱状图可识别非线性关系（单调、U型、倒U型），绿=正收益、红=负收益；样本已按剔除开关过滤，悬停显示 5 组收益与各自配对日数。
            若出现"方向矛盾"：diff 为正但 IC/ICIR 稳定为负，说明信号由少数触发日主导，逐日横截面方向相反，慎用。
            若出现"时间集中"：diff 方向显著但按日配对检验（日均差值 t 值 / 胜率）不显著，说明总差值被少数交易日拉高，逐日看并无稳定超额（典型如暴跌抄底类信号），慎用。结论判定与提示中的 t 均为 Newey-West HAC 稳健 t。
            有效(反向)：连续因子高分位组收益显著更低（diff&lt;0）、IC/ICIR 稳定为负且方向一致，说明因子与未来收益负相关，反向使用（因子值低时买入）有效，常见于市值、流动性等负向因子。
          </p>
        </div>
      )}
    </div>
  )
}
