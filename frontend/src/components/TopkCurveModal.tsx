import { useMemo, useRef, useState, type ReactNode } from 'react'
import { captureSection } from '../captureChart'
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
import type { PerfMetrics, SingleFactorTestResult, TopKCurveItem } from '../api'
import { blobErrorDetail, exportTopkCurveXlsx } from '../api'
import { saveBlob, stamp } from '../downloadFile'

/**
 * 连续因子「持仓期收益曲线」弹窗（v1.18.45）。
 *
 * 数据全部来自单因子测试结果（**打开即秒开，不提交任务**）：
 *   · `topk_curves`       —— 明细曲线：items[0] = **超额收益最强的分位组**（逐日等分），
 *                            其余为固定 K 档（TopK，可点选切换 ⇒ 纯前端切换、零重算）
 *   · `quantile_curves`   —— 十分位累计收益曲线（10 条 + 多空）
 *   · `topk_sensitivity`  —— 各 K 的换手 / 三档年化成本 / 成本吞噬比例
 *
 * ⚠ 口径（页面必须显著标注）：成本为**往返合计**费率的**简化估算**；**买入端**已按剔除开关剔除
 * T/T+1 涨停、T+1 停牌样本（`single_test._exclude`），**未模拟「跌停卖不出」**、未计流动性冲击；
 * 曲线默认**复利**（v1.18.47 起：每期收益滚入本金 = 实盘满仓复投，可切「算术累加」）、
 * **近似净值但不是逐日盯市净值**；多空**不可实现**（A 股空头收益拿不到），仅作有效性参考。
 */
interface Props {
  open: boolean
  onClose: () => void
  name: string
  row: SingleFactorTestResult | null
  /** 单因子测试任务 id：导出 XLSX 要靠它取曲线 / 参数 / **每期持仓索引** ✓
   *  （空 ⇒ 导出按钮禁用，并提示"任务上下文缺失"—— 后端不出半份文件 ✗）。 */
  taskId?: string
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

/** 把数据范围 `[lo, hi]` 化为「**整数百分比 + 均匀分段**」的 Y 轴（v1.19.9）。
 *
 *  用户要求：十分位图刻度要**整数百分比**且**等距**（不要 `−71%` / `128.8%` / `499.1%` 这种）。
 *  做法：先把上下界按 **1%** 向外取整，再选一个「好看的」步长（1/2/5/10/20/25/50/100…%）
 *  使刻度数落在 3~7 个；`domain` 与 `ticks` 都落在**整数百分比**上，且必然含 **0**
 *  （0 是任何 step 的整数倍）。返回的是**小数域**（`0.5` = 50%），recharts 直接可用。
 */
const NICE_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000]
function niceAxis(lo: number, hi: number) {
  const pLo = Math.floor(Math.min(0, lo) * 100)
  const pHi = Math.ceil(Math.max(0, hi) * 100)
  const span = Math.max(1, pHi - pLo)
  const step = NICE_STEPS.find((s) => span / s <= 6) ?? Math.ceil(span / 6)
  const t0 = Math.floor(pLo / step) * step
  let t1 = Math.ceil(pHi / step) * step
  if (t1 === t0) t1 = t0 + step                      // 数据恒为 0：给一段最小可视范围
  const ticks: number[] = []
  for (let v = t0; v <= t1; v += step) ticks.push(v / 100)
  return { domain: [t0 / 100, t1 / 100] as [number, number], ticks }
}

/** 明细档显示名：分位组标「最强分位组 Q几」；固定 K 档标「固定 K=…」（默认档加注）。 */
function itemLabel(it: TopKCurveItem, defaultK: number | null) {
  if (it.kind === 'decile') return `最强分位组 Q${it.quantile}`
  const isDefault = defaultK != null && it.k === defaultK
  return `固定 K=${it.k}${isDefault ? '（默认 10% 档）' : ''}`
}

/** 无量纲比率显示（夏普/索提诺/卡玛）：2 位小数，缺失给 `-`。 */
const fmtRatio = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? '-' : v.toFixed(2)

/**
 * 绩效指标卡（v1.18.48）：**逐日盯市**口径（期内按 h 调仓、持有期内逐日盯市）。
 * 组合与基准各一张 —— 只有同口径对比，才能判断「这份超额值不值得承担这些风险」。
 * ⚠ 指标基于**净值** ⇒ 只有复利口径才有意义（算术累加不是净值，回撤/波动无从定义）。
 */
