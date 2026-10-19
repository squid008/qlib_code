/**
 * `FormulaHandbookModal` 交互测试（★ v2.9.74，2026-10-19 ✓ —— 纯 props，此前零测试 ✗）。
 *
 * ## 最关键的一条：**插入的字符串**
 *
 * 双击插入时，**函数要带 `(`** ✓（`BARSCOUNT(` ✓ —— 用户接着填参数 ✓），
 * **字段不带** ✓（`CLOSE` ✓）。写反的后果很具体 ✗：
 *  · 函数不带括号 ⇒ 用户还得自己补，且很容易补到名字中间 ✗；
 *  · 字段带括号 ⇒ 公式直接**语法错** ✗（`CLOSE(` 后面接运算会解析失败 ✓）。
 * 而弹窗插入完会**自动关闭** ✓ —— 关得太早、用户还没看清插入成什么样 ✗。
 *
 * 其余守：`open=false` 不渲染 ✓；单击看说明 ✓（双击才插入 ✓ —— 两者只差一次点击，
 * 最容易"单击就给插进去"✗）；Esc / 点遮罩关闭 ✓；搜索与"无匹配" ✓。
 *
 * ⚠ 用**真实手册数据** ✓（`filterHandbook('')` 里取第一个函数与第一个字段 ✓）——
 *   手写假数据会和真实 catalog 漂移 ✗。
 */
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import FormulaHandbookModal from '../components/FormulaHandbookModal'
import { filterHandbook } from '../formulaHandbook'

const ALL = filterHandbook('')
const FUNC = ALL.find((e) => e.kind === 'func')!
const FIELD = ALL.find((e) => e.kind === 'field')!

async function mount(over: Partial<React.ComponentProps<typeof FormulaHandbookModal>> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const props = { open: true, onClose: () => {}, onInsert: () => {}, ...over }
  await act(async () => {
    root.render(<FormulaHandbookModal {...props} />)
  })
  return host
}

function rowOf(el: HTMLElement, name: string): HTMLTableRowElement | undefined {
  return (Array.from(el.querySelectorAll('tbody tr')) as HTMLTableRowElement[]).find(
    (tr) => (tr.textContent || '').includes(name),
  )
}

describe('FormulaHandbookModal', () => {
  it('open=false 时什么都不渲染', async () => {
    const el = await mount({ open: false })
    expect(el.textContent).toBe('')
  })

  it('打开即显示标题/提示/条数，并列出条目', async () => {
    const el = await mount()
    expect(el.textContent).toContain('公式函数手册')
    expect(el.textContent).toContain('双击函数名插入')
    expect(el.textContent).toContain(`${ALL.length} 条`)
    expect(rowOf(el, FUNC.name)).toBeTruthy()
  })

  it('⭐ 单击只"看说明"，**不插入**（双击才插 ✓）', async () => {
    const onInsert = vi.fn()
    const el = await mount({ onInsert })
    const tr = rowOf(el, FUNC.name)!
    await act(async () => {
      tr.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    expect(onInsert).not.toHaveBeenCalled()
    // 右侧详情出现该条目（名称 / 中文 / 类型 / 说明 ✓）
    expect(el.textContent).toContain(`[函数]`)
    expect(el.textContent).toContain(FUNC.abbr)
  })

  it('⭐⭐ 双击**函数** ⇒ onInsert(`名称(`)，并关闭弹窗', async () => {
    const onInsert = vi.fn()
    const onClose = vi.fn()
    const el = await mount({ onInsert, onClose })
    await act(async () => {
      rowOf(el, FUNC.name)!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    expect(onInsert).toHaveBeenCalledWith(`${FUNC.name}(`)   // 带左括号 ✓
    expect(onClose).toHaveBeenCalled()
  })

  it('⭐⭐ 双击**字段** ⇒ onInsert(`名称`)，**不带括号**（带了就语法错 ✗）', async () => {
    const onInsert = vi.fn()
    const el = await mount({ onInsert })
    await act(async () => {
      rowOf(el, FIELD.name)!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    })
    expect(onInsert).toHaveBeenCalledWith(FIELD.name)
  })

  it('搜索过滤并显示条数；搜不到给"无匹配"', async () => {
    const el = await mount()
    const input = el.querySelector('input') as HTMLInputElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input, 'zzzz-not-exist')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(el.textContent).toContain('无匹配')
    expect(el.textContent).toContain('0 条')
  })

  it('Esc 关闭；点遮罩关闭', async () => {
    const onClose = vi.fn()
    await mount({ onClose })
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    })
    expect(onClose).toHaveBeenCalledTimes(1)

    const onClose2 = vi.fn()
    const el2 = await mount({ onClose: onClose2 })
    const backdrop = el2.firstElementChild as HTMLElement
    await act(async () => {
      backdrop.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    expect(onClose2).toHaveBeenCalled()
  })
})
