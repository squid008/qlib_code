// @vitest-environment jsdom
/**
 * `DayExclusions` 的交互测试（★ v2.9.41 ✓）—— `App.tsx` 拆出的第一块的守门 ✓。
 *
 * ## 拆组件最怕的不是"画错"，是**悄悄改了口径** ✗
 *
 * 这一块的语义是"回测时按**调仓当日**状态剔除某些票" ✓（与单因子测试的 T+1 口径**不同** ✓）。
 * 拆出去时若把 key 写错（`exclude_st` vs `exclude_stock_gem` ✓）或把 `checked` 弄反 ✗，
 * 页面看着一样 ✓、回测结果却变了 ✗ —— 只有断言能拦住 ✓。
 *
 * 守三件事：
 * 1. **三个开关、三条中文标签、三条 title 提示** 都在 ✓（title 是用户唯一的解释来源 ✗ 不许丢）；
 * 2. `checked` 与 props **严格对应** ✓（不能三个都跟着第一个变 ✗）；
 * 3. 点击回调的 **key 必须对得上** ✓，且传**取反后的值** ✓（不是原始值 ✗）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import DayExclusions from '../components/DayExclusions'

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

function boxes(el: HTMLElement): HTMLInputElement[] {
  return Array.from(el.querySelectorAll('input[type=checkbox]')) as HTMLInputElement[]
}

/** 点一个 checkbox（React 的 onChange 绑在 click 上 ✓）。 */
function click(box: HTMLInputElement) {
  act(() => {
    box.click()
  })
}

const base = { excludeSt: false, excludeGem: false, excludeKcb: false, onToggle: () => {} }

describe('DayExclusions（jsdom 交互）', () => {
  it('三个开关 + 中文标签 + title 提示都在', () => {
    const el = renderInto(<DayExclusions {...base} />)
    const b = boxes(el)
    expect(b).toHaveLength(3)
    expect(el.textContent).toContain('日截面剔除：')
    for (const t of ['剔除ST/退市', '剔除创业板', '剔除科创板']) {
      expect(el.textContent, `缺少开关「${t}」✗`).toContain(t)
    }
    // title 是用户唯一的解释来源 ✓（重构最容易顺手删掉 ✗）
    const titles = b.map((x) => x.closest('label')?.getAttribute('title') || '')
    expect(titles[0]).toContain('ST')
    expect(titles[1]).toContain('SZ30')
    expect(titles[2]).toContain('SH688')
  })

  it('checked 严格对应各自的 props（不能三个都跟着第一个变 ✗）', () => {
    const el = renderInto(<DayExclusions excludeSt excludeGem={false} excludeKcb onToggle={() => {}} />)
    const [st, gem, kcb] = boxes(el)
    expect(st.checked).toBe(true)
    expect(gem.checked).toBe(false)
    expect(kcb.checked).toBe(true)
  })

  it('点「剔除创业板」⇒ onToggle("exclude_stock_gem", true)（key 与取反值都要对 ✓）', () => {
    const onToggle = vi.fn()
    const el = renderInto(<DayExclusions {...base} onToggle={onToggle} />)
    click(boxes(el)[1])
    expect(onToggle).toHaveBeenCalledTimes(1)
    expect(onToggle).toHaveBeenCalledWith('exclude_stock_gem', true)
  })

  it('已勾选的再点 ⇒ 回调 false（不是再传一次 true ✗）', () => {
    const onToggle = vi.fn()
    const el = renderInto(<DayExclusions {...base} excludeKcb onToggle={onToggle} />)
    click(boxes(el)[2])
    expect(onToggle).toHaveBeenCalledWith('exclude_stock_kcb', false)
  })
})
