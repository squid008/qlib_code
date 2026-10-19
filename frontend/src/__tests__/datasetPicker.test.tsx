/**
 * `DatasetPicker` 交互测试（★ v2.9.72，2026-10-19 ✓ —— 纯 props 且此前零测试 ✗）。
 *
 * ## 它守的是几件"用户明确提过"的交互（组件注释里写着日期 ✓）
 *
 * 1. **收起时只显示短标签** ✓（"tushare 口径"），目录名/天数/只数全在展开后 ✓ ——
 *    详情漏出来 ⇒ 那一行高度变、把下面控件顶走 ✗（用户 2026-10-10 反馈过 ✓）；
 * 2. **切换中要就地换文案** ✓（"切换中…"）—— 后端要 0.1~0.7s ✓，没有反馈就是"点了没反应"✗；
 * 3. **只有一个数据集时不能展开** ✓ —— 能展开却只有一个选项 ⇒ 用户以为还有别的 ✓；
 * 4. **点当前项不触发切换** ✓ —— 否则会白白重注册 qlib（0.1~0.7s 卡顿 ✓）；
 * 5. **Esc / 点外面关闭** ✓（手写弹层，原生 select 才自带 ✓）。
 */
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import DatasetPicker from '../components/DatasetPicker'
import type { DatasetsStatus } from '../types'

type Ds = NonNullable<DatasetsStatus['datasets']>[number]

function ds(over: Partial<Ds> = {}): Ds {
  return {
    name: 'cn_data',
    label: 'tushare 口径',
    calendar_days: 3000,
    calendar_first: '2010-01-04',
    calendar_last: '2024-06-28',
    codes: 5000,
    fields: 120,
    has_fin: true,
    has_mf: true,
    has_chip: true,
    ...over,
  } as unknown as Ds
}

function status(list: Ds[], active: string | null): DatasetsStatus {
  return { active, datasets: list } as unknown as DatasetsStatus
}

async function mount(props: React.ComponentProps<typeof DatasetPicker>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(<DatasetPicker {...props} />)
  })
  return host
}

function trigger(el: HTMLElement): HTMLButtonElement {
  return el.querySelector('button') as HTMLButtonElement
}

const TWO = [ds(), ds({ name: 'cn_data3', label: '自建口径' })]

describe('DatasetPicker', () => {
  it('收起时只显示短标签，不泄露目录名与详情', async () => {
    const el = await mount({ datasets: status(TWO, 'cn_data'), onSwitch: () => {} })
    expect(trigger(el).textContent).toContain('tushare 口径')
    expect(el.textContent).not.toContain('cn_data')       // 目录名只在展开后 ✓
    expect(el.textContent).not.toContain('3000 天')
  })

  it('两种"没有数据"的文案要区分开（加载中 / 无可用数据集）', async () => {
    const loading = await mount({ datasets: null, onSwitch: () => {} })
    expect(trigger(loading).textContent).toContain('加载中…')

    const empty = await mount({ datasets: status([], null), onSwitch: () => {} })
    expect(trigger(empty).textContent).toContain('（无可用数据集）')
  })

  it('⭐ 只有一个数据集时点不开（title 也说明了原因）', async () => {
    const el = await mount({ datasets: status([ds()], 'cn_data'), onSwitch: () => {} })
    expect(trigger(el).title).toContain('只有一个数据集')
    await act(async () => {
      trigger(el).click()
    })
    expect(el.textContent).not.toContain('3000 天')       // 没展开 ✓
  })

  it('展开后显示完整信息：目录名 / 天数 / 只数 / 字段数 + 「当前」标记', async () => {
    const el = await mount({ datasets: status(TWO, 'cn_data'), onSwitch: () => {} })
    await act(async () => {
      trigger(el).click()
    })
    expect(el.textContent).toContain('cn_data')
    expect(el.textContent).toContain('3000 天')
    expect(el.textContent).toContain('5000 只')
    expect(el.textContent).toContain('120 字段')
    expect(el.textContent).toContain('当前')
  })

  it('⭐ 切换：点另一项 ⇒ onSwitch(name)，且按钮**立刻**显示「切换中…」直到 Promise 结束', async () => {
    let done: () => void = () => {}
    const onSwitch = vi.fn(
      () => new Promise<void>((res) => { done = () => res() }),
    )
    const el = await mount({ datasets: status(TWO, 'cn_data'), onSwitch })

    await act(async () => {
      trigger(el).click()
    })
    const item = (Array.from(el.querySelectorAll('button')) as HTMLButtonElement[]).find(
      (b) => (b.textContent || '').includes('自建口径'),
    )
    expect(item).toBeTruthy()
    await act(async () => {
      item!.click()
    })

    expect(onSwitch).toHaveBeenCalledWith('cn_data3')
    expect(trigger(el).textContent).toContain('切换中…')   // 后端还没回来 ✓

    await act(async () => {
      done()
    })
    expect(trigger(el).textContent).toContain('tushare 口径')  // 结束后回到当前口径标签 ✓
  })

  it('⭐ 点「当前」那一项不触发切换（否则白重注册 qlib ✓）', async () => {
    const onSwitch = vi.fn()
    const el = await mount({ datasets: status(TWO, 'cn_data'), onSwitch })
    await act(async () => {
      trigger(el).click()
    })
    const cur = (Array.from(el.querySelectorAll('button')) as HTMLButtonElement[]).find(
      (b) => (b.textContent || '').includes('tushare 口径') && (b.textContent || '').includes('当前'),
    )
    expect(cur).toBeTruthy()
    await act(async () => {
      cur!.click()
    })
    expect(onSwitch).not.toHaveBeenCalled()
  })

  it('Esc 关闭弹层', async () => {
    const el = await mount({ datasets: status(TWO, 'cn_data'), onSwitch: () => {} })
    await act(async () => {
      trigger(el).click()
    })
    expect(el.textContent).toContain('3000 天')
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    })
    expect(el.textContent).not.toContain('3000 天')
  })
})
