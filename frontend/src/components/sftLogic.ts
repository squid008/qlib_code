// 单因子测试面板的**纯逻辑**（从 `SingleFactorTestPanel.tsx` 外提 ✓ P1-B，2026-10-18）。
//
// 为什么外提：面板原先 2,483 行 ✗（React 组件 + 400 多行纯逻辑混在一起 ✓）；
// 这部分**没有 JSX、没有 state、没有副作用** ✓ ⇒ 外提零风险 ✓，组件文件从此专注 UI ✓。
//
// ⚠ 只放"输入 → 输出"的纯函数与常量 ✓ —— `useState` / JSX / fetch 一律不许出现 ✗
//   （`EsErrorBoundary` 因为**含 JSX**，特意留在面板里 ✓）。
import type { CustomFormula, SingleFactorTestResult } from '../api'
// ⚠ `verdictRules` 的三个符号**本文件自己在用** ✓（结论判定 ✓）—— 别跟着面板一起删 ✗
//   （实测：删掉后 TS2304 ×4 ✓）
import { excessThresholdOf, pairStabilityOf, type PairStability } from './verdictRules'

export interface SingleFactorTestPanelProps {
  customFormulas: CustomFormula[]
  defaultUniverse: string
  defaultStartDate: string
  defaultEndDate: string
  defaultLabelHorizon: number
  // 任务提交/结束/取消时触发，让 App 立即刷新"并发: x/y"显示（即时 +1/-1，不等 3 秒轮询）
  onCapacityChange?: () => void
}

export type TestResult = SingleFactorTestResult

// 结论判定（与表格"结论"列同源，导出复用，避免两处口径漂移）：
// 按渲染列的判定顺序给出 kind；布尔字段供表格样式分支使用。
export type VerdictKind = 'conflicting' | 'good' | 'goodReverse' | 'lottery' | 'timeConcentrated' | 'watch'
export const VERDICT_LABEL: Record<VerdictKind, string> = {
  conflicting: '方向矛盾',
  good: '有效✓',
  goodReverse: '有效(反向)✓',
  lottery: '彩票型',
  timeConcentrated: '时间集中',
  watch: '待观察',
}
export interface VerdictStats {
  significant: boolean
  goodBase: boolean
  conflicting: boolean
  goodReverseBase: boolean
  dT: number | null
  stable: boolean
  good: boolean
  goodReverse: boolean
  kind: VerdictKind
  // 判「待观察」时的具体原因（供结论列悬停）。存在的意义：判定用未舍入的原始值，
  // 而界面部分列只显示 1~2 位小数，会出现"胜率显示 55.0% 却不通过 >=55%"的困惑
  // （例：win=0.549694 → 显示 55.0%，但 0.549694 < 0.55 → 判待观察）。
  watchReason?: string
}
/** 弹窗的错误边界：渲染异常时显示错误信息，而不是让整个页面白屏（事件研究 / 持仓曲线共用）。 */

/** 事件研究在该行「周期」上的取值点（无对应周期时退化为最长持有期）。 */
export function esPointOf(r: TestResult) {
  const es = r.event_study
  if (!es || !es.curve || es.curve.length === 0) return null
  const k = r.horizon
  if (k != null) {
    const hit = es.curve.find((c) => c.k === k)
    if (hit) return hit
  }
  return es.curve[es.curve.length - 1]
}

/**
 * 取「当前判定持有期 k」对应的**日配对均值超额**（= 触发组 − 未触发组，同口径）。
 *
 * 为何需要它：中位数为正只说明"典型一次触发赚钱"，但若**跑不赢同期未触发组**，
 * 那等于"随便买也比它强"，信号没有实用价值 → 故 v1.18.20 起把 `excess > 0` 纳入
 * 「有效」的必要条件（反向则为 `excess < 0`）。
 *
 * 口径选择：用 `baseline.excess`（均值·日配对）而非 `excess_median`（中位数·事件级）——
 * 前者直接对应"整体收益是否跑赢未触发组"，与用户直觉一致；后者作为参考在弹窗中可见。
 * 无 `baseline`（旧结果 / 基准计算失败）时返回 null → 不加此门槛但明确标注。
 */
export function esExcessOf(r: TestResult, k?: number | null): number | null {
  const bl = r.event_study?.baseline
  if (!bl || !bl.ks || !bl.excess) return null
  const i = k == null ? bl.ks.length - 1 : bl.ks.indexOf(k)
  if (i < 0 || i >= bl.excess.length) return null
  const v = bl.excess[i]
  return v == null ? null : v
}

