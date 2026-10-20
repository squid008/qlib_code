// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import ExitCurveModal from '../components/ExitCurveModal'

/**
 * `ExitCurveModal`（315 行）骨架测试（★ v2.10.37，2026-10-20 ✓）。
 *
 * 数据全来自 props ✓（`row` / `ei` ✓），api 只在**点导出时**才用 ✓ ⇒ 渲染路径零 mock ✓。
 *
 * ## 钉什么
 *
 * ① **`row` 里什么都没有时也不能崩** ✓ —— 源码里 `series` 是**防御式**构建的
 *    （`if (!one || one.error) return` ✓）⇒ 正好用"空 row"验这条防御 ✓；
 * ② `ei` 用来"强调第几条离场"（`null` = 全部 ✓）—— 传越界值也不许崩 ✓
 *    （父组件传错索引是常见事 ✓：比如离场列表变了但选中索引没跟着重置 ✗）。
 *
 * ⚠ 曲线本体（`<LineChart>`）在 SSR/jsdom 里宽高为 0 ⇒ **不渲染** ✗，
 * 所以"哪几条曲线在"这类断言**本轮做不了** ✓（要看图例得改绘图方式 ✗）。
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

const EMPTY_ROW = {} as never

describe('ExitCurveModal（骨架）', () => {
  it('空 row 不崩（防的是"没跑离场就直接点开"✓）', () => {
    expect(() => {
      mount(<ExitCurveModal row={EMPTY_ROW} ei={null} name="f" onClose={() => {}} />)
    }).not.toThrow()
  })

  it('`ei` 越界不崩（父组件索引过期是常见事 ✗）', () => {
    expect(() => {
      mount(<ExitCurveModal row={EMPTY_ROW} ei={99} name="f" onClose={() => {}} />)
    }).not.toThrow()
  })

  it('渲染出内容（不是白屏 ✓）', () => {
    const h = mount(<ExitCurveModal row={EMPTY_ROW} ei={null} name="f" onClose={() => {}} />)
    expect(h.textContent!.length).toBeGreaterThan(20)
  })
})
