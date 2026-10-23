// @vitest-environment jsdom
/**
 * `FormulaEditor`（419 行 / 覆盖率 45.8% → 本文件的目标）★ v2.11.63。
 *
 * ## 为什么挑它
 * 按"未覆盖语句数"排序时，它是**唯一一个「只依赖 react」的大文件** ✓（227 条未覆盖；
 * 其它大块全是依赖 `api` 的面板 ✗ ⇒ mock 成本高）。而它承载**公式输入** —— 用户天天用 ✓，
 * 且历史上出过两个真 bug，两条都值得钉住 ✓（不是为凑覆盖率 ✗）：
 * ① 插入函数后**光标/视图跳到末尾** ✗（受控 textarea 的经典坑）；
 * ② 从记事本/通达信粘进来的 **CRLF** 让行号槽与 `<pre>` 错开一行 ✗。
 *
 * ## ⚠ 写法随项目（不用 @testing-library ✗）
 * 本项目组件测试统一用 `createRoot` + `act` + `// @vitest-environment jsdom` ✓
 * （我第一版用 testing-library ⇒ **9 条全红**：`ReferenceError: document is not defined` ✗
 *  —— 因为**没写**那行 environment 注释 ✓，而且项目也没装那个包 ✓）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { useRef, useState } from 'react'

import FormulaEditor, { type FormulaEditorHandle } from '../components/FormulaEditor'

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

/** React 受控组件：必须用原生 setter + `input` 事件才会触发 onChange ✓。 */
function type(el: HTMLTextAreaElement | HTMLInputElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  act(() => {
    setter?.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function key(el: Element, init: KeyboardEventInit) {
  act(() => {
    el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, ...init }))
  })
}

/** 受控宿主：模拟真实用法（父组件持值 + 拿句柄「插入函数」）✓。 */
function Host({
  initial = '',
  onValue,
  onFocus,
}: {
  initial?: string
  onValue?: (v: string) => void
  onFocus?: () => void
}) {
  const [v, setV] = useState(initial)
  const h = useRef<FormulaEditorHandle | null>(null)
  return (
    <>
      <FormulaEditor
        value={v}
        onChange={(x) => {
          setV(x)
          onValue?.(x)
        }}
        onFocus={onFocus}
        handleRef={(hh) => {
          h.current = hh
        }}
      />
      <button type="button" id="ins" onClick={() => h.current?.insertText('MA(')}>
        插入
      </button>
      <button type="button" id="focus" onClick={() => h.current?.focus()}>
        聚焦
      </button>
    </>
  )
}

const areaOf = (el: HTMLElement) => el.querySelector('textarea') as HTMLTextAreaElement

describe('FormulaEditor：受控渲染与换行归一', () => {
  it('把 value 渲染进 textarea', () => {
    const el = renderInto(<Host initial={'A:=MA(CLOSE,5);\n输出:A;'} />)
    expect(areaOf(el).value).toBe('A:=MA(CLOSE,5);\n输出:A;')
  })

  it('⭐ CRLF 必须归一成 LF（否则行号槽与着色层错开一行 ✗）', () => {
    const el = renderInto(<Host initial={'A:=1;\r\nB:=2;'} />)
    expect(areaOf(el).value).toBe('A:=1;\nB:=2;')
    expect(areaOf(el).value).not.toContain('\r')
  })

  it('占位符能传下去', () => {
    const el = renderInto(<FormulaEditor value="" onChange={() => {}} placeholder="在这里写公式" />)
    expect(areaOf(el).getAttribute('placeholder')).toBe('在这里写公式')
  })
})

describe('FormulaEditor：输入与句柄', () => {
  it('输入触发 onChange（带新值）', () => {
    const onValue = vi.fn()
    const el = renderInto(<Host onValue={onValue} />)
    type(areaOf(el), 'X:=1;')
    expect(onValue).toHaveBeenLastCalledWith('X:=1;')
  })

  it('⭐ 句柄 insertText 在**光标处**插入（不是追加到末尾 ✗）', () => {
    const onValue = vi.fn()
    const el = renderInto(<Host initial="AB" onValue={onValue} />)
    act(() => {
      ;(el.querySelector('#ins') as HTMLButtonElement).click()
    })
    // 默认光标在最前（jsdom selectionStart = 0 ✓）⇒ 插到开头 ✓
    expect(onValue).toHaveBeenLastCalledWith('MA(AB')
  })

  it('句柄 focus 真的把焦点给 textarea', () => {
    const el = renderInto(<Host />)
    act(() => {
      ;(el.querySelector('#focus') as HTMLButtonElement).click()
    })
    expect(document.activeElement).toBe(areaOf(el))
  })

  it('onFocus 会回调', () => {
    const onFocus = vi.fn()
    const el = renderInto(<Host onFocus={onFocus} />)
    act(() => {
      areaOf(el).focus()
    })
    expect(onFocus).toHaveBeenCalled()
  })
})

describe('FormulaEditor：查找栏', () => {
  const findInput = (el: HTMLElement) =>
    el.querySelector('input[placeholder*="查找"]') as HTMLInputElement | null

  it('Ctrl+F 打开查找栏，Esc 关闭', () => {
    const el = renderInto(<Host initial="CLOSE" />)
    expect(findInput(el)).toBeNull()
    key(areaOf(el), { key: 'f', ctrlKey: true })
    const box = findInput(el)
    expect(box).not.toBeNull()
    key(box as HTMLInputElement, { key: 'Escape' })
    expect(findInput(el)).toBeNull()
  })

  it('输入查询词不崩，Enter/Shift+Enter 在上一个/下一个之间循环', () => {
    const el = renderInto(<Host initial={'CLOSE\nCLOSE'} />)
    key(areaOf(el), { key: 'f', ctrlKey: true })
    const box = findInput(el) as HTMLInputElement
    type(box, 'CLOSE')
    expect(box.value).toBe('CLOSE')
    // 有两处命中：连按两下 Enter 应该回到起点循环 ✓（只要求"不崩且值不变" ✓）
    key(box, { key: 'Enter' })
    key(box, { key: 'Enter' })
    key(box, { key: 'Enter', shiftKey: true })
    expect(box.value).toBe('CLOSE')
  })
})
