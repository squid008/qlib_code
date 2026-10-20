// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import ICChart from '../components/ICChart'
import type { ICAnalysis, ICSegment } from '../types'

/**
 * `ICChart` 的**交互**测试（★ v2.10.28，2026-10-20 ✓ —— 不用 RTL，沿用 `taskStatusPanel`
 * 的 `createRoot + act` 写法 ✓，那一套在本仓已被证明能跑 ✓）。
 *
 * ## 为什么这轮才开始写交互测试（一条被推翻的"事实" ✗→✓）
 *
 * `metricCards.test.tsx` 的注释一直写着「CI（Linux + Node 20）上**只要走 jsdom 就崩**」✗
 * ⇒ 后来所有组件测试都退成 `renderToStaticMarkup` ✓ ⇒ **点击类断言一个都写不了** ✗。
 * 本轮去 CI 日志里核对：`taskStatusPanel.test.tsx`（`createRoot` + `act`，**必须要 DOM** ✓）
 * **真的跑了** ✓，而且整轮 **28 文件全过** ✓ ⇒ **jsdom 在 CI 上是好的** ✓，
 * 那条注释是**过期/错误**的 ✗（已同步修正 ✓）。
 * ⇒ 结论：**交互可测** ✓，前端覆盖率才有往上走的余地 ✓。
 *
 * ## 这里钉的：点一下，界面对不对
 *
 * ① 点「训练集 IC」⇒ 列表换成训练集分段 ✓（默认在测试集 ✓，切过去必须真的换 ✓）；
 * ② 点另一个分段 ⇒ 徽章数值跟着换 ✓（徽章串味 = 用户看着 A 段的数据以为是 B 段 ✗）。
 */
const seg = (over: Partial<ICSegment> = {}): ICSegment => ({
  segment: '2024',
  points: [
    { date: '2024-01-02', ic: 0.1 },
    { date: '2024-01-03', ic: -0.2 },
  ],
  mean_ic: 0.0123,
  icir: 0.5,
  ...over,
})

const DATA: ICAnalysis = {
  test: [
    seg({ segment: '测试段A', mean_ic: 0.1111, icir: 1.111 }),
    seg({ segment: '测试段B', mean_ic: 0.2222, icir: 2.222 }),
  ],
  train: [seg({ segment: '训练段C', mean_ic: 0.3333, icir: 3.333 })],
}

let root: Root | null = null
let host: HTMLDivElement | null = null

function mount(ui: React.ReactElement) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(ui))
  return host
}

const clickText = (el: HTMLElement, text: string) => {
  const btn = Array.from(el.querySelectorAll('button')).find((b) => b.textContent === text)
  if (!btn) throw new Error('找不到按钮：' + text + '（实际：'
    + Array.from(el.querySelectorAll('button')).map((b) => b.textContent).join('/') + '）')
  act(() => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('ICChart（交互）', () => {
  it('默认在测试集；点「训练集 IC」后列表真的换过去', () => {
    const h = mount(<ICChart data={DATA} />)
    expect(h.textContent).toContain('测试段A')
    expect(h.textContent).not.toContain('训练段C')

    clickText(h, '训练集 IC')
    expect(h.textContent).toContain('训练段C')
    expect(h.textContent).not.toContain('测试段A')
  })

  it('⭐ 切换分段 ⇒ 徽章数值跟着换（不许串味 ✗）', () => {
    const h = mount(<ICChart data={DATA} />)
    // 默认 active='' ⇒ 落到第一个分段
    expect(h.textContent).toContain('平均IC: 0.1111')
    clickText(h, '测试段B')
    expect(h.textContent).toContain('平均IC: 0.2222')
    expect(h.textContent).not.toContain('平均IC: 0.1111')
  })

  it('切回测试集时选中态被重置（回到第一个分段）', () => {
    const h = mount(<ICChart data={DATA} />)
    clickText(h, '测试段B')
    clickText(h, '训练集 IC')
    clickText(h, '测试集 IC')
    expect(h.textContent).toContain('平均IC: 0.1111')
  })
})