/** 0/1 信号「反向条件成立」时的悬停说明（v1.19.34，用户 2026-09-14 定稿）。
 *
 * ⚠ **为什么反向不再作为结论**：0/1 信号的语义是「**1 = 要买**」，判"反向"等于叫你去买**未触发组**；
 *   而稀疏信号（触发占比 p 极小）下未触发组 ≈ 全池 ⇒ 反向 = 拿 beta、**无 alpha**
 *   （XG2 实测 p=0.028%：剔除影响与"未触发 vs 全池"都是 0.0005% 量级）；
 *   并且「有效(反向)✓」带个绿勾会被读成"好消息"，而它对选股信号的真实含义是「**原方向无效**」。
 *   ⇒ 反向下**直接按「待观察」**，这里只补充说明，不参与结论判定。
 */
export function reverseNoteOf(r: TestResult): string {
  // ⚠ 触发占比要**算准**：后端 `nonzero_ratio` 只存 4 位小数（0.0005 ⇒ 显示 0.050%，比真值 0.028% 大一倍）
  //   ⇒ 优先用 `trigger.count / n_obs`（剔除后触发样本占比，也正是实际会交易到的比例）。
  const n = r.trigger?.count ?? null
  const tot = r.n_obs ?? null
  const p = n != null && tot != null && tot > 0
    ? n / tot
    : (r.nonzero_ratio != null && Number.isFinite(r.nonzero_ratio) ? r.nonzero_ratio : null)
  const pTxt = p == null ? '触发占比未知' : `触发占比 ${(p * 100).toFixed(3)}%`
  const tail = p != null && p <= 0.05
    ? '未触发组 ≈ 全池 ⇒ 反向只是拿 beta（无 alpha），不可落地'
    : '仅在能反向交易时才有意义（A 股空头收益不可得）'
  return `⚠ 反向条件也成立（${pTxt}）：${tail} ⇒ 不判「有效(反向)」，按原方向视作无效`
}

/** 「有效(反向)」徽章的悬停文案（v1.19.40，用户 2026-09-15）。
 *
 *  抽成**模块级函数**（而不是写在 JSX 里）是为了能被回归脚本 `ai_test/check_binary_reverse.mjs`
 *  按函数名截出来断言措辞，避免"改文案改坏语义"没人发现。
 *  ⚠ 必须说清三件事：① 判定条件；② **它不是"好消息"**（原方向无效）；③ 保留可落地性提醒 +
 *     用途（研究线索：能否当离场因子），并把「图上红点」告诉用户。
 */
export function reverseTitleOf(r: TestResult): string {
  if (!r.is_binary) {
    return '连续因子：高分位组收益显著更低且 IC/ICIR 稳定为负，因子值与未来收益负相关，需反向使用（因子值低时买入）'
  }
  return [
    '0/1 信号按事件研究判定：中位数 ≤−0.50%、绝对收益胜率 ≤45%、超额 ≤ −门槛(k)，且日配对稳定（反向口径）。',
    '⚠ 这不是"好消息"：0/1 的语义是「1 = 要买」，判「反向」意味着**原方向无效**。',
    reverseNoteOf(r),
    '用途：作为「研究线索」—— 可研究它能否当**离场因子**（持有中被触发就提前离场）；事件研究图上该类持有日的锚点已标为**红色**。',
  ].join('\n')
}

