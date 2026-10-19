// @vitest-environment jsdom
/**
 * **jsdom 可用性探针**（★ 2026-10-19 ✓）。
 *
 * ## 它存在的唯一目的
 *
 * 2.9.21 发现：CI（Linux + **Node 20**）上只要走 `jsdom` 环境就崩 ✗
 * （`TypeError: Cannot read properties of undefined (reading 'bind')` ✓、0 个用例被收集 ✓），
 * 而本地（**Node 24**）正常 ✓ ⇒ 只能先把全局环境退回 `node` 换 CI 绿 ✓。
 *
 * 现在要拆 `App.tsx`（JSX 大块 ✗）⇒ **必须有真 DOM 的交互测试** ✗ ⇒ 得先把 jsdom 救回来 ✓。
 *
 * ## 做法：**逐文件**开 jsdom（而不是全局 ✗）
 *
 * 全局配置仍是 `environment: 'node'` ✓（绝大多数用例是纯函数 ✓，跑得更快 ✓）；
 * 只有本文件用 `// @vitest-environment jsdom` 开 DOM ✓。
 * ⇒ 这样即使 jsdom 在某个平台有问题，**也只影响这一个探针** ✓，不会拖垮全局 ✓。
 *
 * ## 判读
 *
 * · 本文件在 CI **绿** ⇒ jsdom 可用 ✓ ⇒ 可以开始写 `render()` / 点击 / 表格列宽等交互用例 ✓；
 * · 本文件在 CI **红** ⇒ 说明光升 Node 不够 ✗ ⇒ 需要钉 jsdom 版本或换 `happy-dom` ✓。
 */
import { describe, expect, it } from 'vitest'

describe('jsdom 探针', () => {
  it('document / window 存在，且能做基本 DOM 操作', () => {
    expect(typeof document).toBe('object')
    expect(typeof window).toBe('object')

    const el = document.createElement('div')
    el.className = 'probe'
    el.textContent = '你好'
    document.body.appendChild(el)

    expect(document.body.innerHTML).toContain('你好')
    expect(el.className).toBe('probe')
    expect(document.querySelector('.probe')).toBe(el)
  })

  it('能创建 SVG 元素（图表测试要用 ✓）', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    expect(svg.tagName.toLowerCase()).toBe('svg')
  })
})
