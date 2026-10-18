import { Component, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import DateInput, { type DateInputHandle, isValidDateStr } from './DateInput'
import {
  createSingleFactorTest,
  getSingleFactorTestProgress,
  cancelSingleFactorTest,
  getSingleFactorTestTasks,
  clearSingleFactorTest,
  getFactorCatalog,
} from '../api'
import type {
  EventStudyRequest,
  EventStudyResult,
  ExitFactorPayload,   // ★ v2.5.0：离场（多选）的触发率预检 + 事件研究 ✓
  SingleFactorTestResult,
} from '../api'
import type { FactorCatalog, FactorField } from '../types'
import EventStudyModal from './EventStudyModal'
import TopkCurveModal from './TopkCurveModal'
import ExitCurveModal from './ExitCurveModal'   // ★ v2.8.0：离场曲线（对照 + 某条离场 ✓）
// ★ P1-B（2026-10-18 ✓）：`verdictRules` 的三个符号已随纯逻辑搬进 `sftLogic.ts` ✓
//   ⇒ 本文件不再直接引用它们 ✓（删掉这行是为了通过 `noUnusedLocals` ✓）。

// ★ P1-B（2026-10-18 ✓）：面板的**纯逻辑**（结论判定 / 前瞻收益 / 导出表头 / K 档 / 区间解析 ✓）
//   已抽到 `sftLogic.ts` ✓ —— 本文件从此只负责 UI ✓（`EsErrorBoundary` 含 JSX，仍留在这里 ✓）。
import {
  EXPORT_FORMULA_HEADERS,
  HORIZON_SYNTAX,
  K_PRESETS,
  SHOW_ELAPSED,
  SHOW_TIMING_COL,
  SingleFactorTestPanelProps,
  SourceGroup,
  TestResult,
  VERDICT_LABEL,
  esPointOf,
  exportHeaders,
  horizonHint,
  kLabel,
  parseHorizons,
  reverseTitleOf,
  verdictOf,
} from './sftLogic'

class EsErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { err: Error | null }
> {
  state: { err: Error | null } = { err: null }

  static getDerivedStateFromError(err: Error) {
    return { err }
  }

  render() {
    if (this.state.err) {
      return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white dark:bg-slate-800 rounded-lg shadow-xl p-4 max-w-[640px] text-xs">
            <div className="text-red-500 font-semibold mb-1">
              {this.props.label ?? '事件研究弹窗'}渲染出错
            </div>
            <div className="text-slate-500 break-all">{String(this.state.err)}</div>
            <div className="text-slate-400 mt-2">
              页面其余部分未受影响；按 F5 刷新可重置。请把上面的错误信息反馈给开发者。
            </div>
            <button
              className="mt-3 px-2 py-1 rounded border"
              onClick={() => this.setState({ err: null })}
            >
              重试
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
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
  // 预测周期：支持单值 / 逗号枚举(1,2,3,5) / range 区间(1:5:20 → 1,6,11,16，含终点)
  const [labelHorizonText, setLabelHorizonText] = useState<string>(
    String(Number.isFinite(defaultLabelHorizon) ? defaultLabelHorizon : 2),
  )
  // 日期三段输入：开始日期填完整后自动跳到结束日期年份
  const startDateRef = useRef<DateInputHandle>(null)
  const endDateRef = useRef<DateInputHandle>(null)
  // 复权方式：none/forward/backward（与回测一致，默认前复权；前/后复权在比率类因子与收益率上数学等价）
  const [priceAdjust, setPriceAdjust] = useState('backward')   // v1.19.97：默认后复权
  // 触发组剔除开关：信号日(T)涨停 / 成交日(T+1)涨停 / 成交日停牌（默认全开，保持原行为 + 新增成交日口径）
  const [excludeLimitUpSignal, setExcludeLimitUpSignal] = useState(true)
  const [excludeLimitUpTrade, setExcludeLimitUpTrade] = useState(true)
  const [excludeSuspended, setExcludeSuspended] = useState(true)
  // 日截面剔除：ST(T+1) / 创业板 / 科创板（均只用当日已发布状态，无未来函数）。
  // ST 默认开：ST/*ST/退市整理期个股的收益分布与正常股差异极大，评估触发型信号时
  // 应默认排除；创业板/科创板仍默认关。缺 is_st 标签时后端会明确报错提示取消勾选。
  const [excludeStT1, setExcludeStT1] = useState(true)
  const [excludeGem, setExcludeGem] = useState(false)
  const [excludeKcb, setExcludeKcb] = useState(false)
  // 信号停牌行语义：勾选=SR删行（益盟/通达信"无停牌行"，与回测特征一致，默认）；
  // 取消=停牌日保留 NaN 占位（qlib 官方/聚宽 notebook 口径，对账时用）
  const [suspendRemove, setSuspendRemove] = useState(true)
  // 价格整分：真实价按分取整参与因子计算（仅不复权生效，默认开；与益盟/聚宽整分口径对齐）
  const [priceRound, setPriceRound] = useState(true)
  // 预热缓冲（交易日，v1.18.6）：特征加载多前移 N 个交易日，长回看/动态窗口公式
  // （DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套）在区间首日即有收敛值；0=关闭（旧口径）
  const [warmupDaysText, setWarmupDaysText] = useState('250')
  // ★ v2.1.24（B ③）：**成交时点**（用户 2026-10-11 选定 B：统一到 T+1 开盘 ✓，且要能切换 ✓）——
  //   决定「持仓期收益曲线」的**每期买入价**与**期内逐日盯市价**（两者必须同基准 ✓）。
  //   默认 `t1_close` = 老口径 ✓（不选时数字逐位不变 ✓）。
  // ★ v2.1.34（用户 2026-10-12）：**默认改成「次日开盘价」** ✗→✓ ——
  //   原因：它是行业通行做法 ✓，且**面板净值引擎（`event_study_nav`）本来就用 `t1_open`** ✓
  //   ⇒ 默认与净值引擎一致，用户不用再手动对齐（也少一处"口径分叉"的坑 ✓）。
  //   ⚠ 老口径 `t1_close` 仍可选 ✓（选它会**改变**持仓期曲线的年化/回撤/夏普 ✓，属有意行为 ✓）。
  const [fill, setFill] = useState('t1_open')
  // ★★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径**（审计 ② ✓）——
  //   `legacy`（默认 ✓）= 老口径：成交日**收盘**涨停也拒 ⇒ 保守 ✓（= 既有数字逐位不变 ✓）；
  //   `open_only` = 新口径：**只拒「开盘一字涨停 / 停牌」** ✓（更贴实盘 ✓）。
  //   ⚠ 老口径在"开盘成交"时用了**当日收盘**信息 = 未来信息 ✗（用户原话："可能开盘 1 秒拉涨停，
  //     其实你吃不到" ⇒ 所以默认仍是保守口径 ✓）。
  //   ⚠ **只在「成交时点 = 次日开盘价」时可用** ✓ —— 收盘成交没有"开盘一字板"概念 ⇒
  //     控件置灰 ✗；后端也会自动退回老口径并把生效值写进 `bt.diag["limit_judge"]` ✓（不静默分叉 ✗）。
  const [limitJudge, setLimitJudge] = useState('legacy')
  // ★ v2.1.27：**每期全仓滚动（实盘口径 · 逐点对拍专用）** —— 用户 2026-10-11 指定：
  //   默认 **不开** ✓（很慢 ✓）、悬停注明用途 ✓、放在「成交时点」右边「预热缓冲」左边 ✓。
  //   打开后后端会用与回灌侧同一个 `run_period_roll` 再算一条净值 ⇒ 可与回灌逐点对拍 ✓。
  const [periodRoll, setPeriodRoll] = useState(false)
  // ★★ v2.4.3 / v2.5.0（用户 2026-10-15 拍板 ✓）：**离场因子**（单因子测试的"卖出侧" ✓）
  //   · `exitKeys` = 选中的**自定义公式** key 集合（空 = 不启用 ✓）；v2.5.0 起**多选** ✓
  //     （用户："这个离场因子能不能搞成多选啊，要矩阵化" ✓）；
  //   · 口径 = 「因子值 > 阈值（默认 0）」✓（二值公式与连续因子**通用** ✓；多个离场**共用一个阈值** ✓）；
  //   · 与任一进场因子是**同一公式** ⇒ 前端**直接拦**（红字 + 禁用提交 ✓，后端也会 400 ✓）；
  //   · 勾了离场 ⇒ 后端自动开 `period_roll` ✓（这里也把那个勾打上，免得用户困惑 ✓）；
  //   · 结果 = **矩阵**：`进场 × (1 + 离场数)` 条同源同口径回测 ✓（"不离场"对照必算 ✓），
  //     并给**逐个离场**的触发率预检（> 50% 黄字 ✓）与**离场事件研究**（触发后 h 日 vs 未触发 ✓）。
  //   ★ v2.5.1（用户："改成勾选再显示面板吧，不然一大堆总是显示看着碍眼" ✓）：`exitOn` 默认关 ✓
  //     —— 标签面板只在勾上后展开 ✓（几百条时列表内可滚动 ✓）。
  const [exitOn, setExitOn] = useState(false)
  const [exitKeys, setExitKeys] = useState<Set<string>>(new Set())
  const [exitThresholdText, setExitThresholdText] = useState('0')
  const [exitsInfo, setExitsInfo] = useState<ExitFactorPayload[]>([])
  // ★ v2.8.0：离场曲线弹窗（`ei=null` ⇒ 对照 + 全部离场画一起 ✓；`ei=k` ⇒ 只强调第 k 条 ✓）
  const [exitModal, setExitModal] = useState<{ row: SingleFactorTestResult; ei: number | null; name: string } | null>(null)
  const toggleExit = (k: string) =>
    setExitKeys((cur) => {
      const n = new Set(cur)
      if (n.has(k)) n.delete(k)
      else n.add(k)
      return n
    })
  // 持仓期曲线的「固定 K 档」（v1.18.45）：默认**一个都不勾** = 只算默认档（最强分位组 +
  // 10% 固定档），零额外开销；勾选的档随本次测试一起算出 ⇒ 弹窗里**纯前端切换**、不用重跑。
  const [detailKs, setDetailKs] = useState<number[]>([])
  const toggleDetailK = (k: number) =>
    setDetailKs((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]))
  // v1.18.64：预设档是否已全选（「全选 / 清空」按钮用）。
  // ⚠ 上限说明：预设共 K_PRESETS.length 档，后端 API 上限 12（= 默认档 + 10 预设），故可全选。
  const allPresetsOn = K_PRESETS.every((k) => detailKs.includes(k))

  // 事件研究弹窗：0/1 稀疏信号的"触发事件收益分布"。
  // 动机：稀疏信号按日配对检验在触发日只有 1 只票时会退化成单票收益序列，均值/显著性
  // 不可信；事件研究以"每次触发"为样本单位，给出概率/赔率的真实画像。
  const [estOpen, setEstOpen] = useState(false)
  const [estReq, setEstReq] = useState<EventStudyRequest | null>(null)
  const [estData, setEstData] = useState<EventStudyResult | null>(null)
  const [estName, setEstName] = useState('')
  // 该行的日配对稳定性字段（t/胜率）：供弹窗与表格共用同一套判定门槛（v1.18.32）
  const [estPair, setEstPair] = useState<{ t?: number | null; win?: number | null } | null>(null)
  // v1.18.63：弹窗默认「最长持有」= **被点开那一行的周期**（此前直接用后端 max_k =
  // 任务填的**最大**预测周期 ⇒ 在 1:2:7 这类多周期下，点"周期 3"那行却默认显示 7 日口径，
  // 与表格结论的 k 不符）。null 时回退旧行为（max_k / 40）。
  const [estK, setEstK] = useState<number | null>(null)

  // 持仓期收益曲线弹窗（v1.18.45，连续因子）：曲线/敏感度数据**随本次测试结果一并返回**
  // ⇒ 打开即看（零重算、纯前端切换 K）；0/1 稀疏信号走「事件研究」那套。
  const [curveOpen, setCurveOpen] = useState(false)
  const [curveRow, setCurveRow] = useState<TestResult | null>(null)
  const [curveName, setCurveName] = useState('')
  const openCurve = (r: TestResult) => {
    setCurveRow(r)
    setCurveName(r.name)
    setCurveOpen(true)
  }

  // 按当前面板参数为某一行结果发起事件研究（参数与单因子测试保持一致）
  const openEventStudy = (r: TestResult) => {
    const wn = Math.floor(Number(warmupDaysText))
    const wu =
      warmupDaysText.trim() === '' || !Number.isFinite(wn) ? undefined : Math.max(0, wn)
    setEstData(r.event_study ?? null)
    setEstName(r.name)
    setEstPair({ t: r.daily_t_hac ?? r.daily_t, win: r.daily_win })
    setEstK(typeof r.horizon === 'number' && r.horizon > 0 ? r.horizon : null)
    setEstReq({
      universe,
      start_date: startDate,
      end_date: endDate,
      factor: {
        id: r.id,
        name: r.name,
        expression: r.expression,
        source: r.source,
        source_formula: r.source_formula,
      },
      exclude_limit_up_signal: excludeLimitUpSignal,
      exclude_limit_up_trade: excludeLimitUpTrade,
      exclude_suspended: excludeSuspended,
      exclude_st_t1: excludeStT1,
      exclude_stock_gem: excludeGem,
      exclude_stock_kcb: excludeKcb,
      price_adjust: priceAdjust,
      price_round: priceRound,
      suspend_remove: suspendRemove,
      freeze_suspended_price: true,
      warmup_days: wu,
      // ★ v2.1.65：**成交时点必须一起发** ✗→✓（用户 2026-10-12："事件研究的净值曲线是跟随面板
      //   参数走还是写死了？我怎么测下来两个数一模一样" ✓）—— 后端 v2.1.52 起就支持 `fill` ✓，
      //   但**前端一直没发** ✗（类型里也没声明 ✗）⇒ 弹窗"重算"与净值曲线都只能吃到后端默认值
      //   ⇒ 面板把「成交时点」从"次日开盘"改成"次日收盘"**没有任何效果** ✗✗。
      fill,
      // ★ T1（v2.8.6）：**判停口径**也要随弹窗走 ✓（弹窗里那条净值曲线用它 ✓）
      limit_judge: limitJudge,
    })
    setEstOpen(true)
  }

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
        original: f.text,
      })),
    },
    { source: 'alpha158', label: 'Alpha158', items: [] },
    { source: 'alpha360', label: 'Alpha360', items: [] },
  ])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(0)
  const [progressMsg, setProgressMsg] = useState('')
  // ★ v2.4.2（用户 2026-10-15 反馈 ✓）：记录本次任务开始时刻 ⇒ 进度区显示"已用 N 秒" ——
  //   像「每期全仓滚动」那种"统计完成后还要跑十几秒~几分钟"的阶段，光看百分比分不清"在算"还是"卡死" ✗。
  const startedAtRef = useRef(0)
  const [results, setResults] = useState<TestResult[]>([])
  const [error, setError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [exporting, setExporting] = useState(false)
  const taskIdRef = useRef<string | null>(null)
  /**
   * **已完成任务的 id**（v1.19.61）：`stopPolling()` 会把 `taskIdRef` 清空，但任务跑完后再打开
   * 「事件研究」弹窗仍需要它（后端要靠它找到本次算好的触发事件 ⇒ 画净值曲线）。
   * 用户 2026-09-15 报「净值曲线暂不可用：需要任务上下文」就是这个 ref 已被清空导致的。
   */
  const [doneTaskId, setDoneTaskId] = useState<string | null>(null)
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
                original: f.text,
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
    if (!startedAtRef.current) startedAtRef.current = Date.now()   // ★ v2.4.2：计时起点（已用 N 秒 ✓）
    taskIdRef.current = taskId
    pollTimerRef.current = window.setInterval(async () => {
      try {
        const p = await getSingleFactorTestProgress(taskId)
        setProgress(p.progress)
        setProgressMsg(p.message)
        if (p.status === 'success') {
          setDoneTaskId(taskId)          // 先记下来再 stopPolling（它会把 taskIdRef 清空）
          stopPolling()
          setResults(p.result?.items ?? [])
          // ★ v2.4.3 / v2.5.0：逐个离场的触发率预检 + 事件研究（没勾离场 ⇒ 后端不回该字段 ✓）
          setExitsInfo(p.result?.exit_factors ?? [])
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
    // 日期真实性校验：键盘可绕过三段输入的即时校验（如敲出「6 月 31 日」），
    // 必须在此拦下，否则后端会抛「day is out of range for month」。
    if (!isValidDateStr(startDate) || !isValidDateStr(endDate)) {
      setError('日期无效（如 6 月没有 31 日），请重新选择开始/结束日期')
      return
    }
    if (endDate < startDate) {
      setError('结束日期不能早于开始日期')
      return
    }
    const factors = groups.flatMap((g) =>
      g.items
        .filter((it) => selected.has(it.key))
        .map((it) => ({
          id: it.id,
          name: it.name,
          expression: it.expression,
          source: g.source,
          source_formula: it.original ?? it.expression, // 原文随请求走，结果直接回传展示
        })),
    )
    if (factors.length === 0) {
      setError('请至少勾选一个因子')
      return
    }
    // ★ v2.4.3 / v2.5.0：离场因子（**多选** ✓；用户定的"同公式⇒直接拦"✓ —— 前端先拦，后端再 400 ✓）
    const exitItems = exitOn
      ? (groups.find((g) => g.source === 'custom')?.items ?? []).filter((it) => exitKeys.has(it.key))
      : []
    const exitDup = exitItems.find((it) => selected.has(it.key))
    if (exitDup) {
      setError(
        `离场因子「${exitDup.name}」与某个已勾选的进场因子是同一个公式 ✗\n` +
          '按「同日进+出 ⇒ 不交易」规则会永远开不了仓（净值恒 1 一条线），照做则买完就卖、' +
          '被手续费磨掉 ⇒ 请换一个公式 ✓',
      )
      return
    }
    const exitThresholdNum = Number(exitThresholdText)
    if (exitItems.length > 0 && !Number.isFinite(exitThresholdNum)) {
      setError('离场阈值要填数字（默认 0）')
      return
    }
    // 解析预测周期（单值 / 逗号枚举 / range 区间），前端先校验，后端再兜底
    const parsed = parseHorizons(labelHorizonText)
    if (parsed.error) {
      setError(parsed.error)
      return
    }
    if (parsed.horizons.length === 0) {
      setError('请填写预测周期，如 2 或 1,2,5 或 1:5:20')
      return
    }
    // 预热缓冲（交易日）：空=后端默认（250）；非数字按默认处理，负数取 0，小数取整
    const warmupNum = Math.floor(Number(warmupDaysText))
    const warmup_days =
      warmupDaysText.trim() === '' || !Number.isFinite(warmupNum) ? undefined : Math.max(0, warmupNum)
    setRunning(true)
    setCancelling(false)
    setError('')
    setResults([])
    setDoneTaskId(null)
    setExitsInfo([])                                 // ★ v2.5.0：上一轮的离场预检/事件研究清掉 ✓
    setProgress(1)
    setProgressMsg('提交任务...')
    startedAtRef.current = Date.now()               // ★ v2.4.2：新任务重新计时 ✓
    try {
      const resp = await createSingleFactorTest({
        universe,
        start_date: startDate,
        end_date: endDate,
        label_horizon: parsed.horizons[0], // 兼容旧后端（单周期）
        label_horizons: parsed.horizons,
        factors,
        exclude_limit_up_signal: excludeLimitUpSignal,
        exclude_limit_up_trade: excludeLimitUpTrade,
        exclude_suspended: excludeSuspended,
        exclude_st_t1: excludeStT1,
        exclude_stock_gem: excludeGem,
        exclude_stock_kcb: excludeKcb,
        suspend_remove: suspendRemove,
        price_round: priceRound,
        price_adjust: priceAdjust,
        warmup_days,
        // v2.1.24（B ③）：**成交时点** —— 决定持仓期曲线的"每期买入价 + 期内逐日盯市价" ✓
        //   （t1_close 默认 = 老口径 ✓；t1_open = 次日开盘 ✓）
        fill,
        // ★ T1（v2.8.6）：**涨停判定口径** 必须一起发 ✗→✓（否则控件"看着能选、实际没生效" ✗）
        limit_judge: limitJudge,
        // v2.1.27：**每期全仓滚动（逐点对拍专用）** —— 默认关 ✓（慢 ✓）；打开则多算一条实盘口径净值
        // ★ v2.4.3：勾了离场因子 ⇒ **必须开** ✓（离场只作用在这条真金白银回测上 ✓；
        //   后端也会自动打开兜底 ✓ —— 这里显式传，免得"看起来没生效"✗）
        period_roll: periodRoll || exitItems.length > 0 || undefined,
        // ★ v2.4.3 / v2.5.0：**多个**离场因子 + 共用阈值（阈值默认 0 ⇒ 二值/连续同一套口径 ✓）
        exit_factors: exitItems.length
          ? exitItems.map((it) => ({
              id: it.id,
              name: it.name,
              expression: it.expression,
              source: 'custom',
              source_formula: it.original ?? it.expression,
            }))
          : undefined,
        exit_threshold: exitItems.length ? exitThresholdNum : undefined,
        topk_list: detailKs.length ? detailKs : undefined,
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
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(
        detail
          ? `单因子测试失败：${String(detail)}`
          : `单因子测试失败：${e instanceof Error ? e.message : String(e)}`,
      )
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

  // 结果行"编译前公式"：按 (source,id) 回查勾选项原文（自定义公式用用户写的 text；目录因子无原文则回退 qlib 表达式）
  const srcByKey = useMemo(() => {
    const m = new Map<string, string>()
    for (const g of groups)
      for (const it of g.items) if (it.original != null) m.set(`${g.source}:${it.id}`, it.original)
    return m
  }, [groups])

  // 清理结果（释放内存）：删除后端已完成的测试任务/结果，并清空本次展示。运行中任务不受影响。
  const handleClear = async () => {
    setClearing(true)
    try {
      await clearSingleFactorTest()
      setResults([])
    setDoneTaskId(null)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(`清理失败：${msg}`)
    } finally {
      setClearing(false)
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

  // 导出当前展示结果到 Excel（一个文件两个工作表，纯前端生成不占后端）：
  //   Sheet1 "因子指标"：与界面表格一致的指标（含 error 行，错误信息放"结论"列）
  //   Sheet2 "因子与公式"：因子名 + 用户保存公式/目录表达式（按 因子×来源 去重，仅首个周期行）
  // xlsx 用动态 import（点击才加载）：同事 pull 后未 npm install 时其余功能不受影响，导出给出明确提示；
  // 同时把 ~200KB 的 xlsx 移出首屏包，仅导出时按需下载该 chunk。
  const handleExport = async () => {
    if (results.length === 0) return
    setExporting(true)
    setError('')
    try {
      // CJS 模块的 namespace 兼容 default / 具名两种形态（不同打包/预构建环境下取 utils 都可靠）
      const mod: any = await import('xlsx')
      const XLSX = mod.default ?? mod
      // Q 列数 = 实际分位组数（默认十分位 10；至少 5 列，避免 0/1 信号行的列数与历史不一致）
      const qCols = Math.max(5, ...results.map((r) => r.quintile_ret?.length ?? 0))
      const headers = exportHeaders(qCols)
      const aoa: (string | number)[][] = [headers]
      // ⚠ `dash` 原先声明在下面的 for 里 ✗ ⇒ 下面的「离场矩阵」块用不到（TS2304 ✓）
      //   ⇒ 提到函数作用域 ✓（2026-10-15 v2.5.1 加离场矩阵那页时踩到的 ✓）。
      const dash = '-'
      for (const r of results) {
        const formula = r.source_formula || srcByKey.get(`${r.source}:${r.id}`) || r.expression || ''
        const horizon = r.horizon != null ? String(r.horizon) : '-'
        if (r.error) {
          aoa.push([
            r.name, r.source, horizon, formula,
            ...Array(headers.length - 6).fill(dash),   // 前 4 列 + 末列"结论"放错误信息、再末列空
            `错误：${r.error}`,
          ])
          continue
        }
        const q = r.quintile_ret ?? []
        const qCells = Array.from({ length: qCols }, () => dash as string)
        for (const g of q) if (g.quantile >= 1 && g.quantile <= qCols) qCells[g.quantile - 1] = fmt(g.mean_ret, 3)
        // 0/1 信号的触发组/未触发组逐日截面收益均值（对应界面"分位收益"列双柱），连续因子无此分组故留空
        const dayPair = r.is_binary
          ? [fmt(r.daily_trig_mean, 3), fmt(r.daily_not_mean, 3)]
          : [dash, dash]
        const v = verdictOf(r)
        aoa.push([
          r.name,
          r.source,
          horizon,
          formula,
          fmt(r.coverage, 2),
          r.is_binary ? '0/1' : '连续',
          r.trigger?.count != null ? String(r.trigger.count) : dash,
          fmt(r.trigger?.mean_ret, 3),
          // ★ v2.1.31：首次 / 续期内再触发 分组（与上面两个表头一一对应 ✓）
          r.trigger_split?.first?.count != null ? String(r.trigger_split.first.count) : dash,
          fmt(r.trigger_split?.first?.mean_ret, 3),
          r.trigger_split?.refresh?.count != null ? String(r.trigger_split.refresh.count) : dash,
          fmt(r.trigger_split?.refresh?.mean_ret, 3),
          r.not_trigger?.count != null ? String(r.not_trigger.count) : dash,
          fmt(r.not_trigger?.mean_ret, 3),
          fmt(r.diff, 3),
          fmt(r.daily_diff, 3),
          fmtRaw(v.dT, 2),
          fmt(r.daily_win, 1),
          r.daily_n != null ? String(r.daily_n) : dash,
          r.p_value == null ? dash : fmtP(r.p_value),
          ...qCells,
          ...dayPair,
          fmtRaw(r.ic, 4),
          fmtRaw(r.rank_ic, 4),
          fmtRaw(r.icir, 3),
          VERDICT_LABEL[v.kind],
          (() => {
            const pt = esPointOf(r)
            return pt ? fmt(pt.median, 3) : dash
          })(),
        ])
      }
      const seen = new Set<string>()
      const fAoa: (string | number)[][] = [EXPORT_FORMULA_HEADERS]
      for (const r of results) {
        const key = `${r.source}:${r.id}`
        if (seen.has(key)) continue
        seen.add(key)
        fAoa.push([r.name, r.source, r.source_formula || srcByKey.get(key) || r.expression || ''])
      }
      /* ★★ v2.5.1（用户 2026-10-15："待办的导出加上" ✓）：**离场矩阵**单独一页 ——
          行 = （进场因子 × 周期）×（对照 + 每个离场）✓，列 = 年化 / 最大回撤 / Δ年化 / Δ回撤 /
          离场卖出 / 顺延 / 被名单覆盖 / 持币空转 / 触发率 / 离场事件研究（触发后 − 未触发 pp）✓。
          ⚠ 触发率与事件研究来自任务级回显（`exitsInfo` ✓，按名字匹配 ✓）—— 不在结果行里 ✗。 */
      const exAoa: (string | number)[][] = [[
        '进场因子', '进场来源', '周期(天)', '离场因子', '年化', '最大回撤', 'Δ年化(pp)', 'Δ回撤(pp)',
        '离场卖出(笔)', '顺延(天)', '被名单覆盖(次)', '持币空转(次)', '触发率(%)',
        '离场事件研究(触发后−未触发, pp)', '备注',
      ]]
      let exRows = 0
      const pct1 = (v: unknown) =>
        typeof v === 'number' && Number.isFinite(v) ? (v * 100).toFixed(1) : dash
      for (const r of results) {
        const groups = r.exit_rolls ?? []
        if (!groups.length) continue
        const base = r.period_roll?.perf ?? r.period_roll_items?.[0]?.perf ?? null
        exAoa.push([
          r.name, r.source, r.horizon != null ? String(r.horizon) : dash,
          '（不离场 · 对照）',
          base ? pct1(base.annual_return) : dash,
          base ? pct1(base.max_drawdown) : dash,
          '—', '—', '—', '—', '—', '—', '—', '—', '必算的对照列',
        ])
        exRows++
        for (const g of groups) {
          const nm = g?.exit?.name || g?.exit?.id || '?'
          const first = g?.first ?? {}
          const perf = first.perf ?? null
          const st = (first.stats ?? {}) as Record<string, number | undefined>
          const info = exitsInfo.find((e) => (e.name || '') === nm)
          const pre = info?.stats
          const effKey = info?.effect ? Object.keys(info.effect)[0] : ''
          const eff = effKey && info?.effect ? info.effect[effKey] : null
          exAoa.push([
            r.name, r.source, r.horizon != null ? String(r.horizon) : dash, nm,
            perf ? pct1(perf.annual_return) : dash,
            perf ? pct1(perf.max_drawdown) : dash,
            perf && base
              ? ((perf.annual_return ?? 0) - (base.annual_return ?? 0) >= 0 ? '+' : '') +
                (((perf.annual_return ?? 0) - (base.annual_return ?? 0)) * 100).toFixed(1)
              : dash,
            perf && base
              ? ((perf.max_drawdown ?? 0) - (base.max_drawdown ?? 0) >= 0 ? '+' : '') +
                (((perf.max_drawdown ?? 0) - (base.max_drawdown ?? 0)) * 100).toFixed(1)
              : dash,
            st.exit_sells ?? dash,
            st.exit_deferrals ?? dash,
            st.exit_overridden ?? dash,
            st.exit_ignored ?? dash,
            pre?.rate != null ? (pre.rate * 100).toFixed(1) : dash,
            eff && eff.diff_mean != null ? (eff.diff_mean * 100).toFixed(2) : dash,
            String(first.error ?? ''),
          ])
          exRows++
        }
      }
      const wb = XLSX.utils.book_new()
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), '因子指标')
      XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(fAoa), '因子与公式')
      if (exRows > 0) {
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(exAoa), '离场矩阵')
      }
      const now = new Date()
      const pad = (n: number) => String(n).padStart(2, '0')
      XLSX.writeFile(
        wb,
        `单因子测试结果_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.xlsx`,
      )
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      // 动态 import 失败（同事未 npm install）时 e.message 为依赖解析错误，直接提示可操作步骤
      setError(`导出失败：${msg}。若提示缺少 xlsx，请在 frontend 目录执行 npm install 后重试`)
    } finally {
      setExporting(false)
    }
  }

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
        <label className="flex-1 flex flex-col min-w-[170px]">
          <span className="text-slate-500 mb-1">开始日期</span>
          <DateInput
            ref={startDateRef}
            className="mt-0.5 w-full"
            value={startDate}
            onChange={setStartDate}
            onComplete={() => endDateRef.current?.focusYear()}
          />
        </label>
        <label className="flex-1 flex flex-col min-w-[170px]">
          <span className="text-slate-500 mb-1">结束日期</span>
          <DateInput ref={endDateRef} className="mt-0.5 w-full" value={endDate} onChange={setEndDate} />
        </label>
        <label className="flex-1 flex flex-col min-w-[120px]">
          <span className="text-slate-500 mb-1">预测周期(天)</span>
          <input
            type="text"
            inputMode="numeric"
            placeholder="如 2 / 1,2,3,5 / 1:5:20 / 1:2:7,60"
            title={`${HORIZON_SYNTAX}\n\n${horizonHint(labelHorizonText)}`}
            className="border rounded px-2 py-1"
            value={labelHorizonText}
            onChange={(e) => setLabelHorizonText(e.target.value)}
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
        {/* 按钮容器：宽度补偿面板 p-3+border 相对表单 p-6 的 13px×2 偏移，
            使按钮左端与上方表单第 4 列（"单因子测试/收起单因子测试"按钮列）对齐，
            右端保持在面板内容右缘不动。 */}
        <div className="flex items-end md:w-[calc((100%-3rem)/4-6.5px)]">
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
              // ★ v2.4.3 / v2.5.0：**任一**离场因子与某个进场因子同公式 ⇒ **直接拦**
              //   （用户定的"直接拦"✓；后端也会 400 兜底 ✓ —— 这里只是别让你白等 ✓）
              disabled={[...exitKeys].some((k) => selected.has(k))}
              title={[...exitKeys].some((k) => selected.has(k))
                ? '有离场因子与已勾选的进场因子是同一个公式 ✗ ⇒ 请换一个公式（同日进+出会永远开不了仓）'
                : ''}
              className="w-full px-3 py-1.5 rounded bg-blue-600 text-white text-xs disabled:opacity-50 disabled:cursor-not-allowed"
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
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="成交日(T+1)处于 ST/*ST/退市整理 的样本剔除（用 T+1 当日状态判定，日截面、无未来函数；涨停判定已直接使用交易所标签，覆盖 ST 5% 涨跌停）"
        >
          <input type="checkbox" checked={excludeStT1} onChange={(e) => setExcludeStT1(e.target.checked)} />
          <span>剔除ST(T+1)</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="剔除创业板（SZ30 号段，20% 涨跌幅）；板块归属恒定，无未来函数"
        >
          <input type="checkbox" checked={excludeGem} onChange={(e) => setExcludeGem(e.target.checked)} />
          <span>剔除创业板</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="剔除科创板（SH688，20% 涨跌幅）；板块归属恒定，无未来函数"
        >
          <input type="checkbox" checked={excludeKcb} onChange={(e) => setExcludeKcb(e.target.checked)} />
          <span>剔除科创板</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="信号计算的停牌行语义：勾选=删除停牌日（益盟/通达信『无停牌行』，默认）；取消=停牌日保留为 NaN（qlib 官方 / 聚宽 notebook 口径）。只影响因子信号计算，不影响上方样本剔除；与聚宽对账时取消勾选"
        >
          <input
            type="checkbox"
            checked={suspendRemove}
            onChange={(e) => setSuspendRemove(e.target.checked)}
          />
          <span>停牌删行</span>
        </label>
        <label
          className="flex items-center gap-1 cursor-pointer"
          title="价格整分：真实价按分取整（ROUND 2 位）参与因子计算。A 股价格本为整分，可消除『后复权价÷复权因子』还原的浮点尾差在指标阈值（如 5/15）边界导致的触发误判，与益盟/聚宽（整分原始价）口径一致。仅『不复权』模式下生效"
        >
          <input
            type="checkbox"
            checked={priceRound}
            onChange={(e) => setPriceRound(e.target.checked)}
          />
          <span>价格整分</span>
        </label>
      </div>

      {/* 预热缓冲（v1.18.6）：长回看/动态窗口公式在评估区间首日的收敛保障 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-3">
        {/* ★ v2.1.24（B ③）：**成交时点** —— 用户 2026-10-11 指定放在「预热缓冲」**左边** ✓。
            口径：`t1_open` = T 日收盘出信号、**T+1 开盘**成交（与「交易信号测试」面板统一 ✓）；
            `t1_close` = 次日收盘（老口径 ✓ 默认，保证既有数字不变 ✓）。
            ⚠ 改它会影响持仓期曲线的每期收益与年化/回撤/夏普 ✓，且会写进导出文件、回灌时自动对齐 ✓。 */}
        <label
          className="flex items-center gap-1"
          title="成交时点：持仓期曲线的『每期买入价 + 期内逐日盯市价』都用它。次日开盘 = T 日收盘出信号、T+1 开盘成交（与交易信号测试统一）；次日收盘 = 老口径。⚠ 改它会改变曲线的年化/回撤等指标，也会写进导出文件并影响回灌对齐"
        >
          <span className="text-slate-500">成交时点：</span>
          <select
            className="border rounded px-2 py-0.5 text-xs"
            value={fill}
            onChange={(e) => setFill(e.target.value)}
          >
            <option value="t1_open">次日开盘价</option>
            <option value="t1_close">次日收盘价</option>
          </select>
        </label>
        {/* ★★ T1（v2.8.6，用户 2026-10-16 拍板 ✓）：**涨停判定口径** ——
            ① 位置：紧挨「成交时点」**右边** ✓（用户指定 ✓）；
            ② 宽度：**只到"涨停判定口径"这几个字** ✓（`width: 7.5em` 一行可调 ✓）；
            ③ **仅当成交时点＝次日开盘价时可用** ✓，否则置灰无效 ✗（收盘成交没有"开盘一字板"概念 ✓；
               后端也会自动退回老口径并把生效值写进 `bt.diag["limit_judge"]` ✓ 不静默分叉 ✗）；
            ④ 文案（用户指定 ✓）：`涨停判定口径：` + `保守口径` / `只拒一字板` ✓。 */}
        <label
          className="flex items-center gap-1"
          title="涨停判定口径（仅当成交时点＝次日开盘价时可选）：保守口径 = 成交日『收盘涨停』也买不进（老口径、更保守；注意：开盘成交时它用了当日收盘信息，属未来信息）；只拒一字板 = 只拒『开盘一字涨停 / 停牌』，更贴实盘（通常收益更高）。⚠ 该口径会写进导出文件，回灌时自动对齐"
        >
          <span className={fill === 't1_open' ? 'text-slate-500' : 'text-slate-300'}>
            涨停判定口径：
          </span>
          <select
            className="border rounded px-2 py-0.5 text-xs disabled:bg-slate-100 disabled:text-slate-400 dark:disabled:bg-slate-800"
            style={{ width: '10em' }}
            value={limitJudge}
            disabled={fill !== 't1_open'}
            onChange={(e) => setLimitJudge(e.target.value)}
          >
            <option value="legacy">保守口径</option>
            <option value="open_only">只拒一字板</option>
          </select>
        </label>
        {/* ★ v2.1.27：**每期全仓滚动（实盘口径 · 逐点对拍专用）** —— 用户 2026-10-11 指定：
            ① 位置：在「成交时点」**右边**、「预热缓冲」**左边** ✓；
            ② 悬停注明"速度会慢很多 + 只为了逐点对拍用" ✓；
            ③ **默认不开** ✓。
            用途：面板那条持仓曲线是**组合收益模型**（无资金约束/整手/涨跌停 ✗），
            拖回「交易信号测试」跑的是**真金白银引擎** ⇒ 两者本该不同 ✗；打开本开关后
            后端会用**同一个** `run_period_roll` 再跑一条实盘口径净值 ⇒ 那条才该与回灌**逐位一致** ✓。 */}
        <label
          className="flex items-center gap-1"
          title="每期全仓滚动（实盘口径 · 只为了逐点对拍用）。⚠ 费率固定为『往返 0.004』（= 单边 0.002，与「交易信号测试」默认一致；对应持仓曲线三档里的『往返 0.004』档），另按引擎规则计『最低佣金 5 元/笔』、整手 100 股（科创板 200 股）、涨停买不进/跌停卖不掉顺延：打开后，后端会用『真金白银』回测再算一条净值 —— 在每个调仓日把当时的全部权益按当期名单等权重建（含初始资金约束、整手 100 股、涨跌停买不进/卖不掉顺延）。它才是『交易信号测试』里拖入导出文件后那条 period_roll 曲线的同口径版本，两边应当逐位一致。⚠ 速度会慢很多：每个档位要多取一次全池行情面板并逐日回测（全 A 约 10~20 秒/档）；它不参与任何统计口径与判定，只看净值。默认不开"
        >
          <input
            type="checkbox"
            checked={periodRoll}
            onChange={(e) => setPeriodRoll(e.target.checked)}
          />
          <span className="text-slate-500">每期全仓滚动（逐点对拍）</span>
        </label>
        <label
          className="flex items-center gap-1"
          title="特征加载时多前移 N 个交易日（预热缓冲），使 DYN_*/BARSCOUNT/HHVBARS+Ref 嵌套等『扩展天数无法静态推断』的长回看/动态窗口公式在区间首日即有收敛后的因子值。0=关闭（回到旧口径，与 qlib D.features 冷启动逐位对齐）；留空=后端默认 250 交易日（≈1年）。固定窗口公式（MA/REF 等）结果不受影响，出口仍只按评估区间统计。代价：加载耗时/内存随 N 近似线性增加"
        >
          <span className="text-slate-500">预热缓冲：</span>
          <input
            type="text"
            inputMode="numeric"
            className="border rounded px-2 py-0.5 w-16 text-center"
            value={warmupDaysText}
            onChange={(e) => setWarmupDaysText(e.target.value)}
          />
          <span className="text-slate-500">交易日（0=关闭，留空=默认250）</span>
        </label>
      </div>

      {/* 持仓期曲线「固定 K 档」（v1.18.45，2026-09-12 用户定稿改为预设列表）：
          不勾 = 只算默认档（最强分位组 + 10% 固定档），零额外开销；勾选的档随本次测试一起算出
          ⇒ 弹窗里纯前端切换这些档（不用重跑）。每个勾选的 K **实测约 +0.05s**（每个预测周期各
          算一次；`ai_test/probe_topk_cost.py`：全A/h=20 下 1 档 3.50s → 10 档 3.91s，8 个档
          合计 +0.42s）。⚠ 之所以"只选 1 档"和"全选"感觉差不多：**特征加载（~8.5s）、分位统计、
          K 敏感度表都与 topk_list 无关**（敏感度表始终全量计算）⇒ 固定开销占绝大部分。 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-3">
        <span
          className="text-slate-500"
          title="仅对连续因子有效：持仓期曲线里可切换的「固定 K 档」。不勾 = 只算默认档（超额收益最强的分位组 + 10% 固定档），不增加耗时。整数 = 绝对只数（1 = 只买名次最好的 1 只）；10%/20% = 日均有效只数的百分比。每个勾选的 K 实测约 +0.05s（每个预测周期各算一次）；因特征加载、分位统计与 K 敏感度汇总表（始终全量计算）都与勾选无关，加档对总耗时影响很小（全选 10 档通常只比单档慢 1~2 秒）"
        >
          固定 K 档（持仓期曲线）：
        </span>
        {K_PRESETS.map((k) => {
          const on = detailKs.includes(k)
          return (
            <button
              key={k}
              type="button"
              onClick={() => toggleDetailK(k)}
              title={
                k < 1
                  ? `${kLabel(k)} = 日均有效只数的 ${kLabel(k)}（随股票池变化）`
                  : `${k} 只（按因子名次取前 ${k} 名）`
              }
              className={`px-1.5 py-0.5 rounded border text-[11px] ${
                on
                  ? 'border-emerald-500 text-emerald-600 bg-emerald-50 dark:bg-emerald-900/30'
                  : 'border-slate-300 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700/40'
              }`}
            >
              {kLabel(k)}
            </button>
          )
        })}
        {/* v1.18.64：全选 / 清空（用户要求）；v1.18.65 起**紧跟最后一个档位按钮（20%）**，
            与 10 个档位同属一个视觉组（其后才是"已选 N 档"计数）⇒ 符合"先选档、后看统计"
            的阅读顺序。勾满 10 档 ≈ 每档 0.05s × 预测周期数（4 个周期约 +2s，相对特征加载等固定开销很小），故 title
            给出耗时提示；已全选时按钮变「清空」可一键还原。用**虚线边框**与左侧实线档位按钮
            区分，避免被误读成第 11 个档位。 */}
        <button
          type="button"
          onClick={() => setDetailKs(allPresetsOn ? [] : [...K_PRESETS])}
          title={
            allPresetsOn
              ? '清空已选档位（回到只算默认档：最强分位组 + 10%）'
              : `全选 ${K_PRESETS.length} 个预设档。每档约 +0.05s（每个预测周期各算一次）；相对特征加载/分位统计等固定开销很小，勾满通常只慢 1~2 秒`
          }
          className={`px-1.5 py-0.5 rounded border border-dashed text-[11px] ${
            allPresetsOn
              ? 'border-slate-300 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700/40'
              : 'border-emerald-400 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/30'
          }`}
        >
          {allPresetsOn ? '清空' : '全选'}
        </button>
        <span className="text-slate-400">
          {detailKs.length
            ? `已选 ${detailKs.length} 档`
            : '（不勾 = 只算默认档：最强分位组 + 10%）'}
        </span>
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
          {/* ★ v2.4.2：百分比**与文案一起显示**（以前只在没有文案时才显示百分比 ✗）+
              已用时间 —— 「每期全仓滚动（实盘口径）」这种"统计完成后还要跑十几秒~分钟"的阶段
              能一眼看出在推进而不是卡死 ✓（后端把该阶段报成 95→99.5% ✓）。 */}
          <p className="mt-1 text-slate-500 text-[11px]">
            <span className="font-mono">{Math.round(progress)}%</span>
            {' ｜ '}
            {progressMsg || '进行中…'}
            {SHOW_ELAPSED && startedAtRef.current > 0 && (
              <span className="ml-1 text-slate-400">
                （已用 {Math.max(0, Math.round((Date.now() - startedAtRef.current) / 1000))} 秒）
              </span>
            )}
          </p>
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
                      title={it.original ?? it.expression}
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

      {/* ★★ v2.4.3 / v2.5.0（用户 2026-10-15 拍板 ✓）：**离场因子**（卖出侧 · **多选** ✓）
          口径：因子值 > 阈值（默认 0）⇒ T+1 按「成交时点」卖出 ✓；
          结果 = 矩阵：`进场 × (1 + 离场数)` 条同源同口径回测 ✓（"不离场"对照必算 ✓）；
          同公式 ⇒ 拦 ✓；触发率 > 50% ⇒ 黄字；另给**离场事件研究**（触发后 h 日 vs 未触发 ✓）。 */}
      {(() => {
        const items = (groups.find((g) => g.source === 'custom')?.items ?? [])
        const picked = items.filter((it) => exitKeys.has(it.key))
        const dupItem = picked.find((it) => selected.has(it.key))
        // 事件研究只展示**首个预测周期**那一行（表里看趋势足够 ✓；要多周期可看结果表 ✓）
        const effOf = (name: string) => {
          const hit = exitsInfo.find((e) => (e.name || '') === name)
          const eff = hit?.effect
          if (!eff) return null
          const ks = Object.keys(eff)
          return ks.length ? eff[ks[0]] : null
        }
        return (
          <div className="mb-3 border rounded p-2 bg-white dark:bg-slate-800">
            {/* ★ v2.5.1（用户 2026-10-15："改成勾选再显示面板吧，不然一大堆总是显示看着碍眼" ✓）：
                **默认收起**，勾上才展开标签面板 ✓（不勾 = 完全不碰离场 ⇒ 只跑现在的对照 ✓）。 */}
            <label className="flex items-center gap-2 cursor-pointer flex-wrap">
              <input
                type="checkbox"
                checked={exitOn}
                onChange={(e) => {
                  const on = e.target.checked
                  setExitOn(on)
                  if (!on) setExitKeys(new Set())     // 关掉 ⇒ 清空（免得"看不见却在生效" ✗）
                  // ★★ v2.8.8（用户 2026-10-16 ✓）：**勾上离场 ⇒ 自动把「每期全仓滚动（逐点对拍）」也勾上** ✗→✓
                  //   为什么必须联动：离场信号**只作用在"真金白银"那条回测上** ✓（见 api.ts 的说明 ✓）——
                  //   后端无论如何都会为它跑 `period_roll` ✓（请求里也一直显式传 ✓）。但**勾选框没同步** ✗
                  //   ⇒ 用户会看到"我没勾它，进度条/结果里却处处是它" ✗（2026-10-16 报障原话 ✓）。
                  //   ⇒ 勾上离场时**顺手把那个勾也打上** ✓，让界面与事实一致 ✓。
                  //   ⚠ 反方向**不联动**（关离场不取消它 ✓——用户可能是自己特意开的 ✓）。
                  if (on) setPeriodRoll(true)
                }}
              />
              <span className="font-semibold text-slate-600 dark:text-slate-300">
                启用离场因子测试（卖出侧 · 可多选 ⇒ 出矩阵）
              </span>
              <span className={exitOn ? 'text-slate-500 text-[11px]' : 'text-slate-400 text-[11px]'}>
                {exitOn
                  ? `已选 ${picked.length} 个 ⇒ 每个进场因子会跑 ${1 + picked.length} 条同口径回测（1 条"不离场"对照 + ${picked.length} 条含离场）`
                  : '不勾 = 只跑现有回测（不含离场）；勾上后每个进场因子会多跑"对照 + 每个离场"各一条（⚠ 慢：离场只作用在真金白银那条回测上）'}
              </span>
            </label>
            {exitOn && (
              <>
                <div className="flex items-center gap-2 flex-wrap mt-1">
                  <label className="flex items-center gap-1 text-[11px] text-slate-500">
                    阈值 &gt;
                    <input
                      className="w-16 border rounded px-1 py-0.5 text-[11px]"
                      value={exitThresholdText}
                      onChange={(e) => setExitThresholdText(e.target.value)}
                      title="离场判据：因子值 > 该阈值 ⇒ 离场。默认 0 —— 二值信号（如 BS=2/-2 那类）与连续因子同一套口径；多个离场共用这一个阈值"
                    />
                  </label>
                  <span className="text-slate-400 text-[11px]">
                    共 {items.length} 条公式（列表内可滚动 ✓）；点标签加入 / 移出
                  </span>
                </div>
                {items.length > 0 && (
                  // ⚠ 几百条时必须有滚动条（用户 2026-10-15："几百个就要加滚动条" ✓）
                  <div className="mt-1 max-h-40 overflow-y-auto flex flex-wrap gap-1 pr-1 border border-slate-100 dark:border-slate-700 rounded p-1">
                    {items.map((it) => {
                      const on = exitKeys.has(it.key)
                      const isDup = on && selected.has(it.key)
                      return (
                        <button
                          key={it.key}
                          type="button"
                          onClick={() => toggleExit(it.key)}
                          title={isDup
                            ? '这个公式已经勾在「进场」里了 ✗ ⇒ 同进同出会永远开不了仓（净值恒 1）'
                            : '点一下加入/移出离场（因子值 > 阈值 ⇒ T+1 卖出）'}
                          className={`px-1.5 py-0.5 rounded border text-[11px] ${
                            isDup
                              ? 'bg-red-600 text-white border-red-600'
                              : on
                                ? 'bg-amber-500 text-white border-amber-500'
                                : 'border-slate-200 dark:border-slate-600 hover:border-amber-400'
                          }`}
                        >
                          {it.name}
                        </button>
                      )
                    })}
                  </div>
                )}
                {items.length === 0 && (
                  <p className="mt-1 text-slate-400 text-[11px]">还没有自定义公式 ⇒ 先去「公式库」存一条 ✓</p>
                )}
                {dupItem && (
                  <p className="mt-1 text-red-500 text-[11px]">
                    「{dupItem.name}」既是进场又是离场 ✗ ⇒ 同日进+出时按「不交易」规则会永远开不了仓
                    （净值恒 1 一条线）⇒ 请把它从一侧去掉 ✓
                  </p>
                )}
                {picked.filter((it) => it.key !== dupItem?.key).map((it) => {
                  const info = exitsInfo.find((e) => (e.name || '') === it.name)
                  const st = info?.stats
                  const rate = st?.rate ?? 0
                  const eff = effOf(it.name)
                  return (
                    <p key={it.key}
                       className={'mt-1 text-[11px] ' + (rate > 0.5 ? 'text-amber-600' : 'text-slate-500')}>
                      「{it.name}」触发率：{st
                        ? `${(rate * 100).toFixed(1)}%（${st.hit_cells ?? '-'}/${st.n_cells ?? '-'} 格，日均 ${st.avg_per_day ?? '-'} 只）`
                        : info?.entry_dependent
                          ? '不适用（含入场价 ⇒ 回测里逐日算 ✓，看对比表的离场卖出/顺延）'
                          : '跑完后显示'}
                      {rate > 0.5 ? '　⚠ 触发过半 ⇒ 近似「只持有 1 天」，请先确认口径 ✓' : ''}
                      {eff
                        ? `　｜ 离场事件研究（${eff.horizon ?? '-'} 日）：触发后 ${((eff.mean_trig ?? 0) * 100).toFixed(2)}% vs 未触发 ${((eff.mean_ctrl ?? 0) * 100).toFixed(2)}%（差 ${((eff.diff_mean ?? 0) * 100).toFixed(2)}pp，样本 ${eff.n_trig ?? '-'}）`
                        : ''}
                    </p>
                  )
                })}
              </>
            )}
          </div>
        )
      })()}

      {error && <p className="mb-2 text-red-500 text-[11px] break-all">{error}</p>}

      {/* 取消提示 */}
      {!running && !error && progressMsg === '已取消' && (
        <p className="mb-2 text-amber-600 text-[11px]">测试已取消，未生成结果。</p>
      )}

      {/* 清理入口：展示保留到用户主动清理（收起/展开不丢结果）；清理后才清空展示并释放后端内存 */}
      {results.length > 0 && (
        <div className="flex items-center justify-end gap-2 mb-2">
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            title="导出当前展示结果到 Excel（.xlsx，纯前端生成）：工作表1「因子指标」含每行指标与结论；工作表2「因子与公式」为各因子与其保存公式"
            className="px-2 py-1 rounded border text-[11px] text-blue-600 border-blue-300 hover:bg-blue-50 dark:text-blue-400 dark:border-blue-700 dark:hover:bg-blue-950 disabled:opacity-50"
          >
            {exporting ? '导出中...' : '导出结果'}
          </button>
          <button
            type="button"
            onClick={handleClear}
            disabled={clearing}
            title="删除后端已完成的单因子测试任务与结果（释放进程内存），并清空下方本次展示；运行中的任务不受影响"
            className="px-2 py-1 rounded border text-[11px] text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-50"
          >
            {clearing ? '清理中...' : '清理结果（释放内存）'}
          </button>
        </div>
      )}

      {/* 结果表格 */}
      {results.length > 0 && (
        <div className="overflow-x-auto">
          {/* ★★ v2.8.9（用户 2026-10-16 ✓）：**表改成 `table-fixed`** ✗→✓ —— **这才是"右边空一片"的根因** ✓。
              用户原话：「是不是你**表格方式**不对啊，根因没找到啊，你看前面的周期、覆盖率、信号那几列就挨
              那么近，但是 **IC 后面这几列就空那么多**」✓（判断完全正确 ✓）。
              · 旧做法（`table-layout: auto` ✗）里，单元格上的 `width` 只是**建议** ✓ —— 浏览器算完各列内容宽度后
                还会把**剩余宽度按比例再分配** ✗ ⇒ 我给 IC/RankIC/ICIR/结论/中位数/因子研究 定的宽**全被撑开** ✓；
              · 而左半边（周期/覆盖率/信号/触发数 ✓）内容本来就小 ⇒ 分到的余量少 ⇒ 看着"挨得紧" ✓ ✓
                ⇒ 于是呈现"**左紧右松**"✓（正是用户看到的样子 ✓ —— 我前几轮加 `w-`/去内边距都没用 ✗，
                  因为方向上就错了 ✗）。
              · `table-fixed` 下 `width` 是**硬约束** ✓ ⇒ 定宽的列**就是那么宽** ✓，余量只分给**没定宽**的列 ✓
                ⇒ 尾部几列自然贴在一起 ✓。
              ⚠ 前提：**列宽主要由表头那一行的 `w-[]` 决定** ✓（fixed 模式只看第一行 ✓）⇒ 以后要改列宽，
                改表头即可 ✓；⚠ 内容超宽不会自动撑开 ✗ ⇒ 已有的 `whitespace-nowrap`/换行要自己保证 ✓。 */}
          {/* ⚠★ 同一条改动里两个前提，缺一不可 ✓：
              ① `table-fixed` ⇒ 定宽是硬约束 ✓；
              ② **去掉 `w-full`** ✗→✓ —— 否则表格仍被强行拉到容器 100% ✓ ⇒ 余量还是会被摊给各列 ✗
                 （`table-fixed` 只保证"按比例分配"是确定的，不保证"不分配" ✗）。
                 去掉后：表宽 = 各列宽度之和 ✓ ⇒ 定宽的列**就是那么宽** ✓、没定宽的按内容 ✓
                 ⇒ 尾部自然贴紧 ✓；若总和超过容器 ⇒ 外层 `overflow-x-auto` 横向滚动 ✓（不会挤压 ✓）。 */}
          {/* ⚠★ 用**内联样式**写 `table-layout: fixed` ✗→✓（不再写 Tailwind 的 `table-fixed` 类 ✓）——
              用户 2026-10-16 反馈"改成 colgroup 后没变" ✓ ⇒ 最可能是**那个类没被 Tailwind 生成** ✗
              （一旦类不存在 ⇒ 仍是 `auto` 布局 ⇒ `colgroup` 的宽度只被当"建议" ⇒ 余量继续按比例摊开 ✗
                = 用户看到的"没变" ✓）。改内联后**不依赖 CSS 生成管线** ✓，这一环不可能再失效 ✓。 */}
          {/* ⚠★ 三个前提必须同时成立，缺一个就"没变" ✗（这是本问题绕了 6 轮的真正原因 ✓）：
              ① `table-layout: fixed`（内联 ✓ 不依赖 Tailwind ✓）；
              ② **表宽必须钉成"各列之和"** ✗→✓ —— `fixed` 但 `width:auto` 时浏览器**仍会把表拉到容器宽**
                 再**按比例放大各列** ✗ ⇒ 实测出来正好 ≈ 我设的宽 ×1.15（与用户截图完全一致 ✓）；
              ③ 去掉 `w-full` ✓（要的就是"不再填满" ✓）。
              ⇒ `width: 'max-content'` = 表宽取各列宽之和 ✓（配 colgroup ⇒ 每列**就是**那么宽 ✓）；
                总宽超过容器时由外层 `overflow-x-auto` 横向滚动 ✓（不挤压 ✓）。
              ⚠ 以后调列宽：**只改下面的 `<colgroup>`** ✓（表头里的 `w-[]` 已冗余 ✓）。 */}
          {/* ⚠★ 最终口径（用户 2026-10-16 ✓，三轮试错后定稿 ✓）：
              · `tableLayout: 'fixed'` ⇒ 列宽**由 `colgroup` 定**（不被内容顶开 ✓、也不被浏览器重新分配 ✗）；
              · `width: '100%'` ⇒ 表格**按比例填满容器** ✓ —— 这才是"整体都显示得下"的正确解 ✓：
                  用 `max-content`（上一轮 ✗）会让总宽 = 各列之和 ≈1250px ⇒ 在 ~1100px 的窗口里**右边三列被截** ✗
                  （用户反馈"更宽了" ✓ 正是这个 ✓）。
              · `minWidth: '55rem'`（★ 关键 ✓）—— 用户 2026-10-16 反馈"还不够、**差太多**"✗，
                根因是上一轮把 minWidth 设成 **68rem(1088px)** ✗ > 他的内容区（≈950px）⇒
                **表格被强行撑到 1088px** ⇒ 右边几列被截 + 底部横向滚动条 ✓✓（他截图里那条滚动条就是它 ✓）。
                ⇒ 降到 **55rem(880px)**：只要窗口比它宽（几乎总是 ✓）⇒ 表格就**正好等于容器宽** ✓
                ⇒ **不会截断、不会出现横向滚动** ✓。
              ⇒ 效果：列宽 = 容器宽 × `colgroup` 的**比例** ✓（尾部依旧贴紧 ✓，整体一屏显示 ✓）。
              ⚠ 调"比例"只改 `<colgroup>` ✓（表头 `w-[]` 已冗余 ✓）。 */}
          <table className="text-[11px] border-collapse"
                 style={{ tableLayout: 'fixed', width: '100%', minWidth: '55rem' }}>
            {/* ★★ v2.8.9：**`<colgroup>` 把所有列宽一次钉死** ✗→✓ ——
                为什么必须这样：`table-fixed` 下**没定宽的列会按第一行（表头）的内容算** ✗ ⇒ 表头只有 2~3 个字
                ⇒ 那些列会**太窄**、把下面的数字挤掉 ✗（例如「触发数」列要放 `4699714` ✓）。
                ⇒ 这里按"数据实际需要"给全 19 列定宽 ✓（含 `{SHOW_TIMING_COL && <col/>}` 的耗时列 ✓，
                  开启/关闭都不会错位 ✓）；宽度之和 ≈ 76rem（≈1220px ✓）⇒ 常规 1400px 屏**一屏放得下** ✓，
                  更窄的屏走外层 `overflow-x-auto` 横向滚动 ✓（不再挤压 ✓）。
                ⚠ 改列宽**只改这一处** ✓（表头上的 `w-[]` 现在是冗余的 ✓，别两处改出分歧 ✗）。 */}
            <colgroup>
              {/* ★★★ v2.8.9 关键修正（用户 2026-10-16 "还差很多" ✓）：**列宽合计必须 ≤ 容器宽** ✗→✓ ——
                  CSS 规定：`table-layout: fixed` 下**表格宽度不能小于各列宽之和** ✗ ⇒ 之前合计 ≈70.75rem(1132px)
                  ⇒ 表格**硬是 1132px** ⇒ 用户 ~950px 的内容区必然"右列被截 + 底部横向滚动条" ✓✓
                  （前几轮我一直在调 `width`/`minWidth`/内边距 ✗，方向全错 ✗ —— 真约束是**这个和** ✓）。
                  ⇒ 现在按"内容刚好放得下"逐列收紧，合计 **59.5rem ≈ 952px** ✓（与用户内容区相当 ✓）。
                  ⚠ 以后加列/加宽，**必须同时核这个和** ✓，否则又会撑出滚动条 ✓。 */}
              <col style={{ width: '6rem' }} />      {/* 因子（长名走 `truncate` ✓ 悬停看全 ✓） */}
              <col style={{ width: '2.25rem' }} />   {/* 周期（`60 天` ✓） */}
              {SHOW_TIMING_COL && <col style={{ width: '2.75rem' }} />}{/* 耗时（默认关 ✓） */}
              <col style={{ width: '2.5rem' }} />    {/* 覆盖率（`100.00` ✓） */}
              <col style={{ width: '2.25rem' }} />   {/* 信号（`连续`/`0/1` ✓） */}
              <col style={{ width: '3rem' }} />      {/* 触发数（`875292` ✓） */}
              <col style={{ width: '2.5rem' }} />    {/* 触发收益 */}
              <col style={{ width: '2.5rem' }} />    {/* 首次收益 */}
              <col style={{ width: '2.5rem' }} />    {/* 续期收益 */}
              <col style={{ width: '3rem' }} />      {/* 未触发数（`4699714` ✓） */}
              <col style={{ width: '2.75rem' }} />   {/* 未触发收益 */}
              {/* ★ v2.8.9（用户 2026-10-16 ✓）：差值列 **6.5rem → 8.5rem** ✗→✓ ——
                  用户原话：「刚才我看到很挤的一版其实还可以，**要解决差值列叠在一起的问题**就好了」✓。
                  原因：这格是**两行**（上行 `6.864` ✓、下行小字 `日4.817 · t=6.4 · 日胜78.9%` ✓），
                  下行 ≈110px ✗ > 6.5rem(104px) ⇒ 而且它是 `whitespace-nowrap` ✓ ⇒ **压到/叠到隔壁列上** ✓
                  ⇒ 给足宽度把整行放下 ✓（本列现为 **7.5rem(120px)** ✓ —— 表宽 `width:100%` 时列宽会**按比例**
                  缩放 ✓，所以留的是"比例" ✓；其余列按各自数据量核过：触发数 3.25rem 放得下
                  `875292` ✓、未触发数 4rem 放得下 `4699714` ✓、结论 4.75rem 放得下「有效(反向)✓」✓）。 */}
              <col style={{ width: '3.5rem' }} />    {/* 差值（小字已改走悬停 ✓ ⇒ 只需放数字 ✓） */}
              <col style={{ width: '3rem' }} />      {/* p值（`<1e-300*` ✓） */}
              <col style={{ width: '2.75rem' }} />   {/* 分位收益（柱子 39px ✓） */}
              <col style={{ width: '2.75rem' }} />   {/* IC */}
              <col style={{ width: '3rem' }} />      {/* RankIC */}
              <col style={{ width: '2.75rem' }} />   {/* ICIR */}
              <col style={{ width: '4.5rem' }} />    {/* 结论（「有效(反向)✓」要把最长的放下 ✓） */}
              <col style={{ width: '2.75rem' }} />   {/* 中位数 */}
              <col style={{ width: '5.25rem' }} />   {/* 因子研究（「离场曲线」按钮 ≈62px ✓） */}
            </colgroup>
            <thead>
              <tr className="text-slate-500 border-b">
                <th className="text-center py-1 pr-2 whitespace-nowrap">因子</th>
                <th className="text-center px-1 whitespace-nowrap">周期</th>
                {SHOW_TIMING_COL && (
                  <th
                    className="text-center px-1 whitespace-nowrap"
                    title="阶段计时（秒）：显示「本因子统计+事件研究」耗时，悬停看三段明细。特征加载为全池共享（同一次测试各因子相同）；不含 HTTP 往返与任务排队"
                  >
                    耗时
                  </th>
                )}
                {/* ★★ v2.8.8c（用户 2026-10-16 ✓）：**表头换行受控** ✗→✓ ——
                    用户原话（逐项指定 ✓）：「覆盖、信号、触发数**写在一行**，触发收益**写两行**
                    （触发一行、收益一行），首次收益的首次一行、收益一行，续期收益的续期一行、收益一行，
                    未触发数写一行，未触发收益两行（未触发一行、收益一行），分位收益写两行（分位一行、
                    收益一行），中位数写一行，然后**因子研究跟中位数稍微空开点**」✓。
                    ⇒ 做法：**单行的加 `whitespace-nowrap`**（旧版 `覆盖率` 会被折成"覆盖/率" ✗）；
                      **双行的用 `<br/>` 强制断**（`nowrap` 只禁自动折行、**不会**影响显式 `<br/>` ✓，
                      所以两种写法互不干扰 ✓）。 */}
                {/* ★★ v2.8.8c（用户 2026-10-16 ✓）：**表头一律水平居中** ✗→✓ ——
                    用户原话：「标题要在每列格子里**水平居中**，未触发收益的**收益**要在"未触发"三个字
                    **居中**，不能跟"发"对齐」✓。
                    ⇒ 旧版是 `text-right` ✗ ⇒ 两行都贴右 ⇒ "收益"的右边缘顶在"未触发"的**末字**下面 ✗✓
                      （用户描述得完全准确 ✓）。改成 `text-center` 后 ✓：**每一行各自在本格里居中** ✓
                      ⇒ 第二行"收益"自然落在第一行"未触发"的**正下方** ✓。
                    ⚠ 只改**表头** ✓ —— 数据格保持各自的对齐（数字靠右 ✓、因子名/按钮靠左 ✓），
                      否则数字列会变得难扫读 ✗。 */}
                <th
                  className="text-center px-1 whitespace-nowrap"
                  title="覆盖率 = 因子非空行 ÷ CLOSE 有效行（v1.21.6 起）。即「在有行情的交易日里，该因子有多少比例有值」：分母与同桌测试的其它因子无关，可跨数据集直接对比（旧口径的分母是「面板字段并集行数」，会因同桌多测了带 fin_* 的因子而凭空变低）"
                >
                  覆盖率
                </th>
                <th className="text-center px-1 whitespace-nowrap">信号</th>
                <th className="text-center px-1 whitespace-nowrap">触发数</th>
                <th className="text-center px-1 whitespace-nowrap">
                  触发<br />收益
                </th>
                {/* ★ v2.1.31：**首次 / 续期内再触发** 分组（用户 2026-10-12："加上" ✓）——
                    两组收益常差很多（实测 `黄金坑启动`：首次 +4.956% vs 续期 +0.707% ✓），
                    而组合里同一只票只持一轮（≈ 首次口径 ✓）⇒ 不分列会被"样本均值"误导 ✓ */}
                <th
                  className="text-center px-1 whitespace-nowrap"
                  title="首次/新开仓触发：同一标的**上一次触发已不在持有期内**（间隔 > 持有期 N 个交易日）⇒ 组合里相当于持有一轮 ✓"
                >
                  首次<br />收益
                </th>
                <th
                  className="text-center px-1 whitespace-nowrap"
                  title="续期内再触发：同一标的**上一次触发还在持有期内**（间隔 ≤ N）⇒ 引擎语义是把持有天数【重置为 N 天】（不是加 N）✓。⚠ 这类样本在统计里**每次触发各算一个**，而组合里只持一轮 ⇒ 它与组合净值的差异主要来自这里 ✓"
                >
                  续期<br />收益
                </th>
                <th className="text-center px-1 whitespace-nowrap">未触发数</th>
                <th className="text-center px-1 whitespace-nowrap">
                  未触发<br />收益
                </th>
                <th className="text-center px-1 whitespace-nowrap">差值</th>
                <th className="text-center px-1 whitespace-nowrap">p值</th>
                <th className="text-center px-1 whitespace-nowrap">
                  分位<br />收益
                </th>
                {/* ★ v2.8.8c（用户 2026-10-16 ✓）：IC/RankIC/ICIR 也**定宽** ✗→✓ ——
                    用户原话：「**结论列离 ICIR 再近点**，中位数列离结论列也近点」✓。
                    为什么之前"靠不近"✗：表格是 `w-full` ✓ ⇒ 余量被浏览器摊给**没定宽的列** ✗
                    ⇒ ICIR 这类列被撑宽 ⇒ 它右侧就空出一段 ✓（看着像"结论离得远"✗ —— 其实空格在 ICIR 里 ✓）。
                    定宽取值按内容（数据是 4 位小数的原始数 ✓ 例 `0.355`/`0.0572` ✓）留够即可 ✓。 */}
                {/* ★★★ v2.8.9：**这里原来是 `w-[..] min-w-[..]`，现在必须删掉** ✗→✓ ——
                    用户 2026-10-16 "还差很多" ✓ 的真凶之一 ✓：`min-width` 是**硬性下限** ⇒ 它会**盖过
                    `colgroup` 设的宽度** ✗ ⇒ 各列最小宽之和把表格顶大 ⇒ 右列被截 ✓✓。
                    ⇒ 列宽**只有 `colgroup` 一个来源** ✓（这才是 `table-layout: fixed` 的正用法 ✓）。 */}
                <th className="text-center px-1 whitespace-nowrap">IC</th>
                <th className="text-center px-1 whitespace-nowrap">RankIC</th>
                <th className="text-center px-1 whitespace-nowrap">ICIR</th>
                {/* ★ v2.8.8c（用户 2026-10-16 ✓）：结论列**定个最小宽度** ✗→✓ ——
                    用户原话：「结论这列**稍微宽点**，让**彩票型**可以在一行放下。**有效后面的 √**
                    也可以一行写下」✓ ⇒ 旧版列太窄 ⇒ 「彩票型」被折成"彩票/型"、"有效✓"也被折 ✗。
                    `min-w-[5rem]`（80px ✓）按最长的那枚徽章「**有效(反向)✓**」留够 ✓，
                    单元格再加 `whitespace-nowrap` ✓ ⇒ 徽章永不折行 ✓。 */}
                {/* ★ v2.8.8c（用户 2026-10-16 ✓）：「**中位数列靠近结论一点**、**因子研究也往左一点**，
                    这样整体就能都显示下了」✓ ⇒ 把最后三列**宽度钉死** ✗→✓ ——
                    表格是 `w-full` ✓ ⇒ 多余宽度会被浏览器**摊给没有定宽的列** ✗ ⇒ 结论/中位数/因子研究
                    各自被撑开 ⇒ 看着"空得远" ✗。定宽之后 ✓：余量留给前面的数据列 ✓，
                    这三列**贴在一起** ✓、整表也更容易一屏放下 ✓。 */}
                {/* ★★★ v2.8.9：`w-[]`/`min-w-[]` **全撤** ✓（列宽唯一来源 = `colgroup` ✓，见上 ✓） ——
                    它们曾把这两列撑到 4.75rem/3.25rem 的**硬下限** ✗ ⇒ 是"还差很多"的一部分 ✓。 */}
                <th className="text-center px-0 whitespace-nowrap">结论</th>
                <th
                  className="text-center px-0 whitespace-nowrap"
                  title="事件研究：持有 = 该行「周期」时的收益中位数（仅 0/1 信号有值；悬停可看均值/绝对收益胜率/样本数）"
                >
                  中位数
                </th>
                <th
                  /* ★★ v2.8.8b（用户 2026-10-16 看图第二轮 ✓）：**给这一列定死宽度** ✗→✓ ——
                    用户原话：「然后这一堆按钮都没对齐啊」✓。
                    原因：本表是**自动布局**✗ ⇒ 变体行用了 `colSpan`（整行打通 ✓）之后，浏览器会把
                    各列宽度重新分配 ✗ ⇒ 基础行的按钮与变体行的按钮**横坐标漂移** ✓。
                    ⇒ 在表头把这一列钉成 `7.5rem`（并给同列的 td 也写同样的宽度 ✓，双保险 ✓），
                    再配合 `align-top` + 变体行的**等高占位**（见下 ✓）⇒ 所有「离场曲线」按钮
                    左边缘和中线都落在同一竖线上 ✓。
                    ⚠ 改宽度要**表头 + 两个 td 一起改** ✗（只改一处会被自动布局再分配掉 ✓）。
                    ★ v2.8.8c（用户 2026-10-16 ✓）：再加 `pl-3` ⇒ 与「中位数」那列**稍微空开点** ✓
                    （同列的 td 也一起加 ✓，否则表头与单元格左边缘不一致 ✗）。 */
                  className="text-center pl-2 pr-1 whitespace-nowrap"
                  title="因子研究（两块，按因子类型自动出现一个）：0/1 二值信号 → 事件研究（触发对齐 T=0 的收益分布）；连续因子 → 持仓期曲线（净值曲线 + 十分位曲线 + 成本敏感度表 + 可切换基准）"
                >
                  因子研究
                </th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                // 展示用的公式：优先用户原文（source_formula 随结果回传，最可靠）；
                // 没有原文时回退到本地按 (source,id) 查询；都没有才用 expression（兜底）。
                // 只在 hover 公式名时显示——因子列底下不再常驻公式行。
                const hoverFormula =
                  r.source_formula || srcByKey.get(`${r.source}:${r.id}`) || r.expression
                // ★★ v2.8.0（用户 2026-10-15："我期望的是把 3 组回测都在列表里呈现出来，
                //   因子列显示：X、X+入场价止损、X+首次入场止损，然后三个按钮" ✓）：
                //   把**含离场的那些变体**也展开成独立行 ✓（每行一个「离场曲线」按钮 ✓，
                //   点进去只看这条变体自己的净值 ✓）。数字取**默认档**（items[0] ✓，与表格
                //   其它列同档 ✓；要切换档位看曲线 ⇒ 进「持仓曲线」弹窗 ✓）。
                const basePerfEl = r.period_roll_items?.[0]?.perf ?? r.period_roll?.perf ?? null
                const vRows = (r.exit_rolls ?? []).map((g, gi) => {
                  const one = ((g?.items ?? [])[0] ?? g?.first) || null
                  const perf = one?.perf ?? null
                  const st = (one?.stats ?? {}) as Record<string, number | undefined>
                  return {
                    gi,
                    name: g?.exit?.name || g?.exit?.id || `离场${gi + 1}`,
                    one, perf, st,
                    // ★★ v2.8.8b（用户 2026-10-16 ✓）：这一档**没算出来**时的原因 ✗→✓ ——
                    //   典型就是"0/1 信号 + 含入场价的离场"（事件驱动引擎没有入场价通道 ✓，
                    //   后端现在会明确回一条 error ✓）。没有它的话，下面按 `perf ?? 0` 算出来的
                    //   `Δ年化 +0.0pp` 会被误读成"离场没用"✗（用户原话："肯定也没算对" ✓）⇒ 必须显式标"未计算" ✓。
                    err: String(one?.error || ''),
                    dAnn: (perf?.annual_return ?? 0) - (basePerfEl?.annual_return ?? 0),
                    dDd: (perf?.max_drawdown ?? 0) - (basePerfEl?.max_drawdown ?? 0),
                    mode: String(one?.mode || 'period_roll'),
                    // ⚠ 两种口径的**计数名不同** ✗（v2.8.1 修：实测信号驱动那条 `exit_sells` 是空 ✗）：
                    //   · period_roll ⇒ `exit_sells` / `exit_deferrals`（离场专用计数器 ✓）；
                    //   · signal（事件驱动）⇒ 引擎给的是 `sells` / `sell_deferrals`
                    //     ⚠ 那是**含到期卖出**的总数 ✓ ⇒ 标签要写清"（含到期）"，不能冒充"离场卖出" ✗。
                    sells: Number(one?.mode === 'signal'
                      ? (st.sells ?? 0) : (st.exit_sells ?? 0)),
                    defers: Number(one?.mode === 'signal'
                      ? (st.sell_deferrals ?? 0) : (st.exit_deferrals ?? 0)),
                  }
                })
                // 变体行要用的列数（**实际渲染**的总列数 − 因子 − 周期 − 因子研究 ✓）——
                // 变体行只占 2 个格子（`colSpan` 打通的一块 + 行尾「因子研究」格 ✓）。
                //
                // ★★ v2.8.8b **修一个算错一列的 bug** ✗→✓（用户 2026-10-16 连续两次指出
                //    "按钮还是错位" ✓ —— 前两次我怀疑到列宽/刷新上去了 ✗，这次数出来是**算术**错 ✓）：
                //    · 渲染总列数**不是 20** ✗：字面 `<th>`/`<td>` 各有 20 个 ✓，但其中
                //      「耗时」列是 `{SHOW_TIMING_COL && …}` **条件渲染** ✓ ⇒ 关掉时：
                //      **表头 19 列、基础行 19 格** ✓（`SHOW_TIMING_COL=false` ✓）；
                //    · 而旧式子 `20 + (SHOW_TIMING_COL?1:0) - 3` ⇒ 17 ✗ ⇒ 变体行
                //      `colSpan=1+1+17=19` **再加行尾那格 = 20 列** ✗ ⇒ **比表头多一列** ✗
                //      ⇒ 浏览器把行尾那格排到**第 20 列**（表头只到 19 ✗）⇒ 变体行的
                //      「离场曲线」**整体右移一列** ✓✓（用户截图里正是一列宽的偏移 ✓）。
                //    · 权威口径：**「错误行」的 `colSpan={SHOW_TIMING_COL ? 20 : 19}`** ✓ 就是总列数 ✓
                //      ⇒ 基数取 **19** ✓。
                //   ⚠ 改列（增删 `<th>`）时这里与「错误行 colSpan」**必须一起改** ✗，
                //     且**只数实际渲染的列**（别把条件列算进去 ✗）。
                const MIDCOLS = 19 + (SHOW_TIMING_COL ? 1 : 0) - 3
                // 结论判定统一收敛到模块级 verdictOf（表格"结论"列与导出文件口径一致）
                // 渲染只需 dT（悬停提示）与结论类型；其余判定明细已在 verdictOf 内部消化
                const { dT, kind: verdictKind, watchReason } = verdictOf(r)
                const qr = r.quintile_ret ?? []
                const maxAbs = qr.length > 0 ? Math.max(...qr.map((g) => Math.abs(g.mean_ret))) : 0
                // 分位收益悬停：逐组展示日截面收益（一行一组），最后一行汇总配对日数。
                // 组数由后端 `quantiles` 参数决定（默认十分位 10 组），这里全部按实际组数渲染。
                const qn = qr.length
                const qrTip = qn
                  ? `每日横截面分${qn}组(1=最低值…${qn}=最高值)，日截面平均收益：\n` +
                    qr.map((g) => `第${g.quantile}组: ${(g.mean_ret * 100).toFixed(2)}%`).join('\n') +
                    `\n配对日数：${qr[0]?.n_days ?? '-'}`
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
                // 差值单元格悬停：配对日 ≥2 给完整检验详情；仅 1 个配对日说明"不足统计推断"；
                // 无配对日（两组无同日样本）明示不计算——避免可见区显示误导性的 0。
                // ★★ v2.8.9c（用户 2026-10-16 ✓）：**把两个口径都写进悬停** ✗→✓ ——
                //   用户原话：「这个**差值 6.864** 是啥？怎么 TAG 里**没有一个数跟它对上**？」✓
                //   根因：列里显示的是**样本级差值** ✓（后端 `diff = trig.mean − not_trig.mean` ✓，
                //     每次触发 / 每个未触发**各算一个样本** ⇒ **触发多的日子权重大** ✓）；
                //     而旧悬停**只写了日配对口径** ✗（`daily_diff` ✓ = 每天一个样本、只在"当天两组
                //     都有样本"的**配对日**上算 ✓）⇒ 6.864%（样本级）与 4.817%（日配对）当然对不上 ✓
                //     —— 两个都是对的，只是**口径不同** ✓（后端注释里早就记过同类困惑 ✓）。
                //   ⇒ 现在**第一行先说清列里那个数** ✓，第二段才是日配对详情 ✓（不再让人对着两个数猜 ✗）。
                const pairTitle = [
                  `「差值」${fmt(r.diff, 3)}%（**样本级**口径：触发组样本均值 − 未触发组样本均值；`
                  + '每次触发、每个未触发各算一个样本 ⇒ 触发多的日子权重更大）',
                  '',
                  dT != null
                    ? (() => {
                        const band = r.daily_n > 0 ? 1.96 / Math.sqrt(r.daily_n) : 0
                        const s1 = r.daily_acf1 != null ? Math.abs(r.daily_acf1) > band : null
                        const s5 = r.daily_acf5 != null ? Math.abs(r.daily_acf5) > band : null
                        return [
                          `另一口径 · 按日配对检验（${r.daily_n} 个交易日）：`,
                          `日差值均值 ${fmt(r.daily_diff, 3)}%（每天一个样本 ⇒ 与上面的样本级不同 ✓）`,
                          `HAC t=${fmtRaw(dT, 2)}（普通 t=${r.daily_t != null ? fmtRaw(r.daily_t, 2) : '-'}）`,
                          `相对收益胜率 ${fmt(r.daily_win, 2)}%（触发组日均 > 未触发组日配对的交易日占比）`,
                          `lag-1 自相关=${r.daily_acf1 ?? '-'}${s1 == null ? '' : s1 ? '（显著）' : '（不显著）'}`,
                          `lag-5 自相关=${r.daily_acf5 ?? '-'}${s5 == null ? '' : s5 ? '（显著）' : '（不显著）'}`,
                          `方差稳定性 后半/前半=${r.daily_var_ratio != null ? `${r.daily_var_ratio}x` : '-'}`,
                          `（HAC 修正自相关与异方差；lag 自相关置信带 ±${band.toFixed(3)}）`,
                        ].join('\n')
                      })()
                    : r.daily_diff != null
                      ? `按日配对检验：仅 ${r.daily_n} 个配对日，不足统计推断；日差 = 当日触发组−未触发组日截面均值差 ${fmt(r.daily_diff, 3)}%`
                      : '无配对日（触发组与非触发组无任何同日样本），不计算日配对差',
                ].join('\n')
                return (
                  <>
                  <tr key={`${r.id}:${r.horizon ?? '-'}`} className="border-b border-slate-100 dark:border-slate-700">
                    {r.error ? (
                      <td className="py-1 pr-2 text-red-500" colSpan={SHOW_TIMING_COL ? 20 : 19}>
                        {r.name}{r.horizon ? `（周期 ${r.horizon} 天）` : ''}：{r.error}
                      </td>
                    ) : (
                      <>
                        <td className="py-1 pr-2 truncate" title={hoverFormula}>
                          <span className="font-semibold">{r.name}</span>
                          <span className="text-slate-400 ml-1">[{r.source}]</span>
                        </td>
                        <td className="text-right px-1 whitespace-nowrap text-slate-500">
                          {r.horizon ? `${r.horizon} 天` : '-'}
                        </td>
                        {SHOW_TIMING_COL && (
                        <td
                          className="text-right px-1 whitespace-nowrap text-slate-400"
                          title={
                            r.timing
                              ? [
                                  `本因子统计+事件研究：${r.timing.item_s.toFixed(2)}s`,
                                  `特征加载（全池共享）：${r.timing.feature_s.toFixed(2)}s`,
                                  `qlib 初始化：${r.timing.init_s.toFixed(2)}s（仅首个任务有值，之后为 0）`,
                                  `合计：${r.timing.total_s.toFixed(2)}s`,
                                  '（不含 HTTP 往返、任务排队、股票池成分股解析与 label 表达式构建 —— 故小于前端看到的墙钟）',
                                ].join('\n')
                              : '无计时数据（后端 < v1.18.43，或该结果来自旧版缓存）'
                          }
                        >
                          {r.timing ? `${r.timing.item_s.toFixed(2)}s` : '-'}
                        </td>
                        )}
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
                                (r.trigger?.suspended_excluded ?? 0) +
                                (r.trigger?.extra_excluded ?? 0) >
                              0
                                ? `触发组已剔除（数字为剔除后样本数）：信号日T涨停 ${r.trigger?.limit_up_excluded_t} · 成交日T+1涨停 ${r.trigger?.limit_up_excluded_t1} · 成交日停牌 ${r.trigger?.suspended_excluded}${(r.trigger?.extra_excluded ?? 0) > 0 ? ` · ST/创/科剔除 ${r.trigger?.extra_excluded}` : ''}`
                                : undefined
                            }
                          >
                            {r.trigger?.count ?? '-'}
                          </span>
                        </td>
                        <td className="text-right px-1">{fmt(r.trigger?.mean_ret, 3)}</td>
                        {/* ★ v2.1.31：首次 / 续期内再触发（悬停给出条数 ✓；旧结果无该字段 ⇒ '-' ✓） */}
                        <td
                          className="text-right px-1"
                          title={r.trigger_split?.first
                            ? `首次/新开仓：${r.trigger_split.first.count ?? '-'} 条 ｜ 均值 ${fmt(r.trigger_split.first.mean_ret, 3)}% ｜ 中位 ${fmt(r.trigger_split.first.median_ret, 3)}% ｜ 胜率 ${fmt(r.trigger_split.first.win_rate, 1)}%`
                            : '旧结果没有该字段（重跑一次即可看到 ✓）'}
                        >
                          {fmt(r.trigger_split?.first?.mean_ret, 3)}
                        </td>
                        <td
                          className="text-right px-1"
                          title={r.trigger_split?.refresh
                            ? `续期内再触发：${r.trigger_split.refresh.count ?? '-'} 条 ｜ 均值 ${fmt(r.trigger_split.refresh.mean_ret, 3)}% ｜ 中位 ${fmt(r.trigger_split.refresh.median_ret, 3)}% ｜ 胜率 ${fmt(r.trigger_split.refresh.win_rate, 1)}% ｜ ⚠ 持有天数会被重置为 N 天 ✓`
                            : '旧结果没有该字段（重跑一次即可看到 ✓）'}
                        >
                          {fmt(r.trigger_split?.refresh?.mean_ret, 3)}
                        </td>
                        <td className="text-right px-1">
                          <span
                            title={
                              (r.not_trigger?.limit_up_excluded_t ?? 0) +
                                (r.not_trigger?.limit_up_excluded_t1 ?? 0) +
                                (r.not_trigger?.suspended_excluded ?? 0) +
                                (r.not_trigger?.extra_excluded ?? 0) >
                              0
                                ? `未触发组已剔除（与触发组同口径，数字为剔除后样本数）：信号日T涨停 ${r.not_trigger?.limit_up_excluded_t} · 成交日T+1涨停 ${r.not_trigger?.limit_up_excluded_t1} · 成交日停牌 ${r.not_trigger?.suspended_excluded}${(r.not_trigger?.extra_excluded ?? 0) > 0 ? ` · ST/创/科剔除 ${r.not_trigger?.extra_excluded}` : ''}`
                                : undefined
                            }
                          >
                            {r.not_trigger?.count ?? '-'}
                          </span>
                        </td>
                        <td className="text-right px-1">{fmt(r.not_trigger?.mean_ret, 3)}</td>
                        <td
                          /* ★ v2.8.8c（用户 2026-10-16 ✓）：**差值改为水平居中** ✗→✓ ——
                             用户原话：「底下那个 -0.185 也是水平居中」✓（表头已居中 ✓ ⇒ 数字跟着居中对上 ✓）。
                             ⚠ 它下面那行小字（日…·t=…·日胜…%）同格 ⇒ 一起居中 ✓（不拆开 ✓）。 */
                          className={`text-center px-1 font-semibold ${(r.diff ?? 0) >= 0 ? 'text-red-500' : 'text-emerald-600'}`}
                          title={pairTitle}
                        >
                          <div>{fmt(r.diff, 3)}</div>
                          {/* ★★ v2.8.9（用户 2026-10-16 ✓）：**把这一行小字从格子里撤掉** ✗→✓ ——
                              用户原话：「还不够，**差太多**」✓ —— 你截图底部有横向滚动条 ⇒ 内容区 ≈950px ✗，
                              而列宽合计 ≈1130px ⇒ 差 ~180px ✓。
                              这行小字（`日4.817 · t=6.4 · 日胜78.9%` ✓）实测要 ≈110px ✗ —— 是**最占宽度**的一块 ✓；
                              而这些数字**本来就在本格的悬停提示里**（`title={pairTitle}` ✓，见上 ✓）
                              ⇒ 撤掉文字**不丢任何信息** ✓，只省宽度 ✓（差值列 7.5rem → 3.5rem ✓）。
                              ⚠ 想恢复：把这段放回来即可 ✓（`dT`/`r.daily_diff`/`r.daily_win` 都还在 ✓）。 */}
                        </td>
                        <td className="text-right px-1">
                          {r.p_value === null ? '-' : fmtP(r.p_value)}
                        </td>
                        <td className="text-right px-1">
                          {/* ★★ v2.8.8b（用户 2026-10-16 看图第四轮 ✓）：**柱子搞密集、也细点** ✗→✓ ——
                              用户原话：「分位收益底下那个柱子太宽占格子太多了，给它搞密集一点，柱子也细点，
                              这样按钮就不会到外面去了，就都能显示得下了」✓。
                              实测旧口径：10 根柱 × 6px + 9 个间隙 × 3px = **87px** ✗（把「因子研究」列挤到
                              表格外面 ✓，按钮显示不全 ✓）。改为 **3px + 1px** ⇒ 10×3+9×1 = **39px** ✓
                              （省 ~48px ✓）；0/1 信号的双柱同理 6px→4px、gap 3px→1px ✓。
                              ⚠ 高度计算（`h-6` 容器 + `≈20px` 上限）与悬停提示都没动 ✓（只改宽度与间隙 ✓）。 */}
                          {r.is_binary && hasDaily && dMax > 0 ? (
                            // 0/1 信号：信号组 vs 非信号组的逐日截面收益均值双柱（悬停直接显示两组数值与配对日差）
                            <div className="inline-flex items-end gap-[1px] h-6 align-bottom">
                              <div
                                className={`w-[4px] ${dTrig >= 0 ? 'bg-emerald-500' : 'bg-red-400'}`}
                                style={{ height: `${Math.max(3, Math.round((Math.abs(dTrig) / dMax) * 20))}px` }}
                                title={dailyTip}
                              />
                              <div
                                className={`w-[4px] ${dNot >= 0 ? 'bg-emerald-500' : 'bg-red-400'}`}
                                style={{ height: `${Math.max(3, Math.round((Math.abs(dNot) / dMax) * 20))}px` }}
                                title={dailyTip}
                              />
                            </div>
                          ) : qr.length > 0 && maxAbs > 0 ? (
                            <div className="inline-flex items-end gap-[1px] h-6 align-bottom" title={qrTip}>
                              {qr.map((g) => {
                                const h = Math.max(3, Math.round((Math.abs(g.mean_ret) / maxAbs) * 20))
                                const c = g.mean_ret >= 0 ? 'bg-emerald-500' : 'bg-red-400'
                                return (
                                  <div
                                    key={g.quantile}
                                    className={`w-[3px] ${c}`}
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
                        {/* ★ v2.8.8c（用户 2026-10-16 ✓）：IC / RankIC / ICIR 的**数据格也水平居中** ✗→✓
                            （用户原话：「IC、rankic、ICIR 底下的数字也要水平居中」✓）——
                            表头已居中 ✓，数据再靠右 ⇒ 视觉上"头在一处、数在另一处"✗ ⇒ 统一居中 ✓。 */}
                        <td className="text-center px-1">{fmtRaw(r.ic, 4)}</td>
                        <td className="text-center px-1">{fmtRaw(r.rank_ic, 4)}</td>
                        <td className="text-center px-1">{fmtRaw(r.icir, 3)}</td>
                        <td className="text-center px-0 whitespace-nowrap">
                          {/* ★ v2.8.8c：`whitespace-nowrap` ⇒ 「彩票型」「有效✓」「待观察」都**一行放下** ✓
                              （宽度由表头的 `min-w-[5rem]` 撑着 ✓；居中与表头对齐 ✓） */}
                          {verdictKind === 'conflicting' ? (
                            <span className="text-red-600 font-semibold" title="IC/ICIR 与触发收益差方向相反，信号可能由少数触发日主导，横截面方向相反">
                              方向矛盾
                            </span>
                          ) : verdictKind === 'good' ? (
                            <span
                              className="text-emerald-600 font-semibold"
                              title="0/1 信号按事件研究判定：中位数 ≥0.5%、绝对收益胜率 ≥55%，且超额（触发组 − 未触发组·日配对）>0 —— 三者需同时满足；连续因子按 IC/分位判定"
                            >
                              有效✓
                            </span>
                          ) : verdictKind === 'goodReverse' ? (
                            <span className="text-red-600 font-semibold" title={reverseTitleOf(r)}>
                              有效(反向)✓
                            </span>
                          ) : verdictKind === 'lottery' ? (
                            <span
                              className="text-amber-600 font-semibold"
                              title="事件研究：中位数≈0、绝对收益胜率≈50%，但均值显著更高 —— 收益主要由极少数尾部事件（连板/妖股）撑起，多数触发只是白干，不可作为稳定 alpha。点右侧「事件研究」看分布明细"
                            >
                              彩票型
                            </span>
                          ) : verdictKind === 'timeConcentrated' ? (
                            <span
                              className="text-amber-600 font-semibold"
                              title="总差值方向显著但按日配对检验不显著（|t|&lt;2 或相对收益胜率≈50%）：差值主要由少数交易日驱动，逐日无稳定超额，慎用"
                            >
                              时间集中
                            </span>
                          ) : (
                            <span
                              className="text-slate-400 cursor-help underline decoration-dotted decoration-slate-300 underline-offset-2"
                              title={watchReason ?? '未达任何有效判定门槛'}
                            >
                              待观察
                            </span>
                          )}
                        </td>
                        <td className="text-right px-1">
                          {(() => {
                            const pt = esPointOf(r)
                            if (!pt) return <span className="text-slate-300">-</span>
                            const med = pt.median ?? 0
                            const cls =
                              med > 0.005
                                ? 'text-emerald-600'
                                : med < -0.005
                                  ? 'text-red-500'
                                  : 'text-slate-500'
                            return (
                              <span
                                className={cls}
                                title={`事件研究（持有 ${pt.k} 交易日，n=${pt.n}）：中位数 ${fmt(med, 3)}%｜均值 ${fmt(pt.mean, 3)}%｜绝对收益胜率 ${((pt.win ?? 0) * 100).toFixed(2)}%`}
                              >
                                {fmt(med, 3)}
                              </span>
                            )
                          })()}
                        </td>
                        {/* 因子研究：事件研究（0/1）与持仓曲线（连续）合成一列 —— 两者互斥，
                            按因子类型各出现一个；两个都没有时给一个说明性的短横
                            ★ v2.8.8b：同列 td 写死宽度（与表头一致 ✓）+ `align-top`（与变体行同一基准 ✓） */}
                        {/* ★★ v2.8.8b（用户 2026-10-16 第六轮 ✓）：**按"有没有离场行"自动切对齐** ✗→✓ ——
                            用户原话：「**没有离场因子的时候**，持仓曲线、事件研究按钮怎么又跑到上面去了，
                            **没有垂直居中**呀」✓
                            ⇒ 规则统一成一句话：**孤零零一个按钮 ⇒ 垂直居中** ✓；
                              **上下两个按钮（持仓曲线/事件研究 + 离场曲线）⇒ 贴顶成栈** ✓
                              （后者是用户前面明确要的 ✓："上头有按钮就贴在下边线" ✓）。
                            ⚠ 判据用 `vRows.length`（本行有没有含离场变体 ✓）——
                              它同时也是「离场曲线」按钮出不出现的条件 ✓ 两处一致 ✓。 */}
                        <td className={`text-center pl-2 pr-1  ${
                          vRows.length > 0 ? 'align-top' : 'align-middle'
                        }`}>
                          {/* ★★ v2.8.8b（用户 2026-10-16 看图第四轮 ✓）：**固定两行网格** ✗→✓ ——
                              用户原话：「旁边的按钮没勾离场因子的时候那个持仓曲线、事件研究的位置要跟前面
                              水平对齐」✓。
                              根因：这一格原来靠 `flex-wrap` **自动换行** ✗ ⇒ 「有离场」的行内容两行、
                              「没离场」的行内容一行 ⇒ 两行网格高度不同 ⇒ 各行按钮的纵坐标**错开** ✓。
                              ⇒ 现在写死**两行**：第一行「持仓曲线 / 事件研究」（`h-[22px]` ✓），
                              第二行「离场曲线」（`h-[22px]` + `mt-1` ✓；没勾离场时**留空但照样占位** ✓）。
                              ⇒ 全表所有行的这两行纵坐标**完全一致** ✓；与下面变体行的
                              「`h-[26px]` 占位 + 按钮」也同一套网格（22 + 4 = 26 ✓）。
                              ⚠ 改按钮内边距/字号时要同步改这几个高度 ✗（否则又错开 ✓）。 */}
                          <div className="h-[22px] flex items-center justify-center gap-1">
                            {r.is_binary && (
                              <button
                                onClick={() => openEventStudy(r)}
                                className="px-1.5 py-0.5 rounded border border-sky-400 text-sky-600 hover:bg-sky-50 dark:hover:bg-sky-900/30 text-[11px] whitespace-nowrap"
                                title="事件研究（0/1 信号）：把每次触发对齐到 T=0，统计 T+1 买入后 1~N 个交易日的收益分布（概率/赔率）；稀疏信号建议用它替代按日配对检验"
                              >
                                事件研究
                              </button>
                            )}
                            {(r.topk_curves || r.quantile_curves) && (
                              <button
                                onClick={() => openCurve(r)}
                                className="px-1.5 py-0.5 rounded border border-emerald-400 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 text-[11px] whitespace-nowrap"
                                title="持仓期曲线（连续因子）：**起点=1** 的净值曲线（默认 = 超额收益最强的分位组，含三档往返成本）+ 可切换基准 + 十分位净值曲线 + 成本敏感度表。固定 K 档由表单「明细 K」决定（改 K 需重跑）；组合/基准/图例显隐均为纯前端、零重算。⚠ 简化估算：买入端已按剔除开关剔除 T/T+1 涨停、T+1 停牌样本；未模拟「跌停卖不出」与流动性冲击，近似净值非逐日盯市"
                              >
                                持仓曲线
                              </button>
                            )}
                            {!r.is_binary && !(r.topk_curves || r.quantile_curves) && (
                              <span
                                className="text-slate-300"
                                title="该行没有可用的因子研究数据（连续因子看「结论」列的错误信息；0/1 信号走事件研究）"
                              >
                                -
                              </span>
                            )}
                          </div>
                          {/* ★ v2.8.0：勾了离场 ⇒ 这一行也给它一个「离场曲线」入口 ✓
                              （= 对照 + 全部离场画在一起；与下面那些变体行同源 ✓）
                              ★★ v2.8.8c（用户 2026-10-16 第三次看 ✓）：**没有离场时这一行整行都不要** ✗→✓ ——
                              用户原话：「没有变化啊，**事件研究按钮还是顶格**啊」✓。
                              根因**不是刷新** ✗：旧写法在这里**无条件**留了一个 `h-[22px]` 的空占位 ✗
                              ⇒ 本格内容 = 22 + 4 + 22 = **48px 撑满整格** ⇒ `align-middle`（见上面的 td ✓）
                              **根本没有可居中的余量** ✗ ⇒ 单按钮的行看着还是顶格 ✓✓（实测推断 ✓）。
                              ⇒ 只有真有「离场曲线」按钮时才渲染这一行 ✓ ⇒ 单按钮行内容 22px ✓
                              ⇒ td 的 `align-middle` 才真正把它**竖直居中** ✓。 */}
                          {vRows.length > 0 ? (
                            <div className="h-[22px] mt-1 flex items-center justify-center">
                              <button
                                onClick={() => setExitModal({ row: r, ei: null, name: '' })}
                                className="px-1.5 py-0.5 rounded border border-amber-400 text-amber-700 hover:bg-amber-100 dark:hover:bg-amber-900/30 text-[11px] whitespace-nowrap"
                                title={`离场曲线（对照 + ${vRows.length} 条离场：${vRows.map((v) => v.name).join('、')}）`}
                              >
                                离场曲线
                              </button>
                            </div>
                          ) : null}
                        </td>
                      </>
                    )}
                  </tr>
                  {/* ★★ v2.8.0：**含离场的变体行** ✓ —— 每行一个「离场曲线」按钮 ✓，
                      点进去只看这一条变体自己的净值（对照 + 它 ✓）。 */}
                  {vRows.map((v) => (
                    <tr
                      key={`${r.id}:${r.horizon ?? '-'}:exit${v.gi}`}
                      className="border-b border-slate-100 dark:border-slate-700 bg-amber-50/60 dark:bg-amber-900/10"
                    >
                      {/* ★★ v2.8.8b（用户 2026-10-16 看图后第二轮 ✓）：变体行**整行打通成一格** ✗→✓ ——
                          用户原话：「2、3 行那个占了太宽的格子，你把 2、3 行不要只放一格行不行？
                          跟『统计列与负市值对数相同』那一格**打通形成一行**，这样就没问题了吧？
                          然后注意**中间要有空格**，还有**按钮要对齐**」✓
                          ⇒ 旧实现把这三段分在**三个格子**里：① 因子名+指标（**窄** ✗ ⇒ "年化…Δ回撤…离场卖出"
                          被截断 ✗）、② 空的"周期"格（v2.8.8 刚空的 ✓）、③ `colSpan={MIDCOLS}` 的
                          "统计列与…相同"格 ✗ ⇒ ①/③ 都放不下文字 ✓。
                          ⇒ 现在合并为**一格**（`colSpan = 因子 1 + 周期 1 + MIDCOLS` ✓），三段各占一行、
                          段间留空隙 ✓ ⇒ 都能显示全 ✓；行尾仍是那个**居中的「离场曲线」按钮** ✓
                          ⇒ 与上面基础行第二行的按钮**横坐标一致** ✓（用户要的"按钮要对齐" ✓）。
                          ⚠ 列数校验：本行 = (1+1+MIDCOLS) + 1(按钮格) = MIDCOLS + 3 ✓
                            = 表头列数 ✓（基础行 = 因子 1 + 周期 1 + MIDCOLS + 研究列 1 ✓）—— 不能算错 ✗。 */}
                      <td colSpan={1 + 1 + MIDCOLS} className="text-left py-1 pr-2">
                        <div title={`含离场变体：${r.name} + ${v.name}`}>
                          <span className="text-amber-700 dark:text-amber-400">
                            {r.name}+{v.name}
                          </span>
                        </div>
                        {/* ★ v2.8.8b（用户 2026-10-16 看图第三轮 ✓）：把「统计列与…相同」**并到指标同一行** ✗→✓ ——
                            用户原话：「统计列与「负市值对数」相同（离场只作用于实盘口径那条回测 ✓）
                            这个跑到下一行去了，跟年化12.9%那段**放在一行**」✓
                            ⇒ 整行打通后格子够宽 ✓ ⇒ 两段合成一行 ✓（中间用**全角空格**留白 ✓ ——
                            用户要求"中间要有空格"✓；注记用更浅的灰 ✓ 以便与主指标区分 ✓）。 */}
                        {/* ★★ v2.8.8b（用户 2026-10-16 ✓）：**没算出来就说"未计算"** ✗→✓ ——
                            以前这一档失败时（`perf` 为 null ✓）会按 `?? 0` 显示 `Δ年化 +0.0pp` ✗
                            ⇒ 看起来像"离场没用"✓，实际是**根本没算** ✗（用户："肯定也没算对" ✓）。
                            现在直接把后端给的 `error`（含替代写法 ✓）显示出来 ✓，数字位不再误导 ✓。 */}
                        {v.err ? (
                          <div className="text-[10px] text-red-500 dark:text-red-400 mt-0.5 break-all">
                            ⚠ 本档未计算：{v.err}
                          </div>
                        ) : (
                        <div className="text-[10px] text-slate-500 mt-0.5">
                          年化 {typeof v.perf?.annual_return === 'number'
                            ? `${(v.perf.annual_return * 100).toFixed(1)}%` : '-'}
                          {' ｜ '}回撤 {typeof v.perf?.max_drawdown === 'number'
                            ? `${(v.perf.max_drawdown * 100).toFixed(1)}%` : '-'}
                          {' ｜ '}Δ年化{' '}
                          <b className={v.dAnn >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                            {v.dAnn >= 0 ? '+' : ''}{(v.dAnn * 100).toFixed(1)}pp
                          </b>
                          {' ｜ '}Δ回撤{' '}
                          <b className={v.dDd >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                            {v.dDd >= 0 ? '+' : ''}{(v.dDd * 100).toFixed(1)}pp
                          </b>
                          {v.sells ? ` ｜ ${v.mode === 'signal' ? '卖出（含到期）' : '离场卖出'} ${v.sells}` : ''}
                          <span className="text-slate-400">
                            {'　｜　'}统计列与「{r.name}」相同（离场只作用于实盘口径那条回测 ✓）
                          </span>
                        </div>
                        )}
                      </td>
                      {/* ★★ v2.8.8b（用户 2026-10-16 看图第二轮 ✓）：与基础行的按钮**对齐** ✗→✓ ——
                          ① 同列 td 写死同宽 ✓（与表头一致 ✓，否则自动布局会把按钮推歪 ✗）；
                          ② `align-top` ✓ + **等高空占位** ✗→✓：基础行那里第一行是「持仓曲线 / 事件研究」
                             按钮（≈22px）+ `gap-1`（4px）⇒ 它的「离场曲线」落在**第二行** ✓；
                             变体行只有这一个按钮 ⇒ 不占位的话它会飘到**第一行** ✗（就是用户截图里
                             那堆"没对齐"的按钮 ✓）。占位高度 = 22 + 4 = **26px** ✓
                             ⚠ 改上面的按钮内边距/字号时要**一起改这个数** ✗（否则又错开 ✓）。 */}
                      {/* ★★ v2.8.8b（用户 2026-10-16 第四轮 ✓）：**整列左对齐 + 与基础行同一套两行网格** ✗→✓ ——
                          用户原话：「为啥离场曲线按钮还是会有错位的情况？不能都跟**因子研究**那列对齐吗？」
                          ⇒ ① 本列（表头 + 基础行 + 变体行）**统一 `text-left` + `justify-start`** ✓：
                             居中方案要靠"每格内容宽度一致"才不歪 ✗，左对齐则**任何内容都从同一条竖线起** ✓；
                          ② 两行结构与基础行**逐字一致**（第一行 `h-[22px]` 空占位 ✓、第二行
                             `h-[22px] mt-1` + `justify-start` ✓）⇒ 左边缘与纵向位置都对齐 ✓；
                          ③ 占位 = 22 + 4 = **26px**（= 基础行第一行按钮高 + `gap-1` ✓）。
                          ⚠ 改按钮内边距/字号时要**一起改这几个高度** ✗，否则又错开 ✓。 */}
                      {/* ★★ v2.8.8b（用户 2026-10-16 第五轮 ✓）：**垂直居中** ✗→✓ ——
                          用户原话：「不是放第一行，而是要放在**第一第二行中间**的位置，**离上边有点距离、
                          离下边有点距离**，这个叫水平对齐」✓
                          ⇒ 变体行上面没有按钮 ✓ ⇒ 既不留空行把它挤到下面 ✗、也不贴第一行 ✗，
                          而是**整格垂直居中** ✓（`align-middle` ✓）⇒ 上下各留一点空 ✓
                          ⇒ 与隔壁基础行那两行按钮的**视觉中线**对齐 ✓（那就是用户说的"水平对齐" ✓）。
                          ⚠ 基础行保持 `align-top` 的两行栈不变 ✓（它的离场曲线确实"贴在下边线" ✓）。 */}
                      <td className="text-center pl-2 pr-1  align-middle">
                        <div className="h-[22px] flex items-center justify-center">
                          <button
                            type="button"
                            onClick={() => setExitModal({ row: r, ei: v.gi, name: v.name })}
                            className="px-1.5 py-0.5 rounded border border-amber-400 text-amber-700 hover:bg-amber-100 dark:hover:bg-amber-900/30 text-[11px] whitespace-nowrap"
                            title="含离场这条变体自己的净值曲线（对照 + 它）—— 连续因子走「每期全仓滚动」、0/1 信号走「信号驱动（事件）」，与「交易信号测试」同源"
                          >
                            离场曲线
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  </>
                )
              })}
            </tbody>
          </table>
          {/* v1.18.59：原来全部塞在一个 <p> 里（JSX 折叠换行 ⇒ 视觉上"堆成一坨"），
              现按主题拆成多行并精简。
              v1.18.61：统一排版 —— 行首术语与冒号加粗（<b>术语：</b>），正文一律不加粗；
              术语定义用中文冒号（原「差值 =」等号改冒号）。 */}
          <p className="mt-1 text-slate-400 leading-relaxed">
            <b>触发分组：</b>0/1 信号为「因子值 &gt; 0.5」；连续因子按分位数分组（触发 = 前 20% 高分位，未触发 = 后 20% 低分位）。触发数为按剔除开关过滤后的数量（信号日 T 涨停 = 选股过滤；成交日 T+1 涨停/停牌 = 实际买不到，与回测一致）。
          </p>
          <p className="mt-1 text-slate-400 leading-relaxed">
            <b>差值：</b>触发组<b>样本</b>均值 − 未触发组<b>样本</b>均值（每次触发 / 每个未触发各算一个样本 ⇒ <b>触发多的日子权重更大</b>；正数说明触发组未来收益更高）。悬停里的「<b>日差值均值</b>」是<b>另一口径</b>（按交易日配对、每天一个样本，且只在"当天两组都有样本"的配对日上算）⇒ <b>两个数不同是正常的</b>，不是算错：前者按样本数加权、后者每天等权。收益按信号日收盘价买入、持有「周期」列对应天数，本列与各收益列显示为<b>百分数（已 ×100，不带 % 号）</b>。<b>p值：</b>Mann-Whitney U 检验显著性（&lt;0.05 时标 *）。<b>IC / RankIC / ICIR：</b>逐日横截面相关口径，列内为<b>原始小数</b>（未 ×100），稳定性阈值 |ICIR| ≥ 0.05。
          </p>
          <p className="mt-1 text-slate-400 leading-relaxed">
            <b>0/1 信号「有效 ✓」：</b>需同时满足四条：① 事件研究中位数 ≥ 0.50%；② 绝对收益胜率 ≥ 55%；③ 日配对超额 ≥ 门槛（= max(0.5%, 0.025%×持有期)：20 日 0.50%、40 日 1.00%）；④ 日配对稳定（|HAC t| ≥ 2 或 日胜率 ≥ 55%）。无基准时第③条不启用、第④条仍要求。
          </p>
          <p className="mt-1 text-slate-400 leading-relaxed">
            <b>分位收益：</b>0/1 信号显示「信号组 vs 非信号组」双柱；连续因子按每日横截面分位（默认十分位 10 组，可选 5 组=旧口径），每组为日截面平均收益（每天先算组内均值、再对参与交易日取平均），绿=正、红=负，可识别单调 / U 型 / 倒 U 型；勾选剔除开关时先过滤再分组。
            <b>⚠ 口径提示：</b>本列是日截面平均收益（日频），而「因子研究」弹窗标注的最强 / 最弱分位组按持仓期调仓的复利净值与超额判定 ⇒ 最强组排序可能不一致，二者各自有意义，勿互相印证或否定。
          </p>
          <p className="mt-1 text-slate-400 leading-relaxed">
            <b>判读：</b>方向矛盾（diff 为正但 IC/ICIR 稳定为负）= 信号由少数触发日主导，慎用；时间集中（diff 显著但日配对检验不显著）= 总差值被少数交易日拉高，慎用；有效(反向)（连续因子高分位组收益更低、IC/ICIR 稳定为负）= 反向使用有效（市值、流动性等负向因子常见）。以上 t 均为 Newey-West HAC 稳健 t。
          </p>
        </div>
      )}

      {/* 事件研究弹窗：0/1 稀疏信号的触发事件收益分布（触发对齐 T=0）。
          ErrorBoundary 兜底：弹窗异常时显示错误信息而非整页白屏。 */}
      <EsErrorBoundary>
        <EventStudyModal
          open={estOpen}
          onClose={() => setEstOpen(false)}
          factorName={estName}
          req={estReq}
          data={estData}
          pair={estPair}
          defaultK={estK}
          /* v1.19.60/61：净值曲线要复用**本任务**里已算好的触发事件（"秒开"路径没有独立事件研究任务）。
             ⚠ 优先用 `doneTaskId`（任务成功后保留）—— `taskIdRef` 在任务完成时会被 `stopPolling()` 清空，
               只用它会导致"跑完再点开弹窗 ⇒ 提示需要任务上下文"（用户 2026-09-15 报）。 */
          sourceTaskId={doneTaskId ?? taskIdRef.current}
        />
      </EsErrorBoundary>

      {/* 持仓期收益曲线弹窗（连续因子）：数据已在结果里 ⇒ 秒开、纯前端切换 K。 */}
      <EsErrorBoundary label="持仓曲线弹窗">
        <TopkCurveModal
          open={curveOpen}
          onClose={() => setCurveOpen(false)}
          name={curveName}
          row={curveRow}
          /* v2.1.0：导出 XLSX 要用任务 id 取"每期持仓"（后端任务状态里 ✓）。
             ⚠ 与事件研究同一教训：用 `doneTaskId`（任务成功后保留），`taskIdRef` 会被清空 ✓。 */
          taskId={doneTaskId ?? taskIdRef.current ?? ''}
        />
      </EsErrorBoundary>

      {/* ★ v2.8.0（用户 2026-10-15 ✓）：**离场曲线**弹窗 —— 点结果行里的「离场曲线」打开 ✓，
          只画「对照 + 那一条离场」⇒ 一眼看出这条离场到底帮了还是害了 ✓（无重算、秒开 ✓）。 */}
      {exitModal && (
        <EsErrorBoundary label="离场曲线弹窗">
          <ExitCurveModal
            row={exitModal.row}
            ei={exitModal.ei}
            name={exitModal.name}
            /* ★ v2.8.8b：导出（截图 + 各离场一页调仓记录）要用任务 id 取成交明细 ✓
               ⚠ 与持仓曲线同一教训：用 `doneTaskId`（任务成功后保留 ✓），
                 `taskIdRef` 会在 `stopPolling()` 时被清空 ✗。 */
            taskId={doneTaskId ?? taskIdRef.current ?? ''}
            onClose={() => setExitModal(null)}
          />
        </EsErrorBoundary>
      )}
    </div>
  )
}