/** 「待观察」的原因文案（0/1 信号走事件研究路径）：逐条对比门槛，指出差在哪一项。 */
export function watchReasonOfBinary(
  pt: NonNullable<ReturnType<typeof esPointOf>>,
  ex: number | null,
  exThr: number,
  st: PairStability,
  revNote?: string,
) {
  const med = pt.median ?? 0
  const win = pt.win ?? 0
  const pct = (v: number, d = 3) => `${(v * 100).toFixed(d)}%`
  const medOk = med >= 0.005
  const winOk = win >= 0.55
  const exOk = ex == null || ex >= exThr
  const sgn = (v: number) => `${v >= 0 ? '+' : ''}${pct(v)}`
  const medTxt = medOk
    ? `中位数 ${pct(med)} ≥0.50% ✓`
    : `中位数 ${pct(med)} <0.50%（差 ${pct(0.005 - med)}）✗`
  const winTxt = winOk
    ? `绝对收益胜率 ${pct(win, 2)} ≥55% ✓`
    : `绝对收益胜率 ${pct(win, 2)} <55%（差 ${pct(0.55 - win, 2)}）✗`
  const exTxt =
    ex == null
      ? '超额 无法计算（无基准，未参与判定）—'
      : exOk
        ? `超额 ${sgn(ex)} ≥${pct(exThr)} ✓`
        : `超额 ${sgn(ex)} <${pct(exThr)}（差 ${pct(exThr - ex)}）✗`
  const stTxt = st.missing
    ? '日配对稳定性 无法验证（配对日不足 2）✗'
    : st.ok
      ? st.tOk
        ? `日配对稳定 |HAC t| ${Math.abs(st.tVal as number).toFixed(2)} ≥2 ✓`
        : `日配对稳定 日胜率 ${pct(st.winVal as number, 2)} ≥55% ✓`
      : `日配对不稳定 |HAC t| ${st.tVal == null ? '-' : Math.abs(st.tVal).toFixed(2)} <2 且 日胜率 ${st.winVal == null ? '-' : pct(st.winVal, 2)} <55% ✗`
  const why = [
    !medOk ? '中位数未达标' : '',
    !winOk ? '绝对收益胜率未达标' : '',
    ex != null && !exOk ? `超额未达门槛（${pct(ex)} <${pct(exThr)}）` : '',
    !st.ok ? '日配对未通过稳定性检验' : '',
  ].filter(Boolean).join('、') || '未达任何有效判定条件'
  return [
    `结论：待观察（${why}）`,
    ...(revNote ? [revNote] : []),
    `事件研究（持有 ${pt.k ?? '?'} 交易日，n=${pt.n ?? '?'}）：${medTxt}；${winTxt}；${exTxt}；${stTxt}`,
    `「有效✓」需同时满足：① 中位数 ≥0.50%；② 绝对收益胜率 ≥55%；③ 超额 ≥ 门槛（max(0.5%, 0.025%×持有期)，本次持有 ${pt.k ?? '?'} 日 → ${pct(exThr)}）；④ 日配对稳定（|HAC t| ≥2 或 日胜率 ≥55%）。`,
    '「彩票型」需 |中位数| <1% 且绝对收益胜率 45%~55% 且均值 >max(0.50%, 中位数×3)。',
    '注：判定使用未舍入的原始值（上表绝对收益胜率已显示到 0.01%）。点右侧「事件研究」看各持有期明细与超额曲线。',
  ].join('\n')
}

/** 「待观察」的原因文案（连续因子 / 无事件研究结果时）。 */
export function watchReasonOfContinuous(r: TestResult, significant: boolean, stable: boolean) {
  const pct = (v: number, d = 3) => `${(v * 100).toFixed(d)}%`
  const lines = ['结论：待观察']
  if (r.error) {
    lines.push(`测试异常：${r.error}`)
  } else {
    const bad: string[] = []
    if (r.diff === null || r.diff === undefined) bad.push('无触发/未触发差值')
    else if (r.diff <= 0) bad.push(`差值 ${pct(r.diff)} ≤0`)
    if (r.is_binary && !significant) bad.push('p 值 ≥0.05（两组差异不显著）')
    if (!stable) bad.push('日配对检验不显著（|HAC t| <2 或胜率未偏离 50%）')
    lines.push(bad.length ? `未通过：${bad.join('；')}` : '未达任何有效判定条件')
  }
  lines.push('连续因子判定需同时满足：差值 >0 且显著、日配对检验稳定。')
  return lines.join('\n')
}

