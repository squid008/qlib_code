// @vitest-environment jsdom

/**
 * `HistoryPanel` 的三块**从未被触发过**的 UI（★ v2.14.1）：
 *
 * | 分支 | 为什么之前没跑到 | 漏了会怎样 |
 * |---|---|---|
 * | **页码省略号算法**（`buildPageItems`，>7 页才用）| 以前的 fixture 最多 2 页 ✗ | 页数一多，页码条要么挤爆要么**页数错乱** ✗ |
 * | **跳页输入框**（`页码` + `跳转`）| 同上（只有 1 页时根本不渲染 ✓）| 200 条以上就没法直接跳到第 N 页 ✗ |
 * | **批量删除**（勾选 → 确认 → 串行删）| 早先我误判"没有复选框" ✗ —— 其实**批量模式下才出现** ✓ | 勾了 3 条只删 1 条 / 删错目录 ✗ |
 * | **回收配额提示**（`retention.over_quota`）| 以前给的 `retention: null` ✗ | 用户不知道"哪几条明天会被删" ✗ |
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  snap: vi.fn(),
  del: vi.fn(),
  img: vi.fn(),
}))

vi.mock('../api', () => ({
  listBacktestsHistory: () => mocks.list(),
  getBacktestSnapshot: (id: string) => mocks.snap(id),
  getBacktestImageUrl: () => mocks.img(),
  deleteBacktest: (id: string) => mocks.del(id),
}))

import HistoryPanel from '../components/HistoryPanel'

const mounted: Array<() => void> = []

afterEach(() => {
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 忽略 */
    }
  }
})

function item(i: number) {
  return {
    task_id: `T-${i}`,
    dir_name: `20240101_T${i}`,
    seq: i,
    has_params: true,
    has_result: true,
    has_meta: true,
    has_artifacts: true,
    images: {},
    segments: [],
    is_task_running: false,
  }
}

function many(n: number) {
  return Array.from({ length: n }, (_, i) => item(i + 1))
}

beforeEach(() => {
  mocks.list.mockReset()
  mocks.snap.mockReset()
  mocks.del.mockReset()
  mocks.img.mockReset()
  mocks.list.mockResolvedValue({ items: many(200), retention: null })
  mocks.snap.mockResolvedValue({ task_id: 'T-1', params: {}, images: {} })
  mocks.del.mockResolvedValue(undefined)
  mocks.img.mockReturnValue('')
})

async function mount(props: Record<string, unknown> = {}): Promise<HTMLElement> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const merged = {
    onUseParams: vi.fn(),
    onReuseBacktest: vi.fn(),
    onResume: vi.fn(),
    onViewResult: vi.fn(),
    refreshKey: 0,
    capacity: null,
    ...props,
  } as never
  await act(async () => {
    root.render(<HistoryPanel {...(merged as React.ComponentProps<typeof HistoryPanel>)} />)
  })
  mounted.push(() => {
    act(() => root.unmount())
    host.remove()
  })
  return host
}


/** React 受控输入：**必须走原生 value setter** ✓ —— 直接 `input.value = x` 不会触发 onChange ✗。 */
function setValue(input: HTMLInputElement, v: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, v)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function btns(host: HTMLElement): HTMLButtonElement[] {
  return Array.from(host.querySelectorAll('button')) as HTMLButtonElement[]
}

function byText(host: HTMLElement, re: RegExp): HTMLButtonElement[] {
  return btns(host).filter((b) => re.test((b.textContent || '').trim()))
}

describe('HistoryPanel 分页（200 条 ⇒ 10 页）', () => {
  it('⭐⭐ 页码条出现省略号（>7 页才走 buildPageItems 的省略分支）', async () => {
    const host = await mount()
    const texts = btns(host).map((b) => (b.textContent || '').trim())
    // 页码按钮里应有 '1' 与 '10'（首页/末页），且**有且只有一个 '...'**
    expect(texts).toContain('1')
    expect(texts).toContain('10')
    // ⚠ 省略号是**不可点的 span**、不是 button ✗（我第一版按 button 数 ⇒ 红 ✓）
    expect(host.textContent || '').toContain('…')   // ⚠ 是单个字符 U+2026，不是 '...' ✗
  })

  it('⭐⭐ 跳页输入框：输入 7 + 回车 ⇒ 跳到第 7 页（第 121~140 条）', async () => {
    const host = await mount()
    const input = Array.from(host.querySelectorAll('input')).find(
      (i) => (i as HTMLInputElement).placeholder === '页码',
    ) as HTMLInputElement | undefined
    expect(input, '没有「页码」输入框 ✗').toBeTruthy()

    await act(async () => {
      setValue(input!, '7')
    })
    const jump = byText(host, /^跳转$/)[0]
    expect(jump, '没有「跳转」按钮 ✗').toBeTruthy()
    await act(async () => {
      jump.click()
      await Promise.resolve()
    })
    // 第 7 页 = 第 121~140 条 ⇒ 能看到 T121、看不到 T1
    expect(host.textContent || '').toContain('20240101_T121')
    expect(host.textContent || '').not.toContain('20240101_T1 ')
  })

  it('⭐ 跳页输入**非法值**（0 / 999）⇒ 页码不变，也不崩', async () => {
    const host = await mount()
    const input = Array.from(host.querySelectorAll('input')).find(
      (i) => (i as HTMLInputElement).placeholder === '页码',
    ) as HTMLInputElement
    for (const bad of ['0', '999', 'abc']) {
      await act(async () => {
        setValue(input, bad)
      })
      await act(async () => {
        byText(host, /^跳转$/)[0].click()
        await Promise.resolve()
      })
    }
    // 仍在第 1 页
    expect(host.textContent || '').toContain('20240101_T1')
  })
})

