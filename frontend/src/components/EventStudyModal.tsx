import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useModalScrollLock } from '../useModalScrollLock'
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
import { cancelEventStudy, createEventStudy, getEventStudyProgress, runEventNav } from '../api'
import type { EventNavResult, EventStudyRequest, EventStudyResult } from '../api'
import { excessThresholdOf, isValidAt, isReverseValidAt, pairStabilityOf } from './verdictRules'
import { navColor, navDash, navLabel } from './navSeries'
import ZoomableLineChart from './ZoomableLineChart'
import EventDistChart from './EventDistChart'

interface Props {
  open: boolean
  onClose: () => void
  factorName: string
  /** 参数快照（由单因子测试面板当前设置构造；open 时提交一次） */
  req: EventStudyRequest | null
  /** 单因子测试顺带算出的结果（v1.18.7）：有值时直接展示，不再提交任务（秒开） */
  data?: EventStudyResult | null
  /**
   * 该行的日配对稳定性字段（t / 胜率，v1.18.32）：与表格「结论」列共用同一套门槛
   * （超额门槛 max(0.5%, 0.025%×k) + 稳定性 |t|≥2 或 日胜率≥55%），避免两处口径漂移。
   */
  pair?: { t?: number | null; win?: number | null } | null
  /**
   * 被点开那一行的「周期」（持有期 k，v1.18.63）：弹窗默认「最长持有」取它 ⇒ 默认展示的
   * k 与该行结论一致。此前固定用后端 **max_k**（= 任务填的**最大**预测周期），在 `1:2:7`
   * 这类多周期下会出现「点"周期 3"那行、弹窗却默认显示 7 日口径」的问题（用户报）。
   */
  defaultK?: number | null
  /**
   * 单因子测试任务 id（v1.19.60）：该任务的触发事件已按因子表达式存在后端状态里
   * ⇒ 「净值曲线」可以直接复用（无需重算因子、无需独立事件研究任务）。
   */
  sourceTaskId?: string | null
}

type EstStatus = 'idle' | 'running' | 'success' | 'failed' | 'cancelled'

// 原始小数 → 百分数展示（收益类字段均为小数）
const pct = (v: number | null | undefined, nd = 3) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

const num = (v: number | null | undefined, nd = 1) =>
  v == null || !Number.isFinite(v) ? '-' : `${(v * 100).toFixed(nd)}%`

/** 「有效」/「反向有效」的逐 k 判定已下沉到共享模块 `verdictRules.ts`
 *  （`isValidAt` / `isReverseValidAt`，v1.19.40）—— 表格结论、图上锚点、末点提示、悬停文案
 *  全部共用同一对纯函数，杜绝"图上标有效、说明说待观察"这类口径漂移。 */

/** 「判定有效 / 反向有效」持有日的锚点圆点（v1.19.25 起；v1.19.40 加反向=红点）。
 *
 *  ⚠ Recharts 的 `dot` 回调**必须返回带 `key` 的元素**（不画时也要返回空 `<g/>`），否则告警。
 *  · `payload.valid === true`      ⇒ 正向有效 ⇒ 用 `color`（紫 `#7c3aed`）
 *  · `payload.validRev === true`   ⇒ **反向有效 ⇒ 红色 `#dc2626`**（用户 2026-09-15：红色 = 研究线索）
 */
function validAnchorDot(field: string, color: string, revColor = '#dc2626') {
  return (p: {
    key?: string | number
    cx?: number
    cy?: number
    payload?: { k?: number; valid?: boolean; validRev?: boolean } & Record<string, unknown>
  }) => {
    const v = p.payload?.[field]
    const rev = p.payload?.validRev === true
    if ((p.payload?.valid === true || rev) && typeof v === 'number' && v != null) {
      return (
        <circle
          key={p.key ?? `anchor-${p.payload?.k ?? ''}`}
          cx={p.cx}
          cy={p.cy}
          r={3.5}
          fill={rev ? revColor : color}
          stroke="#fff"
          strokeWidth={1}
        />
      )
    }
    return <g key={p.key ?? `anchor-${p.payload?.k ?? ''}`} />
  }
}

