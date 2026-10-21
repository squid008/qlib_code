// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/**
 * `EventStudyModal` 的 **`defaultK` 优先**（★ v2.10.84，2026-10-21 ✓）。
 *
 * ## 这是用户**报过的真 bug**（v1.18.63 修）
 * > 「点『**周期 3**』那一行，弹窗里却默认显示 **7 日**口径」
 *
 * 真因：净值曲线的口径 `k0` 原来固定取后端 `max_k`（任务里填的**最大**预测周期）——
 * `1:2:7` 这种序列下 `max_k = 7` ⇒ 用户点的是 3 那一行，看到的却是 7 日曲线
 * ⇒ **表格按 3 期判定、曲线按 7 期画**（两张图并排、口径不同，用户不可能看出来）。
 *
 * 修法一行：`const k0 = defaultK ?? last ?? 20`（父组件把"点的那一行"传进来）。
 *
 * ## 为什么断言 `runEventNav` 的入参（而不是界面文字）
 * `k0` 是内部 state ✗，但它唯一的**可观察出口**是请求净值曲线时的参数 ✓
 * —— 那正是用户看到的那条曲线的真实口径 ✓，比断言文字更硬（文字会改，请求参数不会）。
 *
 * ## ⚠⚠ 四条卡过的坑（都实测过，别再踩）
 * 1. 参数叫 **`hold_days`** ✗ 不是 `k`（取 `.k` 永远是 `undefined`）；
 * 2. 请求在 **`window.setTimeout`** 里发出 ⇒ 只 flush 微任务永远等不到，要**真等一会儿**；
 * 3. 组件真读的 `result.*` 字段 **19 个**（`grep "result\.[a-z_]+"` 一次列全）——
 *    少一个就 `Cannot read properties of undefined (reading 'map')`；
 * 4. `runEventNav` 的**返回值**也必须按 `EventNavResult` 给全
 *    （`nav` / `nav_columns` / `stats` / `nav_diag` / `alloc_default`）——
 *    父组件会读这五个，缺了照样崩。
 */
const spies = vi.hoisted(() => ({
  createEventStudy: vi.fn(async () => ({ task_id: 'T1' })),
  getEventStudyProgress: vi.fn(async () => ({ status: 'success', progress: 100, result: null })),
  cancelEventStudy: vi.fn(async () => ({ status: 'ok' })),
  // ⚠ 这里**必须声明参数类型** ✗→✓：否则 `mock.calls[i][0]` 被推成 `undefined` ✓
  //   ⇒ `as { hold_days?: number }` 会报 "两者不重叠"（tsc 直接红 ✗，而 vitest 不查类型所以看不出来 ✓）。
  runEventNav: vi.fn(async (_req: { hold_days?: number; task_id?: string }) => ({
    hold_days: 3,
    nav: [{ date: '2024-01-02', nav: 1.0, bench: 1.0 }],
    nav_columns: ['nav'],
    stats: {},
    diag: {},
    alloc_default: null,
    nav_diag: {},
  })),
  exportEventStudyXlsx: vi.fn(async () => ({ blob: new Blob(), filename: 'x.xlsx' })),
  blobErrorDetail: vi.fn(async () => 'err'),
}))

vi.mock('../api', () => spies)

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

/** 结果夹具：**19 个字段全给** ✓（缺一个渲染期就 `.map` 崩 ✗）。 */
function makeResult(maxK = 7) {
  return {
    factor: { id: 'f1', name: 'F', expression: '$close' },
    params: {
      universe: 'all', start_date: '2024-01-01', end_date: '2024-06-30',
      max_k: maxK, price_adjust: 'pre',
    },
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

async function mount(defaultK: number | null) {
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
        defaultK={defaultK}
      />,
    )
  })
  // ⚠ 必须**真等**：`runEventNav` 在 `window.setTimeout` 里（见文件头坑 2）
  for (let i = 0; i < 4; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 150)) })
  }
  return host
}

const lastNavHoldDays = () => {
  const calls = spies.runEventNav.mock.calls
  return calls.length ? (calls[calls.length - 1][0] as { hold_days?: number }).hold_days : undefined
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
  spies.runEventNav.mockClear()
})

describe('EventStudyModal · defaultK', () => {
  it('⭐⭐⭐ 传 defaultK=3 ⇒ 净值曲线按 **3 期**算（这就是用户报的那个 bug）', async () => {
    // 结果里的 `max_k` 是 7（任务填的最大周期）—— 修前就是拿它当默认，于是显示 7 日口径
    await mount(3)
    expect(lastNavHoldDays(), '必须按用户点的那一行（3 期）算 ✓，而不是 max_k=7 ✗').toBe(3)
  })

  it('⭐⭐ 不传 defaultK ⇒ 回退到结果曲线的**最后一档**（7 期）', async () => {
    await mount(null)
    expect(lastNavHoldDays(), '缺省应取 `curve` 最后一项的 k ✓（与表格末行一致 ✓）').toBe(7)
  })
})
