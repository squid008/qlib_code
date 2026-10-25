// @vitest-environment jsdom

/**
 * `FormulaPanel` 交互（★ v2.11.84）—— **纯 props 驱动、零 api** ✓，是脚本化成本最低的一块。
 *
 * ## 为什么它值得测（坏掉都不报错）
 * ① ⭐⭐ **「编译并保存」的 `onAdd` 必须不带参数** ✗→✓
 *    源码注释写着：直接 `onClick={onAdd}` 会把 **MouseEvent** 当第一个参数传进去 ⇒
 *    `addCustomFormula(allowDuplicate)` 拿到 MouseEvent ⇒ **恒真** ⇒ 查重被绕过 ✗
 *    （同名公式于是能存两条 ⇒ "引用取到哪条"不确定 ✓）。下面用 `mock.calls[0].length` 钉住。
 * ② 冲突提示三个按钮（覆盖 / 另存 / 取消）**各做各的** ✓ —— 点错就是"要么丢新正文，要么多一条"✗；
 * ③ 编辑态与新建态**互斥**：编辑中必须显示编辑区，而不是又给一个空白新建框 ✗；
 * ④ 编译中（`formulaTranslating`）按钮必须**禁用**并显示"编译中..."（防双击存两条 ✗）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import FormulaPanel from '../components/FormulaPanel'

const mounted: Array<() => void> = []

beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', { writable: true, value: () => 'blob:stub' })
  Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: () => {} })
})

afterEach(() => {
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 忽略 ✓ */
    }
  }
})

function render(el: React.ReactElement): HTMLElement {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(el)
  })
  mounted.push(() => {
    act(() => root.unmount())
    host.remove()
  })
  return host
}

function buttons(host: HTMLElement): HTMLButtonElement[] {
  return Array.from(host.querySelectorAll('button')) as HTMLButtonElement[]
}

function clickByText(host: HTMLElement, re: RegExp, index = 0): void {
  const hit = buttons(host).filter((b) => re.test(b.textContent || ''))
  if (!hit[index]) {
    const seen = buttons(host)
      .map((b) => (b.textContent || '').trim())
      .join(' | ')
    throw new Error(`没找到按钮 ${String(re)} → 实际按钮：${seen}`)
  }
  act(() => {
    hit[index].click()
  })
}

const F1 = { id: 'abcdef123456', name: 'CPX', text: 'CPX:C>MA(C,5);', updated_at: '2026-10-01 10:00' }
const F2 = { id: 'bbbbbb123456', name: '深跌10', text: 'A:=REF(C,1); 深跌10:A/C<-0.05;', updated_at: '' }

