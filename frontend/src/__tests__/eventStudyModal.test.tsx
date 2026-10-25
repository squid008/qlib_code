// @vitest-environment jsdom

/**
 * `EventStudyModal`（★ v2.11.89）—— 缺 402 语句，是剩余最大块之一。
 *
 * ## 两条主线（对应缺行最多的两段）
 * ① **提交 + 轮询**（L402-449）：点「开始」⇒ `createEventStudy` ⇒ `setInterval` 每 1.5s 拉进度
 *    ⇒ success/failed/cancelled 三个分支各自收尾 ✓（用 fake timers 才测得到 ✓）；
 * ② **结果区**（L1156 起等）：`data` 里给了 `curve`/`prob`/`upside`/`top_events`/`baseline`
 *    ⇒ 曲线、概率表、上攻空间、榜单、超额曲线才会渲染 ✓（缺数据 ⇒ 整段不跑 ✗）。
 *
 * ## 它坏掉的样子（都不报错）
 * · 点了「开始」**没反应**（或被并发挡住却不提示）⇒ 用户以为没点上 ✗；
 * · 轮询 `failed` 却仍显示"研究完成"✗；`cancelled` 当成失败 ✗；
 * · **超额曲线拿均值当典型**：注释明写"均值＝日配对口径（易被少数暴涨事件主导）；
 *   中位数＝事件级口径（典型一次触发的超额）" ⇒ 两者混用会让人高估 ✗。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  progress: vi.fn(),
  cancel: vi.fn(),
  nav: vi.fn(),
  exportXlsx: vi.fn(),
  capture: vi.fn(),
}))

vi.mock('../captureChart', () => ({ captureSection: () => mocks.capture() }))
vi.mock('../api', async (orig) => {
  const real = await orig<Record<string, unknown>>()
  return {
    ...real,
    createEventStudy: (p: unknown) => mocks.create(p),
    getEventStudyProgress: (id: string) => mocks.progress(id),
    cancelEventStudy: (id: string) => mocks.cancel(id),
    runEventNav: (p: unknown) => mocks.nav(p),
    exportEventStudyXlsx: (p: unknown) => mocks.exportXlsx(p),
    blobErrorDetail: async (e: unknown) => String(e),
  }
})

import EventStudyModal from '../components/EventStudyModal'

const mounted: Array<() => void> = []

beforeEach(() => {
  mocks.create.mockReset().mockResolvedValue({ task_id: 'T-ES' })
  mocks.progress.mockReset()
  mocks.cancel.mockReset().mockResolvedValue(undefined)
  mocks.nav.mockReset()
  mocks.exportXlsx.mockReset().mockResolvedValue({ blob: new Blob(['PK']), filename: 'es.xlsx' })
  mocks.capture.mockReset().mockResolvedValue('data:image/png;base64,AAA')
  Object.defineProperty(URL, 'createObjectURL', { writable: true, value: () => 'blob:stub' })
  Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: () => {} })
})

afterEach(() => {
  vi.useRealTimers()
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 忽略 */
    }
  }
})

/** 一份"数据齐全"的事件研究结果 ✓（缺哪块，弹窗就整段不渲染 ✗）。 */
function mkData(over: Record<string, unknown> = {}) {
  const ks = [1, 3, 5, 10, 20]
  return {
    factor: { id: 'f1', name: '黄金坑启动', expression: 'CPX>0', source_formula: 'CPX>0' },
    params: { universe: 'csi300', start_date: '2024-01-01', end_date: '2024-12-31', max_k: 20, price_adjust: 'hfq' },
    n_events: 120,
    n_aligned: 118,
    n_raw: 130,
    ks,
    curve: ks.map((k) => ({ k, mean: k * 0.002, median: k * 0.0015, counts: 118 })),
    prob: ks.map((k) => ({
      k,
      gt0: 0.55, gt10: 0.3, gt20: 0.18, gt50: 0.05, gt100: 0.01,
      lt0: 0.45, lt10: 0.22, lt20: 0.1, lt50: 0.02, lt100: 0,
    })),
    upside: {
      mean: 0.09, median: 0.07, p75: 0.12, p90: 0.2, p99: 0.4, max: 0.6,
      reach20: 0.3, reach50: 0.08, reach100: 0.01,
      dn_mean: -0.05, dn_median: -0.04, dn_min: -0.2,
    },
    top_events: [
      { code: 'SH600000', dt: '2024-03-01', ret: 0.12, max_ret: 0.2 },
      { code: 'SZ000001', dt: '2024-04-01', ret: 0.1, max_ret: 0.18 },
    ],
    worst_events: [{ code: 'SH600001', dt: '2024-05-01', ret: -0.08, min_ret: -0.15 }],
    baseline: {
      ks,
      trigger_pair: ks.map((k) => k * 0.002),
      baseline: ks.map((k) => k * 0.0008),
      excess: ks.map((k) => k * 0.0012),
      trigger_median: ks.map((k) => k * 0.0015),
      baseline_median: ks.map((k) => k * 0.0006),
      excess_median: ks.map((k) => k * 0.0009),
      n_pair_days: 240,
      t_hac: ks.map(() => 2.1),
      win: ks.map(() => 0.56),
      n_pair_days_k: ks.map(() => 240),
    },
    dist_edges: [-0.2, -0.1, -0.02, 0.02, 0.1, 0.2],
    top_by_k: { '20': [{ code: 'SH600000', dt: '2024-03-01', ret: 0.2, max_ret: 0.2 }] },
    worst_by_k: { '20': [{ code: 'SH600001', dt: '2024-05-01', ret: -0.15, min_ret: -0.15 }] },
    max_k: 20,
    n_unaligned: 2,
    n_short: 3,
    short_events: [{ code: 'SH600002', dt: '2024-12-20', n_valid_k: 3 }],
    ...over,
  }
}

