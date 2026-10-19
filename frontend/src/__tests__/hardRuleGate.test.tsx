// @vitest-environment jsdom
/**
 * `HardRuleGate` 交互测试（★ v2.9.42 ✓ —— `App.tsx` 拆出的第二块的守门 ✓）。
 *
 * ## 这块最要命的一条口径：**留空必须传 `null`，不能传 `0`** ✗
 *
 * `0` 会被后端当成"市值必须为 **0 亿**"✓ ⇒ 一只票都选不出来 ✓（而且用户不会立刻察觉 ✗）。
 * 所以这里用**原生的空值路径**（`''`）断言 `null` ✓；同时断言"填了值就传 number" ✓
 * （`Number('12.5') === 12.5` ✓，不是字符串 `'12.5'` ✗）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import HardRuleGate from '../components/HardRuleGate'

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
  minMktcapBn: null,
  maxMktcapBn: null,
  minPrice: null,
  onChange: () => {},
}

describe('HardRuleGate（jsdom 交互）', () => {
  it('三个数值输入 + 标签 + 提示文案齐全', () => {
    const el = renderInto(<HardRuleGate {...base} />)
    expect(inputs(el)).toHaveLength(3)
    expect(el.textContent).toContain('硬规则闸门')
    expect(el.textContent).toContain('留空=不限')
    for (const t of ['市值下限(亿)', '市值上限(亿)', '股价下限(元)']) {
      expect(el.textContent, `缺少字段「${t}」✗`).toContain(t)
    }
    expect(inputs(el).every((i) => i.type === 'number')).toBe(true)
  })

  it('填值 ⇒ onChange(key, number)（不是字符串 ✗）', () => {
    const onChange = vi.fn()
    const el = renderInto(<HardRuleGate {...base} onChange={onChange} />)
    type(inputs(el)[0], '12.5')
    expect(onChange).toHaveBeenCalledWith('min_mktcap_bn', 12.5)
    expect(typeof onChange.mock.calls[0][1]).toBe('number')
  })

  it('⭐ 清空 ⇒ onChange(key, null)（**绝不是 0** ✗ —— 0 会一只票都选不出来）', () => {
    const onChange = vi.fn()
    const el = renderInto(
      <HardRuleGate {...base} minPrice={8.5} onChange={onChange} />)
    const px = inputs(el)[2]
    expect(px.value).toBe('8.5')                 // 回填 ✓
    type(px, '')
    expect(onChange).toHaveBeenCalledWith('min_price', null)
    expect(onChange.mock.calls[0][1]).not.toBe(0)
  })

  it('三个字段各自的 key 对得上（不能串到别的字段 ✗）', () => {
    const onChange = vi.fn()
    const el = renderInto(<HardRuleGate {...base} onChange={onChange} />)
    type(inputs(el)[1], '500')
    expect(onChange).toHaveBeenLastCalledWith('max_mktcap_bn', 500)
  })

  it('回填：三项都有值时分别显示在自己的输入框里', () => {
    const el = renderInto(
      <HardRuleGate minMktcapBn={20} maxMktcapBn={800} minPrice={3.5} onChange={() => {}} />)
    const [a, b, c] = inputs(el)
    expect(a.value).toBe('20')
    expect(b.value).toBe('800')
    expect(c.value).toBe('3.5')
  })
})
