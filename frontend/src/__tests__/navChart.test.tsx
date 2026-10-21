// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import NavChart from '../components/NavChart'

/**
 * `NavChart`（296 行）测试（★ v2.10.58，2026-10-21 ✓）—— 补完"前端零测试组件"的最后一个。
 *
 * ## ⚠ 先说清这个文件**测不到什么**（不假装 ✓）
 *
 * 它真正的逻辑是 `buildUniverseSeries`（把分段收益**链式拼成**"池内等权"地板线 ✓，
 * 且旧产物没有 `universe` 列时要**返回 null**、该线自动不画 ✓）—— 但那个函数**没有导出** ✗，
 * 而它的产物只喂给 recharts ⇒ 在 jsdom 下 `ResponsiveContainer` 宽高为 0 ⇒ **画不出来** ✗
 * ⇒ 本文件只能钉"空态与不崩" ✓。
 *
 * ⇒ 若哪天要真正验那条地板线，**唯一的路是把它导出来单测** ✓（纯函数、无 DOM ✓）——
 *   已记在这里，避免下一个人又从头试一遍 ✗。
 */
let root: Root | null = null
let host: HTMLDivElement | null = null

function mount(ui: React.ReactElement) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(ui))
  return host
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('NavChart', () => {
  it('没数据 ⇒ 标题 + 「暂无净值数据」（**标题必须在** ✓：否则用户不知道这块是什么 ✗）', () => {
    // ⚠ 标题是「**收益**曲线」而不是「净值曲线」✗→✓：
    //   实测**空态**原来写「净值曲线」、**有数据**写「收益曲线」⇒ 同一块区域两个名字 ✓
    //   （写这条测试时撞出来的 ✓）。已统一成「收益曲线」✓（这块还画基准/池内等权 ✓）。
    const h = mount(<NavChart nav={null} />)
    expect(h.textContent).toContain('收益曲线')
    expect(h.textContent).toContain('暂无净值数据')
  })

  it('空数组同等于没数据（后端可能给 `[]` 而不是 `null` ✓）', () => {
    const h = mount(<NavChart nav={[]} />)
    expect(h.textContent).toContain('暂无净值数据')
  })

  it('有净值点时不崩（图在 jsdom 里画不出 ✓，但**渲染路径**必须无异常 ✓）', () => {
    const nav = [
      { date: '2024-01-02', value: 1.0, benchmark: 1.0 },
      { date: '2024-01-03', value: 1.02, benchmark: 1.005 },
    ] as never
    const h = mount(<NavChart nav={nav} endDate="2024-12-31" layerReturns={null} />)
    expect(h.textContent).toContain('收益曲线')
    expect(h.textContent).not.toContain('暂无净值数据')
  })
})
