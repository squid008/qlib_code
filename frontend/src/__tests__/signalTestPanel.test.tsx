// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/**
 * `SignalTestPanel` 的**骨架**测试（★ v2.10.35，2026-10-20 ✓ —— 前端最后一个大件，1520 行）。
 *
 * ## 打法（第三次沿用，前两次已验证有效 ✓）
 *
 * 只钉"打开面板时必然 / 必然**不**发生的事" ✓：
 * ① 挂载即拉**选项**（`getSignalTestOptions` ✓）—— 拉不到 ⇒ 下拉框全空 ⇒ 用户以为不能用 ✗；
 * ② 挂载**绝不能**解析 CSV 或跑测试 ✗ —— 这条尤其重要：
 *    `runSignalTest` 是**真跑一次回测级测试**（分钟级 + 占并发 ✓）⇒
 *    若谁把"自动跑一次"塞进挂载副作用 ✗，用户**每打开一次面板就白烧一次算力** ✓
 *    （和 `EventStudyModal` 那条 `req=null` 同类 ✓，都属于"**打开 ≠ 执行**"✓）。
 *
 * ## ⚠ 边界（不假装 ✓）
 *
 * 上传 CSV → 解析 → 跑测试 → 看结果这条主链路**未测** ✗：
 * 它要 base64 文件、`SignalTestParseResult` / `SignalTestRunResult` 的完整形状 ✓，
 * 属专门一轮 ✓。本轮只保证"**入口不误触发**"✓。
 */
const spies = vi.hoisted(() => ({
  getSignalTestOptions: vi.fn(async () => ({})),
  parseSignalCsv: vi.fn(async () => ({ kind: '', issues: [] })),
  runSignalTest: vi.fn(async () => ({})),
}))

vi.mock('../api', () => spies)

import SignalTestPanel from '../components/SignalTestPanel'

let root: Root | null = null
let host: HTMLDivElement | null = null

const flush = async () => {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

const mount = async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(<SignalTestPanel />)
  })
  await flush()
  return host
}

beforeEach(() => {
  Object.values(spies).forEach((s) => s.mockClear())
})

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('SignalTestPanel（骨架）', () => {
  it('⭐ 挂载即拉选项（拉不到 ⇒ 下拉框全空 ⇒ 用户以为不能用 ✗）', async () => {
    await mount()
    expect(spies.getSignalTestOptions).toHaveBeenCalled()
  })

  it('⭐⭐ 挂载**绝不**解析 CSV（打开面板 ≠ 上传文件 ✓）', async () => {
    await mount()
    expect(spies.parseSignalCsv).not.toHaveBeenCalled()
  })

  it('⭐⭐ 挂载**绝不**跑测试（`runSignalTest` 是分钟级 + 占并发 ✗）', async () => {
    await mount()
    expect(spies.runSignalTest).not.toHaveBeenCalled()
  })

  it('选项返回空对象也**不白屏**（后端字段缺失时不能崩 ✗）', async () => {
    const h = await mount()
    expect(h.textContent!.length).toBeGreaterThan(50)
  })
})
