// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

/**
 * `SingleFactorTestPanel` 的**骨架**测试（★ v2.10.33，2026-10-20 ✓ —— 前端最大的未测件，2124 行）。
 *
 * ## 打法（沿用 `EventStudyModal` 那轮验证过的套路 ✓）
 *
 * 不追"逐字段渲染"✗，只钉**打开面板时必然发生的两件事** ✓：
 * ① **恢复运行中的任务**（`getSingleFactorTestTasks` ✓）—— 用户切走再回来，
 *    面板必须知道"之前那个任务还在跑" ✓；漏了这条 ⇒ 界面显示"空"，而**后台其实在烧 CPU** ✗
 *    （用户会再点一次提交 ⇒ 两个任务抢同一条数据 ✗）。
 * ② **拉因子目录**（`getFactorCatalog('Alpha158')` ✓）—— 拉不到 ⇒ 下拉框空白 ⇒
 *    用户以为"没有因子可选"✗。
 * 这两条**不需要知道任何结果类型** ✓，用 spy 就能验 ✓。
 *
 * ## ⚠ 已知边界（不假装 ✓）
 *
 * · 面板里的**提交 / 轮询 / 取消 / 清空 / 导出**全都要"点按钮 + 等异步"✗ ——
 *   需要先把 `SingleFactorTestResult` 的表格字段读准 ✓（专门一轮 ✓）；
 * · `onCapacityChange`（并发容量回传）与"运行中自动展开"等联动**未测** ✗。
 */
const spies = vi.hoisted(() => ({
  createSingleFactorTest: vi.fn(async () => ({ task_id: 'T1' })),
  getSingleFactorTestProgress: vi.fn(async () => ({ status: 'running', progress: 10, message: '' })),
  cancelSingleFactorTest: vi.fn(async () => ({ status: 'ok' })),
  getSingleFactorTestTasks: vi.fn(async () => ({})),
  clearSingleFactorTest: vi.fn(async () => ({ status: 'ok' })),
  getFactorCatalog: vi.fn(async () => ({ factors: [], fields: [] })),
}))

vi.mock('../api', () => spies)

import SingleFactorTestPanel from '../components/SingleFactorTestPanel'

let root: Root | null = null
let host: HTMLDivElement | null = null

const PROPS = {
  customFormulas: [],
  defaultUniverse: 'csi300',
  defaultStartDate: '2024-01-01',
  defaultEndDate: '2024-12-31',
  defaultLabelHorizon: 2,
  onCapacityChange: () => {},
}

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
    root!.render(<SingleFactorTestPanel {...PROPS} />)
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

describe('SingleFactorTestPanel（骨架）', () => {
  it('⭐⭐ 挂载即恢复运行中的任务（漏了 ⇒ 界面显示空，后台却在烧 CPU ✗）', async () => {
    await mount()
    expect(spies.getSingleFactorTestTasks).toHaveBeenCalled()
  })

  it('⭐⭐ 因子目录是**按需加载**：挂载时不预先拉（省两次请求 ✓）', async () => {
    // ⚠ 我第一版断言"挂载即拉 Alpha158" ✗ —— 实测**没有被调用** ✓：
    //   源码里是 `loadCatalog(source)` 函数（L335），由"展开某个因子组"时才触发 ✓。
    //   这是**刻意的懒加载** ✓（打开面板就拉 158+360 两份目录纯属浪费 ✓）。
    //   ⇒ 这条断言反过来钉住它 ✓：**谁把懒加载改成挂载即拉，这里立刻红** ✓。
    await mount()
    expect(spies.getFactorCatalog).not.toHaveBeenCalled()
  })

  it('任务列表与目录都为空 ⇒ 不崩、且真的渲染出面板（不是白屏 ✗）', async () => {
    const h = await mount()
    expect(h.textContent!.length).toBeGreaterThan(50)
  })

  it('⭐ 挂载时**不该**自动提交任务（打开面板 ≠ 跑测试 ✓）', async () => {
    await mount()
    expect(spies.createSingleFactorTest).not.toHaveBeenCalled()
  })
})
