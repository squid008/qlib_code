// @vitest-environment jsdom
/**
 * `ExecutionLimits` 交互测试（★ v2.9.46 ✓ —— `App.tsx` 第六刀 ✓）。
 *
 * 三条口径，每条都能把**回测结果改掉而页面看不出** ✗：
 *
 * 1. **「空」必须是 `null`** ✓：成交量限制 `0` = 一手都不能成交 ✗；每手股数 `0` = 除零 ✗；
 * 2. **涨跌停是"布尔外观、数值内核"** ✗：状态存 `limit_threshold`（开启 = **0.1** ✓ / 关 = `null` ✓），
 *    组件对外只发**布尔** ✓，`0.1` 只在 `LIMIT_THRESHOLD_ON` 定义一次 ✓
 *    （散落各处迟早写成 0.05/0.2 ✗ —— 那会改变"能不能成交"✗）；
 * 3. **`title` 里的涨跌幅规则是用户唯一解释** ✓（主板 10% / 创业科创 20% / 北交 30% ✓）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import ExecutionLimits, { LIMIT_THRESHOLD_ON } from '../components/ExecutionLimits'

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

function nums(el: HTMLElement): HTMLInputElement[] {
  return Array.from(el.querySelectorAll('input[type=number]')) as HTMLInputElement[]
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
  volumeThreshold: null as number | null,
  limitEnabled: false,
  tradeUnit: null as number | null,
  onVolumeThreshold: () => {},
  onLimitEnabled: () => {},
  onTradeUnit: () => {},
}

describe('ExecutionLimits（jsdom 交互）', () => {
  it('三个字段 + 标签齐全，涨跌停是勾选框', () => {
    const el = renderInto(<ExecutionLimits {...base} />)
    for (const t of ['成交量限制(比例)', '涨跌停限制', '封板不可交易', '每手股数']) {
      expect(el.textContent, `缺少「${t}」✗`).toContain(t)
    }
    expect(nums(el)).toHaveLength(2)
    expect(el.querySelectorAll('input[type=checkbox]')).toHaveLength(1)
  })

  it('成交量的 step 是 0.05；每手股数未设 step（属性为 `\'\'` ✓）', () => {
    const el = renderInto(<ExecutionLimits {...base} />)
    const [vol, unit] = nums(el)
    expect(vol.step).toBe('0.05')
    // ⚠ 未设置 step 时 `input.step` 是**空串** ✗（不是 `'1'` ✗ —— 我第一版就这么写错的 ✓）
    expect(unit.hasAttribute('step')).toBe(false)
    expect(unit.step).toBe('')
  })

  it('⭐ 成交量：清空 ⇒ null（不是 0 ✗）；填 0.25 ⇒ 0.25', () => {
    const onVolumeThreshold = vi.fn()
    const el = renderInto(
      <ExecutionLimits {...base} volumeThreshold={0.35} onVolumeThreshold={onVolumeThreshold} />)
    const vol = nums(el)[0]
    expect(vol.value).toBe('0.35')                    // 回填 ✓
    type(vol, '')
    expect(onVolumeThreshold).toHaveBeenLastCalledWith(null)
    // ⚠ 不能用 `calls.at(-1)` ✗ —— 本项目 tsconfig 的 lib 是 ES2020 ✓（实测 TS2550 ✓）
    expect(onVolumeThreshold).not.toHaveBeenCalledWith(0)
    type(vol, '0.25')
    expect(onVolumeThreshold).toHaveBeenLastCalledWith(0.25)
  })

  it('⭐ 每手股数：清空 ⇒ null；填 100 ⇒ 100', () => {
    const onTradeUnit = vi.fn()
    const el = renderInto(<ExecutionLimits {...base} tradeUnit={200} onTradeUnit={onTradeUnit} />)
    const unit = nums(el)[1]
    expect(unit.value).toBe('200')
    type(unit, '')
    expect(onTradeUnit).toHaveBeenLastCalledWith(null)
    type(unit, '100')
    expect(onTradeUnit).toHaveBeenLastCalledWith(100)
  })

  it('⭐ 涨跌停：checked = limitEnabled；点击只发布尔（0.1 由常量负责 ✓）', () => {
    const onLimitEnabled = vi.fn()
    const el = renderInto(<ExecutionLimits {...base} onLimitEnabled={onLimitEnabled} />)
    const box = el.querySelector('input[type=checkbox]') as HTMLInputElement
    expect(box.checked).toBe(false)
    act(() => box.click())
    expect(onLimitEnabled).toHaveBeenLastCalledWith(true)
    expect(LIMIT_THRESHOLD_ON).toBe(0.1)      // 单点定义 ✓

    const el2 = renderInto(<ExecutionLimits {...base} limitEnabled onLimitEnabled={onLimitEnabled} />)
    const box2 = el2.querySelector('input[type=checkbox]') as HTMLInputElement
    expect(box2.checked).toBe(true)
    act(() => box2.click())
    expect(onLimitEnabled).toHaveBeenLastCalledWith(false)
  })

  it('涨跌停的 title 保留涨跌幅规则（用户唯一解释 ✓）', () => {
    const el = renderInto(<ExecutionLimits {...base} />)
    const title = el.querySelector('label[title]')?.getAttribute('title') || ''
    expect(title).toContain('主板 10%')
    expect(title).toContain('创业/科创 20%')
    expect(title).toContain('北交 30%')
    expect(title).toContain('理想成交')
  })
})