export default function EventStudyModal({
  open,
  onClose,
  factorName,
  req,
  data,
  pair,
  defaultK,
  sourceTaskId,
}: Props) {
  const [status, setStatus] = useState<EstStatus>('idle')
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<EventStudyResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [maxK, setMaxK] = useState(40)
  // 曲线显隐（点击图例切换）。key = recharts 的 dataKey；两张图共用一份状态（键名不重叠）。
  //
  // 默认隐藏 mean / p25 / p75（v1.18.21）：主图只留**成对可比较**的线 ——
  //   ① 日配对：trigger_pair ↔ baseline（可相减 → 差值即下方超额曲线）
  //   ② 事件级中位数：median ↔ baseline_median
  // `mean`（事件级均值）是**该口径下的孤立线**（没有同口径基准），留在主图里极易被
  // 误用来减 `baseline`（日配对口径）—— 本项目已发生过一次（用户据 6.326% − 3.067%
  // 得出不存在的"+3.259% 超额"）。其诊断价值（与 median 的差距 = 是否「彩票型」）
  // 由弹窗的 upside 表与概率表承担，故默认收起、点击图例可随时开启。
  const [hidden, setHidden] = useState<Record<string, boolean>>({
    mean: true,
    p25: true,
    p75: true,
  })
  const toggleSeries = useCallback((key?: string | number) => {
    if (typeof key !== 'string' || !key) return
    setHidden((h) => ({ ...h, [key]: !h[key] }))
  }, [])

  // ---- 净值曲线（v1.19.60）：复用任务里已算好的触发事件 + 缓存价格面板，只花"一次回测"的钱
  //      （实测 0.12~0.6s/次）⇒ 持仓周期可自己调、净值跟着变；按 k 缓存 ⇒ 来回改秒开。
  const [navK, setNavK] = useState<number | null>(null)
  // ⚠ v1.2.2（用户 2026-09-18 要求）：**输入框显示值与"已生效的 k"分开** ✗ ——
  //   原来 `value={navK ?? ''}` + `onChange={setNavK(max(1, Number(v)))}` ⇒ 一按删除键
  //   `Number('') == 0` ⇒ 立刻被回填成 **1** ⇒ **永远删不光** ✓（个位数尤其烦 ✓）。
  //   现在：输入框绑 `navKInput`（**可为空** ✓），只有"合法正整数"才去改 `navK`（⇒ 才发请求）；
  //   空/非法 ⇒ **不动 `navK`** ⇒ **不发请求、图保留上一次** ✓（正是用户要的手感 ✓）。
  //   `lastOkKRef` 记录**上一次请求成功时的 k** ⇒ 删光时把 `navK` 回退到它 ⇒ 命中缓存**秒回** ✓
  //   （⇒ "6 算到一半删光 ⇒ 停在 60" 这个诉求靠它实现 ✓）。
  const [navKInput, setNavKInput] = useState('')
  // ⚠ v1.2.9：净值曲线改为**手动计算** ✓ —— `navTick` 是"点按钮"的计数器 ✓，
  //   只有它变化才会触发请求 effect ✓（原因见 effect 尾部的注释：高触发公式单次 57s ✗）。
  const [navTick, setNavTick] = useState(0)
  const lastOkKRef = useRef<number | null>(null)
  const [navCost, setNavCost] = useState(0.004)
  const [navRes, setNavRes] = useState<EventNavResult | null>(null)
  const [navBusy, setNavBusy] = useState(false)
  const [navErr, setNavErr] = useState('')
  const navCacheRef = useRef<Map<string, EventNavResult>>(new Map())

  // 净值曲线：默认持仓周期 = 当前展示的 k；按 (k, cost) 缓存 ⇒ 来回改秒开。
  useEffect(() => {
    if (!result) return
    navCacheRef.current.clear()
    const last = result.curve?.length ? result.curve[result.curve.length - 1].k : null
    const k0 = defaultK ?? last ?? 20
    setNavK(k0)
    setNavKInput(String(k0))          // ⚠ 输入框跟着同步（否则换结果后显示值不动 ✗）
    setNavRes(null)
    setNavErr('')
    // ⚠ v1.19.84：换结果/换周期入口也要清 spinner（否则上一轮的 "计算中…" 会跟着新结果一起挂着）
    setNavBusy(false)
  }, [result, defaultK])

  useEffect(() => {
    if (!result || navK == null) return
    const tid = sourceTaskId || taskRef.current
    // ⚠ 没有任务 id 也可以试：后端会按**因子表达式**在最近的任务里找回触发事件
    //   （v1.19.61）；只有连表达式都没有时才真的没法算。
    // ⚠ v1.19.84：改用 `reqRef` 而不是依赖 `req` 对象 —— 父组件每次渲染都可能重建 req，
    //   把它放进依赖数组会让本 effect 反复重跑（spinner 抖动/无谓重算）。reqRef.current 已在渲染期同步。
    const expr = (reqRef.current?.factor as { expression?: string } | undefined)?.expression
    if (!tid && !expr) {
      setNavErr('这次结果没有关联到测试任务，也没有因子表达式 ⇒ 无法复用触发明细；' +
        '点上方「重新计算」重跑一次即可看到净值曲线')
      setNavBusy(false)              // v1.19.84：提前返回也必须收尾，否则 spinner 永挂
      return
    }
    const key = `${navK}|${navCost}|${tid ?? ''}`
    const hit = navCacheRef.current.get(key)
    if (hit) {
      // ★ 用户 2026-09-17 报的 bug 就在这里：命中缓存 ⇒ 曲线**立刻**出来，但此前没复位 busy
      //   ⇒ 界面上「曲线已算好、却一直显示计算中…」。缓存命中本就是"秒出"，必须 busy=false。
      setNavRes(hit)
      setNavErr('')
      setNavBusy(false)
      return
    }
    setNavBusy(true)
    setNavErr('')
    let cancelled = false
    const timer = window.setTimeout(() => {
      /**
       * 取净值（失败时**自动重试**）。
       *
       * ⚠ 为什么要有重试（用户 2026-09-15 报「净值曲线暂不可用：Network Error」）：
       *   `Network Error` 是 axios 在**拿不到 HTTP 响应**时的文案（连接被掐/后端没在听），与业务错误无关。
       *   本项目**发版会重启后端**（`restart_backend.ps1` 杀进程再起，约 4~6s 不可用）——
       *   用户若正好在这个窗口点开弹窗/点「重新计算」，就会看到它。
       *   所以这里对"无响应"这一类失败自动重试 2 次（1.5s / 4s，合计覆盖 ~6s 重启窗口），
       *   并把文案写成"连接中断（后端可能正在重启）"而不是甩一句英文 `Network Error`。
       *   ⚠ 有响应（400/404 等）**不重试**：那是真结论（例如触发明细已被清理），重试没意义。
       */
      const attempt = (left: number) => {
        if (cancelled) return
        runEventNav({ task_id: tid ?? '', hold_days: navK, cost: navCost, factor_id: expr })
          .then((r) => {
            if (cancelled) return
            navCacheRef.current.set(key, r)
            lastOkKRef.current = navK        // 记下"最后一次真正算出结果的 k" ✓
            setNavRes(r)
            setNavErr('')
            setNavBusy(false)
          })
          .catch((e: unknown) => {
            if (cancelled) return
            const resp = (e as { response?: { data?: { detail?: string } } })?.response
            const netish = !resp                     // 无响应 ⇒ 连接层失败（不是业务错误）
            if (netish && left > 0) {
              setNavErr(`连接中断，正在自动重试（剩 ${left} 次）…`)
              window.setTimeout(() => attempt(left - 1), left === 2 ? 1500 : 4000)
              return
            }
            setNavErr(netish
              ? '连接中断（后端可能正在重启）—— 已自动重试仍失败：点「重新计算」再试即可，'
                + '不用重跑整个测试'
              : (resp?.data?.detail || (e instanceof Error ? e.message : String(e))))
            setNavRes(null)
            setNavBusy(false)
          })
      }
      attempt(2)
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      // ⚠ v1.19.84：上一轮请求被取消时，`.then` 里 `if (cancelled) return` 会直接退出
      //   ⇒ 永远不会 setNavBusy(false) ⇒ spinner 永挂。cleanup 里显式收尾
      //   （紧接着的新一轮若确实要算，会再 setNavBusy(true)，顺序安全）。
      setNavBusy(false)
    }
    // ⚠ v1.2.9（用户 2026-09-18：**"计算很慢，改成手动按钮、不自动算"** ✓）：
    //   本 effect 原来依赖 `[result, navK, navCost, sourceTaskId]` ✗ ⇒ **输入框一改就自动重算** ✓；
    //   而「过顶」这类高触发公式一次回测实测 **57s**（14.88 万笔 ✓，取价另 17.6s ✓）⇒
    //   改一下持仓周期就卡一分钟 ✓ ⇒ 改为**只由 `navTick`（按钮）驱动** ✓。
    //   ⚠ `navK` / `navCost` 仍从闭包读取（每次点按钮时取当前值 ✓）⇒ 故不放进依赖 ✓。
    // ⚠ 依赖**只留 `navTick`** ✗ 不放 `result` —— 换结果时也**不自动算** ✓
    //   （打开弹窗就自动跑 57s 正是用户要避免的 ✓；`result` 变化由上面的初始化 effect
    //    负责清空旧图 ✓，用户点一次按钮即可 ✓）。
  }, [navTick])                                                               // eslint-disable-line react-hooks/exhaustive-deps

  const taskRef = useRef<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const reqRef = useRef<EventStudyRequest | null>(null)
  reqRef.current = req
  const dataRef = useRef<EventStudyResult | null>(null)
  dataRef.current = data ?? null

  const stopPoll = useCallback(() => {
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const start = useCallback(
    async (mk: number) => {
      const r = reqRef.current
      if (!r) return
      stopPoll()
      setStatus('running')
      setProgress(0)
      setMessage('提交中...')
      setResult(null)
      setError(null)
      try {
        const { task_id } = await createEventStudy({ ...r, max_k: mk })
        taskRef.current = task_id
        timerRef.current = window.setInterval(async () => {
          try {
            const p = await getEventStudyProgress(task_id)
            setProgress(p.progress)
            setMessage(p.message)
            if (p.status === 'success') {
              setResult(p.result ?? null)
              setStatus('success')
              stopPoll()
            } else if (p.status === 'failed') {
              setError(p.error || p.message || '事件研究失败')
              setStatus('failed')
              stopPoll()
            } else if (p.status === 'cancelled') {
              setStatus('cancelled')
              stopPoll()
            }
          } catch {
            /* 单次轮询失败忽略，下个周期重试 */
          }
        }, 1500)
      } catch (e: unknown) {
        const detail =
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(e)
        setError(detail)
        setStatus('failed')
      }
    },
    [stopPoll],
  )

  // open 时：优先直接展示单因子测试顺带算出的结果（秒开）；否则提交独立任务兜底
  useEffect(() => {
    if (open) {
      const d = dataRef.current
      if (d) {
        setResult(d)
        setStatus('success')
        setError(null)
        setMaxK(defaultK ?? d.params?.max_k ?? 40)
        stopPoll()
        taskRef.current = null
      } else if (reqRef.current) {
        setMaxK(defaultK ?? 40)
        void start(40)
      }
    }
    if (!open) {
      stopPoll()
      taskRef.current = null
    }
    return () => stopPoll()
    // 仅在 open 切换时触发（req/data 每次渲染都是新对象，不能进依赖）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const onCancel = async () => {
    if (!taskRef.current) return
    try {
      await cancelEventStudy(taskRef.current)
    } catch {
      /* 忽略 */
    }
  }

  // 后端一次就算出 1..computedMaxK 的全部持有期（单因子测试顺带算的期数 = 该任务填的
  // **最大预测周期**，v1.18.37 起；此前固定至少 40 期），因此把「最长持有」调小只是
  // **截断展示**，触发样本完全相同、无需重算；
  // 只有调到超过 computedMaxK 时才需要真正重算（后端没有更长的期数）。
  const computedMaxK = result?.params?.max_k ?? result?.ks?.length ?? 0
  const needRecompute = result != null && maxK > computedMaxK
  const viewCurve = useMemo(
    () => (result?.curve ?? []).filter((c) => c.k <= maxK),
    [result, maxK],
  )

  // v1.19.25：**逐 k 的「有效」判定**（用户要求：鼠标移到图上就能看到哪些持有日有效）。
  // 供 ① 两张曲线 tooltip 的「持有 k 个交易日 · 有效/待观察」② 两条紫色曲线的锚点 共用。
  // 与末点提示 `verdictHint` 同源（同一个 `isValidAt`），不会出现"图上标有效、说明说待观察"。
  // ⚠ 第④条口径：新结果按 k（`baseline.t_hac/win`）；**旧结果整体回退行级**（`pair` prop），
  //   与 `verdictHint` 里的回退规则一致。
  const validKind = useMemo(() => {
    // v1.19.40：从 Set<k> 升级为 Map<k, 'good' | 'reverse'> —— 正向有效锚点紫色、**反向有效锚点红色**
    const out = new Map<number, 'good' | 'reverse'>()
    const bl = result?.baseline
    if (!result || viewCurve.length === 0) return out
    const perK = Array.isArray(bl?.t_hac) && Array.isArray(bl?.win)
    for (const c of viewCurve) {
      const i = bl && bl.ks ? bl.ks.indexOf(c.k) : -1
      const ex = i >= 0 ? (bl?.excess?.[i] ?? null) : null
      const raw = perK && i >= 0
        ? { t: bl!.t_hac![i] ?? null, win: bl!.win![i] ?? null }
        : { t: pair?.t, win: pair?.win }
      const med = c.median ?? null
      const win = c.win ?? null
      if (isValidAt(c.k, med, win, ex, pairStabilityOf(raw, false).ok)) {
        out.set(c.k, 'good')
      } else if (isReverseValidAt(c.k, med, win, ex, pairStabilityOf(raw, true).ok)) {
        out.set(c.k, 'reverse')   // 同时具备：表格判「有效(反向)」时这里也会亮红点
      }
    }
    return out
  }, [result, viewCurve, pair])

  // 图表数据：curve → {k, mean, median, p25, p75} + 触发组/基准各两条线
  //
  // ⚠ 口径必须分开看（本项目曾因此误读）：同一持有期 k 有**两套互不可比的均值口径**。
  //   · 事件级（每个事件 1 票）：curve[].mean / curve[].median（触发组）、baseline_median
  //     —— 743 个事件等权平均；极少数暴涨事件会主导均值。
  //   · 日配对（每个配对日 1 票）：trigger_pair（触发组）、baseline
  //     —— 先按触发日取截面均值，再对各配对日平均；等价于"每天等权"。
  //   **只有 trigger_pair 与 baseline 是同口径，可以相减**（= excess）；
  //   **mean 与 baseline 不同口径，相减没有意义**（curve.mean 是事件级）。
  //   trigger_pair 来自后端 baseline.trigger_pair（v1.18.8 起返回）。
  const chartData = useMemo(() => {
    const bl = result?.baseline
    const baseMap = new Map<number, number | null>()
    const baseMedMap = new Map<number, number | null>()
    const trigPairMap = new Map<number, number | null>()
    if (bl && bl.ks) {
      bl.ks.forEach((k, i) => {
        baseMap.set(k, bl.baseline?.[i] ?? null)
        baseMedMap.set(k, bl.baseline_median?.[i] ?? null)
        trigPairMap.set(k, bl.trigger_pair?.[i] ?? null)
      })
    }
    return viewCurve.map((c) => {
      const b = baseMap.get(c.k)
      const bm = baseMedMap.get(c.k)
      const tp = trigPairMap.get(c.k)
      return {
        k: c.k,
        mean: c.mean == null ? null : c.mean * 100,
        median: c.median == null ? null : c.median * 100,
        p25: c.p25 == null ? null : c.p25 * 100,
        p75: c.p75 == null ? null : c.p75 * 100,
        // 日配对口径（与 baseline 同口径，可相减）
        trigger_pair: tp == null ? null : tp * 100,
        baseline: b == null ? null : b * 100,
        // 事件级口径（与 median 同口径）
        baseline_median: bm == null ? null : bm * 100,
        // v1.19.25：该持有日是否判定「有效」—— tooltip 文案与紫色曲线锚点共用
        // v1.19.40：`validRev` = 判定「反向有效」⇒ 锚点画**红色**
        valid: validKind.get(c.k) === 'good',
        validRev: validKind.get(c.k) === 'reverse',
      }
    })
  }, [viewCurve, result, validKind])

  // 超额曲线（触发组 − 基准）：均值口径（日配对）+ 中位数口径（事件级）两条线
  const excessChart = useMemo(() => {
    const bl = result?.baseline
    if (!bl || !bl.ks) return []
    return bl.ks
      .map((k, i) => {
        const v = bl.excess?.[i]
        const vm = bl.excess_median?.[i]
        return {
          k,
          excess: v == null ? null : v * 100,
          excess_median: vm == null ? null : vm * 100,
          // v1.19.25：同 `chartData` —— 有效持有日（锚点 + tooltip 文案）；v1.19.40 加反向（红点）
          valid: validKind.get(k) === 'good',
          validRev: validKind.get(k) === 'reverse',
        }
      })
      .filter((d) => d.k <= maxK)
  }, [result, maxK, validKind])

  // 后端是否给出了中位数口径（旧结果没有该字段时不画这条线）
  const hasExcessMedian = useMemo(
    () => excessChart.some((d) => d.excess_median != null),
    [excessChart],
  )

  // 末点超额（摘要卡片用；依赖 viewCurve 以避免引用后面才定义的 lastPoint）
  const excessAtLast = useMemo(() => {
    const bl = result?.baseline
    if (!bl || !bl.ks || viewCurve.length === 0) return null
    const i = bl.ks.indexOf(viewCurve[viewCurve.length - 1].k)
    return i >= 0 ? (bl.excess?.[i] ?? null) : null
  }, [result, viewCurve])

  // 末点「中位数超额」：中位数口径下，典型一次触发的超额（均值超额易被暴涨事件主导）
  const excessMedianAtLast = useMemo(() => {
    const bl = result?.baseline
    if (!bl || !bl.ks || !bl.excess_median || viewCurve.length === 0) return null
    const i = bl.ks.indexOf(viewCurve[viewCurve.length - 1].k)
    return i >= 0 ? (bl.excess_median?.[i] ?? null) : null
  }, [result, viewCurve])

  // v1.19.81：原来这里从 `result.prob` 抽几档 k 给"概率分档表"用；现在界面已改为**分布图**
  // （`EventDistChart`，随「最长持有」联动），故整段下线 —— 后端 `prob[]` 仍保留，
  // 需要精确档位数字时（或旧结果）可随时再取用。

  const lastPoint = viewCurve.length > 0 ? viewCurve[viewCurve.length - 1] : null

  // Top / 最差事件：取「逐 k 榜单」里当前 k 的那份，保证与表头「持有 k 日收益」口径一致。
  // 旧结果（无 top_by_k）回退到顶层字段（= max_k 期口径）。
  // 之所以逐 k 取：① 固定用 max_k 期会与表头写明的 k 不符（滑到 2 日却显示 40 日收益）；
  // ② 未持满 max_k 期但在更短 k 上有效的事件（如区间末端贴近数据末尾的触发）会漏掉。
  const topEvents = useMemo(() => {
    const k = lastPoint?.k
    const byK = result?.top_by_k
    if (k != null && byK && byK[String(k)]) return byK[String(k)]
    return result?.top_events ?? []
  }, [result, lastPoint])

  const worstEvents = useMemo(() => {
    const k = lastPoint?.k
    const byK = result?.worst_by_k
    if (k != null && byK && byK[String(k)]) return byK[String(k)]
    return result?.worst_events ?? []
  }, [result, lastPoint])

  const verdictHint = useMemo(() => {
    if (!result || !lastPoint) return null
    const win = lastPoint.win ?? 0
    const med = lastPoint.median ?? 0
    // 当前 k 的日配对超额（与判定同口径）；无基准时为 null（不加此条件）
    const bl = result.baseline
    const bi = bl && bl.ks ? bl.ks.indexOf(lastPoint.k) : -1
    const ex = bi >= 0 ? (bl?.excess?.[bi] ?? null) : null
    const pct = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(3)}%`
    // v1.18.32：与表格「结论」列共用门槛 —— 超额按持有期缩放（max(0.5%, 0.025%×k)）+ 日配对稳定性
    const thr = excessThresholdOf(lastPoint.k)
    const thrTxt = `${(thr * 100).toFixed(3)}%`
    // v1.19.24：**第④条（日配对稳定）也随「最长持有 k」** —— 取后端逐 k 的 HAC t / 日胜率
    // （`baseline.t_hac` / `baseline.win`，与 ①②③ 同一条逐日序列算出）。
    // 修复的 bug：此前 ④ 固定用"被点开那一行"的统计量（`pair` prop），于是在 60 天行里把
    // 「最长持有」改成 40 时，只有 ①②③ 换成 40 口径、④ 仍按 60 口径 ⇒ 判定永远出不来（用户报）。
    // ⚠ 旧结果（v1.19.24 之前算的）没有该字段 ⇒ 回退到行级口径，并在提示里明确标注。
    // ⚠ 回退是**整体**的（不逐字段混用）：新结果 ⇒ ④ 全按 k；旧结果 ⇒ ④ 全按行级。
    const hasPerK = bi >= 0 && Array.isArray(bl?.t_hac) && Array.isArray(bl?.win)
    const rawStats = hasPerK
      ? { t: bl!.t_hac![bi] ?? null, win: bl!.win![bi] ?? null }
      : { t: pair?.t, win: pair?.win }
    const st = pairStabilityOf(rawStats)
    // v1.19.40：反向口径的稳定性（日胜率 ≤45% 算通过），供「反向有效」提示与红点判定共用
    const stRev = pairStabilityOf(rawStats, true)
    const stFromRow = !hasPerK
    const scope =
      `（本提示按当前「最长持有 ${lastPoint.k ?? '?'} 日」口径；表格结论按该行「周期」列口径）` +
      (stFromRow
        ? '；⚠ 其中第④条（日配对稳定）仍是该行「周期」口径 —— 旧结果缺逐 k 统计，重跑后即按当前 k'
        : '')
    // v1.18.63：与表格「结论」列口径对齐 —— 彩票型 = |中位数| <1% 且 胜率 45~55%
    // 且 均值 > max(0.50%, |中位数|×3)。原弹窗只判 |中位数| <0.5% 且缺均值条件 ⇒ 与表格漂移。
    const mean = lastPoint.mean ?? 0
    if (win >= 0.45 && win <= 0.55 && Math.abs(med) < 0.01
        && mean > Math.max(0.005, Math.abs(med) * 3)) {
      return {
        tone: 'warn' as const,
        text:
          '绝对收益胜率约 50%、中位数≈0：无方向优势，均值几乎全部来自少数尾部事件（典型"彩票型"分布），不可作为稳定 alpha。' +
          scope,
      }
    }
    // 中位数与胜率都好看，但未达超额门槛 / 日配对不稳定 → 不能算"正向事件效应"
    if (med > 0 && win >= 0.55 && ((ex != null && ex < thr) || !st.ok)) {
      const bad: string[] = []
      if (ex != null && ex < thr) {
        bad.push(
          `日配对超额 ${pct(ex)} 未达门槛 ${thrTxt}（门槛 = max(0.5%, 0.025%×持有期)，0.5% 约合覆盖一次往返交易成本）`,
        )
      }
      if (!st.ok) {
        bad.push(
          st.missing
            ? '日配对稳定性无法验证（配对日不足 2）'
            : `日配对不稳定（|HAC t| ${st.tVal == null ? '-' : Math.abs(st.tVal).toFixed(2)} <2 且 日胜率 ${
                st.winVal == null ? '-' : `${(st.winVal * 100).toFixed(2)}%`
              } <55%；口径 = ${stFromRow ? '该行「周期」' : `当前 ${lastPoint.k} 日`}）`,
        )
      }
      return {
        tone: 'warn' as const,
        text: `中位数为正、绝对收益胜率高于 50%，但${bad.join('；')} —— 收益不可交易 / 不可复现，判定为「待观察」。${scope}`,
      }
    }
    // v1.18.63：阈值与表格「有效✓」**完全对齐**（表格：med ≥ 0.50% + win ≥ 55% +
    // 日配对超额 ≥ 门槛 + 日配对稳定）。原为 `med > 0.01`（**1%**）⇒ 中位数落在 0.5%~1%
    // 之间时，表格判「有效✓」而弹窗落到「临界区间」，出现自相矛盾的提示（用户报）。
    // v1.19.25：条件改用模块级 `isValidAt`（与曲线上锚点/ tooltip 的「有效」判定**同一个函数**）
    //   —— 语义与原来完全等价：能走到这里说明 warn 分支未命中（即 `ex` 达标且 `st.ok`）。
    if (isValidAt(lastPoint.k, med, win, ex, st.ok)) {
      return {
        tone: 'good' as const,
        text: `中位数为正、绝对收益胜率高于 50%，且日配对超额 ≥${thrTxt}、日配对稳定：存在可复制的正向事件效应。${scope}`,
      }
    }
    // v1.19.40（用户 2026-09-15）：**反向有效**单独给提示（与表格「有效(反向)」同一条件集），
    //   并写明用途与注意 —— 它是「**研究线索**」（典型用途：研究能否当**离场因子**），不是"好消息"。
    if (isReverseValidAt(lastPoint.k, med, win, ex, stRev.ok)) {
      return {
        tone: 'bad' as const,
        text: `中位数为负、绝对收益胜率低于 50%，且日配对超额 ≤ −${thrTxt}、日配对稳定：判定「反向有效」` +
          `（对选股信号的含义是「原方向无效」）—— 图上该持有日标为**红点**，可作研究线索` +
          `（如：持有中被触发就提前离场）。⚠ 稀疏 0/1 信号的反向 ≈ 拿 beta，落地前先验可行域。${scope}`,
      }
    }
    if (med < -0.005) {
      return { tone: 'bad' as const, text: `中位数为负：多数触发事件是亏的，均值靠少数大赢家——需谨慎。${scope}` }
    }
    return {
      tone: 'plain' as const,
      text: `中位数与绝对收益胜率均处于临界区间，建议结合概率表与明细复核。${scope}`,
    }
  }, [result, lastPoint, pair])

  // v1.19.18：弹窗打开期间锁定页面滚动 —— 修「滚到弹窗底部后继续滚会带动背后主页面滚动」
  // （scroll chaining，用户反馈）。⚠ 必须放在 `if (!open) return null` **之前**（hooks 不能条件调用）。
  useModalScrollLock(open)

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-slate-800 rounded-lg shadow-xl w-[980px] max-w-full max-h-[92vh] overflow-auto overscroll-contain"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-slate-700">
          <div>
            <span className="font-semibold text-slate-700 dark:text-slate-200">事件研究</span>
            <span className="text-slate-400 ml-2 text-xs">
              {factorName}
              {result && result.params
                ? ` · ${result.params.universe} · ${result.params.start_date}~${result.params.end_date} · ${result.params.price_adjust}`
                : ''}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label
              className="text-xs text-slate-500"
              title={`显示到第几个持有交易日（后端已算 ${computedMaxK} 期）；不超过它时只切换展示、无需重算`}
            >
              最长持有
              <input
                type="number"
                min={1}
                max={120}
                value={maxK}
                onChange={(e) => setMaxK(Number(e.target.value) || computedMaxK || 40)}
                className="ml-1 w-16 px-1 py-0.5 border rounded text-xs dark:bg-slate-700 dark:border-slate-600"
              />
              日
              {computedMaxK > 0 && (
                <span className="ml-1 text-slate-400">(已算 {computedMaxK} 期)</span>
              )}
            </label>
            <button
              onClick={() => void start(maxK)}
              disabled={status === 'running' || !needRecompute}
              title={
                needRecompute
                  ? `重算到 ${maxK} 个交易日（超过后端已算的 ${computedMaxK} 期）`
                  : `后端已算到 ${computedMaxK} 个交易日，${maxK} 期直接切换展示即可，无需重算`
              }
              className="px-2 py-1 text-xs rounded bg-sky-600 text-white disabled:opacity-40"
            >
              {needRecompute ? `重算至 ${maxK} 期` : '重新计算'}
            </button>
            {status === 'running' && (
              <button
                onClick={() => void onCancel()}
                className="px-2 py-1 text-xs rounded border border-red-400 text-red-500"
              >
                取消
              </button>
            )}
            <button onClick={onClose} className="px-2 py-1 text-xs rounded border">
              关闭
            </button>
          </div>
        </div>

        {/* 运行中 */}
        {status === 'running' && (
          <div className="px-4 py-6 text-sm">
            <div className="flex items-center gap-3">
              <div className="flex-1 h-2 bg-slate-100 dark:bg-slate-700 rounded overflow-hidden">
                <div
                  className="h-full bg-sky-500 transition-all"
                  style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
                />
              </div>
              <span className="text-slate-500 w-12 text-right">{progress.toFixed(0)}%</span>
            </div>
            <div className="mt-2 text-slate-500 text-xs">{message}</div>
          </div>
        )}

        {/* 失败 / 取消 */}
        {(status === 'failed' || status === 'cancelled') && (
          <div className="px-4 py-6 text-sm">
            <div className="text-red-500">{error || '任务已取消'}</div>
            <div className="text-slate-400 text-xs mt-1">
              提示：事件研究仅适用于 0/1 二值信号；若该因子在区间内从未触发也会无结果。
            </div>
          </div>
        )}

        {/* 结果 */}
        {status === 'success' && result && result.curve && result.upside && (
          <div className="px-4 py-3 text-xs">
            {/* 摘要卡片 */}
            <div className="grid grid-cols-6 gap-2 mb-3">
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">触发事件数</div>
                <div className="text-base font-semibold">{result.n_events}</div>
                <div className="text-slate-400">
                  剔除前 {result.n_raw}
                  {result.n_short != null && result.n_short > 0 && (
                    <>
                      　·　
                      <span
                        className="text-amber-600 dark:text-amber-400 cursor-help"
                        title={
                          `在 ${result.max_k ?? computedMaxK} 期口径下可统计 ${result.n_aligned ?? '-'} 个；` +
                          `另有 ${result.n_short} 个因信号日贴近数据末尾（未平仓）未计入该期` +
                          (result.n_unaligned ? `，${result.n_unaligned} 个完全无法对齐` : '') +
                          `。各期的有效样本数见曲线 hover；明细见页面底部「未纳入统计的触发（数据不足）」`
                        }
                      >
                        {result.n_short} 个未平仓
                      </span>
                    </>
                  )}
                </div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">持有 {lastPoint?.k ?? '-'} 日中位数</div>
                <div className="text-base font-semibold">{pct(lastPoint?.median)}</div>
                <div className="text-slate-400">均值 {pct(lastPoint?.mean)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">绝对收益胜率（{lastPoint?.k ?? '-'}日）</div>
                <div className="text-base font-semibold">{num(lastPoint?.win, 1)}</div>
                <div className="text-slate-400">p25 {pct(lastPoint?.p25)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">曾达 +50%（期内）</div>
                <div className="text-base font-semibold">{num(result.upside.reach50, 1)}</div>
                <div className="text-slate-400">+20% {num(result.upside.reach20, 1)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">理想最高点卖出中位数</div>
                <div className="text-base font-semibold">{pct(result.upside.median)}</div>
                <div className="text-slate-400">翻倍 {num(result.upside.reach100, 1)}</div>
              </div>
              <div className="rounded border border-slate-200 dark:border-slate-700 p-2">
                <div className="text-slate-400">
                  k={viewCurve.length ? viewCurve[viewCurve.length - 1].k : '-'} 超额
                </div>
                <div className="text-base font-semibold">
                  {excessAtLast == null ? '-' : `${(excessAtLast * 100).toFixed(3)}%`}
                </div>
                <div className="text-slate-400">
                  触发 − 未触发（日均值口径）
                  {excessMedianAtLast != null && (
                    <>　·　中位数 {(excessMedianAtLast * 100).toFixed(3)}%</>
                  )}
                </div>
              </div>
            </div>

            {/* 结论提示 */}
            {verdictHint && (
              <div
                className={`mb-3 rounded px-3 py-2 ${
                  verdictHint.tone === 'warn'
                    ? 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
                    : verdictHint.tone === 'good'
                      ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                      : verdictHint.tone === 'bad'
                        ? 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300'
                        : 'bg-slate-50 text-slate-600 dark:bg-slate-700/40 dark:text-slate-300'
                }`}
              >
                {verdictHint.text}
              </div>
            )}

            {/* 曲线 */}
            <div className="border border-slate-200 dark:border-slate-700 rounded p-2 mb-3">
              <div className="text-slate-500 mb-1">
                持有期收益曲线（T+1 收盘买入，持有 k 个交易日；单位 %）
                {/* v1.19.25：新增锚点图例（紫点 = 判定有效的持有日；悬停 tooltip 也会写 有效/待观察）
                    v1.19.40：**红点 = 判定「反向有效」的持有日**（研究线索，不是好消息） */}
                <span className="text-violet-600 dark:text-violet-400 ml-2">
                  ● 紫点 = 判定「有效」的持有日
                </span>
                <span className="text-red-600 dark:text-red-400 ml-2">
                  ● 红点 = 判定「反向有效」的持有日（研究线索：可查能否当离场因子）
                </span>
                <span className="text-amber-600 dark:text-amber-400 ml-2">
                  ⚠ 两套口径不可混算。默认只显示两对<b>同口径</b>曲线：
                  ① 「触发组(日配对)」↔「基准·未触发组(日配对)」（每个配对日 1 票，可相减 → 差值见下方超额曲线）；
                  ② 「中位数(事件级)」↔「基准中位数·未触发组(事件级)」（每个事件 1 票）。
                  「均值(事件级)」与 p25/p75 无同口径基准，已默认隐藏，点击图例可开启——<b>请勿用它减「基准(日配对)」</b>。
                </span>
              </div>
              {/* 触发集中度：配对日数 vs 事件数。两者相差越大，说明触发在时间上越集中，
                  事件级均值越容易被少数「触发密集日」抬高（而日配对口径不受其影响）——
                  这正是「事件级均值为正、日配对超额为负」这类现象的判读依据。 */}
              {result?.baseline?.n_pair_days != null && (
                <div className="text-slate-500 mb-1">
                  配对日 <span className="font-semibold text-slate-700 dark:text-slate-200">{result.baseline.n_pair_days}</span> 天
                  {' / '}
                  事件 <span className="font-semibold text-slate-700 dark:text-slate-200">{result.n_events}</span> 个
                  {result.baseline.n_pair_days > 0 && (
                    <>
                      {'　·　平均每个配对日触发 '}
                      <span className="font-semibold text-slate-700 dark:text-slate-200">
                        {(result.n_events / result.baseline.n_pair_days).toFixed(1)}
                      </span>
                      {' 个'}
                    </>
                  )}
                  {(result.n_events / Math.max(1, result.baseline.n_pair_days)) >= 3 && (
                    <span className="text-amber-600 dark:text-amber-400 ml-2">
                      ⚠ 触发高度集中 —— 事件级均值易被少数密集触发日抬高，请以「日配对」超额为准
                    </span>
                  )}
                </div>
              )}
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={chartData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                  <XAxis dataKey="k" tick={{ fontSize: 11 }} label={{ value: 'k (交易日)', position: 'insideBottomRight', offset: -2, fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={45} />
                  <Tooltip
                    formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(3)}%` : v)}
                    labelFormatter={(l) =>
                      /* v1.19.26：卡片里「有效 ✓」标绿（沿用结论列的 emerald）、「待观察」保持默认色不变（用户要求）。
                         ⚠ Recharts 会判断 `React.isValidElement` ⇒ 返回 JSX 原样渲染（不会被转成字符串）。 */
                      validKind.get(Number(l)) === 'good' ? (
                        <span>
                          持有 {l} 个交易日 ·{' '}
                          <span className="text-emerald-700 dark:text-emerald-300 font-semibold">
                            有效 ✓
                          </span>
                        </span>
                      ) : validKind.get(Number(l)) === 'reverse' ? (
                        <span>
                          持有 {l} 个交易日 ·{' '}
                          <span className="text-red-600 dark:text-red-400 font-semibold">
                            反向有效 ⟲
                          </span>
                        </span>
                      ) : (
                        <span>持有 {l} 个交易日 · 待观察</span>
                      )
                    }
                  />
                  <Legend
                    wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
                    onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                  />
                  {/* 成对口径（每组两条同口径、可互比）：
                      ① 日配对：trigger_pair ↔ baseline —— 可相减，差值即下方「超额曲线」
                      ② 事件级中位数：median ↔ baseline_median
                      默认隐藏的 mean / p25 / p75 见 hidden 初始值处的说明。 */}
                  {/* v1.19.25：紫色「触发组(日配对)」曲线**只在判定有效的持有日**画锚点圆点 */}
                  <Line type="monotone" dataKey="trigger_pair" name="触发组(日配对)" stroke="#7c3aed" dot={validAnchorDot('trigger_pair', '#7c3aed')} strokeWidth={2} hide={!!hidden.trigger_pair} />
                  <Line type="monotone" dataKey="baseline" name="基准·未触发组(日配对)" stroke="#94a3b8" dot={false} strokeWidth={1.5} strokeDasharray="4 4" hide={!!hidden.baseline} />
                  <Line type="monotone" dataKey="median" name="中位数(事件级)" stroke="#0284c7" dot={false} strokeWidth={2} hide={!!hidden.median} />
                  <Line type="monotone" dataKey="baseline_median" name="基准中位数·未触发组(事件级)" stroke="#0e7490" dot={false} strokeWidth={1.5} strokeDasharray="2 2" hide={!!hidden.baseline_median} />
                  <Line type="monotone" dataKey="p75" name="p75(事件级·默认隐藏)" stroke="#cbd5e1" dot={false} strokeWidth={1} hide={!!hidden.p75} />
                  <Line type="monotone" dataKey="p25" name="p25(事件级·默认隐藏)" stroke="#cbd5e1" dot={false} strokeWidth={1} hide={!!hidden.p25} />
                  <Line type="monotone" dataKey="mean" name="均值(事件级·无同口径基准)" stroke="#dc2626" dot={false} strokeWidth={2} strokeDasharray="6 2" hide={!!hidden.mean} />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {/* 超额曲线：触发组 − 基准（未触发组）。两条线口径不同、不可相加：
                - 均值：日配对口径（先按日截面均值、再对配对日平均）→ 易被少数暴涨事件主导
                - 中位数：事件级口径（全部样本收益的中位数）→ 刻画「典型一次触发」的超额
                点击图例可单独隐藏/显示。 */}
            {excessChart.length > 0 && (
              <div className="border border-slate-200 dark:border-slate-700 rounded p-2 mb-3">
                <div className="text-slate-500 mb-1">
                  超额曲线（触发组 − 基准·未触发组；单位 %）
                  <span className="text-violet-600 dark:text-violet-400 ml-2">
                    ● 紫点 = 判定「有效」的持有日
                  </span>
                  <span className="text-red-600 dark:text-red-400 ml-2">
                    ● 红点 = 判定「反向有效」的持有日
                  </span>
                  <span className="text-slate-400 ml-2">
                    均值＝日配对口径（易被少数暴涨事件主导）；中位数＝事件级口径（典型一次触发的超额）
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={170}>
                  <LineChart data={excessChart} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="k" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} width={45} />
                    <Tooltip
                      formatter={(v: number | string) => (typeof v === 'number' ? `${v.toFixed(3)}%` : v)}
                      labelFormatter={(l) =>
                      /* v1.19.26：卡片里「有效 ✓」标绿（沿用结论列的 emerald）、「待观察」保持默认色不变（用户要求）。
                         ⚠ Recharts 会判断 `React.isValidElement` ⇒ 返回 JSX 原样渲染（不会被转成字符串）。 */
                      validKind.get(Number(l)) === 'good' ? (
                        <span>
                          持有 {l} 个交易日 ·{' '}
                          <span className="text-emerald-700 dark:text-emerald-300 font-semibold">
                            有效 ✓
                          </span>
                        </span>
                      ) : validKind.get(Number(l)) === 'reverse' ? (
                        <span>
                          持有 {l} 个交易日 ·{' '}
                          <span className="text-red-600 dark:text-red-400 font-semibold">
                            反向有效 ⟲
                          </span>
                        </span>
                      ) : (
                        <span>持有 {l} 个交易日 · 待观察</span>
                      )
                    }
                    />
                    <Legend
                      wrapperStyle={{ fontSize: 11, cursor: 'pointer' }}
                      onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                    />
                    <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
                    <Line
                      type="monotone"
                      dataKey="excess"
                      name="超额(均值·日配对)"
                      stroke="#7c3aed"
                      /* v1.19.25：紫色「日配对超额」曲线同样**只在判定有效的持有日**画锚点圆点 */
                      dot={validAnchorDot('excess', '#7c3aed')}
                      strokeWidth={2}
                      hide={!!hidden.excess}
                    />
                    {hasExcessMedian && (
                      <Line
                        type="monotone"
                        dataKey="excess_median"
                        name="超额(中位数·事件级)"
                        stroke="#0d9488"
                        dot={false}
                        strokeWidth={2}
                        strokeDasharray="5 3"
                        hide={!!hidden.excess_median}
                      />
                    )}
                  </LineChart>
                </ResponsiveContainer>
                {hasExcessMedian && excessAtLast != null && excessMedianAtLast != null && (
                  <div className="text-slate-400 mt-1">
                    差值（k={viewCurve.length ? viewCurve[viewCurve.length - 1].k : '-'}）：均值超额{' '}
                    {(excessAtLast * 100).toFixed(3)}%　中位数超额{' '}
                    {(excessMedianAtLast * 100).toFixed(3)}%　
                    {excessAtLast - excessMedianAtLast > 0.01
                      ? '→ 均值明显高于中位数，超额主要来自少数暴涨事件'
                      : '→ 两者接近，超额较普遍而非仅靠尾部'}
                  </div>
                )}
              </div>
            )}

            {/* 净值曲线（v1.19.60）：用**任务里已算好的触发事件** + 缓存价格面板跑一次回测
                （实测 0.12~0.6s/次）⇒ 持仓周期可以自己调、净值跟着变；按 k 缓存 ⇒ 往回改秒开。 */}
            <div className="border border-slate-200 dark:border-slate-700 rounded p-2 mb-3">
              <div className="flex flex-wrap items-center gap-3 text-xs mb-1">
                <span className="text-slate-500">净值曲线（两种资金方案 + 基准）</span>
                <label className="flex items-center gap-1 text-slate-500">
                  持仓周期
                  <input
                    type="number"
                    min={1}
                    max={250}
                    className="w-16 border rounded px-1 py-0.5"
                    value={navKInput}
                    onChange={(e) => {
                      const v = e.target.value
                      setNavKInput(v)                       // 显示什么就是什么（**可空** ✓ 能删光）
                      const n = Number(v)
                      if (v.trim() !== '' && Number.isFinite(n) && n >= 1) {
                        setNavK(Math.max(1, Math.min(250, Math.round(n))))   // 合法 ⇒ 才发请求 ✓
                      } else if (v.trim() === '' && lastOkKRef.current != null) {
                        setNavK(lastOkKRef.current)         // 删光 ⇒ 回到上一次成功的那条（**命中缓存秒回** ✓）
                      }
                      // 其余非法输入（如 0 / 负数）⇒ 什么都不做 ⇒ **图保留上次** ✓
                    }}
                  />
                  天
                </label>
                <label className="flex items-center gap-1 text-slate-500">
                  成本（往返）
                  <input
                    type="number"
                    step={0.0005}
                    min={0}
                    className="w-20 border rounded px-1 py-0.5"
                    value={navCost}
                    onChange={(e) => setNavCost(Number(e.target.value))}
                  />
                </label>
                <button
                  type="button"
                  onClick={() => setNavTick((t) => t + 1)}
                  disabled={navBusy}
                  className="border rounded px-2 py-0.5 text-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
                >
                  {navBusy ? '计算中…' : navRes ? '重新计算（按当前持仓周期）' : '开始计算'}
                </button>
                <span className="text-amber-600 dark:text-amber-500">
                  已改为<strong>手动计算</strong>：高触发公式一次回测可达 1 分钟，
                  改完持仓周期后请点左侧按钮 ✓
                </span>
                {navBusy && <span className="text-sky-600">计算中…（期间旧曲线保留）</span>}
                {/* ⚠ v1.2.10（用户 2026-09-18 要求）：**删掉**「取价 0s + 回测 0s（按持仓周期缓存，
                    改回去秒开）」那一行 ✗ —— 命中缓存时它显示 "0s + 0s"，既没信息量又占地方 ✓。
                    （耗时仍可从响应 `timings` 与后端日志查看 ✓，不占界面 ✓） */}
              </div>
              {navErr ? (
                <div className="text-slate-400 text-xs">净值曲线暂不可用：{navErr}</div>
              ) : navRes ? (
                <>
                  <ZoomableLineChart
                    data={navRes.nav}
                    keys={navRes.nav_columns}
                    labelOf={navLabel}
                    colorOf={navColor}
                    dashOf={navDash}
                    focus={navRes.alloc_default}
                    hidden={hidden}
                    onToggle={toggleSeries}
                    statKey={navRes.alloc_default}
                    height={240}
                  />
                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-slate-400 mt-1">
                    {Object.entries(navRes.stats ?? {}).map(([m, st]: [string, any]) => (
                      <span key={m}>
                        {navLabel(m)}：期末 <b className="text-slate-600 dark:text-slate-300">{st.final_nav}</b>
                        　最大回撤 {pct(st.perf?.max_drawdown, 1)}
                        　成交 {st.trades} 笔
                        {st.rejects_no_cash ? `（资金不足被拒 ${st.rejects_no_cash}）` : ''}
                      </span>
                    ))}
                  </div>
                </>
              ) : (
                <div className="text-slate-400 text-xs">（选好持仓周期后自动计算；首次约 0.1~0.6s）</div>
              )}
            </div>

            {/* ★ 分布图（v1.19.81，用户 2026-09-17）：「干脆两个概率表都改成分布图吧，这不是很直观嘛，
                中位数、均值、后尾啥的直接都能看到了，省得自己划分档位对吧？」
                同意 —— 分档是**有损压缩**：5 个数丢掉整个形状，而且得先决定档位才知道看什么。
                直方图 + 均值/中位数/尾部标注一次给全（形状 + 位置 + 两侧尾巴），并随上面的
                「最长持有」滑动联动（换 k 就换一张分布）。
                ⚠ 后端 `prob[]`（收益/亏损两侧分档）**保留**：旧结果兼容，且需要精确档位数字时仍可用。 */}
            <div className="border border-slate-200 dark:border-slate-700 rounded p-2">
              <div className="text-slate-500 mb-1">
                持有 {lastPoint?.k ?? '-'} 日收益分布（每次触发一个样本；纵轴 = 该区间事件占比）
                <span className="text-slate-400 ml-2">n={lastPoint?.n ?? '-'}</span>
                <span className="text-slate-400 ml-2">
                  （T+1 收盘买入；已剔除 T 涨停 / T+1 涨停 / T+1 停牌样本）
                </span>
                {result.n_short != null && result.n_short > 0 && (
                  <span className="text-amber-600 dark:text-amber-400">
                    {' '}
                    另有 {result.n_short} 个触发在 {result.max_k ?? computedMaxK} 期上尚未平仓（数据不足），未计入
                  </span>
                )}
              </div>
              <EventDistChart
                edges={result.dist_edges}
                counts={lastPoint?.counts}
                k={lastPoint?.k ?? null}
                stat={lastPoint}
              />
            </div>

            <div className="grid grid-cols-2 gap-3 mt-3">
              {/* Top 事件 */}
              <div className="border border-slate-200 dark:border-slate-700 rounded p-2">
                <div className="text-slate-500 mb-1">贡献最大的事件（持有 {lastPoint?.k ?? '-'} 日收益）</div>
                <table className="w-full">
                  <thead>
                    <tr className="text-slate-400">
                      <th className="text-left font-normal">代码</th>
                      <th className="text-left font-normal">信号日</th>
                      <th className="text-right font-normal">{lastPoint?.k ?? '-'}日</th>
                      <th className="text-right font-normal">期内最高</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topEvents.slice(0, 8).map((e, i) => (
                      <tr key={`${e.code}:${e.dt}:${i}`} className="border-t border-slate-100 dark:border-slate-700">
                        <td>{e.code}</td>
                        <td>{e.dt}</td>
                        <td className="text-right">{pct(e.ret, 2)}</td>
                        <td className="text-right">{pct(e.max_ret, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 亏损最大事件（与 Top 榜对称：也同样按当前 k 取逐 k 榜单） */}
              <div className="border border-slate-200 dark:border-slate-700 rounded p-2">
                <div className="text-slate-500 mb-1">亏损最大的事件（持有 {lastPoint?.k ?? '-'} 日收益）</div>
                <table className="w-full">
                  <thead>
                    <tr className="text-slate-400">
                      <th className="text-left font-normal">代码</th>
                      <th className="text-left font-normal">信号日</th>
                      <th className="text-right font-normal">{lastPoint?.k ?? '-'}日</th>
                      <th
                        className="text-right font-normal"
                        title="期内最低（到当前 k 期为止的最大浮亏）—— 与左表「期内最高」对称"
                      >
                        期内最低
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {worstEvents.slice(0, 8).map((e, i) => (
                      <tr key={`${e.code}:${e.dt}:${i}`} className="border-t border-slate-100 dark:border-slate-700">
                        <td>{e.code}</td>
                        <td>{e.dt}</td>
                        <td className="text-right text-red-500 dark:text-red-400">{pct(e.ret, 2)}</td>
                        <td className="text-right">{pct(e.min_ret, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* 数据不足（未平仓 / 无法对齐）的事件：此前被静默丢弃，界面上看不出来 */}
            {(result.n_short != null && result.n_short > 0) ||
            (result.n_unaligned != null && result.n_unaligned > 0) ? (
              <div className="mt-3 border border-amber-300 dark:border-amber-700/60 rounded p-2 bg-amber-50/50 dark:bg-amber-900/10">
                <div className="text-amber-700 dark:text-amber-400 mb-1">
                  未纳入统计的触发（数据不足）
                  {result.n_short != null && result.n_short > 0 && (
                    <>
                      　·　<span className="font-semibold">{result.n_short}</span> 个触发未平仓
                      （信号日距数据末尾不足 {result.max_k ?? computedMaxK} 个交易日，无法持满 {result.max_k ?? computedMaxK} 日）
                    </>
                  )}
                  {result.n_unaligned != null && result.n_unaligned > 0 && (
                    <>
                      　·　<span className="font-semibold">{result.n_unaligned}</span> 个无法对齐
                      （信号日或 T+1 缺行情：连买入价都取不到）
                    </>
                  )}
                </div>
                <div className="text-slate-500 mb-1">
                  这些事件不会出现在上面的曲线与「贡献最大 / 最差事件」里（对应 k 的样本数会相应变少）。
                  若数量偏多，说明 <span className="font-semibold">评估区间末端已接近数据末尾</span>，
                  建议把结束日期提前 {result.max_k ?? computedMaxK} 个交易日以上。
                </div>
                {(result.short_events ?? []).length > 0 && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1 max-h-24 overflow-auto">
                    {(result.short_events ?? []).slice(0, 30).map((e, i) => (
                      <span key={`${e.code}:${e.dt}:${i}`} className="text-slate-500">
                        {e.code} <span className="text-slate-400">{e.dt}</span>{' '}
                        <span className="text-amber-600 dark:text-amber-400">已得 {e.n_valid_k} 期</span>
                      </span>
                    ))}
                    {(result.short_events ?? []).length > 30 && (
                      <span className="text-slate-400">
                        …另有 {(result.short_events ?? []).length - 30} 个
                      </span>
                    )}
                  </div>
                )}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