export function verdictOf(r: TestResult): VerdictStats {
  const significant = r.p_value !== null && r.p_value < 0.05
  const goodBase =
    r.error == null && r.diff !== null && r.diff > 0 && (!r.is_binary || significant)
  // 方向矛盾：IC 与触发收益差方向相反且 ICIR 稳定（|ICIR|>=0.05，即×100后>=5）
  const conflicting =
    r.error == null &&
    r.ic !== null &&
    r.icir !== null &&
    Math.abs(r.icir) >= 0.05 &&
    r.diff !== null &&
    ((r.ic < 0 && r.diff > 0) || (r.ic > 0 && r.diff < 0))
  // 反向有效：连续因子高分位组收益显著更低（diff<0），且 IC/ICIR 稳定为负（方向一致）
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
  const dT = r.daily_t_hac ?? r.daily_t
  const stable =
    dT === null ||
    dT === undefined ||
    (Math.abs(dT) >= 2 &&
      ((r.diff ?? 0) >= 0 ? (r.daily_win ?? 0) >= 0.5 : (r.daily_win ?? 0) <= 0.5))
  let good = goodBase && stable
  let goodReverse = goodReverseBase && stable
  let kind: VerdictKind = 'watch'

  // ---- 0/1 信号：结论以「事件研究」为准（v1.18.7）----
  // 稀疏信号每天可能只有 1 只票触发 → 按日配对检验退化成单票收益序列，其 t 值/胜率
  // 不可信（实测 CCCMA250 全 A 每日触发只数中位数 = 1，Top20 天贡献日差净和的 100.1%，
  // 剔除后剩余日均 -0.002%）。改用事件研究的「中位数 + 胜率」判定，并单独识别
  // 「彩票型」：中位数≈0、胜率≈50%，但均值显著更高（收益靠极少数连板/妖股事件撑起）。
  const pt = r.is_binary ? esPointOf(r) : null
  if (pt) {
    const med = pt.median ?? 0
    const win = pt.win ?? 0
    const mean = pt.mean ?? 0
    // 超额（触发组 − 未触发组·日配对均值，与判定同一持有期 k）与门槛
    const ex = esExcessOf(r, pt.k)
    const exThr = excessThresholdOf(pt.k)
    // v1.18.20 引入「超额 >0」；v1.18.32 收紧为「超额 ≥ 门槛(k)」并追加「日配对稳定」：
    // 实测出现过 0.081% 这种噪声级超额（连一次往返成本 0.2~0.35% 都覆盖不了）被判有效。
    // 门槛 = max(0.5%, 0.025%×k)：0.5% 覆盖一次往返成本、0.025%/日 ≈ 年化 6% 机会成本。
    // 稳定性：|HAC t| ≥2 或 日胜率 ≥55%（两者皆缺 → 不通过，宁严勿松）。
    // ex == null（无基准/旧结果）时第③条不启用（向后兼容），第④条仍要求。
    const st = pairStabilityOf({ t: r.daily_t_hac ?? r.daily_t, win: r.daily_win }, false)
    const stRev = pairStabilityOf({ t: r.daily_t_hac ?? r.daily_t, win: r.daily_win }, true)
    const exOk = ex == null || ex >= exThr
    const exRevOk = ex == null || ex <= -exThr
    const esGood = med >= 0.005 && win >= 0.55 && exOk && st.ok
    const esReverse = med <= -0.005 && win <= 0.45 && exRevOk && stRev.ok
    const lottery =
      Math.abs(med) < 0.01 && win >= 0.45 && win <= 0.55 && mean > Math.max(0.005, med * 3)
    good = esGood
    // v1.19.40（用户 2026-09-15 定稿，**推翻 v1.19.34**）：0/1 信号的「反向」**恢复为结论**。
    //   目的：把这类因子当「**研究线索**」捞出来 —— 典型用途是研究它能否当**离场因子**
    //   （持有中被触发就提前离场；负 alpha 在 long-only 下唯一可变现的形式）。
    //   ⚠ 但语义与呈现都必须说清：**它不是"好消息"**（对选股信号的真实含义是「原方向无效」）⇒
    //     表格用**红色**呈现（不是绿勾），悬停里保留 `reverseNoteOf` 的可落地性提醒
    //     （p ≤ 5% ⇒ 未触发组≈全池 ⇒ 反向只是拿 beta）；事件研究图上该类持有日的锚点也为**红色**。
    goodReverse = esReverse
    if (conflicting && !esGood) kind = 'conflicting'
    else if (esGood) kind = 'good'
    else if (esReverse) kind = 'goodReverse'
    else if (lottery) kind = 'lottery'
    else kind = 'watch'
    return {
      significant, goodBase, conflicting, goodReverseBase, dT, stable, good, goodReverse, kind,
      watchReason: kind === 'watch'
        ? watchReasonOfBinary(pt, ex, exThr, st, esReverse ? reverseNoteOf(r) : undefined)
        : undefined,
    }
  }

  if (conflicting) kind = 'conflicting'
  else if (good) kind = 'good'
  else if (goodReverse) kind = 'goodReverse'
  else if ((goodBase || goodReverseBase) && !stable) kind = 'timeConcentrated'
  else kind = 'watch'
  return {
    significant, goodBase, conflicting, goodReverseBase, dT, stable, good, goodReverse, kind,
    watchReason: kind === 'watch' ? watchReasonOfContinuous(r, significant, stable) : undefined,
  }
}

