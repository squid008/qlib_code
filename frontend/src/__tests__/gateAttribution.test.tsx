// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/**
 * `GateAttribution`（390 行）测试（★ v2.10.39，2026-10-20 ✓）。
 *
 * api 驱动：挂载即 `getBacktestSnapshot(taskId)` ✓ 取 `snap.compose` 的归因 ✓
 * （归因是**每段**写的 ✓，跑完才完整 ✓）。
 *
 * ## 钉什么
 *
 * ① 挂载**必须**去拉快照 ✓（不拉 ⇒ 永远显示"暂无归因"✗，而用户以为"这个任务没有归因"✗）；
 * ② **快照里没有 `compose` ⇒ 不崩** ✓（老任务/未跑 gate 的任务都走这条 ✓）。
 */
const spies = vi.hoisted(() => ({
  getBacktestSnapshot: vi.fn(async () => ({ compose: null })),
}))
vi.mock('../api', () => spies)

import GateAttribution from '../components/GateAttribution'

let root: Root | null = null
let host: HTMLDivElement | null = null

const flush = async () => {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

const mount = async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(<GateAttribution taskId="t1" />)
  })
  await flush()
  return host
}

beforeEach(() => Object.values(spies).forEach((s) => s.mockClear()))
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('GateAttribution', () => {
  it('⭐ 挂载即拉快照（不拉 ⇒ 永远"暂无归因"，用户会以为任务没做 gate ✗）', async () => {
    await mount()
    expect(spies.getBacktestSnapshot).toHaveBeenCalledWith('t1')
  })

  it('⭐ 快照没有 compose ⇒ **整块不渲染**（不是显示"暂无归因"✗）', async () => {
    // ⚠ 实测：不是"空态文案"，而是 `return null` ✓ —— 由**父组件**决定要不要给标题 ✓
    //   （否则没跑 gate 的任务上会留一个空壳卡片 ✓，这正是很多页面"莫名多一块空白"的来源 ✗）。
    const h = await mount()
    expect(h.textContent).toBe('')
  })

  it('传 formulas 也不崩（映射回"你起的名字"✓）', async () => {
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    await act(async () => {
      root!.render(
        <GateAttribution
          taskId="t1"
          formulas={[{ id: '1', name: '门控A', text: 'x', expression: 'x' }]}
        />,
      )
    })
    await flush()
    // 同样：没有 compose ⇒ 不渲染 ✓（`formulas` 只是"映射表"，不能凭空造出归因 ✓）
    expect(host.textContent).toBe('')
  })
})
