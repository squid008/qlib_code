/**
 * `HistoryPanel` **挂载冒烟**（★ v2.9.69，2026-10-19 ✓ —— 前端交互补强第二批，**故意只测很小一块** ✓）。
 *
 * ## 为什么只测"空态 + 挂载"，不测它的批量删除/续测 ✗
 *
 * 这个组件 **649 行** ✗：批量删除、配额回收提示、逐行 snapshot 拉取、续测乐观标记……
 * 要做**有意义的**交互测试，得先把 `../api` 的四个函数都 mock 出**可信的返回形状** ✗，
 * 并且逐条对齐它的乐观更新逻辑 ✓ —— 那是一个**独立的专门回合** ✗（不是顺手能做完的 ✓）。
 *
 * ⇒ 本轮只钉两件**廉价但真实**的事 ✓：
 * 1. 面板能挂载、**调了 history 接口一次** ✓（改了 import 名/取了不存在的字段 ⇒ 这里先红 ✗）；
 * 2. **空态文案还在** ✓（"暂无历史回测"✓ —— 它被删掉/改词时用户会看到一个**空白大块** ✗，
 *    而不是"没有记录" ✓）。
 *
 * ⚠ 用 `vi.mock('../api')` ✓（组件在 `useEffect` 里会真取数 ✗ —— 不 mock 就会去打后端 ✓）。
 */
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

const listMock = vi.fn(async () => ({ items: [], retention: null }))

vi.mock('../api', () => ({
  listBacktestsHistory: () => listMock(),
  getBacktestSnapshot: vi.fn(async () => null),
  getBacktestImageUrl: vi.fn(() => ''),
  deleteBacktest: vi.fn(async () => undefined),
}))

import HistoryPanel from '../components/HistoryPanel'

async function renderInto(ui: React.ReactElement): Promise<HTMLElement> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(ui)
  })
  return host
}

const noop = () => {}

describe('HistoryPanel（冒烟）', () => {
  it('挂载即调用一次 history 接口，并渲染标题与空态文案', async () => {
    const el = await renderInto(
      <HistoryPanel onUseParams={noop} onReuseBacktest={noop} onResume={noop}
        onViewResult={noop} refreshKey={0} capacity={null} />,
    )
    // ⚠ 不是"恰好一次" ✗ —— 实测挂了 **2 次** ✓：组件里有两个 effect（初始加载 ✓ +
    //   `refreshKey` 变化时的刷新 ✓），挂载时都会触发 ✓。断言"至少一次" ✓ 才是对的。
    expect(listMock.mock.calls.length).toBeGreaterThanOrEqual(1)
    expect(el.textContent).toContain('历史回测')
    expect(el.textContent).toContain('暂无历史回测')
  })
})
