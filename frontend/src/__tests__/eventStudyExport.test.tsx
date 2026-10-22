// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/**
 * `EventStudyModal` 的**导出**主链路（★ v2.11.8）。
 *
 * ## 这个弹窗唯一"带副作用"的操作
 * 其他都是渲染 ✓；只有「导出」会**真的生成一个文件** ✓ —— 所以它最值得钉：
 * 导错的代价不是崩溃，而是**用户拿着一个数字对不上的 Excel 去复盘** ✓。
 *
 * ## ⭐⭐ 核心契约：导出必须与**页面上那次净值回测同口径**
 * 源码注释明写（`hold_days: navK ?? undefined` 那一行上方）：
 * > 「`navK` 可能为 null（未解析出 k）⇒ 不传，后端按默认 20 算 —— 与页面上那次
 * >   `runEventNav`（同样不传有效值）**取到同一次回测** ✓」
 *
 * 换句话说：**导出文件里的净值曲线，必须就是用户屏幕上看到的那条** ✓。
 * 若这里传了别的 k（比如又用回 `max_k`），导出的曲线和页面**不是同一次回测** ✗，
 * 而两者都在"合理范围"内 ⇒ 只有逐位对比才能发现 ✗。
 *
 * ## ⚠ 三个必须 mock 的东西（否则 jsdom 下必红）
 * · `../downloadFile`（`saveBlob` 用 `URL.createObjectURL` ✗ jsdom 没实现）；
 * · `../captureChart`（`html2canvas` 之类，jsdom 跑不了 ✗）；
 * · `../api`（全部请求打桩 ✓）。
 *
 * ## 复用来的四条坑（见 `eventStudyDefaultK.test.tsx` 文件头）
 * `hold_days` 不是 `k` ｜ 请求在 `setTimeout` 里要**真等** ｜
 * `result.*` 共 **19 个字段** ｜ `runEventNav` 的**返回**也要按 `EventNavResult` 给全。
 */
const spies = vi.hoisted(() => ({
  createEventStudy: vi.fn(async () => ({ task_id: 'T1' })),
  getEventStudyProgress: vi.fn(async () => ({ status: 'success', progress: 100, result: null })),
  cancelEventStudy: vi.fn(async () => ({ status: 'ok' })),
  runEventNav: vi.fn(async (_req: { hold_days?: number; task_id?: string }) => ({
    hold_days: 3,
    nav: [{ date: '2024-01-02', nav: 1.0, bench: 1.0 }],
    nav_columns: ['nav'],
    stats: {},
    diag: {},
    alloc_default: null,
    nav_diag: {},
  })),
  exportEventStudyXlsx: vi.fn(
    async (_req: { task_id?: string; hold_days?: number; shots?: Record<string, string> }) => ({
      blob: new Blob(),
      filename: 'x.xlsx',
    }),
  ),
  blobErrorDetail: vi.fn(async () => 'err'),
}))

vi.mock('../api', () => spies)

// `saveBlob` 内部用 `URL.createObjectURL`（jsdom 无实现 ✗）⇒ 打桩 ✓
const dl = vi.hoisted(() => ({ saveBlob: vi.fn(), stamp: () => 'S' }))
vi.mock('../downloadFile', () => dl)

// 截图走 html2canvas（jsdom 跑不了 ✗）⇒ 打桩返回一张假图 ✓
const cap = vi.hoisted(() => ({ captureSection: vi.fn(async () => 'data:image/png;base64,AAA') }))
vi.mock('../captureChart', () => cap)

import EventStudyModal from '../components/EventStudyModal'

let root: Root | null = null
let host: HTMLDivElement | null = null

const CURVE = [
  { k: 3, n: 20, mean: 0.01, median: 0.01, win: 0.6, p10: null, p25: null, p75: null },
  { k: 7, n: 20, mean: 0.02, median: 0.02, win: 0.6, p10: null, p25: null, p75: null },
]
const PROB = [
  { k: 3, gt0: 0.6, gt10: 0.2, gt20: 0.1, gt50: null, gt100: null, lt0: 0.4, lt10: 0.1 },
  { k: 7, gt0: 0.55, gt10: 0.25, gt20: 0.1, gt50: null, gt100: null, lt0: 0.45, lt10: 0.15 },
]
const UPSIDE = {
  mean: 0.05, median: 0.04, p75: 0.08, p90: 0.12, p99: 0.2, max: 0.3,
  reach20: 0.3, reach50: 0.05, reach100: null, dn_mean: -0.04,
}