// 导出工作表1"因子指标"表头（列口径与界面表格一致：覆盖率/收益/差值/胜率/Q组 = ×100 百分数，IC/RankIC/ICIR/t = 原始小数）
// Q 组收益：仅连续因子有（日截面分位收益）；**列数由实际分位组数决定**（v1.18.45 起后端默认
// 十分位 10 组，见请求参数 quantiles），至少保留旧的 Q1~Q5 列宽。"触发组日均/未触发组日均"：仅 0/1 信号有
// （对应界面"分位收益"列的双柱，即触发组 vs 未触发组的逐日截面收益均值）。两类分组口径不同故不混填 Q 列，
// 避免"触发组与最低值组同列"的误读。
export const exportHeaders = (qCols: number) => [
  '因子', '来源', '周期(天)', '公式', '覆盖率(%)', '信号', '触发数', '触发收益(%)',
  // ★ v2.1.31：**首次/续期内再触发**分组（用户 2026-10-11："加分组显示" ✓）——
  //   两组收益差很大（实测 `黄金坑启动`：首次 +4.956% vs 续期 +0.707% ✓），
  //   而组合里同一只票只持一轮（≈ 首次口径 ✓）⇒ 不分列看会误判"样本均值 vs 组合净值" ✓
  '首次触发数', '首次触发收益(%)', '续期触发数', '续期触发收益(%)',
  '未触发数', '未触发收益(%)',
  '差值(%)', '日差值(%)', 't(HAC)', '相对收益胜率(%)', '配对日数', 'p值',
  ...Array.from({ length: qCols }, (_, i) => `Q${i + 1}收益(%)`),
  '触发组日均(%)', '未触发组日均(%)',
  'IC', 'RankIC', 'ICIR', '结论', '事件中位数(%)',
]
// 导出工作表2"因子与公式"表头
export const EXPORT_FORMULA_HEADERS = ['因子', '来源', '公式（用户保存原文 / 目录表达式）']

/** 持仓期曲线的「固定 K 档」**预设**（用户 2026-09-12 定稿，取代原先的自由文本输入）：
 *  整数 = 绝对只数；`0.1` / `0.2` = 日均有效只数的 10% / 20%。
 *  勾选的档随本次测试一起算出来 ⇒ 弹窗里纯前端切换、**不用重跑**；每个额外 K 实测约 +0.05s
 *  （每个预测周期各算一次）。⚠ 与后端 `topk_sensitivity.BASE_KS` 保持超集关系。 */
export const K_PRESETS = [1, 2, 3, 5, 10, 20, 50, 100, 0.1, 0.2]
export const kLabel = (k: number) => (k < 1 ? `${Math.round(k * 100)}%` : `${k}`)

/** 「耗时」列是否展示（用户 2026-09-13：「耗时列先隐藏去掉吧，后面我让你显示再显示出来」）。
 *  改 `true` 即恢复 —— 表头/单元格/colSpan 三处都跟着这个开关走，只改这一行即可。
 *  后端 `timing` 字段**照常回传**（`api.ts` 的类型也不动）⇒ 只是不占列宽、不显示。
 *  ⚠ 表格列数：显示耗时 = 18 列，隐藏 = 17 列（错误行的 colSpan 必须同步，否则整行错位）。 */
export const SHOW_TIMING_COL = false

/** 进度条那行要不要显示「（已用 N 秒）」✓（★ 2026-10-16 用户："把这个计时暂时隐藏" ✓）。
 *  `false` = 隐藏（默认 ✓）；想恢复把这里改回 `true` 即可 ✓ —— **只改这一行** ✓
 *  （计时逻辑 `startedAtRef` 照旧在跑 ✓，只是不显示 ✓，恢复后数字仍是真实的 ✓）。
 *  ⚠ 为什么保留计时逻辑而不是删掉：它是"卡死 vs 在算"的唯一线索（v2.4.2 加的 ✓），
 *    隐藏只是嫌它盯着看晃眼 ✓，将来排查慢任务还要用 ✓。 */
export const SHOW_ELAPSED = false

