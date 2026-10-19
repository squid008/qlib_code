// @vitest-environment jsdom
/**
 * `CostSettings` 交互测试（★ v2.9.45 ✓ —— `App.tsx` 第五刀 ✓）。
 *
 * ## 这块唯一但致命的考点：**百分数 ↔ 比率** ✗
 *
 * 状态里存的是**比率**（0.0013 ✓），界面显示**百分数**（0.13 ✓）。
 * 换算写反 ⇒ 手续费差 **100 倍** ✗ —— 页面看着正常 ✓、净值曲线"变得很好"或"变得很差" ✓
 * （很容易被误读成策略变化 ✗）。所以这里把两个方向**分别钉死** ✓：
 * · 显示：比率 0.0013 ⇒ 输入框 `0.13` ✓；
 * · 写回：输入 `0.3` ⇒ 回调收到 **0.003** ✓（不是 0.3 ✗、不是 30 ✗）。
 *
 * 另：最低手续费是**金额（元）**✓，**不换算** ✓。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import CostSettings from '../components/CostSettings'

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

function inputs(el: HTMLElement): HTMLInputElement[] {
  return Array.from(el.querySelectorAll('input')) as HTMLInputElement[]
}

function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const base = {
  openCost: 0.0013,
  closeCost: 0.0013,
  impactCost: 0.002,
  minCost: 5,
  onCost: () => {},
  onMinCost: () => {},
}

describe('CostSettings（jsdom 交互）', () => {
  it('四个字段 + 标签齐全，且 step=0.01（百分数精度 ✓）', () => {
    const el = renderInto(<CostSettings {...base} />)
    for (const t of ['买入手续费(%)', '卖出手续费(%)', '滑点/冲击成本(%)', '最低手续费(元)']) {
      expect(el.textContent, `缺少字段「${t}」✗`).toContain(t)
    }
    const ins = inputs(el)
    expect(ins).toHaveLength(4)
    expect(ins.slice(0, 3).every((i) => i.step === '0.01')).toBe(true)
  })

  it('⭐ 显示方向：比率 0.0013 ⇒ 输入框显示 `0.13`（不是 0.0013 ✗）', () => {
    const el = renderInto(<CostSettings {...base} />)
    const [buy, sell, impact] = inputs(el)
    expect(Number(buy.value)).toBeCloseTo(0.13, 10)
    expect(Number(sell.value)).toBeCloseTo(0.13, 10)
    expect(Number(impact.value)).toBeCloseTo(0.2, 10)
  })

  it('⭐ 写回方向：输入 `0.3` ⇒ 回调收到 **0.003**（差 100 倍就是这里 ✗）', () => {
    const onCost = vi.fn()
    const el = renderInto(<CostSettings {...base} onCost={onCost} />)
    type(inputs(el)[0], '0.3')
    expect(onCost).toHaveBeenCalledTimes(1)
    expect(onCost).toHaveBeenCalledWith('open_cost', 0.003)
    expect(onCost.mock.calls[0][1]).not.toBe(0.3)     // 不能漏除 100 ✗
    expect(onCost.mock.calls[0][1]).not.toBe(30)      // 不能多乘 100 ✗
  })

  it('三个百分数字段的 key 不串（滑点写回 impact_cost ✓）', () => {
    const onCost = vi.fn()
    const el = renderInto(<CostSettings {...base} onCost={onCost} />)
    type(inputs(el)[2], '1')
    expect(onCost).toHaveBeenLastCalledWith('impact_cost', 0.01)
  })

  it('最低手续费是**金额**：原样显示 5、写回 8（不乘除 100 ✓）', () => {
    const onMinCost = vi.fn()
    const el = renderInto(<CostSettings {...base} onMinCost={onMinCost} />)
    const min = inputs(el)[3]
    expect(min.value).toBe('5')
    type(min, '8')
    expect(onMinCost).toHaveBeenCalledWith(8)
  })
})
