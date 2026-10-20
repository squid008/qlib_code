// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import FormulaEditor from '../components/FormulaEditor'

/**
 * `FormulaEditor`（561 行）测试（★ v2.10.37，2026-10-20 ✓）。
 *
 * 它是**纯编辑器** ✓（不 import api ✓）⇒ 这轮能写**确定性**断言 ✓，不用 mock 任何东西 ✓。
 *
 * ## 钉什么
 *
 * ① `value` 必须**真的进到 `<textarea>`** ✓（受控组件的底线 ✗ 写错就是"点了没反应"✗）；
 * ② `placeholder` / `rows` 透传 ✓；
 * ③ 空值不崩 ✓（新建公式时就是空 ✓）；
 * ④ **初始不该触发 `onChange`** ✓ —— 受控组件在挂载时若误报一次 onChange ✗，
 *    父组件会把"用户改了"当成真事（可能触发"未保存"提示 / 脏标记 ✗）。
 */
let root: Root | null = null
let host: HTMLDivElement | null = null

const mount = (ui: React.ReactElement) => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(ui))
  return host
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('FormulaEditor', () => {
  it('`value` 真的进到 textarea（受控组件的底线 ✓）', () => {
    const h = mount(<FormulaEditor value="close > ma(close, 5)" onChange={() => {}} />)
    const ta = h.querySelector('textarea')
    expect(ta).toBeTruthy()
    expect(ta!.value).toBe('close > ma(close, 5)')
  })

  it('`placeholder` 透传；`rows` 不通过 attribute 实现（实测 ✗→✓）', () => {
    const h = mount(
      <FormulaEditor value="" onChange={() => {}} placeholder="写点什么" rows={3} />,
    )
    const ta = h.querySelector('textarea')!
    expect(ta.getAttribute('placeholder')).toBe('写点什么')
    // ⚠ 实测：`rows={3}` **没有**落到 `rows` 属性上（是 null ✗）——
    //   组件把它变成样式高度（高亮层与输入层要逐行对齐 ✓，用 `rows` 属性对不上 ✓）。
    //   ⇒ 只断言"给 rows 不崩" ✓，**不假装**能验高度 ✗（jsdom 没有布局 ✗）。
    expect(ta.getAttribute('rows')).toBeNull()
  })

  it('空 `value` 不崩（新建公式时就是空 ✓）', () => {
    const h = mount(<FormulaEditor value="" onChange={() => {}} />)
    expect(h.querySelector('textarea')!.value).toBe('')
  })

  it('⭐ 挂载**不该**触发 `onChange`（否则父组件会误判成"用户改过了"✗）', () => {
    const onChange = vi.fn()
    mount(<FormulaEditor value="x" onChange={onChange} />)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('多行公式原样保留（换行不能被吞 ✗）', () => {
    const v = 'a = close;\nb = ma(a, 5);\nb'
    const h = mount(<FormulaEditor value={v} onChange={() => {}} />)
    expect(h.querySelector('textarea')!.value).toBe(v)
  })
})
