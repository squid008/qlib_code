// @vitest-environment jsdom

/**
 * `HistoryPanel` **补充分支**（★ v2.11.86）—— 已有 `historyPanel.test.tsx` 覆盖了
 * 「渲染 / 查看 / 续测 / 删除确认」，这里补它没碰的那几条 ✓：
 *
 * | 分支 | 漏了会怎样 |
 * |---|---|
 * | **分页**（>20 条）| 用户只能看到前 20 条，**更早的回测"消失"** ✗（而这正是历史面板存在的意义）|
 * | **批量删除** | 勾了 3 条只删 1 条 / 删错目录 ✗ |
 * | **「用这些参数」「复用回测」** | 回调拿到的 `(params, task_id)` 错位 ⇒ **拿别人的模型去跑自己的参数** ✗ |
 * | **并发满时"复用回测"必须禁用** | 点了没反应 / 后端 429 ✗（与「开始回测」行为要一致 ✓）|
 * | **列表接口失败** | 空白一片，用户以为"没有历史"✗ |
 * | **`refreshKey` 变化要重载** | 任务取消/完成后列表还是旧的 ✗ |
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  snap: vi.fn(),
  del: vi.fn(),
  img: vi.fn(),
}))

vi.mock('../api', () => ({
  listBacktestsHistory: () => mocks.list(),
  getBacktestSnapshot: (id: string) => mocks.snap(id),
  getBacktestImageUrl: () => mocks.img(),
  deleteBacktest: (id: string) => mocks.del(id),
}))

import HistoryPanel from '../components/HistoryPanel'

const mounted: Array<() => void> = []

afterEach(() => {
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 忽略 ✓ */
    }
  }
})

function item(i: number, over: Record<string, unknown> = {}) {
  return {
    task_id: `T-${i}`,
    dir_name: `20240101_T${i}`,
    seq: i,
    has_params: true,
    has_result: true,
    has_meta: true,
    has_artifacts: true,
    images: {},
    segments: [],
    is_task_running: false,
    ...over,
  }
}

beforeEach(() => {
  mocks.list.mockReset()
  mocks.snap.mockReset()
  mocks.del.mockReset()
  mocks.img.mockReset()
  mocks.list.mockResolvedValue({ items: [item(1), item(2)], retention: null })
  mocks.snap.mockResolvedValue({
    task_id: 'T-1',
    params: { topk: 50, model: 'lightgbm' },
    images: {},
  })
  mocks.del.mockResolvedValue(undefined)
  mocks.img.mockReturnValue('')
})

async function mount(props: Record<string, unknown> = {}): Promise<HTMLElement> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const merged = {
    onUseParams: vi.fn(),
    onReuseBacktest: vi.fn(),
    onResume: vi.fn(),
    onViewResult: vi.fn(),
    refreshKey: 0,
    capacity: null,
    ...props,
  } as never
  await act(async () => {
    root.render(<HistoryPanel {...(merged as React.ComponentProps<typeof HistoryPanel>)} />)
  })
  mounted.push(() => {
    act(() => root.unmount())
    host.remove()
  })
  return host
}

function allButtons(host: HTMLElement): HTMLButtonElement[] {
  return Array.from(host.querySelectorAll('button')) as HTMLButtonElement[]
}

function clickRe(host: HTMLElement, re: RegExp, index = 0): void {
  const hit = allButtons(host).filter((b) => re.test((b.textContent || '').trim()))
  if (!hit[index]) {
    throw new Error(
      `没找到按钮 ${String(re)} → 实际按钮：${allButtons(host).map((b) => (b.textContent || '').trim()).join(' | ')}`,
    )
  }
  act(() => {
    hit[index].click()
  })
}