function makeResult(maxK = 7) {
  return {
    factor: { id: 'f1', name: 'F', expression: '$close' },
    params: { universe: 'all', start_date: '2024-01-01', end_date: '2024-06-30', max_k: maxK, price_adjust: 'pre' },
    n_events: 20, n_aligned: 20, n_raw: 25,
    max_k: maxK, n_short: 0, n_unaligned: 0, dist_edges: [],
    baseline: null, top_by_k: [], worst_by_k: [],
    ks: [3, 7],
    curve: CURVE, prob: PROB, upside: UPSIDE,
    top_events: [], worst_events: [], short_events: [],
  } as never
}

const REQ = {
  factor: { id: 'f1', name: 'F', expression: '$close' },
  universe: 'all', start_date: '2024-01-01', end_date: '2024-06-30',
  max_k: 7, price_adjust: 'pre', fill: 'next_open', limit_judge: true,
} as never

async function mount(opts: { taskId?: string | null } = {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(
      <EventStudyModal
        open
        onClose={() => {}}
        factorName="F"
        req={REQ}
        data={makeResult()}
        defaultK={3}
        sourceTaskId={opts.taskId ?? null}
      />,
    )
  })
  // ⚠ 必须真等（`runEventNav` 在 setTimeout 里，见文件头）
  for (let i = 0; i < 4; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 150)) })
  }
  return host
}

const exportBtn = (h: HTMLElement) =>
  Array.from(h.querySelectorAll('button')).find((b) => /导出|导出中/.test(b.textContent ?? ''))!

const lastNavHoldDays = () => {
  const calls = spies.runEventNav.mock.calls
  return calls.length ? (calls[calls.length - 1][0] as { hold_days?: number }).hold_days : undefined
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
  Object.values(spies).forEach((s) => s.mockClear())
  dl.saveBlob.mockClear()
  cap.captureSection.mockClear()
})

describe('EventStudyModal · 导出', () => {
  it('⭐⭐ 没有任务上下文 ⇒ 导出按钮**禁用**（导出会拿到坏文件 ✗）', async () => {
    const h = await mount({ taskId: null })
    const btn = exportBtn(h)
    expect(btn, '导出按钮必须存在（否则用户找不到入口 ✓）').toBeTruthy()
    // 源码注释：这次结果没有任务上下文（如页面刷新后打开）⇒ 无法导出
    // （触发明细与持仓都在后端任务状态里 ✓）
    expect(btn.disabled, '没有 task_id 时后端根本取不到持仓明细 ⇒ 必须禁用 ✗').toBe(true)
  })

  it('⭐⭐⭐ 导出与**页面那次净值回测同口径**（`hold_days` 必须一致）', async () => {
    const h = await mount({ taskId: 'T9' })
    const navHoldDays = lastNavHoldDays()
    expect(navHoldDays, '前提：页面已经发过净值请求 ✓').toBe(3)

    await act(async () => { exportBtn(h).click() })
    for (let i = 0; i < 4; i += 1) {
      await act(async () => { await new Promise((r) => setTimeout(r, 120)) })
    }

    expect(spies.exportEventStudyXlsx).toHaveBeenCalledTimes(1)
    const arg = spies.exportEventStudyXlsx.mock.calls[0][0] as {
      task_id?: string
      hold_days?: number
    }
    expect(arg.task_id, 'task_id 必须来自父组件传进来的那次任务 ✓').toBe('T9')
    expect(
      arg.hold_days,
      '导出里的净值必须是屏幕上那条（同 hold_days ✓）—— 传别的 k 会导出**另一次回测** ✗',
    ).toBe(navHoldDays)
  })

  it('⭐⭐ 导出失败 ⇒ **不崩、也不写出文件**（宁可报错，不给一个坏文件 ✗）', async () => {
    spies.exportEventStudyXlsx.mockRejectedValueOnce(new Error('boom'))
    const h = await mount({ taskId: 'T9' })

    await act(async () => { exportBtn(h).click() })
    for (let i = 0; i < 4; i += 1) {
      await act(async () => { await new Promise((r) => setTimeout(r, 120)) })
    }

    expect(dl.saveBlob, '失败时绝不能 saveBlob（否则用户拿到空文件 ✓ 以为成功了 ✗）').not.toHaveBeenCalled()
  })
})
