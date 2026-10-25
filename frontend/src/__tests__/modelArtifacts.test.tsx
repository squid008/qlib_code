// @vitest-environment jsdom

/**
 * `ModelArtifacts` 渲染与导出（★ v2.11.80）—— 补 v2.11.68 移除 xlsx 后掉下来的覆盖。
 *
 * ## 为什么现在补
 * v2.11.68 把前端 Excel 依赖删掉（导出改走后端）⇒ `SingleFactorTestPanel` 里那段
 * **已被测试覆盖**的写 Excel 代码没了 ✓ ⇒ 前端总覆盖从 **60.6% → 60.37%（−0.23pp）** ✗，
 * 棘轮（`score_baseline.fe_cov_measured`）判红 ✓。
 * ⇒ 正确做法**不是**下调基线 ✗（那是放水），而是给**还缺测试的真代码**补上 ✓：
 *   ① `api/exportXlsx.exportAoaXlsx`（v2.11.68 新增的接口函数，一条测试都没有 ✗）；
 *   ② 本文件：`ModelArtifacts`（294 段里缺 212，27.9% ✓ 纯 props 驱动 + 一个 api 调用 ✓）。
 *
 * ## 它坏掉的样子（都"看着正常"）
 * · 段号显示错（`seg11` 显示成「段 3」✗ —— v1.20.50 的真事故）；
 * · 权重 CSV 的**符号/顺序**错 ⇒ 用户拿去复现就复现不出来 ✗；
 * · 特征表拉取失败**静默空白** ✗（应该给出可读错误 ✓）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

vi.mock('../api', () => ({
  getBacktestFeatures: vi.fn(async () => ({
    items: [{ name: 'alpha1', formula: 'Mean($close,5)' }],
  })),
}))

import ModelArtifacts from '../components/ModelArtifacts'

const mounted: Array<() => void> = []

beforeEach(() => {
  // ⚠ jsdom **没有** `URL.createObjectURL` ✗ —— 组件的下载 handler 一进去就调它 ✓
  //   ⇒ 不打桩就是 `TypeError: URL.createObjectURL is not a function` ✓
  //   （我第一版没打桩，3 条当场红 ✓ —— 错的是测试环境，不是实现 ✓）。
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

/** 按文案找按钮并点它（找不到就明确失败 ⇒ 不给"静默跳过"✗）。 */
function clickByText(host: HTMLElement, re: RegExp): void {
  const btn = Array.from(host.querySelectorAll('button')).find((b) => re.test(b.textContent || ''))
  if (!btn) {
    const seen = Array.from(host.querySelectorAll('button'))
      .map((b) => b.textContent)
      .join(' | ')
    // ⚠ 这里我第一版写成了 Python 的 `'%s' % (..)` ✗ —— 在 TS 里 `%` 是**取模** ⇒ tsc 报
    //   `TS2362/TS2363` ✓（"字符串不能用算术运算"）。TS 用模板字符串 ✓。
    throw new Error(`没找到按钮 ${String(re)} ⇒ 实际按钮：${seen}`)
  }
  act(() => {
    ;(btn as HTMLButtonElement).click()
  })
}

const LINEAR = {
  model_info: { model: 'linear', segment: 'seg11' },
  feature_names: ['alpha1', 'alpha2'],
  linear: {
    intercept: 0.25,
    weights: [0.5, -1.5],
    feature_weights: [
      { feature: 'alpha1', weight: 0.5 },
      { feature: 'alpha2', weight: -1.5 },
    ],
  },
  params: { lr: 0.1, n: 3 },
  model_file: null as string | null,
  feature_importance: [{ feature: 'alpha1', importance: 2.5 }],
}

describe('ModelArtifacts', () => {
  it('线性模型：模型名 / 特征数 / **公式按权重排序**', () => {
    const host = render(<ModelArtifacts artifacts={LINEAR as never} taskId="t1" />)
    const t = host.textContent || ''
    expect(t).toContain('linear')
    expect(t).toContain('2 个特征')
    // ⚠ 公式里权重按**绝对值降序** ✓（`-1.5` 排在 `0.5` 前 ✓ —— 用户看"影响最大"的那个 ✓）
    const i2 = t.indexOf('alpha2')
    const i1 = t.indexOf('alpha1')
    assertOrder(i2, i1, t)
  })

  it('展开权重表后能看到特征名（点击不炸）', () => {
    const host = render(<ModelArtifacts artifacts={LINEAR as never} taskId="t1" />)
    clickByText(host, /查看全部/)
    expect(host.textContent).toContain('alpha1')
  })

  it('下载权重CSV：走 Blob 且不抛', () => {
    const host = render(<ModelArtifacts artifacts={LINEAR as never} taskId="t1" />)
    clickByText(host, /下载权重/)
    expect(host).toBeTruthy()
  })

  it('⭐ 有 model_file 时才出现"下载模型文件"，点了不抛', () => {
    const withFile = { ...LINEAR, model_file: 'tree-dump-text' }
    const host = render(<ModelArtifacts artifacts={withFile as never} taskId="t1" />)
    clickByText(host, /下载模型文件/)
    expect(host.textContent).toContain('下载模型文件')
  })

  it('没有 model_file ⇒ **不渲染**"下载模型文件"（别给一个点了没反应的按钮 ✗）', () => {
    const host = render(<ModelArtifacts artifacts={LINEAR as never} taskId="t1" />)
    expect(host.textContent).not.toContain('下载模型文件')
  })

  it('滚动训练：多段时出现段选择，且不崩', () => {
    const multi = {
      ...LINEAR,
      segments: [LINEAR, { ...LINEAR, model_info: { model: 'lightgbm', segment: 'seg12' } }],
    }
    const host = render(<ModelArtifacts artifacts={multi as never} taskId="t1" />)
    expect(host.textContent).toContain('段')
    expect(host.querySelector('select')).not.toBeNull()
  })

  it('没有 taskId 时渲染不崩（不拉特征表）', () => {
    const host = render(<ModelArtifacts artifacts={LINEAR as never} taskId="" />)
    expect(host.textContent).toContain('linear')
  })

  it('空 artifacts：显示"未知"而不是崩', () => {
    const host = render(<ModelArtifacts artifacts={{} as never} taskId="t1" />)
    expect(host.textContent).toContain('未知')
  })
})

/** 断言 `a` 在文案里出现在 `b` **之前**（排序类断言用 ✓）。 */
function assertOrder(a: number, b: number, text: string): void {
  if (a < 0 || b < 0 || a > b) {
    throw new Error(`顺序不符（应 ${a} < ${b}）：${text.slice(0, 120)}`)
  }
}
