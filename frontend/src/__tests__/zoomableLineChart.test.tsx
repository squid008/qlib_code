// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import ZoomableLineChart from '../components/ZoomableLineChart'

/**
 * `ZoomableLineChart`（577 行）测试（★ v2.10.51，2026-10-21 ✓ —— 前端最后一个零测试组件）。
 *
 * ## 它为什么重要
 *
 * 这是全站**所有折线图的唯一实现** ✓（IC / 分层 / 净值 / 离场曲线都走它 ✓）。
 * 它的缩放窗口算错 ⇒ **用户看到的区间和结论都不对** ✓，而且图**照样画得出来** ✗
 * （最多是"看起来这段很稳"✓）—— 典型的"错了但没人报错"。
 *
 * ## 只测"图外"的东西（图内画不出）
 *
 * recharts 的 `<ResponsiveContainer>` 在 jsdom 里宽高为 **0** ⇒ 内部不渲染 ✗
 * （`vitest.setup.ts` 的 `ResizeObserver` 桩只解决"不报错"，不解决"没有布局"✓）。
 * ⇒ 断言落在**控件与文字**上：`显示第 x~y 点 / 共 n 点` ✓、`重置缩放` ✓、
 * 起点对齐下拉的三种口径 ✓、右上角角标的 `自建仓起 / 当前区间` ✓。
 * ⚠ 这些恰好就是**判断"窗口对不对"最直接的证据** ✓，不是妥协 ✓。
 */

let root: Root | null = null
let host: HTMLDivElement | null = null

const DATA = Array.from({ length: 10 }, (_, i) => ({
  date: `2024-01-${String(i + 2).padStart(2, '0')}`,
  a: 1 + i * 0.01,
  b: 1 + i * 0.005,
}))

const PROPS = {
  keys: ['a', 'b'],
  labelOf: (k: string) => (k === 'a' ? '策略' : '基准'),
  colorOf: () => '#123456',
}

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

describe('ZoomableLineChart', () => {
  it('空数据不崩（换文件/没结果时都会传空 ✓）', () => {
    expect(() => mount(<ZoomableLineChart data={[]} {...PROPS} />)).not.toThrow()
  })

  it('⭐ 默认展示**全区间**（用户 2026-09-15 的要求 ✓）', () => {
    const h = mount(<ZoomableLineChart data={DATA} {...PROPS} />)
    expect(h.textContent).toContain('显示第 1~10 点 / 共 10 点')
  })

  it('⭐⭐ 「重置缩放」**只在该缩放时才出现**（全区间时没有它 ✓）', () => {
    // ⚠ 我第一版断言"全区间时就该有这个按钮"✗ —— 实测源码是 `{zoomed && <button>重置缩放</button>}`
    //   ⇒ **没缩放时它根本不渲染** ✓（没东西可重置，画一个按钮反而误导 ✗）。
    //   顺带记一个坑 ✓：按钮文字在 JSX 里带换行/缩进 ⇒ `textContent === '重置缩放'` 找不到 ✗，
    //   要比就得 `includes(...)` ✓。
    const h = mount(<ZoomableLineChart data={DATA} {...PROPS} />)
    const hasReset = Array.from(h.querySelectorAll('button')).some((b) =>
      (b.textContent || '').includes('重置缩放'))
    expect(hasReset, '全区间不该出现「重置缩放」✗').toBe(false)
    // 但控件行本身要在 ✓（否则用户缩放后也没地方点）
    expect(h.textContent).toContain('缩放后起点对齐')
  })

  it('⭐ 起点对齐默认「各自对齐」，三种口径都在选项里', () => {
    const h = mount(<ZoomableLineChart data={DATA} {...PROPS} />)
    const sel = h.querySelector('select') as HTMLSelectElement
    expect(sel, '对齐方式下拉必须存在 ✓').toBeTruthy()
    expect(sel.value).toBe('each')
    const opts = Array.from(sel.options).map((o) => o.textContent || '')
    expect(opts.some((t) => t.includes('各自对齐'))).toBe(true)
    expect(opts.some((t) => t.includes('锚定主曲线起点'))).toBe(true)
    expect(opts.some((t) => t.includes('不对齐'))).toBe(true)
  })

  it('⭐⭐ 有 `statKey` ⇒ 角标标「自建仓起」（全区间时口径不同 ✓）', () => {
    const h = mount(<ZoomableLineChart data={DATA} {...PROPS} statKey="a" />)
    expect(h.textContent).toContain('策略（自建仓起）')
  })

  it('不给 `statKey` ⇒ 不显示角标（不许画一个"无名"角标 ✗）', () => {
    const h = mount(<ZoomableLineChart data={DATA} {...PROPS} />)
    expect(h.textContent).not.toContain('自建仓起')
    expect(h.textContent).not.toContain('当前区间')
  })

  it('⭐⭐ 换数据 ⇒ 窗口**自动重置**为全区间（不许停在上一轮的区间 ✗）', () => {
    const h = mount(<ZoomableLineChart data={DATA} {...PROPS} />)
    // 换一份**更短**的数据：若沿用旧窗口（0~9）就会越界 ✗
    act(() => root!.render(<ZoomableLineChart data={DATA.slice(0, 4)} {...PROPS} />))
    expect(h.textContent).toContain('显示第 1~4 点 / 共 4 点')
  })

  it('`onToggle` 传了也不会在初始化时被调用（点图例才该触发 ✓）', () => {
    const onToggle = vi.fn()
    mount(<ZoomableLineChart data={DATA} {...PROPS} onToggle={onToggle} />)
    expect(onToggle).not.toHaveBeenCalled()
  })
})