// 解析批量预测周期输入：单值 / 逗号枚举(1,2,3,5) / range 区间。
// ⚠ 区间有**两种写法，中间那个数的含义不同**（易踩，故提示里也写明）：
//   · 三段 `a:b:c` = **起点:步长:终点**（含终点）⇒ `1:5:20` → 1,6,11,16、`1:2:7` → 1,3,5,7；
//   · 两段 `a:b`   = **起点:终点**（步长默认 1）⇒ `1:5` → 1,2,3,4,5（**不是**"每隔 5"）。
// 分隔符：逗号/全角逗号/分号/空格皆可；各段可混用（`1:2:7,60` → 1,3,5,7,60）。
// 值范围 1~250（后端同样校验兜底）。返回去重后的周期列表，非法时带中文错误。
export function parseHorizons(text: string): { horizons: number[]; error?: string } {
  const s = text
    .trim()
    .replace(/[，;；\s]+/g, ',')
    .replace(/,+/g, ',')
    .replace(/^,|,$/g, '')
  if (!s) return { horizons: [] }
  const out: number[] = []
  for (const part of s.split(',')) {
    if (!part) continue
    const m = part.match(/^(\d+)(?::(\d+)(?::(\d+))?)?$/)
    if (!m) return { horizons: [], error: `无法识别"${part}"：支持单个数字、逗号枚举(如 1,2,5)或区间(如 1:5:20)` }
    const a = Number(m[1])
    if (m[2] == null) {
      out.push(a)
    } else {
      // a:b → a..b（步长默认 1）；a:b:c → a..c（含终点，b 为步长）
      const step = m[3] == null ? 1 : Number(m[2])
      const b = m[3] == null ? Number(m[2]) : Number(m[3])
      if (step <= 0) return { horizons: [], error: `步长必须为正整数：${step}` }
      if (a > b) return { horizons: [], error: `区间起点 ${a} 不能大于终点 ${b}` }
      for (let v = a; v <= b; v += step) out.push(v)
    }
  }
  const bad = out.find((v) => v < 1 || v > 250)
  // ⚠⚠ 判"有没有找到"必须用 `!== undefined`：`out.find` 返回的是**命中的值本身**，而 `0` 是假值
  //   ⇒ 写 `if (bad)` 会让 `0`（非法值）静默通过前端校验，直到后端 400
  //   （后端用显式区间 `1 <= hi <= 250`，没这个洞；同一坑 `rebalance_period` 也踩过，见 routers/factors.py）。
  if (bad !== undefined) return { horizons: [], error: `预测周期需在 1~250 之间：${bad}` }
  return { horizons: [...new Set(out)] } // 去重保序
}

/** 「预测周期」输入框的鼠标提示：静态语法说明（示例随 `parseHorizons` 的语义走，改语义时一起改）。 */
export const HORIZON_SYNTAX =
  '单个数字=测一个周期；逗号/全角逗号/分号/空格分隔可批量混写（如 1:2:7,60 → 1,3,5,7,60）；\n' +
  '三段 a:b:c = 起点:步长:终点（含终点），如 1:5:20 → 1,6,11,16；\n' +
  '⚠ 两段 a:b = 起点:终点（步长 1）⇒ 1:5 → 1,2,3,4,5（不是"每隔 5"）。\n' +
  '取值范围 1~250，自动去重并保持顺序。'

/** 鼠标提示里的**实时预览**（用户 2026-09-14：「加预览加在鼠标 TAG 里」）：
 *  把当前输入解析成人话，挂在 `title` 上 ⇒ 每敲一个字、悬停即见，避免"两段/三段的中间数含义不同"被误读
 *  （最坏情况：把 `1:60` 当"每隔 60"，实际展开成 60 个周期 —— 展开结果 + 周期数一眼可辨）。
 *  ⚠ 不加"耗时提醒"（用户同日要求去掉：「周期数多时加耗时提醒这个去掉」）——只报事实，不劝。 */
export function horizonHint(text: string): string {
  const r = parseHorizons(text)
  if (r.error) return `当前输入：⚠ ${r.error}`
  const n = r.horizons.length
  if (!n) return '当前输入：空（提交时会提示「请填写预测周期」）'
  // ⚠ 展开结果要**截断**：`1:60` 会展开 60 个数，全列进 tooltip 就没法读了（周期数才是重点）
  const CAP = 10
  const list = n > CAP
    ? `${r.horizons.slice(0, CAP).join(', ')}, …, ${r.horizons[n - 1]}`
    : r.horizons.join(', ')
  return `当前输入 ⇒ ${list}（${n} 个周期）`
}

export interface SourceGroup {
  source: 'custom' | 'alpha158' | 'alpha360'
  label: string
  items: { key: string; id: string; name: string; expression: string; original?: string }[]
  loaded?: boolean
}