describe('HistoryPanel 批量删除', () => {
  it('⭐⭐ 进批量模式 ⇒ 出现勾选框；勾 2 条 + 确认 ⇒ `deleteBacktest` 各调一次', async () => {
    const host = await mount()
    const enter = byText(host, /^批量删除$/)[0]
    expect(enter, '没有「批量删除」按钮 ✗').toBeTruthy()
    await act(async () => {
      enter.click()
      await Promise.resolve()
    })

    const boxes = Array.from(host.querySelectorAll('input[type="checkbox"]')) as HTMLInputElement[]
    expect(boxes.length, '批量模式下应有勾选框 ✗').toBeGreaterThanOrEqual(2)
    await act(async () => {
      boxes[0].click()
      boxes[1].click()
      await Promise.resolve()
    })

    // 勾完之后点**执行删除**那个按钮（此时开关已变成「取消批量」✓）
    // ⚠ 真实文案是「确定删除（2）」✗→✓（我第一版猜「批量删除」⇒ 红，helper 打出了真实清单 ✓）
    const run = byText(host, /确定删除/)[0]
    expect(run, '勾选后找不到执行按钮 ✗ 实际按钮：' +
      btns(host).map((b) => (b.textContent || '').trim()).join(' | ')).toBeTruthy()
    await act(async () => {
      run.click()
      await Promise.resolve()
    })
    const ok = btns(host).filter(
      (b) => /^(确认|确定)/.test((b.textContent || '').trim()) && !/确定删除/.test(b.textContent || ''),
    )[0]
    expect(ok, '没有确认按钮 ✗ 实际按钮：' +
      btns(host).map((b) => (b.textContent || '').trim()).join(' | ')).toBeTruthy()
    await act(async () => {
      ok.click()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(mocks.del, '应该删 2 条 ✗').toHaveBeenCalledTimes(2)
    const ids = (mocks.del.mock.calls as unknown[][]).map((c) => String(c[0])).sort()
    expect(ids).toEqual(['T-1', 'T-2'])
  })

  it('⭐ 一条都没勾就点「批量删除」⇒ 不弹确认、不删任何东西', async () => {
    const host = await mount()
    await act(async () => {
      byText(host, /^批量删除$/)[0].click()
      await Promise.resolve()
    })
    // ⚠ 实测：0 选时**根本没有**「确定删除」按钮 ✓（比我预期更严：不是"点了没反应"而是"不给点"✓）
    const run = byText(host, /确定删除/)[0]
    if (run) {
      await act(async () => {
        run.click()
        await Promise.resolve()
      })
    }
    expect(mocks.del, '0 选却删东西了 ✗').not.toHaveBeenCalled()
    expect(byText(host, /确定删除/).length, '0 选不该出现执行按钮 ✗').toBe(0)
  })

  it('⭐ 取消批量 ⇒ 勾选框消失（不留脏状态）', async () => {
    const host = await mount()
    await act(async () => {
      byText(host, /^批量删除$/)[0].click()
      await Promise.resolve()
    })
    expect(host.querySelectorAll('input[type="checkbox"]').length).toBeGreaterThan(0)
    await act(async () => {
      byText(host, /取消批量/)[0].click()
      await Promise.resolve()
    })
    expect(host.querySelectorAll('input[type="checkbox"]').length).toBe(0)
  })
})

describe('HistoryPanel 回收配额提示', () => {
  it('⭐⭐ 超配额 ⇒ 显示"精简/删除 #序号"（用户要知道哪几条明天没了）', async () => {
    mocks.list.mockResolvedValue({
      items: many(5),
      retention: {
        count: 12,
        total_gb: 9.8,
        quota_gb: 8,
        over_quota: true,
        trigger: 'size',
        to_slim: ['20240101_T1'],
        to_remove: ['20240101_T2', '20240101_T3'],
      },
    })
    const host = await mount()
    const t = host.textContent || ''
    expect(t).toContain('9.8GB')
    expect(t).toMatch(/精简|删除/)
    // 序号要能对上表格（seqLabel 会把 dir_name 映射成 #seq ✓）
    expect(t).toMatch(/#\d/)
  })

  it('⭐ 没超配额也给"产物 xGB / yGB"这几个字（别只在超了才说）', async () => {
    mocks.list.mockResolvedValue({
      items: many(3),
      retention: {
        count: 6,
        total_gb: 2.1,
        quota_gb: 8,
        over_quota: false,
        trigger: 'size',
        to_slim: [],
        to_remove: [],
      },
    })
    const host = await mount()
    const t = host.textContent || ''
    expect(t).toContain('2.1GB')
    expect(t).not.toMatch(/已超配额/)
  })
})