function PerfCard({
  title,
  titleExtra,
  perf,
  extra,
  note,
}: {
  title: string
  /** 标题行右侧的自定义控件（如「成本档」切换选项卡）。 */
  titleExtra?: ReactNode
  perf?: PerfMetrics | null
  extra?: string
  /** 覆盖补充行末尾的口径说明（默认「逐日盯市、rf=0」）；同时把「日胜率」改为「期胜率」。 */
  note?: string
}) {
  if (!perf) return null
  const cell = (label: string, val: string, cls = '') => (
    <div className="text-center">
      <div className="text-slate-400">{label}</div>
      <div className={`font-semibold text-slate-700 dark:text-slate-200 ${cls}`}>{val}</div>
    </div>
  )
  return (
    <div className="border border-slate-200 dark:border-slate-700 rounded px-2 py-1">
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-slate-500">{title}</span>
        {titleExtra}
      </div>
      <div className="grid grid-cols-5 gap-1">
        {cell('年化', signedPct(perf.annual_return, 1))}
        {cell('最大回撤', signedPct(perf.max_drawdown, 1), 'text-red-500')}
        {cell('夏普', fmtRatio(perf.sharpe))}
        {cell('索提诺', fmtRatio(perf.sortino))}
        {cell('卡玛', fmtRatio(perf.calmar))}
      </div>
      <div className="text-slate-400 mt-0.5 leading-relaxed">
        年化波动 {pct(perf.vol_annual, 1)}｜{note ? '期胜率' : '日胜率'}{' '}
        {pct(perf.win_rate, 1)}｜样本 {perf.n_days} 个交易日｜{note ?? '逐日盯市、rf=0'}
        {extra ? `｜${extra}` : ''}
      </div>
    </div>
  )
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

export default function TopkCurveModal({ open, onClose, name, row, taskId }: Props) {
  const [sel, setSel] = useState(0)
  /** 绩效卡的成本档（v1.18.54）：默认**往返 0.004**（更贴近实盘；无成本档常被高估）。
   *  仅切换「年化 / 最大回撤 / 夏普 / 索提诺 / 卡玛」等指标卡，不影响曲线图（三档成本线始终都在图里、可显隐）。 */
  const [perfCost, setPerfCost] = useState<'0.0000' | '0.0040' | '0.0080'>('0.0040')
  const [bench, setBench] = useState('')
  const [hidden, setHidden] = useState<Record<string, boolean>>({})
  // 口径（v1.18.47）：**默认复利**（= 实盘）；后端旧版无 curves_compound 时自动回退算术
  const [basis, setBasis] = useState<Basis>('compound')
  // v2.1.0：导出 XLSX（**后端生成** ⇒ 第 1 页有原生 Excel 图表、第 2 页是每期持仓明细 ✓）；
  //   导的正是"当前看到的东西"：档位 / 曲线口径 / 基准 / 绩效成本档 ✓（用户 2026-10-11 需求）
  const [expBusy, setExpBusy] = useState(false)
  const [expMsg, setExpMsg] = useState<{ text: string; ok: boolean } | null>(null)
  // 抓图容器：导出时把这两张图**按当前档位**（含用户显隐的曲线 ✓）抓成 PNG 传给后端 ✓
  const navShotRef = useRef<HTMLDivElement | null>(null)
  const decShotRef = useRef<HTMLDivElement | null>(null)
  // v1.19.18：弹窗打开期间锁定页面滚动 —— 修「滚到弹窗底部后继续滚会带动背后主页面滚动」
  // （scroll chaining，用户反馈）。
  useModalScrollLock(open)
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
  // 「池内等权」基准（v1.18.57）：后端只下发**指数**（自由流通市值加权）；等权版由
  // `quantile_curves` 各分位组的逐期收益**平均**推得（等频分组 ⇒ 等价于池内等权）。
  // ⚠ **默认不选中**（默认仍按股票池映射到指数），仅在用户手动切换时启用 ——
  // 它与聚宽 / 外部检验脚本的「池内等权」口径一致，用来解释"相对指数 vs 相对等权"的差额。
  const EQW_CODE = '__eqw__'

  /**
   * 导出当前档位的曲线与每期持仓（v2.1.0）。
   *
   * ⚠ 三处刻意的取舍：
   *   ① 数据**不从前端拼**：每期持仓只在后端（测试进程内算完即存在任务状态里 ✓）
   *      ⇒ 请求只带"当前界面的选择"，本体由后端组装（导出的数值必然与页面逐位一致 ✓）；
   *   ② 「池内等权」基准是**前端现算的伪基准**（`EQW_CODE`）⇒ 传空，让文件用后端默认指数 ✓
   *      （否则后端找不到该代码，会静默少一列基准 ✗）；
   *   ③ 失败原因走 `blobErrorDetail`：blob 请求的报错体是 JSON（否则界面上只能看到"导出失败" ✗）。
   *   ④ **曲线图是"截图"**：把当前渲染的 `<svg>` 抓成 PNG 一起传 ⇒ 文件里的图就是你眼前的图
   *      （档位/基准/显隐都一致 ✓）。抓图失败**不影响导出**（后端会写明"未附图"并指路数值页 ✓）。
   */
  const doExport = async () => {
    if (!row || !item || !taskId || expBusy) return
    setExpBusy(true)
    setExpMsg(null)
    try {
      const effBench = bench && bench !== EQW_CODE ? bench : (tc?.benchmarks?.default ?? '')
      let shots: { nav?: string; decile?: string } = {}
      try {
        // ⚠ 串行（不是 Promise.all）：`captureSection` 会临时滚动再恢复 ⇒ 并发会互相抄滚动位置 ✗
        const nav = await captureSection(navShotRef.current)
        const decile = await captureSection(decShotRef.current)
        shots = { ...(nav ? { nav } : {}), ...(decile ? { decile } : {}) }
      } catch (shotErr) {
        // 抓图是"锦上添花"⇒ 绝不因为它把整个导出搞失败 ✗（后端会如实写"未附图" ✓）
        console.warn('曲线截图失败，改为导出不含图的文件：', shotErr)
      }
      const { blob, filename } = await exportTopkCurveXlsx({
        task_id: taskId,
        factor_id: row.id || row.name || '',
        horizon: row.horizon ?? null,
        item_kind: item.kind === 'decile' ? 'decile' : 'topk',
        item_k: item.kind === 'topk' ? item.k : null,
        item_quantile: item.kind === 'decile' ? (item.quantile ?? null) : null,
        basis: basis === 'compound' ? 'compound' : 'arithmetic',
        benchmark: effBench,
        cost_tier: perfCost,
        shots,
      })
      saveBlob(blob, filename || `持仓曲线_${name}_${stamp()}.xlsx`)
      const withShot = !!(shots.nav || shots.decile)
      setExpMsg({
        ok: true,
        text:
          (withShot
            ? '已导出 ✓（第 1 页含**当前档位/基准/显隐的曲线截图** + 参数与公式）'
            : '已导出 ✓（⚠ 抓图未成功 ⇒ 第 1 页只有参数与数值页，没有图）') +
          '；第 2 页是本档位的每期持仓' +
          (bench === EQW_CODE
            ? '；「池内等权」是页面现算的伪基准 ⇒ 文件里的基准列用的是后端默认指数 ✓'
            : ''),
      })
    } catch (e) {
      setExpMsg({ ok: false, text: await blobErrorDetail(e) })
    } finally {
      setExpBusy(false)
    }
  }
  // 池内等权的**逐期收益序列**（各分位组 cum 差分后取平均；等频分组 ⇒ 等价池内成分等权）。
  // ⚠ 它与组合曲线**完全同口径**（同一批调仓日、同一持有 h、**同样含分红再投**）。
  const eqwPer = useMemo(() => {
    const gs = qc?.groups ?? []
    const n = qc?.dates?.length ?? 0
    if (!gs.length || !n) return null
    const per: number[][] = gs.map((g) => {
      const cum = g.cum ?? []
      const r: number[] = []
      for (let i = 0; i < n; i++) r.push((cum[i] ?? 0) - (i > 0 ? (cum[i - 1] ?? 0) : 0))
      return r
    })
    const out: number[] = []
    for (let i = 0; i < n; i++) out.push(per.reduce((s, x) => s + (x[i] ?? 0), 0) / per.length)
    return out
  }, [qc])
  const eqwBench = useMemo(() => {
    if (!eqwPer) return null
    const cum: number[] = []
    const cmp: number[] = []
    let a = 0
    let c = 1
    for (const r of eqwPer) {
      a += r
      c *= 1 + r
      cum.push(a)
      cmp.push(c - 1)
    }
    return { code: EQW_CODE, name: '池内等权', cum, cum_compound: cmp, dates: [], perf: null }
  }, [eqwPer])
  // 池内等权的绩效指标（v1.18.58）：后端只给**指数**算了逐日盯市 perf；等权基准由前端按
  // **调仓期**近似 —— 年化/最大回撤用期收益序列、夏普/索提诺用期均值/标准差 × sqrt(243/h)。
  // ⚠ 粒度比组合卡的「逐日盯市」粗（卡内 note 会注明）；含分红（与组合同口径）。
  const eqwPerf = useMemo(() => {
    if (!eqwPer || !eqwPer.length) return null
    const n = eqwPer.length
    const h = Math.max(1, Math.round(Number(tc?.rebalance_period) || 1))
    const annF = Math.sqrt(243 / h)
    const mean = eqwPer.reduce((s, x) => s + x, 0) / n
    const sd = Math.sqrt(eqwPer.reduce((s, x) => s + (x - mean) ** 2, 0) / Math.max(n - 1, 1))
    const dsd = Math.sqrt(eqwPer.reduce((s, x) => s + Math.min(x, 0) ** 2, 0) / n)
    let nv = 1
    let peak = 1
    let mdd = 0
    for (const x of eqwPer) {
      nv *= 1 + x
      peak = Math.max(peak, nv)
      mdd = Math.min(mdd, nv / peak - 1)
    }
    const yrs = Math.max((n * h) / 243, 1e-9)
    const annual = nv ** (1 / yrs) - 1
    return {
      annual_return: annual,
      max_drawdown: mdd,
      sharpe: sd > 0 ? (mean / sd) * annF : null,
      sortino: dsd > 0 ? (mean / dsd) * annF : null,
      calmar: mdd < 0 ? annual / Math.abs(mdd) : null,
      vol_annual: sd * annF,
      win_rate: eqwPer.filter((x) => x > 0).length / n,
      n_days: Math.round(n * h),
    } as unknown as PerfMetrics
  }, [eqwPer, tc])
  const benchOptions: { code: string; name: string }[] = [
    ...benchList.map((b) => ({ code: b.code, name: b.name })),
    ...(eqwBench ? [{ code: EQW_CODE, name: eqwBench.name }] : []),
  ]
  const benchCode =
    benchList.find((b) => b.code === bench)?.code ??
    (bench === EQW_CODE && eqwBench ? EQW_CODE : undefined) ??
    benchList.find((b) => b.code === tc?.benchmarks?.default)?.code ??
    benchList[0]?.code ??
    ''
  const benchItem =
    benchCode === EQW_CODE ? eqwBench : (benchList.find((b) => b.code === benchCode) ?? null)
  // 「用数据尾部价兜底」的期数（后端 `n_partial`）：>0 ⇒ 末期持有窗口不足 h 日（数据到尾部了），
  // 组合侧同样是部分持有期（label 的冻结价兜底）⇒ 两侧同口径、可直接相减；仅作提示。
  // （「池内等权」由前端现算、无该字段 ⇒ 恒 0。）
  const benchPartial = benchList.find((b) => b.code === benchCode)?.n_partial ?? 0

  // 图 1：选中档的三档累计曲线（0 / 0.004 / 0.008，均按调仓期扣费）+ 选中基准
  // 口径（basis）：复利取 `curves_compound` / `cum_compound`；后端旧版缺该字段时自动回退算术
  /**
   * ★ v2.1.27：**每期全仓滚动（实盘口径）** 净值（用户 2026-10-11 加的开关 ✓，**默认关** ✓）。
   * 为什么要画在这张图上：面板这条是**组合收益模型**（无资金约束/整手/涨跌停 ✗），而
   * 「交易信号测试」拖回灌的是**真金白银引擎** ⇒ 两者**本该不同** ✗ —— 这条是**同口径实盘版**
   * （与回灌侧**同一个** `run_period_roll` ✓）⇒ **它才该与回灌那条 period_roll 逐点一致** ✓。
   * ⚠ 取值口径：`row.period_roll.nav` 是**逐日**净值 ⇒ 按**调仓日**采样（= 每期期末值 ✓），
   *    与组合曲线「每期一个点」同轴 ✓。
   */
  /**
   * ★ v2.1.35：**实盘口径指标**（`row.period_roll.perf` ✓）—— 用户 2026-10-12 指定：
   * 在图题与图之间的空白处显示「年化 / 最大回撤 / 夏普 / 索提诺 / 卡玛」✓。
   * ⚠ 数据只在测试时勾了「每期全仓滚动（逐点对拍）」开关时才有 ✓（默认关 ⇒ 不显示 ✓），
   *   它与「交易信号测试」回灌那条 `period_roll` 是**同一个实现的两次运行** ✓
   *   ⇒ 这组指标才是"真金白银口径"的 ✓（上面那张卡的 组合 指标是**收益模型**口径 ✗）。
   * ⚠ 单位：`perf.annual_return`/`max_drawdown` 是**小数** ⇒ `signedPct`（内部 ×100 ✓）；
   *   `sharpe`/`sortino`/`calmar` 无量纲 ⇒ `fmtRatio` ✓ —— **与上面指标卡逐字同款** ✓
   *   （单位这事我栽过两次 ✗，这次直接复用同一对函数，不再自己拼 ✓）。
   */
  /**
   * ★ v2.1.38（用户 2026-10-12："应该是我测试的时候选了哪些档位就算哪些档位" ✓）：
   * **按当前选中档取** ✓ —— `row.period_roll_items` 与 `topk_curves.items` **同序** ✓
   * （`idx` 就是当前选中档的下标 ✓，已 clamp ✓）⇒ 在弹窗里切档位时，红线与这行指标
   * **一起变** ✓（v2.1.27~v2.1.37 只算 `items[0]` ✗ ⇒ 切换时它们不动 ✗，用户实测发现 ✓）。
   * 兜底：旧结果没有 `period_roll_items` ⇒ 退回 row 级 `period_roll`（= 默认档 ✓）。
   */
  const prObj = (row?.period_roll_items ?? [])[idx] ?? row?.period_roll ?? null
  const prPerf = prObj?.perf ?? null
  /**
   * ★ v2.1.37：**最大回撤深于 −30% 才标红** ✓（用户 2026-10-12 指定："最大回撤≥30%就标红吧。
   * 其他时候灰色" ✓）。
   * ⚠ 单位（这次先查过再写 ✗→✓）：`perf.max_drawdown` 是**小数**（−0.461 ✓，同卡 `signedPct`
   *   内部 `×100` 才成 −46.1% ✓）⇒ 阈值写 `0.30` ✓ **不能**写 `30` ✗。
   * ⚠ 用 `Math.abs` ✓：不依赖它到底存的是负数（−0.461 ✓）还是幅度（0.461）⇒ 两种约定都对 ✓。
   *   非数值/缺失 ⇒ 不红（走默认灰 ✓）。
   */
  const prDdDeep =
    typeof prPerf?.max_drawdown === 'number'
    && Number.isFinite(prPerf.max_drawdown)
    && Math.abs(prPerf.max_drawdown) >= 0.30

  /**
   * ★★ v2.4.3（用户 2026-10-15 拍板 ✓）：**含离场**那条（与 `period_roll_items` 同序 ✓）。
   * 「对照列必算」的兑现 ✓：上面那份 `period_roll` 就是**不离场**的对照 ✓（口径与数字逐位不变 ✓），
   * 这里再给"加了离场"那条 ⇒ **Δ才是离场因子的净效果** ✓。
   * ⚠ 别用"含离场净值 ÷ 对照净值"当结论 ✗（两条线持仓结构不同 ⇒ 比值混了再平衡溢价 ✓）。
   */
  const exObj = (row?.exit_roll_items ?? [])[idx] ?? row?.exit_roll ?? null
  const exPerf = exObj?.perf ?? null
  /**
   * ★★ v2.5.0（用户 2026-10-15："这个离场因子能不能搞成多选啊，要矩阵化" ✓）：
   * **逐离场各取"当前档位"那一条** ✓（`exit_rolls[i].items[idx]` ✓，与 `topk_curves` 同序 ✓）⇒
   * 下面出对比表：第 1 行永远是「不离场 · 对照」✓（口径与数字逐位不变 ✓），其后每个离场一行 ✓，
   * 并给 **Δ年化 / Δ回撤**（两条同源 ⇒ 差值才是该离场因子的净效果 ✓）。
   * ⚠ 单数形态（v2.4.3 的 `exit_roll_items`）也兼容 ✓：没有 `exit_rolls` 就退回它 ✓。
   */
  const exitRows = ((): {
    name: string
    perf: typeof exPerf
    stats: Record<string, number | undefined>
    deltaAnn: number
    deltaDd: number
    err: string
  }[] => {
    const groups = row?.exit_rolls
    const list = groups && groups.length
      ? groups.map((g) => ({
          name: g?.exit?.name || g?.exit?.id || `离场${(g?.exit?.index ?? 0) + 1}`,
          pr: (g?.items ?? [])[idx] ?? g?.first ?? null,
        }))
      : [{ name: '含离场', pr: exObj }]
    return list.map(({ name, pr }) => {
      const perf = pr?.perf ?? null
      return {
        name,
        perf,
        stats: (pr?.stats ?? {}) as Record<string, number | undefined>,
        deltaAnn: (perf?.annual_return ?? 0) - (prPerf?.annual_return ?? 0),
        deltaDd: (perf?.max_drawdown ?? 0) - (prPerf?.max_drawdown ?? 0),
        err: String(pr?.error ?? ''),
      }
    })
  })()
  const exDdDeep = (v: unknown) =>
    typeof v === 'number' && Number.isFinite(v) && Math.abs(v) >= 0.30

  const prMap = useMemo(() => {
    const m = new Map<string, number>()
    const ds = prObj?.dates ?? []
    const vs = prObj?.nav ?? []
    for (let i = 0; i < ds.length; i++) {
      const v = vs[i]
      if (typeof v === 'number' && Number.isFinite(v)) m.set(ds[i], v)
    }
    return m
    // v2.1.38：依赖 `prObj`（= 当前选中档那条 ✓）⇒ 切档时红线跟着重取 ✓
  }, [prObj])

  const detailData = useMemo(() => {
    if (!tc || !item) return []
    const cv = basis === 'compound' ? (item.curves_compound ?? item.curves) : item.curves
    const bv =
      basis === 'compound' ? (benchItem?.cum_compound ?? benchItem?.cum) : benchItem?.cum
    const st = stride(tc.dates.length)
    const pr = (d: string): number | null => (prMap.size ? (prMap.get(d) ?? null) : null)
    const pick = (i: number) => ({
      d: tc.dates[i],
      c0: nav(cv['0.0000']?.[i]),
      c4: nav(cv['0.0040']?.[i]),
      c8: nav(cv['0.0080']?.[i]),
      b: nav(bv?.[i]),
      pr: pr(tc.dates[i]),
    })
    // v1.18.56 起点对齐：后端 `curves` / 基准 `cum` 的**首值都是「第一期结束时」**
    // （实测 csi300：组合 −5.49%、沪深300 +2.47% 都在第 0 位）⇒ 直接画会让两条线起点
    // 各偏一边（0.945 / 1.025）。这里在序列**最前面补一个「起点」点（值 = 1）**，
    // 使组合 / 三档成本 / 基准的起点统一为 1。
    const out: Record<string, number | string | null>[] = [
      { d: '起点', c0: 1, c4: 1, c8: 1, b: 1, pr: prMap.size ? 1 : null },
    ]
    for (let i = 0; i < tc.dates.length; i += st) out.push(pick(i))
    const last = tc.dates.length - 1
    if (last >= 0 && (out.length === 0 || out[out.length - 1].d !== tc.dates[last])) {
      out.push(pick(last))
    }
    return out
  }, [tc, item, benchItem, basis, prMap])

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
  // 绩效指标（v1.18.54）：按**成本档** `perfCost` 取（默认往返 0.004）—— 三档指标后端都已算好，
  // 切换纯前端零重算；无成本档对应曲线 c0，0.004 / 0.008 对应 c004 / c008。
  // ⚠ 基于净值 ⇒ 仅复利口径展示（算术累加不是净值，回撤/波动无从定义）。
  const itemPerf = item?.perf?.[perfCost] ?? null
  // 基准绩效：指数用后端逐日盯市 perf；「池内等权」无后端 perf ⇒ 用前端按调仓期的近似值
  const benchPerf = benchItem?.perf ?? (benchCode === EQW_CODE ? eqwPerf : null)
  const benchPerfNote =
    benchCode === EQW_CODE ? '按调仓期口径（非逐日盯市）、含分红；与组合同口径' : undefined
  const costAnnual = item?.perf
    ? `三档年化：无成本 ${signedPct(item.perf['0.0000']?.annual_return, 1)}`
      + ` / 0.004 ${signedPct(item.perf['0.0040']?.annual_return, 1)}`
      + ` / 0.008 ${signedPct(item.perf['0.0080']?.annual_return, 1)}`
    : ''

  // 图 2：十分位累计收益曲线（10 条 + 多空 + 基准虚线）
  const decileData = useMemo(() => {
    if (!qc || !qc.groups?.length) return []
    const st = stride(qc.dates.length)
    const cumOf = (g: { cum: number[]; cum_compound?: number[] }) =>
      basis === 'compound' ? (g.cum_compound ?? g.cum) : g.cum
    // 基准（同口径净值）：图 1 的日期轴是 `tc.dates`、图 2 是 `qc.dates`，两者理论上同为调仓日，
    // 但为防长度/起点差异造成错位，这里按**日期字符串**建 Map 对齐（v1.18.55）。
    const bv = basis === 'compound' ? (benchItem?.cum_compound ?? benchItem?.cum) : benchItem?.cum
    // v1.19.8：图 2 全部改成**累计收益**（0 起），不再转成净值（1 起）—— 见下面 decileData 注释。
    const bMap = new Map<string, number | null>()
    if (tc && bv) for (let i = 0; i < tc.dates.length; i++) bMap.set(tc.dates[i], bv[i] ?? null)
    const benchAt = (d: string) => bMap.get(d) ?? null
    // v1.18.56 起点对齐：与图 1 同因 —— 后端 `groups[].cum` / 基准 `cum` 首值都是
    // 「第一期结束时」，故在最前面补一个「起点」点。
    // v1.19.8：分位组与基准的起点由 **1（净值）改为 0（累计收益）** —— 理由见下条注释。
    const startRow: Record<string, number | string | null> = { d: '起点', ls: 0, b: 0 }
    for (const g of qc.groups) startRow[`q${g.quantile}`] = 0
    const out: Record<string, number | string | null>[] = [startRow]
    // ⚠ 多空 = 最强−最弱 的**价差曲线**（不是净值）⇒ 不 +1，走右轴单独看、起点 0。
    //   两种口径都给（v1.19.5）：
    //     · 算术 = `Σ(逐期价差)`       —— 可加，读数 = 累计价差
    //     · 复利 = `Π(1+逐期价差)−1`   —— 每期全额再平衡的美元中性组合（与回测页分层图同口径）
    //   后端旧版无 `cum_compound` 时自动回退算术（不空白）。
    //   v1.19.7：提到**循环外**定义 —— 原在循环内，补末点时（循环后）引用不到。
    const lsv = basis === 'compound'
      ? (qc.long_short.cum_compound ?? qc.long_short.cum)
      : qc.long_short.cum
    for (let i = 0; i < qc.dates.length; i += st) {
      const p: Record<string, number | string | null> = { d: qc.dates[i] }
      for (const g of qc.groups) p[`q${g.quantile}`] = cumOf(g)[i] ?? null
      p.ls = lsv[i] ?? null
      p.b = benchAt(qc.dates[i])          // 基准虚线（同口径**累计收益**、起点 0）
      out.push(p)
    }
    const last = qc.dates.length - 1
    if (last >= 0 && out.length && out[out.length - 1].d !== qc.dates[last]) {
      const p: Record<string, number | string | null> = { d: qc.dates[last] }
      for (const g of qc.groups) p[`q${g.quantile}`] = cumOf(g)[last] ?? null
      // v1.19.7 修：此处原写 `basis === 'arith' ? cum : null` —— 复利模式把**末点**置 null，
      // 导致多空曲线**最后一截断掉**（v1.19.5 改成"复利也画多空"时漏改这一处）。
      p.ls = lsv[last] ?? null
      p.b = benchAt(qc.dates[last])
      out.push(p)
    }
    return out
  }, [qc, basis, benchItem, tc])

  // v1.19.9：左右轴**共用同一 domain**，且刻度按「**整数百分比 + 均匀分段**」生成。
  // 图 2 全部是**累计收益**（0 起，左轴 = 十分位组/基准、右轴 = 多空价差）⇒ 两轴天然同基准；
  // 再喂同一个 domain ⇒ 0 点完全对齐、无多余留白（原先各自 `auto` 时 0 点高度不同）。
  // ⚠ 历史：v1.19.7「到各自基准的最大偏离对称展开」会留大片空白且刻度带长小数（用户反馈）；
  //   v1.19.8 改「统一 0 起 + 共用 domain + toFixed(1) 百分数」，仍会得到 −71% / 128.8% / 499.1%
  //   这类**零散非整数**刻度 ⇒ v1.19.9 交给 `niceAxis` 取整数百分比等距刻度（用户定稿）。
  const yDomains = useMemo(() => {
    let lo = 0
    let hi = 0
    for (const d of decileData) {
      for (const [k, v] of Object.entries(d)) {
        if (k === 'd' || typeof v !== 'number' || !Number.isFinite(v)) continue
        lo = Math.min(lo, v)
        hi = Math.max(hi, v)
      }
    }
    const ax = niceAxis(lo, hi)
    return { left: ax, right: ax }                    // 两轴**共用同一 domain 与 ticks** ⇒ 0 点完全对齐
  }, [decileData])

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
        className="bg-white dark:bg-slate-800 rounded-lg shadow-xl w-[1040px] max-w-full max-h-[92vh] overflow-auto overscroll-contain"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ⚠ `items-start`（v1.20.19，用户 2026-09-18）：标题区是多行文本，用 `items-center`
            会把「关闭」按钮**垂直居中于整个文本块** ⇒ 看着比第一行"掉下去"一截 ✗。
            改成顶部对齐 ⇒ 按钮与**第一行**文字齐平 ✓（同「事件研究」弹窗观感 ✓）。 */}
        <div className="flex items-baseline justify-between gap-3 px-4 py-3 border-b border-slate-200 dark:border-slate-700">
          <div className="min-w-0">
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
              {/* ⚠ 认账（用户 2026-09-18 定稿口径）：
                  ① **「最强 / 最弱分位」是样本内 argmax** —— 事后从十档里挑出来的最优/最差那档，
                     不是样本外结论 ⇒ 不能拿它当"因子有效"的证据（十档不单调 = 没 alpha ✓）；
                  ② **但 TopK 的 K 不是平台挑的** —— K 由用户选择，平台只把整排 K 并列展示；
                     TopK 选股 = **按最强分位所在的方向，取名次最头上的 K 只** ✓（不是 K 扫描后的 argmax ✗）。
                  ⚠ v2.1.0（用户 2026-10-11）：**这句提示不再显示在标题行**（太占宽度、且下面
                     已经有"十档单调性 ρ"与"分段"两条更硬的证据 ⇒ 重复 ✗）。口径本身没变，
                     只是不再写在标题里 ✓；导出文件同步去掉（见 `exports/topk_doc.py` ✓）。 */}
              {/* ★ 单调性（v1.19.94）：|ρ| 小 ⇒ 十档乱跳 = 没有横截面 alpha，`最强档`只是事后挑的噪声 ✗ */}
              {/* ★ 分段稳健性（v1.19.94，用户 2026-09-18）：用**已有的** `groups[].cum`（逐期算术累加）
                  看"最强档 − 最弱档"在几个起点之后**是不是每段都赢** —— 只在某一段赢的多半是
                  风格/时点运气 ✗（LLT 那种"只有 2021 之后好"的，一眼就露馅 ✓）。
                  ⚠ 口径与上面十分位图一致（无成本、算术累加、同一基准线）⇒ 只作稳健性参考 ✓ */}
              {/* ⚠ v1.20.26（用户 2026-09-19）：顶部那行「分位档 / 单调性 / 分段」拼在同一个
                  `<span>` 里，内容一多就**跨行断在中间**（用户截图：「分段（最强−最弱，算累）…」
                  被拆两行 ✗）。⇒ 在「分段」之前强制换行，让它**整块落在第二行** ✓。
                  ⚠ v1.20.27：换行后第二行会从**最左**起（比第一行的 `{name}` 靠左 ✗）⇒
                  用一个**同样式但不可见**（`invisible`）的标题占位撑出等宽缩进 ⇒
                  「分段」与上面的 `{name}`（因子名，如"市值"）**左边界对齐** ✓
                  （不用硬编码宽度：字体/字号一变也自动跟着走 ✓）。 */}
              <br />
              {/* ⚠⚠ v1.20.28（用户 2026-09-19：「还是没对齐，而且字体、大小、颜色也不太一样」）：
                  ① **占位必须带上与真实标题完全相同的字号 `text-xs`** ✗ —— 原先漏了 ⇒ 它继承
                     **更大的默认字号**（16px）⇒ 占位比真实标题**宽** ⇒ 「分段」被推得**偏右** ✗；
                  ② 第二行的「分段」原本用 `text-[11px] text-slate-500` ✗，而第一行用
                     `text-xs text-slate-400` ⇒ **字号小一号、灰色更深** ⇒ 两行风格不一致 ✗
                     ⇒ 统一成 `text-xs text-slate-400`（见下方「分段」那段的 className ✓）；
                  ③ 内部的「+187.5pp」与「十档单调性 ρ=…」是**绿色高亮**（`emerald-700` ✓）
                     —— 这是**有意醒目**的，保持不变 ✓（用户明确要求保留 ✓）。 */}
              <span className="invisible font-semibold text-xs mr-2" aria-hidden="true">
                持仓期收益曲线（含三档成本）
              </span>
              {qc && (() => {
                const gb = qc.groups.find((g) => g.quantile === qc.best_quantile)
                const gw = qc.groups.find((g) => g.quantile === qc.worst_quantile)
                if (!gb || !gw) return null
                // ⚠⚠ v1.19.95 修 bug（用户 2026-09-18 抓到）：以前用「第一个 ≥ YYYY-01-01 的日期」
                //   找分段起点 —— 若该年份**早于数据起点**，findIndex 会返回 **0** ✗ ⇒ 这些年份
                //   全部退化成"整段"，于是 2013/2018/2021 显示**同一个数**（实测都是 126.4pp ✗）。
                //   ⇒ 只保留 **≥ 数据实际起点年** 的年份（其余不可评估，直接不显示 ✓）。
                const _y0 = Number(String(qc.dates[0] ?? '').slice(0, 4)) || 0
                const segs = [2013, 2018, 2021, 2023]
                  .filter((y) => y >= _y0)
                  .map((y) => {
                    const i = qc.dates.findIndex((d) => d >= `${y}-01-01`)
                    if (i < 0 || i >= qc.dates.length - 1) return null
                    const last = qc.dates.length - 1
                    const db = gb.cum[last] - gb.cum[i]
                    const dw = gw.cum[last] - gw.cum[i]
                    return { y, spread: db - dw, win: db > dw }
                  })
                  .filter((x): x is { y: number; spread: number; win: boolean } => !!x)
                if (!segs.length) return null
                const nWin = segs.filter((s) => s.win).length
                return (
                  <span
                    className="text-xs text-slate-400"
                    title="用十分位图的逐期累计（无成本、算术累加）算「最强档 − 最弱档」在各起点之后的表现；只在某一段赢 ⇒ 多半是风格/时点运气"
                  >
                    {/* ⚠⚠ v1.20.29（用户 2026-09-19：「还是没对齐」）：这段是**行首**，
                        原先以**全角空格** `　` 开头（那是"段间分隔"的习惯写法 ✗）⇒
                        等于凭空多 ~1 个汉字宽（12px 字号下 ≈12px）⇒ 整块**右移** ✗，
                        怎么调占位都对不齐 ✓。⇒ 行首**去掉**全角空格（行内其余 `　` 分隔保留 ✓）。 */}
                    {'分段（最强−最弱，算累）：'}
                    {segs.map((s) => (
                      <span key={s.y}>
                        {s.y}
                        {'起 '}
                        <b className={s.win ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-600 dark:text-red-400'}>
                          {s.spread >= 0 ? '+' : ''}
                          {(s.spread * 100).toFixed(1)}pp
                        </b>
                        {'　'}
                      </span>
                    ))}
                    {nWin === segs.length ? '（每段都赢 ✓）' : `（仅 ${nWin}/${segs.length} 段赢 ✗）`}
                  </span>
                )
              })()}
              {qc && qc.monotonicity != null && (
                <span
                  className={
                    Math.abs(qc.monotonicity) < 0.5
                      ? ' text-red-600 dark:text-red-400 font-semibold'
                      : ' text-emerald-700 dark:text-emerald-300'
                  }
                  title="十档均值 vs 档位的 Spearman：越接近 ±1 越单调（单调只是必要条件，也可能是风格暴露）"
                >
                  {'　十档单调性 ρ='}
                  {qc.monotonicity.toFixed(2)}
                  {Math.abs(qc.monotonicity) < 0.5 ? '（无单调性 ⇒ 别信"最强档"）' : ''}
                </span>
              )}
            </span>
          </div>
          {/* 两个按钮**必须同组靠右**（v2.1.0，用户 2026-10-11："导出按钮离关闭按钮太远了"）：
              ⚠ 原先两个按钮是标题栏 `justify-between` 下的**两个独立子项** ⇒ 被均匀分摊到整行 ✗
                （标题块越短、两个按钮被撑得越开 ✓ 实测就是这样）。⇒ 包一层 `flex gap-2` ✓。 */}
          <div className="flex items-center gap-2 shrink-0">
            {/* 导出（v2.1.0）：放在「关闭」左边 ✓ —— 导当前档位/口径/基准】
                `disabled`：任务 id 缺失（历史结果）或档位没有曲线时**禁用**，
                宁可点不动也不出半份文件 ✗；`title` 说明为什么点不动 ✓。 */}
            <button
              onClick={doExport}
              disabled={expBusy || !taskId || !item}
              title={
                !taskId
                  ? '这次结果没有任务上下文（如页面刷新后从历史记录打开）⇒ 无法导出（持仓明细在后端任务状态里）'
                  : !item
                    ? '当前档位没有曲线数据 ⇒ 无法导出'
                    : '导出 XLSX：第 1 页 = 参数/公式 + **当前档位的曲线截图**，第 2 页 = 本档位的每期持仓明细；' +
                      '该文件可拖回「交易信号测试」复现净值曲线'
              }
              className="px-2 py-1 text-xs rounded border whitespace-nowrap disabled:opacity-50"
            >
              {expBusy ? '导出中…' : '导出'}
            </button>
            {/* ⚠ `whitespace-nowrap`（v1.20.19）：标题栏右侧被别的元素挤压时，
                按钮宽度会被压到比"关闭"两个字还窄 ⇒ 文字**竖排换行**（用户 2026-09-18 报 ✓）。
                与「事件研究」弹窗的关闭按钮保持一致：永远一行 ✓。 */}
            <button onClick={onClose}
                    className="px-2 py-1 text-xs rounded border whitespace-nowrap">
              关闭
            </button>
          </div>
        </div>

        {/* 导出结果提示：★ v2.1.48 —— **成功的那条不再显示** ✓（用户 2026-10-12：
            "已导出 ✓（第 1 页含 5 张当前面板截图 + 参数与公式）；第 2 页是每次调仓的持仓明细
             —— 把这个状态文字先隐藏掉吧" ✓）；
            **失败照旧显示** ✓（否则出错只能靠猜 ✗ —— 当初加它就是为这个 ✓）。
            ⚠ 想恢复成功提示：把下面的 `&& !expMsg.ok` 去掉即可 ✓（`setExpMsg` 那段仍原样保留 ✓）。 */}
        {expMsg && !expMsg.ok && (
          <div
            className={`mx-4 mt-2 text-[11px] ${
              expMsg.ok ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-600 dark:text-red-400'
            }`}
          >
            {expMsg.text}
          </div>
        )}

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

        {/* 绩效指标（v1.18.52 上移至此）：**年化 / 最大回撤**等，组合 vs 基准并排 ——
            只有同口径对比，才能判断「这份超额值不值得承担这些风险」。
            逐日盯市口径（期内按 h 调仓、持有期内逐日盯市）；⚠ 指标基于**净值** ⇒ 仅复利口径展示。
            （空数据时 itemPerf/benchPerf 均为 null ⇒ 不渲染；原位于图 1 之后，与下方「口径说明」
              面板对调了位置。） */}
        {basis === 'compound' && (itemPerf || benchPerf) && (
          <div className="mx-4 mt-2 grid grid-cols-1 md:grid-cols-2 gap-2">
            <PerfCard
              title={`组合${
                item?.kind === 'decile'
                  ? `·最强分位组 Q${item?.quantile}`
                  : `·固定 K=${item?.k}`
              }`}
              titleExtra={
                <span className="flex items-center gap-0.5">
                  {(
                    [
                      ['0.0000', '无成本'],
                      ['0.0040', '往返 0.004'],
                      ['0.0080', '往返 0.008'],
                    ] as const
                  ).map(([k, label]) => (
                    <button
                      key={k}
                      onClick={() => setPerfCost(k)}
                      title={
                        k === '0.0000'
                          ? '无成本：不扣往返费率（与曲线 c0 同口径）'
                          : `往返（买+卖）合计 ${Number(k)} 费率：按调仓期只扣实际调仓股、首期建仓不计费`
                      }
                      className={`px-1 py-0.5 rounded border text-[10px] leading-none ${
                        perfCost === k
                          ? 'border-sky-500 text-sky-600 bg-sky-50 dark:bg-sky-900/30'
                          : 'border-slate-300 text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700/40'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </span>
              }
              perf={itemPerf}
              extra={costAnnual}
            />
            <PerfCard
              title={`基准 ${benchItem?.name ?? ''}`}
              perf={benchPerf}
              note={benchPerfNote}
            />
          </div>
        )}
        {basis === 'compound' && !itemPerf && !benchPerf && tc && (
          <div className="mx-4 mt-1 text-[11px] text-slate-400">
            绩效指标不可用（调仓期小于预测周期时各期持有区间重叠、无法拼逐日净值；
            或后端版本 &lt; v1.18.48）
          </div>
        )}

        {empty ? (
          <div className="px-4 py-6 text-xs text-slate-500">
            该行没有曲线数据
            {row?.topk_curves_error ? `：${row.topk_curves_error}` : '（0/1 稀疏信号走「事件研究」，无此曲线）'}
          </div>
        ) : (
          <div className="px-4 py-3">
            {/* ---- 图 1：明细档（默认 = 最强分位组）----
                ⚠ 抓图 ref 必须包**整个区块**（标题 + 切换按钮 + 图 + 图例 + 区间累计行 ✓）：
                  只抓 `<svg>` 会丢图例（recharts 的 Legend 是 HTML ✗）与「最强 Q2」标注 ✗
                  —— 用户 2026-10-11 实测指出"图例、标题都没有""不知道看的是哪档" ✓ */}
            <div ref={navShotRef} className="bg-white">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                持仓期净值曲线（三档成本 · 同一组合 · <b>起点 = 1</b> ·{' '}
                <b className="text-sky-600">{basis === 'compound' ? '复利' : '算术累加'}</b>）
              </span>
              <span className="text-[11px] text-slate-400">切换组合（纯前端，零重算）：</span>
              <span className="text-[11px] text-slate-400 ml-1">｜基准：</span>
              {benchOptions.map((b) => (
                <button
                  key={b.code}
                  onClick={() => setBench(b.code)}
                  title={
                    b.code === EQW_CODE
                      ? '池内等权（本图现算、含分红）：每个调仓期内「池内全部成分股等权」持有 h 日的收益 = 各分位组逐期收益的平均，按当前口径累计；与组合完全同口径（同样含分红再投），与聚宽 / 外部检验脚本的"池内等权"口径一致（⚠ 非指数 ⇒ 无加权概念；上面的指数基准是**不含分红**的价格指数）'
                      : `${b.code}（同口径：指数在同一调仓期的持有 h 日收益、按当前口径累计；价格指数不含分红）`
                  }
                  className={`px-1.5 py-0.5 rounded border text-[11px] ${
                    b.code === benchCode
                      ? 'border-slate-500 text-slate-700 bg-slate-100 dark:bg-slate-700'
                      : 'border-slate-300 text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700/40'
                  }`}
                >
                  {b.name}
                </button>
              ))}
              {!benchOptions.length && (
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
                ? `每日取因子最强端的 1/${qc?.n_groups ?? 10}（等分）⇒ 持仓约 ${item.k} 只，只数逐期变化`
                : `固定持仓 ${item?.k} 只（按名次取，与分位组口径略有差异）`}
              {'；点图例可显隐曲线'}
            </div>
            {/* ★ v2.1.35：**实盘口径指标行**（用户 2026-10-12 指定位置 = 红框处的空白 ✓）——
                只在勾了「每期全仓滚动（逐点对拍）」开关时才有数据 ✓；
                它与「交易信号测试」回灌的 `period_roll` 同引擎同口径 ✓ ⇒ 这才是可落地口径的指标 ✓
                （上面那张「组合」卡是**收益模型**口径：无资金约束/整手/涨跌停 ✗）。 */}
            {prPerf && (
              <div
                className="text-[11px] text-slate-500 mb-1 text-center"
                title="实盘口径 = 每期全仓滚动：含资金约束 / 整手 100 股 / 涨跌停买不进卖不掉；与「交易信号测试」拖回灌用的是同一个回测引擎（period_roll）⇒ 两者应当逐位一致。只在勾了上方「每期全仓滚动（逐点对拍）」时才有这组数字。"
              >
                <b className="text-rose-600">实盘口径</b>：
                年化 <b>{signedPct(prPerf.annual_return, 1)}</b>
                ｜ 最大回撤{' '}
                {/* v2.1.37：**深于 −30% 才红** ✓，否则与其他数字同为默认灰 ✓（用户指定 ✓） */}
                <b className={prDdDeep ? 'text-red-500' : ''}>
                  {signedPct(prPerf.max_drawdown, 1)}
                </b>
                ｜ 夏普 <b>{fmtRatio(prPerf.sharpe)}</b>
                ｜ 索提诺 <b>{fmtRatio(prPerf.sortino)}</b>
                ｜ 卡玛 <b>{fmtRatio(prPerf.calmar)}</b>
                {typeof prPerf.n_days === 'number' ? ` ｜ 样本 ${prPerf.n_days} 个交易日` : ''}
              </div>
            )}
            {!prPerf && prObj?.error && (
              <div className="text-[11px] text-amber-600 mb-1 text-center">
                实盘口径指标不可用：{String(prObj.error)}
              </div>
            )}
            {/* ★★ v2.4.3 / v2.5.0（用户 2026-10-15 ✓）：**含离场矩阵**（对照 + 逐个离场，同源同口径 ✓）
                第 1 行 = 「不离场 · 对照」✓（口径与数字逐位不变 ✓）；其后每个离场一行 ✓；
                Δ年化 / Δ回撤 = 含离场 − 对照 ⇒ **这才是该离场因子的净效果** ✓。
                ⚠ 别拿两条净值相除当结论 ✗（持仓结构不同 ⇒ 比值混了再平衡溢价 ✓）。 */}
            {prPerf && exitRows.length > 0 && (
              <div
                className="text-[11px] mb-1 overflow-x-auto"
                title="含离场 = 在「每期全仓滚动」上再加一条离场通道：因子值 > 阈值 ⇒ T+1 卖出（停牌/一字跌停卖不掉 ⇒ 顺延逐日重试）；调仓日若它仍在名单 ⇒ 离场被覆盖（这就是「同日进+出 ⇒ 不交易」）。第 1 行『不离场 · 对照』就是上面那组数字；其后每个离场一行，两条同源（同一份名单 / 同一份价格面板 / 同一个引擎）⇒ Δ 才是该离场因子的净效果。"
              >
                <table className="w-full text-center border-collapse">
                  <thead>
                    <tr className="text-slate-400">
                      <th className="font-normal text-left px-1">离场因子</th>
                      <th className="font-normal">年化</th>
                      <th className="font-normal">最大回撤</th>
                      <th className="font-normal">Δ年化</th>
                      <th className="font-normal">Δ回撤</th>
                      <th className="font-normal">离场卖出</th>
                      <th className="font-normal">顺延</th>
                      <th className="font-normal">被名单覆盖</th>
                      <th className="font-normal">持币空转</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="text-slate-500 border-t border-slate-200 dark:border-slate-700">
                      <td className="text-left px-1">（不离场 · 对照）</td>
                      <td><b>{signedPct(prPerf.annual_return, 1)}</b></td>
                      <td>
                        <b className={prDdDeep ? 'text-red-500' : ''}>
                          {signedPct(prPerf.max_drawdown, 1)}
                        </b>
                      </td>
                      <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
                    </tr>
                    {exitRows.map((g) => (
                      <tr key={g.name}
                          className="text-slate-600 dark:text-slate-300 border-t border-slate-100 dark:border-slate-800">
                        <td className="text-left px-1 text-amber-600">{g.name}</td>
                        {g.err ? (
                          <td colSpan={8} className="text-amber-600">
                            {g.err}
                          </td>
                        ) : (
                          <>
                            <td><b>{g.perf ? signedPct(g.perf.annual_return, 1) : '-'}</b></td>
                            <td>
                              <b className={exDdDeep(g.perf?.max_drawdown) ? 'text-red-500' : ''}>
                                {g.perf ? signedPct(g.perf.max_drawdown, 1) : '-'}
                              </b>
                            </td>
                            <td>
                              <b className={g.deltaAnn >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                                {g.deltaAnn >= 0 ? '+' : ''}{(g.deltaAnn * 100).toFixed(1)}pp
                              </b>
                            </td>
                            <td>
                              <b className={g.deltaDd >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                                {g.deltaDd >= 0 ? '+' : ''}{(g.deltaDd * 100).toFixed(1)}pp
                              </b>
                            </td>
                            <td>{g.stats.exit_sells ?? '-'}</td>
                            <td>{g.stats.exit_deferrals ?? '-'}</td>
                            <td>{g.stats.exit_overridden ?? '-'}</td>
                            <td>{g.stats.exit_ignored ?? '-'}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="relative">
              {/* 图 1 右上角：醒目提示当前组合 = 最强分位组（红色） */}
              {item?.kind === 'decile' && (
                <span className="absolute right-2 top-0 z-10 text-xs font-bold text-red-600">
                  最强 Q{item.quantile}
                </span>
              )}
              {item?.kind !== 'decile' && item ? (
                <span className="absolute right-2 top-0 z-10 text-[11px] text-slate-500">
                  固定 K={item.k}
                </span>
              ) : null}
              {/* v2.1.0：抓图用容器（`ref` ⇒ 导出时把这个 `<svg>` 序列化成 PNG 贴进 XLSX ✓）。
                  ⚠ 白底：Excel 里透明底会与单元格背景混着看，很脏 ✗（见 `captureSvgPng` ✓）。 */}
              <div className="bg-white">
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
                {/* ★ v2.1.27：**每期全仓滚动（实盘口径）** —— 只在开关打开时才有数据 ✓。
                    它的对拍对象是「交易信号测试」里那条 `period_roll`（同引擎同口径 ✓）。 */}
                {prMap.size > 0 && (
                  <Line
                    type="monotone"
                    dataKey="pr"
                    name="每期全仓滚动（实盘口径）"
                    stroke="#f43f5e"
                    dot={false}
                    strokeWidth={1.8}
                    hide={!!hidden.pr}
                  />
                )}
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
              </div>
            </div>
            {benchItem && (
              <div className="text-[11px] text-slate-500 mt-1">
                区间累计（{basis === 'compound' ? '复利' : '算术'}）：组合（无成本）
                <b>{signedPct(_pv)}</b>
                <span className="mx-1.5 text-slate-300">｜</span>
                基准 {benchItem.name} <b>{signedPct(_bv)}</b>
                <span className="mx-1.5 text-slate-300">｜</span>
                ⇒ 超额{basis === 'compound' ? '（净值比）' : ''}{' '}
                <b className={excess != null && excess >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                  {signedPct(excess)}
                </b>
                {_bv == null ? '（基准尾部数据不足，已断线）' : ''}
                {benchPartial > 0 && _bv != null && (
                  <span
                    className="ml-1 text-amber-600 dark:text-amber-400"
                    title={
                      '数据只到最近一个交易日 ⇒ 最后一期持有窗口不足 h 日：组合与基准都改用「数据最后一天的收盘价」结算' +
                      '（组合侧即 label 的冻结价兜底），两侧同口径、可直接相减；n_partial = ' +
                      benchPartial +
                      '（用了兜底的期数）'
                    }
                  >
                    （末期只持有不足 {tc?.rebalance_period ?? '-'} 个交易日，两侧同口径用尾部价结算）
                  </span>
                )}
              </div>
            )}

            {/* 口径 / 免责（v1.18.52 下移至此：先看图与指标，再核对口径细则）——
                与上方「绩效指标（年化 / 最大回撤）」面板对调了位置。 */}
            </div>
            {/* ---- 黄底"简化估算 · 口径说明"（**不**进截图：它是解释文字，不是面板内容 ✓）---- */}
            <div className="mt-3 px-3 py-2 rounded bg-amber-50 dark:bg-amber-900/20 text-[11px] text-amber-700 dark:text-amber-300 leading-relaxed">
              <b>简化估算 · 口径说明</b>（读图后可对照核对）：① 费率是<b>往返（买+卖）合计</b>，按<b>调仓期</b>扣、
              只扣<b>实际调仓</b>的股票（首期建仓不计费）；② <b>买入端</b>已按剔除开关剔除
              <b>T/T+1 涨停、T+1 停牌</b>的样本（与表单剔除开关同口径）；
              <b>未模拟「跌停卖不出」</b>（无跌停开关），也未计流动性冲击；
              ③ 曲线 = 每期持有组合的收益（调仓日取信号、T+1 买入、持有到下一个调仓日；调仓期默认 =
              预测周期 h）—— 当前口径：<b>{basis === 'compound'
                ? '复利（每期收益滚入本金 = 实盘满仓复投）'
                : '算术累加（各期收益直接相加，非实盘净值）'}</b>，
              <b>以 1 为起点</b>、<b>近似净值但不是逐日盯市净值</b>；
              ④ 默认档 = <b>超额收益最强的分位组</b>（逐期等分，只数与固定 K 档略有差异）；
              ⑤ <b>⚠ 分红口径差异</b>：上面 5 个<b>指数基准是价格指数、不含分红</b>，而<b>组合与
              「池内等权」都含分红再投</b> ⇒ <b>相对指数的超额里含约 2~3pp/年 的分红差</b>。
            </div>

            {/* ---- 图 2：十分位累计收益曲线 ---- */}
            {qc && (
              <>
                {/* 抓图 ref 包整个区块（标题 + 图 + 图例 ✓，见上面图 1 的说明） */}
                <div ref={decShotRef} className="bg-white">
                <div className="flex items-center gap-2 mt-4 mb-1">
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                    十分位累计收益曲线（<b>无成本</b> · 起点 0）
                  </span>
                  <span className="text-[11px] text-slate-400">
                    最强 Q{qc.best_quantile} / 最弱 Q{qc.worst_quantile}
                    （<b className="text-amber-600 dark:text-amber-400">样本内最优</b>
                    ⇒ 十档不单调时不能当结论）；
                    {basis === 'arith' ? (
                      <>
                        <b>右轴 = 多空</b>（最强−最弱，<b>逐期价差累加</b>，起点 0；不可实现，仅作参考）
                      </>
                    ) : (
                      <>
                        <b>右轴 = 多空</b>（最强−最弱，<b>逐期价差复利</b>，起点 0；不可实现，仅作参考）
                      </>
                    )}
                    ；点图例可显隐曲线
                  </span>
                </div>
                <div className="bg-white">
                <ResponsiveContainer width="100%" height={250}>
                  <LineChart data={decileData} margin={{ top: 5, right: 12, left: 0, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="d" tick={{ fontSize: 10 }} minTickGap={48} />
                    <YAxis
                      tick={{ fontSize: 10 }}
                      width={52}
                      domain={yDomains.left.domain}
                      ticks={yDomains.left.ticks}
                      tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                    />
                    {/* 多空是**价差**曲线（不是收益曲线）⇒ 单独一根右轴，避免把收益曲线压扁。
                        v1.19.5 起两种口径都画（复利 = 逐期价差复利）⇒ 右轴常显。
                        v1.19.8：图 2 全部改为**累计收益（0 起）**，两轴**共用同一 domain**
                        ⇒ 0 点完全对齐、无多余留白；刻度统一按百分数显示（原先净值域带一串小数）。 */}
                    <YAxis
                      yAxisId="r"
                      orientation="right"
                      tick={{ fontSize: 10 }}
                      width={52}
                      domain={yDomains.right.domain}
                      ticks={yDomains.right.ticks}
                      tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                    />
                    <Tooltip formatter={(v: number | string) => (typeof v === 'number' ? v.toFixed(4) : v)} />
                    <Legend
                      wrapperStyle={{ fontSize: 10, cursor: 'pointer' }}
                      onClick={(d) => toggleSeries((d as { dataKey?: string }).dataKey)}
                    />
                    <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
                    {/* 基准虚线（v1.18.55）：同口径**累计收益**（起点 0），与图 1 是同一条数据；
                        与图 1 共用 `hidden.b` ⇒ 点任一图的图例可同时显隐两图的基准线 */}
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
                    {qc.groups.map((g) => (
                      <Line
                        key={g.quantile}
                        type="monotone"
                        dataKey={`q${g.quantile}`}
                        name={`Q${g.quantile}${
                          g.quantile === qc.best_quantile
                            ? '(最强)'
                            : g.quantile === qc.worst_quantile
                              ? '(最弱)'
                              : ''
                        }`}
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
                      hide={!!hidden.ls}
                    />
                  </LineChart>
                </ResponsiveContainer>
                </div>
                </div>
              </>
            )}

            {/* ---- K 敏感度表 ---- */}
            {sens && (
              <>
                <div className="mt-4 mb-1 text-xs font-semibold text-slate-600 dark:text-slate-300">
                  成本敏感度：不同持仓只数（换手 / 年化成本 / 吞噬比例）
                </div>
                <table className="text-[11px] w-full border-t border-slate-200 dark:border-slate-700">
                  <thead>
                    <tr className="text-slate-400">
                      <th className="text-left px-1 py-1 font-normal">持仓只数</th>
                      <th className="text-right px-1 font-normal">每期换手</th>
                      <th className="text-right px-1 font-normal">年化成本（往返 0.004）</th>
                      <th className="text-right px-1 font-normal">年化成本（往返 0.008）</th>
                      <th className="text-right px-1 font-normal">成本吞噬（往返 0.004）</th>
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
                  {sens.note}　年化按 {sens.trading_days} 交易日；持仓只数与明细曲线的固定档一一对应。
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
