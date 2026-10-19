/**
 * 「结果区」的**展示态推导**（★ v2.9.49，2026-10-19 ✓ —— 从 `App.tsx` 里抽出的**纯逻辑** ✓）。
 *
 * ## 为什么单独抽它（而不是继续留在 JSX 前面 ✗）
 *
 * 这 20 行推导里**埋着两个线上 bug 的修复** ✓，而且都是"看代码看不出来、只有出事才知道"那类 ✗：
 *
 * 1. **`partialStatus` 必须查实时任务列表**（v1.19.39 ✓）——
 *    用户点「取消」后任务变 `cancelling`，再点「刷新」会把已结束任务从 `tasks` 里移除 ✓，
 *    但 `viewResult` 仍是**点击那一刻的快照**（`status='running'` ✗）
 *    ⇒ 拿快照判状态 ⇒ 界面一直显示「⏳ 滚动训练进行中」，用户以为没取消掉 ✗。
 *    ⇒ 规则：**实时列表里有就按它的状态 ✓，没有（已被清掉/已结束）一律视为 `cancelled`** ✓。
 * 2. **`shownTaskId` 必须与"正在显示结果的那个任务"对齐**（v1.19.74 ✓）——
 *    `partialTaskId` 是「`viewResult` 优先」✓，而结果/产物是「实时成功任务优先」✓
 *    ⇒ 两者不一致时，**归因表会来自另一个任务** ✗（用户 2026-09-16 看到"未在公式库中找到的因子"✓，
 *    其实是临时验证公式那次 ✓）。⇒ `shownTaskId` 单独算 ✓：**成功任务优先** ✓。
 *
 * 抽成纯函数后，这些规则**第一次可以被单测钉住** ✓（见 `__tests__/backtestView.test.ts` ✓）。
 */

/** 只声明本逻辑用到的字段 ✓（其余字段原样透传 ✓，不做窄化 ✗） */
export interface TaskLike {
  task_id: string
  status: string
  result?: unknown
  partial_result?: PartialLike | null
}

export interface PartialLike {
  // ⚠ 这些字段在真实类型里是 `T | null | undefined` ✗ —— 只写 `T | undefined` 会让
  //   `BacktestTask` **不可赋值** ✓（TS2322 实测报的就是 `nav: null` ✓）。
  nav?: unknown[] | null
  segments_done?: number | null
  segments_total?: number | null
  end_date?: string | null
  layer_returns?: unknown
  ic_analysis?: unknown
}

export interface ViewResultLike {
  task_id?: string | null
  result?: unknown
  partial_result?: PartialLike | null
}

export interface ResultViewInput {
  task: TaskLike | null
  tasks: TaskLike[]
  viewResult: ViewResultLike | null
  viewArtifacts: unknown
  artifacts: unknown
}

export interface ResultView {
  /** 用于判断"部分结果属于哪个任务"的 id（`viewResult` 优先 ✓ —— v1.19.39 的口径 ✓） */
  partialTaskId: string | null
  /** **查实时列表**得到的 partial 状态 ✓（查不到 ⇒ `cancelled` ✓） */
  partialStatus: string
  /** 结果/产物对应的任务 id（**成功任务优先** ✓ —— v1.19.74 的口径 ✓） */
  shownTaskId: string | null
  shownRunning: boolean
  partialRunning: boolean
  /** 完整结果 ✓
   *  ⚠ 用显式 `any` 而不是窄类型 ✗：它会被原样传给 `MetricCards` / `NavChart` / …
   *  而这些组件的 props 类型来自 `types.ts` ✓（`BacktestResult` 等 ✓）。
   *  在这里窄化会逼着 `App` 做三处重复断言 ✗；真正的类型安全**由子组件的 props 保证** ✓
   *  （传错字段时 `tsc` 会在子组件那份报 ✓）。 */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  r: any
  /** 训练产物 ✓（同上，由 `ModelArtifactsPanel` 的 props 把关 ✓） */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  a: any
  /** 部分结果（滚动训练途中 / 已停止但保留已跑段 ✓）
   *  ⚠ 同样用显式 `any` ✓ —— 它会被拆开传给 `NavChart` / `LayerChart` / `ICChart` ✓，
   *  各字段类型（`NavPoint[]` / `LayerReturns` / `ICAnalysis`）由**那些组件的 props** 把关 ✓。 */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  partial: any
  /** 结果区是否该渲染 ✓（完整结果 / 产物 / 有任何 nav 的 partial ✓） */
  shouldRender: boolean
  /** 结果区**内部**是否再渲染一次「历史回测」表格 ✓（有 partial 且没有可续看的历史 ⇒ 不重复 ✓） */
  showHistoryInResult: boolean
  /** 结果区**外**是否单独渲染「历史回测」表格 ✓（既无结果也无产物时 ✓，用于直接复用参数 ✓） */
  showHistoryStandalone: boolean
}

