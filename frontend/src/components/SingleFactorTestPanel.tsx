import { useEffect, useRef, useState } from 'react'
import { createSingleFactorTest, getSingleFactorTestProgress, cancelSingleFactorTest, getFactorCatalog } from '../api'
import type { CustomFormula, SingleFactorTestResult } from '../api'
import type { FactorCatalog, FactorField } from '../types'

interface SingleFactorTestPanelProps {
  customFormulas: CustomFormula[]
  defaultUniverse: string
  defaultStartDate: string
  defaultEndDate: string
  defaultLabelHorizon: number
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
}: SingleFactorTestPanelProps) {
  const [universe, setUniverse] = useState(defaultUniverse)
  const [startDate, setStartDate] = useState(defaultStartDate)
  const [endDate, setEndDate] = useState(defaultEndDate)
  const [labelHorizon, setLabelHorizon] = useState<number>(
    Number.isFinite(defaultLabelHorizon) ? defaultLabelHorizon : 2,
  )

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
      })
      const task_id = resp?.task_id
      if (!task_id) {
        // 后端仍是旧版（同步返回 items/total），无法轮询进度
        setError('后端接口未返回任务ID（后端版本过旧），请重启后端服务（start_backend.bat）后重试')
        setRunning(false)
        return
      }
      taskIdRef.current = task_id
      // 轮询进度：每 700ms 刷新一次，直到成功/失败/取消
      pollTimerRef.current = window.setInterval(async () => {
        try {
          const p = await getSingleFactorTestProgress(task_id)
          setProgress(p.progress)
          setProgressMsg(p.message)
          if (p.status === 'success') {
            if (pollTimerRef.current !== null) window.clearInterval(pollTimerRef.current)
            pollTimerRef.current = null
            taskIdRef.current = null
            setResults(p.result?.items ?? [])
            setRunning(false)
            setCancelling(false)
          } else if (p.status === 'failed') {
            if (pollTimerRef.current !== null) window.clearInterval(pollTimerRef.current)
            pollTimerRef.current = null
            taskIdRef.current = null
            setError(`单因子测试失败：${p.error ?? p.message}`)
            setRunning(false)
            setCancelling(false)
          } else if (p.status === 'cancelled') {
            if (pollTimerRef.current !== null) window.clearInterval(pollTimerRef.current)
            pollTimerRef.current = null
            taskIdRef.current = null
            setProgressMsg('已取消')
            setRunning(false)
            setCancelling(false)
          }
        } catch (e: unknown) {
          // 轮询瞬间失败（网络抖动）不中断，下一次轮询继续
          const msg = e instanceof Error ? e.message : String(e)
          if (msg.includes('404')) {
            if (pollTimerRef.current !== null) window.clearInterval(pollTimerRef.current)
            pollTimerRef.current = null
            taskIdRef.current = null
            setError('单因子测试任务不存在或已过期')
            setRunning(false)
            setCancelling(false)
          }
        }
      }, 700)
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

  return (
    <div className="mt-2 border rounded p-3 bg-slate-50 dark:bg-slate-900 text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-slate-600 dark:text-slate-300">单因子测试</span>
        <span className="text-slate-400">不训练模型，快速诊断因子预测力：稀疏 0/1 信号看"触发 vs 未触发"收益，连续因子看 IC</span>
      </div>

      {/* 参数行 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-3">
        <label className="flex flex-col">
          <span className="text-slate-500 mb-1">股票池</span>
          <select className="border rounded px-2 py-1" value={universe} onChange={(e) => setUniverse(e.target.value)}>
            <option value="csi300">沪深300</option>
            <option value="csi500">中证500</option>
            <option value="csi800">中证800</option>
            <option value="csi1000">中证1000</option>
            <option value="all">全部A股</option>
          </select>
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500 mb-1">开始日期</span>
          <input type="date" className="border rounded px-2 py-1" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500 mb-1">结束日期</span>
          <input type="date" className="border rounded px-2 py-1" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <label className="flex flex-col">
          <span className="text-slate-500 mb-1">预测周期(天)</span>
          <input
            type="number"
            min={1}
            className="border rounded px-2 py-1"
            value={Number.isFinite(labelHorizon) ? labelHorizon : 2}
            onChange={(e) => setLabelHorizon(Number(e.target.value))}
          />
        </label>
        <div className="flex items-end gap-2">
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
                <th className="text-right px-1">IC</th>
                <th className="text-right px-1">RankIC</th>
                <th className="text-right px-1">ICIR</th>
                <th className="text-right pl-2">结论</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                const significant = r.p_value !== null && r.p_value < 0.05
                const good =
                  r.error == null && r.diff !== null && r.diff > 0 && (!r.is_binary || significant)
                // 方向矛盾：IC 与触发收益差方向相反且 ICIR 稳定（|ICIR|>=0.02，即×100后>=2）
                // 说明"触发后收益"由少数触发日主导，逐日横截面方向相反，不能仅凭 diff 下结论
                // （对连续因子同样成立，不限于 0/1 信号）
                const conflicting =
                  r.error == null &&
                  r.ic !== null &&
                  r.icir !== null &&
                  Math.abs(r.icir) >= 0.02 &&
                  r.diff !== null &&
                  ((r.ic < 0 && r.diff > 0) || (r.ic > 0 && r.diff < 0))
                return (
                  <tr key={r.id} className="border-b border-slate-100 dark:border-slate-700">
                    {r.error ? (
                      <td className="py-1 pr-2 text-red-500" colSpan={13}>
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
                            <span className="text-slate-400">连续</span>
                          )}
                        </td>
                        <td className="text-right px-1">
                          {r.trigger?.count ?? '-'}
                          {(r.trigger?.limit_up_excluded ?? 0) > 0 && (
                            <span className="text-amber-600 ml-1" title="信号当日涨停（买不到）已剔除的样本数">
                              -{(r.trigger as { limit_up_excluded?: number })?.limit_up_excluded}涨停
                            </span>
                          )}
                        </td>
                        <td className="text-right px-1">{fmt(r.trigger?.mean_ret, 3)}</td>
                        <td className="text-right px-1">{r.not_trigger?.count ?? '-'}</td>
                        <td className="text-right px-1">{fmt(r.not_trigger?.mean_ret, 3)}</td>
                        <td className={`text-right px-1 font-semibold ${(r.diff ?? 0) >= 0 ? 'text-red-500' : 'text-emerald-600'}`}>
                          {fmt(r.diff, 3)}
                        </td>
                        <td className="text-right px-1">
                          {r.p_value === null ? '-' : significant ? `${r.p_value.toFixed(4)}*` : r.p_value.toFixed(4)}
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
            触发数为剔除"信号当日涨停"样本后的数量（涨停买不到，已按板块 10%/20%/30% 剔除）。
            差值 = 触发均值 − 未触发均值（正数说明信号触发后未来 {labelHorizon} 日收益更高）；收益按信号日收盘价买入持有 {labelHorizon} 个交易日计算；p值* 表示 Mann-Whitney U 检验显著（&lt;0.05）。
            IC = 逐日横截面 Pearson 相关均值，ICIR = 平均IC/IC标准差。表中 IC/RankIC/ICIR 均为原始小数（不加%），稳定性阈值 |ICIR|≥0.02（即×100后≥2）按同一口径判定；覆盖率/收益/差值为 ×100 百分比。
            若出现"方向矛盾"：diff 为正但 IC/ICIR 稳定为负，说明信号由少数触发日主导，逐日横截面方向相反，慎用。
          </p>
        </div>
      )}
    </div>
  )
}
