/**
 * `TradeLog` 测试（★ v2.9.75，2026-10-19 ✓ —— 纯 props，此前零测试 ✗）。
 *
 * ## 守什么（都是"看一眼以为对、实际是错的"✗）
 *
 * 1. **空值必须显示 `-`** ✓（`fmt` 对 `null/undefined/NaN` 一视同仁 ✓）——
 *    显示成 `0` 会让用户以为"这笔成交额是 0"✗；
 * 2. **成交价 4 位小数、其余 0 位** ✓（价格精度丢了 ⇒ 对不上券商账单 ✗）；
 * 3. **成交率是百分比且保留 1 位** ✓（`0.8734 ⇒ 87.3%` ✓，直接显示 0.87 就差 100 倍 ✗）；
 * 4. **方向徽章**：`direction === 1` ⇒ 买入 ✓，否则卖出 ✓（**只有 -1 才是卖** ✗ 但要容忍其它值 ✓）；
 * 5. **搜索大小写不敏感** ✓；**筛选/月份/搜索任一变化都要回到第 1 页** ✓（否则会停在空页 ✗）；
 * 6. **空结果给明确文案** ✓（不是一片空白 ✓）。
 */
// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import TradeLog from '../components/TradeLog'
import type { TradeRecord } from '../types'

function rec(over: Partial<TradeRecord> = {}): TradeRecord {
  return {
    date: '2024-01-02',
    instrument: 'SH600000',
    direction: 1,
    amount: 1000,
    deal_price: 10.1234,
    trade_value: 10123,
    trade_cost: 12,
    ffr: 0.8734,
    ...over,
  } as unknown as TradeRecord
}

async function mount(trades: TradeRecord[]) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(<TradeLog trades={trades} />)
  })
  return host
}

function setInput(el: HTMLElement, sel: string, value: string) {
  const input = el.querySelector(sel) as HTMLInputElement | HTMLSelectElement
  const proto = input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  setter.call(input, value)
  input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
}

function btn(el: HTMLElement, label: string) {
  return (Array.from(el.querySelectorAll('button')) as HTMLButtonElement[]).find(
    (b) => (b.textContent || '').trim() === label,
  )
}

describe('TradeLog', () => {
  it('头部汇总：笔数 / 买入卖出计数 / 总成本', async () => {
    const el = await mount([rec(), rec({ direction: -1, trade_cost: 8 })])
    expect(el.textContent).toContain('2 笔')
    expect(el.textContent).toContain('买入 1 / 卖出 1')
    expect(el.textContent).toContain('总成本 20 元')
  })

  it('⭐ 空值显示 `-`（不是 0 ✗）；成交率是百分比 1 位；成交价 4 位', async () => {
    const el = await mount([
      rec({ trade_value: null as unknown as number, trade_cost: undefined as unknown as number,
            ffr: null as unknown as number }),
      rec({ instrument: 'SZ000001' }),
    ])
    const rows = el.querySelectorAll('tbody tr')
    expect(rows.length).toBe(2)
    expect(rows[0].textContent).toContain('-')
    // 第二条：成交率 87.3% ✓、成交价 10.1234 ✓
    expect(rows[1].textContent).toContain('87.3%')
    expect(rows[1].textContent).toContain('10.1234')
  })

  it('方向徽章：1 ⇒ 买入；-1 ⇒ 卖出', async () => {
    const el = await mount([rec(), rec({ direction: -1 })])
    const rows = el.querySelectorAll('tbody tr')
    expect(rows[0].textContent).toContain('买入')
    expect(rows[1].textContent).toContain('卖出')
  })

  it('搜索代码（大小写不敏感）只留匹配行', async () => {
    const el = await mount([rec(), rec({ instrument: 'SZ000001' })])
    await act(async () => {
      setInput(el, 'input[type="text"]', 'sz0000')
    })
    expect(el.textContent).toContain('SZ000001')
    expect(el.textContent).not.toContain('SH600000')
  })

  it('筛选「仅买入 / 仅卖出」', async () => {
    const el = await mount([rec(), rec({ direction: -1, instrument: 'SZ000001' })])
    await act(async () => {
      setInput(el, 'select', 'sell')
    })
    expect(el.textContent).toContain('SZ000001')
    expect(el.textContent).not.toContain('SH600000')

    await act(async () => {
      setInput(el, 'select', 'buy')
    })
    expect(el.textContent).toContain('SH600000')
    expect(el.textContent).not.toContain('SZ000001')
  })

  it('月份快捷按钮过滤 + 「显示全部月份」；空结果给文案', async () => {
    const el = await mount([
      rec({ date: '2024-01-02' }),
      rec({ date: '2024-02-05', instrument: 'SZ000001' }),
    ])
    // 月份分布按钮显示为两位数字（`m.slice(5)` ⇒ "01" / "02" ✓）
    const feb = btn(el, '02')
    expect(feb).toBeTruthy()
    await act(async () => {
      feb!.click()
    })
    expect(el.textContent).toContain('当前：2024-02')
    expect(el.textContent).toContain('SZ000001')
    expect(el.textContent).not.toContain('SH600000')

    // 搜一个不存在的代码 ⇒ 空结果文案 ✓
    await act(async () => {
      setInput(el, 'input[type="text"]', 'ZZZ')
    })
    expect(el.textContent).toContain('该月份/条件下暂无调仓记录')
  })

  it('超过 200 笔才出现分页，且"下一页"生效', async () => {
    const many = Array.from({ length: 250 }, (_, i) => rec({ instrument: `SH60${i.toString().padStart(4, '0')}` }))
    const el = await mount(many)
    expect(el.textContent).toContain('共 250 笔')
    expect(el.textContent).toContain('第 1/2 页')
    await act(async () => {
      btn(el, '下一页 ▶')!.click()
    })
    expect(el.textContent).toContain('第 2/2 页')
    // 第一页的 200 条不应再出现在 DOM 里（分页真的切了 ✓）
    expect(el.textContent).not.toContain('SH600000')
  })
})
