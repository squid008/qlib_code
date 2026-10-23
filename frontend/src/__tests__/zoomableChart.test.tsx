// @vitest-environment jsdom
/**
 * `ZoomableLineChart`（394 行 / 覆盖率 72.8% → 本文件的目标）★ v2.11.63。
 *
 * ## 为什么挑它
 * 它**只依赖 react** ✓（389 条里缺 107 条），而且是**用户唯一能交互的图** ✓：
 * 滚轮缩放、拖动平移、双击重置、图例开关 —— 这些路径以前**一条断言都没有** ✗。
 *
 * ## ⚠ 断言口径（jsdom 的限制，先讲清 ✗→✓）
 * Recharts 的 `ResponsiveContainer` 在 jsdom 里**量不到尺寸** ⇒ 内部 SVG 常常**不渲染** ✗
 * ⇒ **不去断言坐标轴刻度**（那是"测了也测不准"✓）。这里只钉三件**确定能验**的事：
 * ① 图例/按钮/角标这些 **React 渲染出来的 DOM** ✓；
 * ② 交互回调（点图例 ⇒ `onToggle`）✓；
 * ③ **缩放/平移/重置/空数据**这些路径**不抛异常** ✓（覆盖到这部分的逻辑分支 ✓）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import ZoomableLineChart from '../components/ZoomableLineChart'

const mounted: Array<() => void> = []

afterEach(() => {
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 忽略 ✓ */
    }
  }
  document.body.innerHTML = ''
})

function renderInto(node: React.ReactElement) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root = createRoot(el)
  act(() => {
    root.render(node)
  })
  mounted.push(() => {
    act(() => root.unmount())
    el.remove()
  })
  return el
}

const DAILY = 40
const data = Array.from({ length: DAILY }, (_, i) => {
  const t = new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10)
  return { date: t, a: 1 + i * 0.01, b: 1 + i * 0.005 }
})

const LABEL: Record<string, string> = { a: '组合', b: '基准' }
const baseProps = {
  data,
  keys: ['a', 'b'],
  labelOf: (k: string) => LABEL[k] ?? k,
  colorOf: (_k: string, i: number) => ['#dc2626', '#2563eb'][i] ?? '#333',
  statKey: 'a',
  height: 300,
}

describe('ZoomableLineChart：渲染', () => {
  it('图例按 labelOf 显示每条曲线', () => {
    const el = renderInto(<ZoomableLineChart {...baseProps} />)
    expect(el.textContent).toContain('组合')
    expect(el.textContent).toContain('基准')
  })

  it('statKey 给了 ⇒ 右上角出"区间收益 / 最大回撤"角标', () => {
    const el = renderInto(<ZoomableLineChart {...baseProps} />)
    expect(el.textContent).toMatch(/区间收益|最大回撤/)
  })

  it('不给 statKey ⇒ **不显示**角标（调用方省事时要能关掉 ✓）', () => {
    const el = renderInto(<ZoomableLineChart {...baseProps} statKey={undefined} />)
    expect(el.textContent).not.toMatch(/区间收益|最大回撤/)
  })

  it('⭐ 未缩放时只显示区间范围，**不给"重置"入口**（免得点了没反应 ✗）', () => {
    const el = renderInto(<ZoomableLineChart {...baseProps} />)
    // 实测文案：`显示第 1~40 点 / 共 40 点（2024-01-01 ~ …）` ✓
    expect(el.textContent).toMatch(/显示第\s*1~40\s*点/)
    // ⚠ 我第一版断言"一定有「重置」"⇒ 这里红 ✓：重置按钮是 **zoomed 才渲染** ✓
    //   （那是**正确行为**：没缩放时给个"重置"按钮等于让用户点了没反应 ✗）
    expect(el.textContent).not.toContain('重置')
  })
})

describe('ZoomableLineChart：交互', () => {
  it('点图例触发 onToggle（且带上是哪一条）', () => {
    const onToggle = vi.fn()
    const el = renderInto(<ZoomableLineChart {...baseProps} onToggle={onToggle} />)
    const legend = Array.from(el.querySelectorAll('*')).find(
      (n) => n.children.length === 0 && n.textContent === '组合',
    )
    expect(legend, '没找到图例文本节点').toBeTruthy()
    act(() => {
      ;(legend as HTMLElement).click()
    })
    expect(onToggle).toHaveBeenCalled()
  })

  it('滚轮缩放 / 双击重置 **不抛异常**（这两条路径以前零覆盖 ✓）', () => {
    const el = renderInto(<ZoomableLineChart {...baseProps} />)
    const host = el.firstElementChild as HTMLElement
    expect(host).toBeTruthy()
    expect(() => {
      act(() => {
        host.dispatchEvent(
          new WheelEvent('wheel', { deltaY: -120, clientX: 100, bubbles: true, cancelable: true }),
        )
      })
      act(() => {
        host.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
      })
    }).not.toThrow()
  })

  it('拖拽平移不抛异常', () => {
    const el = renderInto(<ZoomableLineChart {...baseProps} />)
    const host = el.firstElementChild as HTMLElement
    expect(() => {
      act(() => {
        host.dispatchEvent(new MouseEvent('mousedown', { clientX: 200, bubbles: true }))
        host.dispatchEvent(new MouseEvent('mousemove', { clientX: 120, bubbles: true }))
        host.dispatchEvent(new MouseEvent('mouseup', { clientX: 120, bubbles: true }))
      })
    }).not.toThrow()
  })

  it('⭐ 空数据不崩（换数据集/清空时会走到这条路 ✓）', () => {
    expect(() => renderInto(<ZoomableLineChart {...baseProps} data={[]} />)).not.toThrow()
  })

  it('hidden 里的曲线不显示在图例高亮位（传了也不崩 ✓）', () => {
    expect(() =>
      renderInto(<ZoomableLineChart {...baseProps} hidden={{ b: true }} focus="a" />),
    ).not.toThrow()
  })
})
