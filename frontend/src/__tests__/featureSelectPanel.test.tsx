/**
 * `FeatureSelectPanel` 交互测试（★ v2.9.73，2026-10-19 ✓ —— 纯 props 且此前零测试 ✗）。
 *
 * ## 守什么
 *
 * 这个面板是"**数百个特征里挑子集**"的唯一入口 ✓，一旦口径错，回测结果会变而界面照常 ✓：
 * 1. **计数文案**：`已选 N / M 个特征` ✓ —— 并且 **`selected=false` 时要补「（全量）」** ✓
 *    （不补 ⇒ 用户以为"已选 0"= 没特征 ⇒ 不敢跑 ✗，而实际是全量 ✓）；
 * 2. **勾选态跟着 `customFeatures`** ✓（不是组件内部的临时 state ⚠）；
 * 3. **搜索只影响显示，不影响已选** ✓ —— 搜完再点某特征，传的仍是**它的名字** ✓；
 * 4. **无匹配时要给关键词** ✓（`无匹配特征（关键词：xxx）` ✓ —— 否则用户以为面板坏了 ✗）；
 * 5. **全选 / 清空** 分别传 `true` / `false` ✓。
 */
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import FeatureSelectPanel from '../components/FeatureSelectPanel'
import type { FactorCatalog } from '../types'

const CATALOG = {
  total: 3,
  groups: [
    { group: 'Alpha158', fields: [
      { name: 'ROC5', expression: '$close/Ref($close,5)-1', description: '5日动量' },
      { name: 'STD20', expression: 'Std($close,20)', description: '20日波动' },
    ] },
    { group: '自定义', fields: [
      { name: 'MY_SIGNAL', expression: 'A>B', description: '自建触发' },
    ] },
  ],
} as unknown as FactorCatalog

async function mount(over: Partial<React.ComponentProps<typeof FeatureSelectPanel>> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const props = {
    factorCatalog: CATALOG,
    customFeatures: [],
    selected: false,
    onToggle: () => {},
    onToggleAll: () => {},
    ...over,
  } as React.ComponentProps<typeof FeatureSelectPanel>
  await act(async () => {
    root.render(<FeatureSelectPanel {...props} />)
  })
  return host
}

function box(el: HTMLElement, name: string): HTMLInputElement | undefined {
  return (Array.from(el.querySelectorAll('label')) as HTMLLabelElement[])
    .filter((l) => (l.textContent || '').trim() === name)
    .map((l) => l.querySelector('input') as HTMLInputElement)[0]
}

function labelOf(el: HTMLElement, name: string): HTMLLabelElement | undefined {
  return (Array.from(el.querySelectorAll('label')) as HTMLLabelElement[]).find(
    (l) => (l.textContent || '').trim() === name,
  )
}

describe('FeatureSelectPanel', () => {
  it('⭐ 计数文案：`已选 N / M 个特征`，且"全量"与"有子集"要能区分', () => {
    return mount({ customFeatures: [], selected: false }).then((all) => {
      expect(all.textContent).toContain('已选 0 / 3 个特征')
      expect(all.textContent).toContain('（全量）')       // 没传子集 = 全量 ✓

      return mount({ customFeatures: ['ROC5'], selected: true }).then((some) => {
        expect(some.textContent).toContain('已选 1 / 3 个特征')
        expect(some.textContent).not.toContain('（全量）')
      })
    })
  })

  it('分组与特征都渲染，且 title 里带公式与说明（用户唯一的解释来源 ✓）', async () => {
    const el = await mount()
    expect(el.textContent).toContain('Alpha158')
    expect(el.textContent).toContain('自定义')
    expect(el.textContent).toContain('ROC5')
    const l = labelOf(el, 'ROC5')!
    expect(l.title).toContain('$close/Ref($close,5)-1')
    expect(l.title).toContain('5日动量')
  })

  it('勾选态来自 `customFeatures`（不是组件内部临时状态 ⚠）', async () => {
    const el = await mount({ customFeatures: ['ROC5'], selected: true })
    expect(box(el, 'ROC5')!.checked).toBe(true)
    expect(box(el, 'STD20')!.checked).toBe(false)
  })

  it('点特征 ⇒ onToggle(该特征名)', async () => {
    const onToggle = vi.fn()
    const el = await mount({ onToggle })
    await act(async () => {
      box(el, 'MY_SIGNAL')!.click()
    })
    expect(onToggle).toHaveBeenCalledWith('MY_SIGNAL')
  })

  it('⭐ 搜索只过滤显示，不影响"传哪个名字"；分组里全不匹配则整组隐藏', async () => {
    const onToggle = vi.fn()
    const el = await mount({ onToggle })
    const input = el.querySelector('input[type="text"]') as HTMLInputElement
    await act(async () => {
      // React 只监听原生 input 事件 ⇒ 必须走原生 setter ✓（见 modelParamsForm 的说明 ✓）
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, 'roc')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(el.textContent).toContain('ROC5')
    expect(el.textContent).not.toContain('STD20')
    expect(el.textContent).not.toContain('自定义')      // 该组无匹配 ⇒ 整组隐藏 ✓

    await act(async () => {
      box(el, 'ROC5')!.click()
    })
    expect(onToggle).toHaveBeenCalledWith('ROC5')       // 传的仍是真名 ✓
  })

  it('搜不到 ⇒ 给出关键词（否则用户以为面板坏了 ✗）', async () => {
    const el = await mount()
    const input = el.querySelector('input[type="text"]') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, 'zzz')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(el.textContent).toContain('无匹配特征')
    expect(el.textContent).toContain('zzz')
  })

  it('全选 / 清空 分别传 true / false', async () => {
    const onToggleAll = vi.fn()
    const el = await mount({ onToggleAll })
    const btn = (t: string) =>
      (Array.from(el.querySelectorAll('button')) as HTMLButtonElement[]).find(
        (b) => (b.textContent || '').trim() === t,
      )!
    await act(async () => {
      btn('全选').click()
    })
    expect(onToggleAll).toHaveBeenLastCalledWith(true)
    await act(async () => {
      btn('清空').click()
    })
    expect(onToggleAll).toHaveBeenLastCalledWith(false)
  })
})
