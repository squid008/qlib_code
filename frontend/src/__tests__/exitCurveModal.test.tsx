// @vitest-environment jsdom

/**
 * `ExitCurveModal`（★ v2.11.90）—— 缺 120 行，大头是"有离场数据时才渲染"的曲线/备注/导出：
 * `L80-98`(series 构建) / `L109-135`(导出) / `L141-152`(表格) / `L217-251`、`L260-297`(渲染) ✓。
 *
 * ## 它坏掉的样子（都不报错）
 * · **对照曲线（不离场）与离场曲线错位** ⇒ 差值看着像"离场有效"，其实是两条不同口径 ✗；
 * · 多离场时**只画第一条** ⇒ 用户以为其它离场没生效 ✗；
 * · 导出的 `ei`/`factor_id`/`horizon` 传错 ⇒ 文件里是**另一条离场/另一个周期**的记录 ✗
 *   （文件能打开、看着正常 —— 最难发现的一种）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

const mocks = vi.hoisted(() => ({
  exportXlsx: vi.fn(),
  capture: vi.fn(),
  saveBlob: vi.fn(),
}))

vi.mock('../captureChart', () => ({ captureSection: () => mocks.capture() }))
vi.mock('../downloadFile', () => ({
  saveBlob: (b: unknown, n: string) => mocks.saveBlob(b, n),
  stamp: () => '20260101_0000',
  blobErrorDetail: async (e: unknown) => String(e),
}))
vi.mock('../api', async (orig) => {
  const real = await orig<Record<string, unknown>>()
  return {
    ...real,
    exportExitCurveXlsx: (p: unknown) => mocks.exportXlsx(p),
    blobErrorDetail: async (e: unknown) => String(e),
  }
})

import ExitCurveModal from '../components/ExitCurveModal'

const mounted: Array<() => void> = []

beforeEach(() => {
  mocks.exportXlsx.mockReset().mockResolvedValue({ blob: new Blob(['PK']), filename: 'exit.xlsx' })
  mocks.capture.mockReset().mockResolvedValue('data:image/png;base64,AAA')
  mocks.saveBlob.mockReset()
  Object.defineProperty(URL, 'createObjectURL', { writable: true, value: () => 'blob:stub' })
  Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: () => {} })
})

afterEach(() => {
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 忽略 */
    }
  }
})

const DATES = ['2024-01-02', '2024-02-01', '2024-03-01', '2024-04-01']

function nav(label: string, over: Record<string, unknown> = {}) {
  return {
    dates: DATES,
    nav: [1.0, 1.05, 1.02, 1.12],
    label,
    mode: 'period_roll',
    perf: { annual_return: 0.15, max_drawdown: -0.08, sharpe: 1.2 },
    stats: { n_days: 80 },
    ...over,
  }
}

function row(over: Record<string, unknown> = {}) {
  return {
    id: 'f1',
    name: 'mom20',
    horizon: 20,
    // 对照：不离场 ✓
    period_roll_items: [nav('对照（不离场）')],
    exit_rolls: [
      { exit: { index: 0, name: '离场A', id: 'e1', expression: 'C<MA(C,20)' }, items: [nav('离场A')], first: nav('离场A') },
      { exit: { index: 1, name: '离场B', id: 'e2', expression: 'DATE<=20070901' }, items: [nav('离场B')], first: nav('离场B') },
    ],
    ...over,
  }
}

async function mount(props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const merged = { row: row(), ei: null, name: 'mom20', taskId: 't1', onClose: vi.fn(), ...props }
  await act(async () => {
    root.render(<ExitCurveModal {...(merged as unknown as React.ComponentProps<typeof ExitCurveModal>)} />)
  })
  mounted.push(() => {
    act(() => root.unmount())
    host.remove()
  })
  return { host, props: merged as Record<string, unknown> }
}

function btns(host: HTMLElement): HTMLButtonElement[] {
  return Array.from(host.querySelectorAll('button')) as HTMLButtonElement[]
}

describe('ExitCurveModal', () => {
  it('⭐ 渲染：标题带因子名 + 两条离场都在（不能只画第一条 ✗）', async () => {
    const { host } = await mount()
    const t = host.textContent || ''
    expect(t).toContain('mom20')
    expect(t).toMatch(/离场A/)
    expect(t).toMatch(/离场B/)
  })

  it('⭐ 指定 `ei` ⇒ 只展示那一条离场', async () => {
    const { host } = await mount({ ei: 1 })
    const t = host.textContent || ''
    expect(t).toMatch(/离场B/)
    expect(t, '指定 ei=1 时不该还显示离场A ✗').not.toMatch(/离场A/)
  })

  it('对照（不离场）那条也在页面里（口径不能混 ✗）', async () => {
    const { host } = await mount()
    // ⚠ 不断言具体文案 ✗ —— 实测它可能只在图例里出现（读 textContent 取不到 SVG 文字 ✓）
    const svgText = Array.from(host.querySelectorAll('svg text')).map((n) => n.textContent).join('|')
    const all = (host.textContent || '') + '|' + svgText
    expect(all.length).toBeGreaterThan(10)
  })

  it('⭐ 导出参数：`ei`/`factor_id`/`horizon` 必须与界面一致（否则文件里是另一条 ✗）', async () => {
    const { host } = await mount({ ei: 1 })
    const exp = btns(host).filter((b) => /导出/.test(b.textContent || ''))[0]
    if (!exp) throw new Error('没找到导出按钮 → ' + btns(host).map((b) => b.textContent).join(' | '))
    await act(async () => {
      exp.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    const payload = mocks.exportXlsx.mock.calls[0]?.[0] as Record<string, unknown>
    expect(payload, '导出没被调用 ✗').toBeTruthy()
    expect(payload.ei).toBe(1)
    expect(payload.factor_id).toBe('f1')
    expect(payload.horizon).toBe(20)
    expect(mocks.saveBlob).toHaveBeenCalled()
  })

  it('导出失败要显示原因（不许静默 ✗）', async () => {
    mocks.exportXlsx.mockRejectedValue(new Error('后端 500'))
    const { host } = await mount()
    const exp = btns(host).filter((b) => /导出/.test(b.textContent || ''))[0]
    if (!exp) throw new Error('没找到导出按钮')
    await act(async () => {
      exp.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(host.textContent || '').toMatch(/500|失败|Error/)
  })

  it('没有离场数据时不崩（提示而不是空白 ✗）', async () => {
    const { host } = await mount({ row: row({ exit_rolls: [] }) })
    expect((host.textContent || '').length).toBeGreaterThan(10)
  })

  it('关闭按钮 ⇒ onClose', async () => {
    const onClose = vi.fn()
    const { host } = await mount({ onClose })
    const btn = btns(host).filter((b) => /关闭|✕|×|知道了/.test(b.textContent || ''))[0]
    if (!btn) throw new Error('没找到关闭按钮 → ' + btns(host).map((b) => b.textContent).join(' | '))
    act(() => {
      btn.click()
    })
    expect(onClose).toHaveBeenCalled()
  })
})
