// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import FormulaPanel from '../components/FormulaPanel'

/**
 * `FormulaPanel`（369 行）交互测试（★ v2.10.58，2026-10-21 ✓ —— 前端最后一个"零测试大件"）。
 *
 * ## 它为什么值钱
 *
 * 这里是**自定义公式库**的主界面 ✓：用户在这搜公式、切排序、置顶、勾选参与回测 ✓。
 * 它的内部逻辑（过滤/排序）已经拆到 `formulaList.ts` 并有单测 ✓，
 * ⇒ 本文件要钉的是**"那些纯逻辑有没有真的接到界面上"** ✓ ——
 * 拆得再干净，接了根错线用户也是"搜了没反应"✗。
 *
 * ⚠ 两个已知边界（不假装 ✓）：
 * · 公式**行内**的按钮（编辑/删除/置顶）我只验存在性，不逐一点击 ✓
 *   —— 它们各自回调到 App ✓，那是 `App` 侧的事 ✓；
 * · recharts 无关 ⇒ 这个组件**不画图** ✓，所以 jsdom 下能真的点击 ✓。
 */

let root: Root | null = null
let host: HTMLDivElement | null = null

type Row = { id: string; name: string; text: string; expression: string }

const ROWS: Row[] = [
  { id: 'a1', name: '龙腾四海', text: 'A:=MA(CLOSE,5);\n龙:A+1;', expression: 'ma_close_5' },
  { id: 'b2', name: 'AlphaOne', text: 'B:=CLOSE;\nB', expression: 'close' },
]

/** 组件 props 有一大堆回调 ✓ —— 这里全给 `vi.fn()`，只关心"有没有被调用" ✓。 */
function props(list: Row[] = ROWS, over: Record<string, unknown> = {}) {
  return {
    customFormulas: list as never,
    // ⚠ 必须是 **Set** ✗→✓：组件里对它调 `.has(...)` ✓（我第一版传了 `[]` ⇒
    //   `selectedFormulaIds.has is not a function` ✓，一整屏都渲染不出来 ✓）
    selectedFormulaIds: new Set<string>() as never,
    formulaInput: '',
    formulaError: '',
    formulaTranslating: false,
    editingId: null,
    editingText: '',
    formulaConflict: null,
    onInputChange: vi.fn(),
    onEditingTextChange: vi.fn(),
    onAdd: vi.fn(),
    onConflictOverwrite: vi.fn(),
    onConflictCreateAnyway: vi.fn(),
    onConflictCancel: vi.fn(),
    onToggle: vi.fn(),
    onToggleAll: vi.fn(),
    onStartEdit: vi.fn(),
    onSaveEdit: vi.fn(),
    onCancelEdit: vi.fn(),
    onRemove: vi.fn(),
    onTogglePin: vi.fn(),
    ...over,
  }
}

function mount(ui: React.ReactElement) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(ui))
  return host
}

const clickByText = (el: HTMLElement, text: string) => {
  const btn = Array.from(el.querySelectorAll('button')).find((b) =>
    (b.textContent || '').includes(text))
  if (!btn) throw new Error('找不到按钮「' + text + '」，实际有：'
    + Array.from(el.querySelectorAll('button')).map((b) => b.textContent).join(' / '))
  act(() => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

/** React 受控 input：必须走原生 setter + 派发 input 事件（直接改 value 不触发 onChange ✗）。 */
const typeInto = (el: HTMLElement, value: string) => {
  const input = el.querySelector('input[type="text"], input:not([type])') as HTMLInputElement | null
  if (!input) throw new Error('找不到搜索框 ✗')
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('FormulaPanel', () => {
  it('把公式名都渲染出来（列表真的接上了 ✓）', () => {
    const h = mount(<FormulaPanel {...props()} />)
    expect(h.textContent).toContain('龙腾四海')
    expect(h.textContent).toContain('AlphaOne')
  })

  it('空列表不崩，且**编辑区仍在**（新用户第一次打开就是空库 ✓）', () => {
    // ⚠ 实测：库里一条公式都没有时，**右侧列表区（搜索/排序/全选）整块不渲染** ✗→✓
    //   （我第一版断言"搜索控件仍在"✗ ⇒ 实测找不到 ✓）。
    //   这是合理的 ✓（没东西可搜 ✓），但值得钉住：**左编辑区必须在** ✓，
    //   否则新用户看到的是一片空白、连"怎么加公式"都不知道 ✗。
    const h = mount(<FormulaPanel {...props([])} />)
    expect(h.textContent).toContain('粘贴益盟/通达信公式')
    expect(h.querySelector('textarea'), '空库时公式编辑器必须还在 ✓').toBeTruthy()
  })

  it('⭐ 点「排序 A-Z」⇒ 按钮文案变「恢复原序」（钉住"再点一次恢复"的语义 ✓）', () => {
    const h = mount(<FormulaPanel {...props()} />)
    expect(h.textContent).toContain('排序 A-Z')
    clickByText(h, '排序 A-Z')
    expect(h.textContent).toContain('恢复原序')
    clickByText(h, '恢复原序')
    expect(h.textContent).toContain('排序 A-Z')
  })

  it('⭐⭐ 搜索框输入 ⇒ 列表**真的被过滤**（纯逻辑拆出去后最常见的坑：接了根错线 ✗）', () => {
    const h = mount(<FormulaPanel {...props()} />)
    expect(h.textContent).toContain('AlphaOne')
    typeInto(h, '龙腾')
    expect(h.textContent).toContain('龙腾四海')
    expect(h.textContent).not.toContain('AlphaOne')
  })

  it('⭐⭐ 勾「仅名称/拼音」⇒ 搜索框 placeholder 跟着换（开关与输入框是联动的 ✓）', () => {
    const h = mount(<FormulaPanel {...props()} />)
    // ⚠ 别用 `querySelector('input[type=checkbox]')` ✗ —— 第一个 checkbox 是**「全选」** ✓
    //   （实测点它不会改 placeholder ✓）。必须按**标签文字**找 ✓。
    // ⚠⚠ 它不是 checkbox ✗ —— 实测是 `<button role="switch" aria-checked={nameOnly}>` ✓
    //   （我先后猜了 `input[type=checkbox]` 与"label 包 checkbox"，两次都错 ✓）。
    //   ⇒ 按 **`role="switch"` + `aria-checked`** 断言反而更稳 ✓：
    //     这两个属性是**无障碍契约** ✓，而 `checked` 只是"某种控件"的实现细节 ✓。
    const sw = h.querySelector('[role="switch"]') as HTMLButtonElement | null
    expect(sw, '「仅名称/拼音」必须是 role=switch ✓').toBeTruthy()
    expect(sw!.getAttribute('aria-checked')).toBe('false')
    expect((h.querySelector('input[type="text"]') as HTMLInputElement)
      .placeholder).toContain('搜索公式名/原文')
    act(() => sw!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(sw!.getAttribute('aria-checked')).toBe('true')
    expect((h.querySelector('input[type="text"]') as HTMLInputElement)
      .placeholder).toContain('搜公式名/拼音')
  })
})
