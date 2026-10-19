/**
 * `DateInput` 测试（★ v2.9.76，2026-10-19 ✓ —— 纯 props，此前零测试 ✗）。
 *
 * ## 为什么它最值得测（比图表强得多 ✓）
 *
 * 日期是**每个回测的输入** ✓，而它的校验里埋着一个真实的线上修复 ✓（注释写着 v1.18.24）：
 * **只卡 `D <= 31` 会让 `2026-06-31` 提交给后端** ⇒ pandas 抛
 * `day is out of range for month` ✗ —— 用户看到的是一句看不懂的后端异常 ✓。
 *
 * 所以这里先钉**导出的纯函数 `isValidDateStr`** ✓（闰年 / 大小月 / 年份范围 ✓），
 * 再钉组件三条交互契约 ✓：
 *  · 日超出当月 ⇒ **钳到月末并回写输入框** ✓（不是静默丢弃 ✗）；
 *  · 失焦时**月/日单数字补零**（`1 → 01` ✓），但**年份不补**（避免 `0202` ✗）；
 *  · 三段填满 ⇒ `onChange('YYYY-MM-DD')` ＋ `onComplete()` ✓（父组件靠它跳到下一个框 ✓）。
 */
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import DateInput, { isValidDateStr } from '../components/DateInput'

describe('isValidDateStr（纯函数）', () => {
  it('合法日期', () => {
    expect(isValidDateStr('2024-01-02')).toBe(true)
    expect(isValidDateStr('2024-02-29')).toBe(true)   // 闰年 ✓
    expect(isValidDateStr('2024-12-31')).toBe(true)
  })

  it('⭐ 不存在的日子必须判否（v1.18.24：只卡 D<=31 会让后端抛 pandas 异常 ✗）', () => {
    expect(isValidDateStr('2026-06-31')).toBe(false)  // 6 月只有 30 天 ✓
    expect(isValidDateStr('2023-02-29')).toBe(false)  // 平年 ✓
    expect(isValidDateStr('2024-04-31')).toBe(false)
    expect(isValidDateStr('2024-02-30')).toBe(false)
  })

  it('月/日/年份范围与格式', () => {
    expect(isValidDateStr('2024-13-01')).toBe(false)  // 月 13 ✗
    expect(isValidDateStr('2024-00-01')).toBe(false)
    expect(isValidDateStr('2024-01-00')).toBe(false)
    expect(isValidDateStr('1899-01-01')).toBe(false)  // 年份下限 ✓
    expect(isValidDateStr('2101-01-01')).toBe(false)  // 年份上限 ✓
    expect(isValidDateStr('2024-1-1')).toBe(false)    // 必须补零 ✓
    expect(isValidDateStr('')).toBe(false)
    expect(isValidDateStr('abc')).toBe(false)
  })
})

function segs(el: HTMLElement): HTMLInputElement[] {
  return Array.from(el.querySelectorAll('input')) as HTMLInputElement[]
}

function type(el: HTMLElement, i: number, v: string) {
  const input = segs(el)[i]
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(input, v)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

async function mount(value: string, over: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const props = { value, onChange: () => {}, ...over } as React.ComponentProps<typeof DateInput>
  await act(async () => {
    root.render(<DateInput {...props} />)
  })
  return host
}

describe('DateInput（组件）', () => {
  it('三段各有占位符，且回显传入的日期', async () => {
    const el = await mount('2024-01-02')
    expect(segs(el)).toHaveLength(3)
    expect(segs(el)[0].placeholder).toBe('YYYY')
    expect(segs(el)[1].placeholder).toBe('MM')
    expect(segs(el)[2].placeholder).toBe('DD')
    expect(segs(el).map((i) => i.value)).toEqual(['2024', '01', '02'])
  })

  it('⭐ 三段填满 ⇒ onChange(`YYYY-MM-DD`) 且触发 onComplete', async () => {
    const onChange = vi.fn()
    const onComplete = vi.fn()
    const el = await mount('', { onChange, onComplete })
    await act(async () => {
      type(el, 0, '2024')
    })
    await act(async () => {
      type(el, 1, '03')
    })
    await act(async () => {
      type(el, 2, '15')
    })
    expect(onChange).toHaveBeenLastCalledWith('2024-03-15')
    expect(onComplete).toHaveBeenCalled()
  })

  it('⭐ 日超出当月 ⇒ 钳到月末并回写输入框（不是静默丢弃 ✗）', async () => {
    const onChange = vi.fn()
    const el = await mount('2024-02-01', { onChange })
    await act(async () => {
      type(el, 2, '31')
    })
    expect(segs(el)[2].value).toBe('29')          // 闰年 2 月 ⇒ 29 ✓
    expect(onChange).toHaveBeenLastCalledWith('2024-02-29')
  })

  it('失焦：月/日单数字补零，但年份不补（避免 0202 ✗）', async () => {
    const onChange = vi.fn()
    const el = await mount('2024-01-02', { onChange })
    await act(async () => {
      type(el, 1, '3')
    })
    await act(async () => {
      segs(el)[1].dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    expect(segs(el)[1].value).toBe('03')
    expect(onChange).toHaveBeenLastCalledWith('2024-03-02')

    // 年份只输 2 位 ⇒ 不补零、也不提交 ✓
    const el2 = await mount('', { onChange: vi.fn() })
    await act(async () => {
      type(el2, 0, '20')
    })
    await act(async () => {
      segs(el2)[0].dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    })
    expect(segs(el2)[0].value).toBe('20')
  })

  it('日历：点按钮弹出该年该月，点某天 ⇒ onChange(该日)', async () => {
    const onChange = vi.fn()
    const el = await mount('2024-03-15', { onChange })
    const calBtn = el.querySelector('button[title="选择日期"]') as HTMLButtonElement
    await act(async () => {
      calBtn.click()
    })
    expect(el.textContent).toContain('2024 年 3 月')
    const day7 = (Array.from(el.querySelectorAll('button')) as HTMLButtonElement[]).find(
      (b) => (b.textContent || '').trim() === '07',
    )
    expect(day7).toBeTruthy()
    await act(async () => {
      day7!.click()
    })
    expect(onChange).toHaveBeenLastCalledWith('2024-03-07')
  })
})
