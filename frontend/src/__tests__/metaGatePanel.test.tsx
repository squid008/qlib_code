// @vitest-environment jsdom
/**
 * `MetaGatePanel` 交互测试（★ v2.9.43 ✓ —— `App.tsx` 拆出的第三块 ✓）。
 *
 * 这块是"风控闸门"的入口 ✓，三条口径最容易在重构里丢掉 ✗：
 *
 * 1. **剔除比例夹在 `[0, 0.9]`，清空回到 0.25** ✓ —— 变 0 等于"开了风控但没剔除"✗
 *    （用户会以为生效了 ✓ 其实没效果 ✗）；
 * 2. **归因默认 `all`** ✓，三档文案里的**实测耗时**（"36 段约 +12~18 分钟"✓）不能删 ✗；
 * 3. **附加特征计数分母是"全部公式"** ✓，不是搜索过滤后的 ✓ —— 否则搜索时显示
 *    "已选 1 / 1" 会让人以为选满了 ✗。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import MetaGatePanel from '../components/MetaGatePanel'

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

function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const FORMULAS = [
  { id: 'f1', name: '涨停放量', expression: 'A>1' },
  { id: 'f2', name: '均线多头', expression: 'B>1' },
]

const base = {
  enabled: false,
  rejectRatio: null,
  attribution: null,
  extraFeatures: [] as string[],
  formulas: FORMULAS,
  onToggleEnabled: () => {},
  onRatio: () => {},
  onAttribution: () => {},
  onExtraAll: () => {},
  onToggleExtra: () => {},
}

function numberInputs(el: HTMLElement) {
  return Array.from(el.querySelectorAll('input[type=number]')) as HTMLInputElement[]
}

describe('MetaGatePanel（jsdom 交互）', () => {
  it('关闭时只渲染开关（比例/归因/附加特征都不显示）', () => {
    const el = renderInto(<MetaGatePanel {...base} />)
    expect(el.textContent).toContain('Meta-Gate 风控')
    expect(el.textContent).not.toContain('候选内剔除比例')
    expect(el.textContent).not.toContain('因子归因')
    expect(el.textContent).not.toContain('Gate 附加特征')
    expect(numberInputs(el)).toHaveLength(0)
  })

  it('打开开关 ⇒ onToggleEnabled(true)', () => {
    const onToggleEnabled = vi.fn()
    const el = renderInto(<MetaGatePanel {...base} onToggleEnabled={onToggleEnabled} />)
    const box = el.querySelector('input[type=checkbox]') as HTMLInputElement
    act(() => box.click())
    expect(onToggleEnabled).toHaveBeenCalledWith(true)
  })

  it('开启后：默认比例 0.25，归因三档齐全', () => {
    const el = renderInto(<MetaGatePanel {...base} enabled />)
    expect(numberInputs(el)[0].value).toBe('0.25')
    const opts = Array.from(el.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(opts).toEqual(['all', 'seg1', 'off'])
    expect(el.textContent).toContain('36 段约 +12~18 分钟')
  })

  it('⭐ 比例夹在 [0,0.9]：填 2 ⇒ 0.9；填 9 也 ⇒ 0.9；清空 ⇒ 0.25', () => {
    const onRatio = vi.fn()
    const el = renderInto(<MetaGatePanel {...base} enabled onRatio={onRatio} />)
    const ratio = numberInputs(el)[0]
    type(ratio, '2')
    expect(onRatio).toHaveBeenLastCalledWith(0.9)
    type(ratio, '')
    expect(onRatio).toHaveBeenLastCalledWith(0.25)
    expect(onRatio.mock.calls.every((c) => c[0] !== 0)).toBe(true)
  })

  it('归因切换 ⇒ onAttribution("seg1")', () => {
    const onAttribution = vi.fn()
    const el = renderInto(<MetaGatePanel {...base} enabled onAttribution={onAttribution} />)
    const sel = el.querySelector('select') as HTMLSelectElement
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLSelectElement.prototype, 'value')!.set!
      setter.call(sel, 'seg1')
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(onAttribution).toHaveBeenCalledWith('seg1')
  })

  it('⭐ 附加特征：计数分母是**全部公式**，搜索只影响列表', () => {
    const el = renderInto(
      <MetaGatePanel {...base} enabled extraFeatures={['A>1']} />)
    expect(el.textContent).toContain('已选 1 / 2')

    const search = el.querySelector('input[type=text]') as HTMLInputElement
    type(search, '均线')
    const boxes = Array.from(el.querySelectorAll('input[type=checkbox]')) as HTMLInputElement[]
    // 第 0 个是总开关 ✓；剩下是过滤后的公式列表 ✓（只剩"均线多头"）
    expect(boxes.length).toBe(2)
    expect(el.textContent).toContain('均线多头')
    expect(el.textContent).not.toContain('涨停放量')
    expect(el.textContent).toContain('已选 1 / 2')   // 分母不跟着搜索变 ✓
  })

  it('勾选公式 ⇒ onToggleExtra(id)；全选/清空 ⇒ onExtraAll(true/false)', () => {
    const onToggleExtra = vi.fn()
    const onExtraAll = vi.fn()
    const el = renderInto(
      <MetaGatePanel {...base} enabled onToggleExtra={onToggleExtra} onExtraAll={onExtraAll} />)

    const boxes = Array.from(el.querySelectorAll('input[type=checkbox]')) as HTMLInputElement[]
    act(() => boxes[1].click())                       // 第 1 个 = 第一条公式 ✓
    expect(onToggleExtra).toHaveBeenCalledWith('f1')

    const btns = Array.from(el.querySelectorAll('button')) as HTMLButtonElement[]
    act(() => btns[0].click())
    expect(onExtraAll).toHaveBeenLastCalledWith(true)
    act(() => btns[1].click())
    expect(onExtraAll).toHaveBeenLastCalledWith(false)
  })

  it('没有可用公式时，附加特征块不渲染', () => {
    const el = renderInto(<MetaGatePanel {...base} enabled formulas={[]} />)
    expect(el.textContent).not.toContain('Gate 附加特征')
  })
})
