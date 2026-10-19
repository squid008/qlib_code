// @vitest-environment jsdom
/**
 * `TradingBasics` 交互测试（★ v2.9.44 ✓ —— `App.tsx` 第四刀 ✓）。
 *
 * ## 这块最不能错的是**档位集合** ✗
 *
 * 成交价基准有 **5 档** ✓、复权 **3 档** ✓。少一档 ⇒ 用户选不到原来的口径 ⇒
 * **回测结果会变** ✗ —— 而且页面看着完全正常 ✓（这正是"重构打坏数字"最典型的方式 ✗）。
 * 所以这里**逐档断言 `value` 与文案** ✓，而不是只数个数 ✓。
 *
 * 另外两条：复权**空值按 `none`** ✓（不是 `backward` ✗）；起始总资产走 `Number(...)` ✓
 * （**沿用既有语义** ✓ —— 清空会得 0 ✓，本次不改 ✗）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import TradingBasics, {
  DEAL_PRICE_OPTIONS,
  PRICE_ADJUST_OPTIONS,
} from '../components/TradingBasics'

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

function setNative(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLSelectElement
    ? window.HTMLSelectElement.prototype
    : window.HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}

const base = {
  capitalWan: 10000,
  dealPrice: 'close',
  priceAdjust: 'none',
  onCapitalWan: () => {},
  onDealPrice: () => {},
  onPriceAdjust: () => {},
}

describe('TradingBasics（jsdom 交互）', () => {
  it('三个字段 + 中文标签齐全', () => {
    const el = renderInto(<TradingBasics {...base} />)
    for (const t of ['起始总资产(万元)', '成交价基准', '复权方式']) {
      expect(el.textContent, `缺少字段「${t}」✗`).toContain(t)
    }
    expect(el.querySelectorAll('select')).toHaveLength(2)
    expect(el.querySelectorAll('input[type=number]')).toHaveLength(1)
  })

  it('⭐ 成交价基准 **5 档逐档核对**（少一档 = 口径变了 ✗）', () => {
    const el = renderInto(<TradingBasics {...base} />)
    const sel = el.querySelectorAll('select')[0] as HTMLSelectElement
    const got = Array.from(sel.querySelectorAll('option')).map((o) => [
      (o as HTMLOptionElement).value, o.textContent,
    ])
    expect(got).toEqual(DEAL_PRICE_OPTIONS.map((o) => [o.value, o.label]))
    expect(got.map((g) => g[0])).toEqual(['close', 'open', 'vwap', 'avg_co', 'avg_ohlc'])
  })

  it('⭐ 复权 **3 档逐档核对**，且空值按 `none` 显示（不是 backward ✗）', () => {
    const el = renderInto(<TradingBasics {...base} priceAdjust="" />)
    const sel = el.querySelectorAll('select')[1] as HTMLSelectElement
    expect(Array.from(sel.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value))
      .toEqual(PRICE_ADJUST_OPTIONS.map((o) => o.value))
    expect(sel.value).toBe('none')
  })

  it('起始总资产 ⇒ onCapitalWan(number)', () => {
    const onCapitalWan = vi.fn()
    const el = renderInto(<TradingBasics {...base} onCapitalWan={onCapitalWan} />)
    const inp = el.querySelector('input[type=number]') as HTMLInputElement
    expect(inp.value).toBe('10000')      // 回填 ✓
    setNative(inp, '5000')
    expect(onCapitalWan).toHaveBeenCalledWith(5000)
    expect(typeof onCapitalWan.mock.calls[0][0]).toBe('number')
  })

  it('切换成交价基准 / 复权方式 ⇒ 各自的回调与值', () => {
    const onDealPrice = vi.fn()
    const onPriceAdjust = vi.fn()
    const el = renderInto(
      <TradingBasics {...base} onDealPrice={onDealPrice} onPriceAdjust={onPriceAdjust} />)
    const [deal, adj] = Array.from(el.querySelectorAll('select')) as HTMLSelectElement[]
    setNative(deal, 'vwap')
    expect(onDealPrice).toHaveBeenCalledWith('vwap')
    setNative(adj, 'backward')
    expect(onPriceAdjust).toHaveBeenCalledWith('backward')
  })
})
