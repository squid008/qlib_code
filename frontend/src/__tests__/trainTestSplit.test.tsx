// @vitest-environment jsdom
/**
 * `TrainTestSplit` 交互测试（★ v2.9.47 ✓ —— `App.tsx` 第七刀 ✓）。
 *
 * 这块守的是**"用户还能不能判断自己的配置有没有前视"** ✓：
 * 1. `single` 时**不显示**训练/测试窗口 ✓（否则用户会以为这些参数在一次性训练里也生效 ✗）；
 * 2. `custom` 时四个控件齐全 ✓，单位**天/周/月三档** ✓（少一档 ⇒ 存过的配置读不出来 ✗）；
 * 3. **两段"避免未来数据泄漏 / 每段重训门控"的说明必须在** ✓ —— 它们被删掉后，
 *    用户就再也无法确认滚动训练有没有用到未来数据 ✗（比排版问题严重得多 ✓）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import TrainTestSplit, { SPLIT_MODE_OPTIONS, UNIT_OPTIONS } from '../components/TrainTestSplit'

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

function pick(sel: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype, 'value')!.set!
  act(() => {
    setter.call(sel, value)
    sel.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

const base = {
  splitMode: 'single',
  trainWin: 250,
  trainUnit: 'day',
  testWin: 20,
  testUnit: 'day',
  onChange: () => {},
}

describe('TrainTestSplit（jsdom 交互）', () => {
  it('划分方式两档齐全', () => {
    const el = renderInto(<TrainTestSplit {...base} />)
    const opts = Array.from(el.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(opts).toEqual(SPLIT_MODE_OPTIONS.map((o) => o.value))
    expect(el.textContent).toContain('训练/测试划分')
  })

  it('⭐ single ⇒ 不显示训练/测试窗口（避免误导 ✗）', () => {
    const el = renderInto(<TrainTestSplit {...base} splitMode="single" />)
    expect(el.textContent).not.toContain('训练窗口')
    expect(el.textContent).not.toContain('测试窗口')
    expect(el.textContent).not.toContain('避免未来数据泄漏')
  })

  it('⭐ custom ⇒ 四个控件 + 两段说明都在', () => {
    const el = renderInto(<TrainTestSplit {...base} splitMode="custom" />)
    for (const t of ['训练窗口', '训练单位', '测试窗口', '测试单位']) {
      expect(el.textContent, `缺少「${t}」✗`).toContain(t)
    }
    expect(el.querySelectorAll('input[type=number]')).toHaveLength(2)
    expect(el.querySelectorAll('select')).toHaveLength(3)   // 划分方式 + 两个单位 ✓
    expect(el.textContent).toContain('避免未来数据泄漏')
    expect(el.textContent).toContain('每个测试段')
  })

  it('切换划分方式 ⇒ onChange("split_mode", "custom")', () => {
    const onChange = vi.fn()
    const el = renderInto(<TrainTestSplit {...base} onChange={onChange} />)
    pick(el.querySelector('select') as HTMLSelectElement, 'custom')
    expect(onChange).toHaveBeenCalledWith('split_mode', 'custom')
  })

  it('改训练/测试窗口 ⇒ 回调 number（不是字符串 ✗）', () => {
    const onChange = vi.fn()
    const el = renderInto(<TrainTestSplit {...base} splitMode="custom" onChange={onChange} />)
    const [train, test] = Array.from(el.querySelectorAll('input[type=number]')) as HTMLInputElement[]
    expect(train.value).toBe('250')          // 回填 ✓
    type(train, '300')
    expect(onChange).toHaveBeenLastCalledWith('train_win', 300)
    type(test, '5')
    expect(onChange).toHaveBeenLastCalledWith('test_win', 5)
  })

  it('单位三档（天/周/月），改单位 ⇒ 各自的 key', () => {
    const onChange = vi.fn()
    const el = renderInto(<TrainTestSplit {...base} splitMode="custom" onChange={onChange} />)
    const sels = Array.from(el.querySelectorAll('select')) as HTMLSelectElement[]
    const unitVals = Array.from(sels[1].querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(unitVals).toEqual(UNIT_OPTIONS.map((o) => o.value))
    pick(sels[1], 'week')
    expect(onChange).toHaveBeenLastCalledWith('train_unit', 'week')
    pick(sels[2], 'month')
    expect(onChange).toHaveBeenLastCalledWith('test_unit', 'month')
  })
})