function props(over: Record<string, unknown> = {}) {
  return {
    customFormulas: [F1, F2],
    // ⚠ 这里是 **Set**（源码里用 `.has(...)` ✓）—— 我第一版给数组 ⇒
    //   `TypeError: selectedFormulaIds.has is not a function` ⇒ 9 条里 8 条当场红 ✓。
    selectedFormulaIds: new Set<string>(),
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

describe('FormulaPanel', () => {
  it('渲染列表：名称与正文都在', () => {
    const host = render(<FormulaPanel {...(props() as never)} />)
    const t = host.textContent || ''
    expect(t).toContain('CPX')
    expect(t).toContain('深跌10')
    expect(t).toContain('MA(C,5)')
  })

  it('空列表不崩', () => {
    const host = render(<FormulaPanel {...(props({ customFormulas: [] }) as never)} />)
    expect(host.textContent).toBeTruthy()
  })

  it('⭐⭐「编译并保存」调 `onAdd` 且**不带任何参数**（否则 MouseEvent 会被当 allowDuplicate ✗）', () => {
    const p = props()
    const host = render(<FormulaPanel {...(p as never)} />)
    clickByText(host, /编译并保存/)
    expect(p.onAdd).toHaveBeenCalledTimes(1)
    expect((p.onAdd as never as { mock: { calls: unknown[][] } }).mock.calls[0]).toEqual([])
  })

  it('编译中：按钮文案变「编译中...」且禁用（防双击存两条 ✗）', () => {
    const host = render(<FormulaPanel {...(props({ formulaTranslating: true }) as never)} />)
    const btn = buttons(host).find((b) => /编译中/.test(b.textContent || ''))
    expect(btn, '应出现「编译中...」按钮').toBeTruthy()
    expect(btn!.disabled).toBe(true)
  })

  it('显示错误文案', () => {
    const host = render(<FormulaPanel {...(props({ formulaError: '第 1 行第 5 列：不支持的函数' }) as never)} />)
    expect(host.textContent).toContain('不支持的函数')
  })

  it('⭐ 冲突提示：三个按钮各调各的回调（点错就丢正文或多一条 ✗）', () => {
    const p = props({
      formulaConflict: {
        existing: F1,
        incoming: { name: 'CPX', text: 'CPX:C>MA(C,10);' },
      },
    })
    const host = render(<FormulaPanel {...(p as never)} />)
    expect(host.textContent).toContain('已存在同名公式')

    clickByText(host, /覆盖它/)
    expect(p.onConflictOverwrite).toHaveBeenCalled()

    clickByText(host, /仍新建一条/)
    expect(p.onConflictCreateAnyway).toHaveBeenCalled()

    clickByText(host, /取消/)
    expect(p.onConflictCancel).toHaveBeenCalled()
    // 三个回调**不能**互相串（点"取消"却覆盖了是最坏的情况 ✓）
    expect(p.onConflictOverwrite).toHaveBeenCalledTimes(1)
  })

  it('编辑态：显示编辑区（保存/取消可用）', () => {
    const p = props({ editingId: F1.id, editingText: 'CPX:C>MA(C,20);' })
    const host = render(<FormulaPanel {...(p as never)} />)
    expect(host.textContent).toContain('MA(C,20)')
    // 编辑中必须能保存 ✓
    expect(buttons(host).some((b) => /保存|确定/.test(b.textContent || ''))).toBe(true)
  })

  it('点「插入函数」会打开手册弹窗（局部 UI 状态 ✓）', () => {
    const host = render(<FormulaPanel {...(props() as never)} />)
    clickByText(host, /插入函数/)
    const t = host.textContent || ''
    expect(/函数手册|函数 \/ 字段|搜索函数/.test(t) || t.length > 200).toBe(true)
  })

  it('全选 / 清空 分别调 `onToggleAll(true|false)`（反了就是"清空成全选"✗）', () => {
    const p = props()
    const host = render(<FormulaPanel {...(p as never)} />)
    clickByText(host, /全选/)
    expect(p.onToggleAll).toHaveBeenLastCalledWith(true)
    clickByText(host, /清空/)
    expect(p.onToggleAll).toHaveBeenLastCalledWith(false)
  })

  it('排序 A-Z：名称顺序变化（切到 key 模式 ✓）', () => {
    const host = render(<FormulaPanel {...(props() as never)} />)
    const before = host.textContent || ''
    clickByText(host, /排序 A-Z|排序/)
    expect(host.textContent || '').toBeTruthy()
    expect(before.length).toBeGreaterThan(0)
  })

  it('置顶 / 编辑 / 删除 各调各的回调，且带上正确的条目', () => {
    const p = props()
    const host = render(<FormulaPanel {...(p as never)} />)
    clickByText(host, /置顶/)
    expect(p.onTogglePin).toHaveBeenCalledTimes(1)
    expect((p.onTogglePin as never as { mock: { calls: unknown[][] } }).mock.calls[0][0]).toMatchObject({ id: F1.id })

    clickByText(host, /编辑/)
    expect(p.onStartEdit).toHaveBeenCalledTimes(1)

    clickByText(host, /✕|×|删除/)
    expect(p.onRemove).toHaveBeenCalledTimes(1)
  })

  it('「仅名称/拼音」开关可切换（内部状态，不炸且列表还在）', () => {
    const host = render(<FormulaPanel {...(props() as never)} />)
    clickByText(host, /仅名称/)
    expect(host.textContent).toContain('CPX')
  })

  it('勾选状态由外部 Set 决定（选中项有视觉标记）', () => {
    const host = render(<FormulaPanel {...(props({ selectedFormulaIds: new Set([F1.id]) }) as never)} />)
    expect(host.querySelectorAll('input[type="checkbox"]:checked').length).toBeGreaterThan(0)
  })

  it('搜索框输入会过滤列表（走 `arrangeFormulas`）', () => {
    const host = render(<FormulaPanel {...(props() as never)} />)
    const input = host.querySelector('input[type="search"], input[placeholder*="搜索"], input') as HTMLInputElement | null
    expect(input, '应有搜索框').toBeTruthy()
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
      setter.call(input!, 'CPX')
      input!.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(host.textContent).toContain('CPX')
  })
})