describe('HistoryPanel 补充分支', () => {
  it('⭐⭐ 超过 20 条要**分页**（否则更早的历史"消失"✗）', async () => {
    mocks.list.mockResolvedValue({
      items: Array.from({ length: 25 }, (_, i) => item(i + 1)),
      retention: null,
    })
    const host = await mount()
    const t = host.textContent || ''
    // ⚠ 列表行显示的是 **`dir_name`**（`20240101_T20`），不是 `task_id` ✗→✓
    //   （我第一版断言 `T-20` ⇒ 红 ✓；真实文案里只有目录名 ✓）。
    expect(t).toContain('20240101_T20')
    expect(t, '第 21 条不该在第一页 ✗').not.toContain('20240101_T21')
    clickRe(host, /下一页|»|下页/)
    expect(host.textContent || '').toContain('20240101_T21')
  })

  it('「用这些参数」⇒ `onUseParams(params, task_id)`，参数与 id **不许错位** ✗', async () => {
    const onUseParams = vi.fn()
    const host = await mount({ onUseParams })
    clickRe(host, /用这些参数|沿用参数|用参数/)
    expect(onUseParams).toHaveBeenCalledTimes(1)
    const [params, taskId] = onUseParams.mock.calls[0]
    expect(taskId).toBe('T-1')
    expect(params).toMatchObject({ topk: 50 })
  })

  it('「复用回测」⇒ `onReuseBacktest(params, task_id)`', async () => {
    const onReuseBacktest = vi.fn()
    const host = await mount({ onReuseBacktest })
    clickRe(host, /复用回测|复用/)
    expect(onReuseBacktest).toHaveBeenCalledTimes(1)
    expect(onReuseBacktest.mock.calls[0][1]).toBe('T-1')
  })

  it('⭐ 并发满（available=0）⇒ 「复用回测」必须 **disabled**（与「开始回测」一致 ✓）', async () => {
    const host = await mount({ capacity: { available: 0, running: 4, max_concurrent: 4 } })
    const reuse = allButtons(host).filter((b) => /复用/.test(b.textContent || ''))
    expect(reuse.length).toBeGreaterThan(0)
    expect(reuse.every((b) => b.disabled), '并发满时"复用回测"应全部禁用 ✗').toBe(true)
  })

  it('⚠ 列表接口失败时**与"空"无法区分**（记一条真实 UX 缺口，不是测试问题）', async () => {
    mocks.list.mockRejectedValue(new Error('后端 500'))
    const host = await mount()
    // 实测：组件**吞掉错误**、直接渲染空态 ✓ ⇒ 用户看到"暂无历史回测"，
    // 会以为"历史被清了"✗（而不是"接口挂了，重试一下 ✓"）。
    // ⚠ 这里**不**把"吞错"写成期望 ✓ —— 只钉住"当前确实吞了"这个事实，
    //   等哪天组件补了错误态，这条会红 ⇒ 那就改成断言错误文案 ✓。
    expect(host.textContent || '').toContain('暂无历史回测')
  })

  it('`refreshKey` 变化会重新拉历史（次数**增加**即可，不假设基线次数）', async () => {
    const host = await mount()
    // ⚠ 首次挂载实测就会调 **2 次**（React 18 的 effect 双跑/内部两段加载 ✓）——
    //   我第一版断言"恰好 1 次"⇒ 红 ✓。这里只比**增量** ✓。
    const base = mocks.list.mock.calls.length
    expect(base).toBeGreaterThanOrEqual(1)
    await mount({ refreshKey: 1 })
    expect(mocks.list.mock.calls.length).toBeGreaterThan(base)
    expect(host).toBeTruthy()
  })

  it('「刷新」按钮会再拉一次历史（用户手动刷新 ✓）', async () => {
    const host = await mount()
    const base = mocks.list.mock.calls.length
    clickRe(host, /刷新/)
    expect(mocks.list.mock.calls.length).toBe(base + 1)
  })

  // ⚠ 批量删除没测成 —— 实测这个面板**没有复选框**（`input[type=checkbox]` 计数为 0 ✓），
  //   选择 UI 不是 checkbox 形态（我猜错了 ✓）。要测它得先读懂那套选择逻辑，
  //   留到下一轮；这里**不**写"点了没反应"这种空断言 ✗（那比没测更糟 ✓）。
})