const REQ = { factor: { id: 'f1', name: '黄金坑启动', expression: 'CPX>0' }, max_k: 20 }

async function mount(props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const merged = {
    open: true,
    onClose: vi.fn(),
    factorName: '黄金坑启动',
    req: REQ,
    data: mkData(),
    // ⚠ 属性名是 **`sourceTaskId`**（不是 `taskId`）✗→✓ —— 导出的 `doExport` 靠它取
    //   `tid`；不给 ⇒ 早退 ⇒ 点了"导出"什么都不发生（我第一版就这么红的 ✓）。
    sourceTaskId: 'T-ES',
    ...props,
  }
  await act(async () => {
    root.render(<EventStudyModal {...(merged as unknown as React.ComponentProps<typeof EventStudyModal>)} />)
  })
  mounted.push(() => {
    act(() => root.unmount())
    host.remove()
  })
  return { host, props: merged as Record<string, unknown> }
}

function btns(host: HTMLElement): HTMLButtonElement[] {
  return Array.from(host.querySelectorAll('button')) as HTMLButtonElement[]
}

function clickRe(host: HTMLElement, re: RegExp, idx = 0) {
  const hit = btns(host).filter((b) => re.test((b.textContent || '').trim()))
  if (!hit[idx]) {
    throw new Error(`没找到 ${String(re)} → 按钮：${btns(host).map((b) => (b.textContent || '').trim()).join(' | ')}`)
  }
  act(() => {
    hit[idx].click()
  })
}

describe('EventStudyModal', () => {
  it('open=false 不渲染', async () => {
    const { host } = await mount({ open: false })
    expect((host.textContent || '').length).toBeLessThan(40)
  })

  it('⭐ 数据齐全：曲线 / 概率 / 上攻空间 / 榜单 / 超额 都要出现', async () => {
    const { host } = await mount()
    const t = host.textContent || ''
    expect(t).toContain('黄金坑启动')
    expect(t, '概率表没渲染 ✗').toMatch(/概率|>0%|>10%|占比/)
    expect(t, '上攻空间没渲染 ✗').toMatch(/上攻|最高点|p90|回撤/)
    expect(t, '榜单没渲染 ✗').toMatch(/SH600000|榜单|最优/)
    expect(t, '超额曲线没渲染 ✗').toMatch(/超额/)
  })

  it('⭐⭐ 打开即**自动提交**（小公式自动算），随后按 1.5s 轮询', async () => {
    vi.useFakeTimers()
    mocks.progress.mockResolvedValue({ status: 'success', progress: 100, message: '完成', result: mkData() })
    const { host } = await mount({ data: null })
    // ⚠ 这里**没有**"开始"按钮 ✗→✓ —— 实测按钮只有 `取消 | 导出 | 关闭` ✓
    //   （v1.20.11 起"小公式自动算、大公式才露按钮" ⇒ 首次打开就自动提交 ✓）。
    await act(async () => {
      await Promise.resolve()
    })
    expect(mocks.create, '打开就该自动提交一次 ✗').toHaveBeenCalledTimes(1)
    await act(async () => {
      vi.advanceTimersByTime(1600)
      await Promise.resolve()
    })
    expect(mocks.progress, '自动提交后应按 1.5s 轮询 ✗').toHaveBeenCalled()
    expect(host).toBeTruthy()
  })

  it('轮询 failed ⇒ 显示后端原因（不能当成功 ✗）', async () => {
    vi.useFakeTimers()
    mocks.progress.mockResolvedValue({
      status: 'failed', progress: 0, message: '样本不足', error: '样本不足：触发 < 10',
    })
    const { host } = await mount({ data: null })
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      vi.advanceTimersByTime(1600)
      await Promise.resolve()
    })
    expect(host.textContent || '').toMatch(/样本不足|失败/)
  })

  it('关闭按钮 ⇒ onClose', async () => {
    const onClose = vi.fn()
    const { host } = await mount({ onClose })
    clickRe(host, /关闭|✕|×|知道了/)
    expect(onClose).toHaveBeenCalled()
  })

  it('导出失败要显示原因（不许静默 ✗）', async () => {
    mocks.exportXlsx.mockRejectedValue(new Error('后端 500'))
    const { host } = await mount()
    const exp = btns(host).filter((b) => /导出/.test(b.textContent || ''))[0]
    if (!exp) throw new Error('没找到导出按钮 → ' + btns(host).map((b) => b.textContent).join(' | '))
    await act(async () => {
      exp.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    // ⚠ 失败提示走 `expMsg`（`ok:false` 才渲染 ✓）—— 要等 promise 链跑完再断言 ✓
    expect(host.textContent || '').toMatch(/500|失败|Error/)
  })

  it('数据不足（n_short/short_events）要提示，不能静默少样本 ✗', async () => {
    const { host } = await mount()
    expect(host.textContent || '').toMatch(/数据不足|未平仓|不足/)
  })
})
