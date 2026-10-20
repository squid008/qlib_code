// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/**
 * `ModelArtifacts`（368 行）测试（★ v2.10.39，2026-10-20 ✓）。
 *
 * **props 驱动** ✓（`artifacts` 由父组件传 ✓），api 只在**展开特征表时**才用 ✓（懒加载 ✓）。
 *
 * ## 钉什么
 *
 * ① **`artifacts={}` 不崩** ✓ —— 父组件在"产物还没到"时就会渲染它 ✓；
 * ② **未展开时不请求特征对照** ✓ —— 这是刻意的懒加载 ✓（源码 `if (!showFeatures || !taskId …) return` ✓）；
 *    谁改成挂载即拉，这里立刻红 ✓（每开一个历史任务就多一次请求 ✗）。
 */
const spies = vi.hoisted(() => ({
  getBacktestFeatures: vi.fn(async () => ({ items: [] })),
}))
vi.mock('../api', () => spies)

import ModelArtifacts from '../components/ModelArtifacts'

let root: Root | null = null
let host: HTMLDivElement | null = null
const mount = (ui: React.ReactElement) => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(ui))
  return host
}
beforeEach(() => Object.values(spies).forEach((s) => s.mockClear()))
afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('ModelArtifacts', () => {
  it('空 artifacts 不崩（产物还没到时就会渲染它 ✓）', () => {
    expect(() => mount(<ModelArtifacts artifacts={{} as never} taskId="t" />)).not.toThrow()
  })

  it('有线性权重时渲染出内容（不是白屏 ✓）', () => {
    const artifacts = {
      model_info: { model: 'linear', feature: 'Alpha158', topk: 50 },
      linear: { coef: [0.1, 0.2], intercept: 0.0, weights: [] },
    }
    const h = mount(<ModelArtifacts artifacts={artifacts as never} taskId="t" />)
    expect(h.textContent!.length).toBeGreaterThan(20)
  })

  it('⭐ 未展开时**不**请求特征对照（懒加载 ✓）', () => {
    mount(<ModelArtifacts artifacts={{} as never} taskId="t" />)
    expect(spies.getBacktestFeatures).not.toHaveBeenCalled()
  })
})