const RUNNING_STATES = ['running', 'pending']

export function resolveResultView(input: ResultViewInput): ResultView {
  const { task, tasks, viewResult, viewArtifacts, artifacts } = input

  // ⚠ ①：partial 状态**必须查实时列表** ✗→✓（快照会撒谎 ✓，见文件头 v1.19.39 ✓）
  const partialTaskId = viewResult?.task_id ?? task?.task_id ?? null
  const livePartialTask = partialTaskId
    ? (tasks.find((t) => t.task_id === partialTaskId) ?? null)
    : null
  const partialStatus = livePartialTask?.status ?? 'cancelled'

  // ⚠ ②：结果/产物对应的任务 —— **成功任务优先** ✗→✓（见文件头 v1.19.74 ✓）
  const shownTaskId = task?.status === 'success'
    ? task.task_id
    : (viewResult?.task_id ?? task?.task_id ?? null)
  const shownStatus = shownTaskId
    ? (tasks.find((t) => t.task_id === shownTaskId)?.status ?? null)
    : null
  const shownRunning = shownStatus !== null && RUNNING_STATES.includes(shownStatus)
  const partialRunning = RUNNING_STATES.includes(partialStatus)

  const r = (task?.status === 'success' ? task.result : viewResult?.result) ?? null
  // ★ v1.20.50：只要"当前展示的是某个实时任务"就用它的产物 ✓（原先限定 success ✗ ⇒
  //   运行中点任务卡片永远看不到训练产物 ✓）；没有实时任务时才用历史查看那份 ✓。
  const a = (task ? (artifacts ?? viewArtifacts) : viewArtifacts) ?? null
  // 运行中 ⇒ 实时任务 partial ✓；查看历史 ⇒ viewResult 携带的 partial ✓
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const partial: any = task?.status === 'running'
    ? (task.partial_result ?? null)
    : (viewResult?.partial_result ?? null)

  const hasPartialNav = Boolean(
    (task?.status === 'running' && task.partial_result?.nav?.length) ||
    viewResult?.partial_result?.nav?.length,
  )
  const shouldRender = Boolean(
    ((task?.result && task.status === 'success') || viewResult?.result) ||
    viewArtifacts ||
    hasPartialNav,
  )

  // ⚠ 下面两条决定「历史回测」表格出现在**哪里** ✓（它原来在 `App.tsx` 里被抄了两遍 ✗）：
  //   ① 结果区内部 ✓：有 partial 且**不是**"可查看的历史 partial" ⇒ 不重复渲染 ✓
  //      （查看历史未完成任务时，历史表格本来就在下面 ✓，再渲染一份会看到两条一样的 ✓）；
  //   ② 结果区外部 ✓：**既无结果也无产物**时才单独渲染 ✓（用于"直接复用旧参数"✓）。
  //   两条都只在**纯函数**里算 ✓ ⇒ 可以单测 ✓（以前只能靠肉眼对两处 ✗）。
  const showHistoryInResult = !partial || Boolean(viewResult?.partial_result)
  const noResultNoArtifacts = !(
    ((task?.result && task.status === 'success') || viewResult?.result) || viewArtifacts
  )
  const showHistoryStandalone = noResultNoArtifacts

  return {
    partialTaskId, partialStatus, shownTaskId, shownRunning, partialRunning,
    r, a, partial, shouldRender, showHistoryInResult, showHistoryStandalone,
  }
}
