// @vitest-environment jsdom
/**
 * `useTaskActions` 单测（★ v2.9.48 ✓ —— 从 `App.tsx` 抽出的**任务卡片动作** ✓）。
 *
 * ## 为什么这四个动作值得单独测 ✓
 *
 * 它们此前是 `App.tsx` 里的**内联闭包** ✗ ⇒ 一行测试都没有 ✓，而它们管的是
 * "**清理结果**"和"**取消任务**"—— 两类操作都**直接动用户看到的东西** ✓：
 *
 * 1. 刷新清理漏掉某个结束态（比如 `cancelled`）⇒ 用户点了清理、卡片还在 ✗；
 *    忘了写 `localStorage` ⇒ **刷新页面后已清理的结果又复活** ✗；
 * 2. 取消全部时，**一个任务失败必须不影响其他** ✓（用户的诉求是"尽量都停"✓）；
 * 3. 强制停止在"没找到取数进程"时必须给**可读解释** ✓，否则用户看到空白提示 ✗。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import { useTaskActions, type TaskActionsDeps } from '../hooks/useTaskActions'
import type { BacktestTask } from '../types'

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

/** 只用来调 hook 的空壳组件 ✓（hook 必须在组件里调用 ✓） */
function Probe({ deps, onReady }: { deps: TaskActionsDeps; onReady: (a: ReturnType<typeof useTaskActions>) => void }) {
  onReady(useTaskActions(deps))
  return null
}

function mk(status: BacktestTask['status'], id: string): BacktestTask {
  return { task_id: id, status, progress: 0 } as unknown as BacktestTask
}

function setup(tasks: BacktestTask[], over: Partial<TaskActionsDeps> = {}) {
  let current = [...tasks]
  const applied: BacktestTask[][] = []
  const deps: TaskActionsDeps = {
    tasks,
    setTasks: (up) => {
      current = up(current)
      applied.push(current)
    },
    setError: vi.fn(),
    clearedTaskIdsRef: { current: new Set<string>() },
    persistCleared: vi.fn(),
    setClearedTaskIds: vi.fn(),
    cancelBacktest: vi.fn(async () => ({})),
    forceCancelBacktest: vi.fn(async () => ({ killed_workers: [] })),
    ...over,
  }
  let actions: ReturnType<typeof useTaskActions> | null = null
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root = createRoot(el)
  act(() => {
    root.render(<Probe deps={deps} onReady={(a) => { actions = a }} />)
  })
  mounted.push(() => {
    act(() => root.unmount())
    el.remove()
  })
  return { deps, applied, actions: actions as unknown as ReturnType<typeof useTaskActions> }
}

describe('useTaskActions（jsdom）', () => {
  it('⭐ 刷新清理：三种结束态都要移出，活跃态保留', () => {
    const { deps, applied, actions } = setup([
      mk('success', 'a'), mk('failed', 'b'), mk('cancelled', 'c'),
      mk('running', 'd'), mk('pending', 'e'), mk('cancelling', 'f'),
    ])
    actions.onRefresh()
    expect(applied[0].map((t) => t.task_id)).toEqual(['d', 'e', 'f'])
    const persisted = (deps.persistCleared as unknown as { mock: { calls: [Set<string>][] } })
      .mock.calls[0][0]
    expect([...persisted].sort()).toEqual(['a', 'b', 'c'])
    expect(deps.setClearedTaskIds).toHaveBeenCalled()
  })

  it('⭐ 刷新清理要把 id **写进 ref**（否则刷新页面会复活 ✗）', () => {
    const ref = { current: new Set<string>(['old']) }
    const { deps, actions } = setup([mk('success', 'a')], { clearedTaskIdsRef: ref })
    actions.onRefresh()
    const persisted = (deps.persistCleared as unknown as { mock: { calls: [Set<string>][] } })
      .mock.calls[0][0]
    expect([...persisted].sort()).toEqual(['a', 'old'])   // 旧的必须还在 ✓
  })

  it('取消全部：只对活跃态发请求，且一个失败不影响其他 ✓', async () => {
    const cancel = vi.fn(async (id: string) => {
      if (id === 'a') throw new Error('boom')
      return {}
    })
    const { applied, actions } = setup(
      [mk('running', 'a'), mk('pending', 'b'), mk('success', 'c')],
      { cancelBacktest: cancel as unknown as TaskActionsDeps['cancelBacktest'] })

    await act(async () => {
      await actions.onCancelAll()
    })
    expect(cancel.mock.calls.map((c) => c[0]).sort()).toEqual(['a', 'b'])   // c 不该被取消 ✓
    expect(applied[0].find((t) => t.task_id === 'a')?.status).toBe('cancelling')
    expect(applied[0].find((t) => t.task_id === 'b')?.status).toBe('cancelling')
    expect(applied[0].find((t) => t.task_id === 'c')?.status).toBe('success')
  })

  it('单个取消：失败也不抛，且仍标记 cancelling ✓', async () => {
    const cancel = vi.fn(async () => { throw new Error('nope') })
    const { applied, actions } = setup([mk('running', 'x')],
      { cancelBacktest: cancel as unknown as TaskActionsDeps['cancelBacktest'] })
    await act(async () => {
      await actions.onCancelOne('x')
    })
    expect(cancel).toHaveBeenCalledWith('x')
    expect(applied[0][0].status).toBe('cancelling')
  })

  it('⭐ 强制停止：没找到取数进程时给可读解释（不是空白 ✗）', async () => {
    const setError = vi.fn()
    const { actions } = setup([mk('running', 'x')], {
      setError,
      forceCancelBacktest: vi.fn(async () => ({ killed_workers: [] })) as unknown as TaskActionsDeps['forceCancelBacktest'],
    })
    await act(async () => {
      await actions.onForceCancel('x')
    })
    expect(setError).toHaveBeenCalledWith(
      expect.stringContaining('未发现活动的取数进程'))
  })

  it('强制停止成功（杀到了进程）⇒ 清空错误提示 ✓；抛错 ⇒ 写原因 ✓', async () => {
    const setError = vi.fn()
    const ok = setup([mk('running', 'x')], {
      setError,
      forceCancelBacktest: vi.fn(async () => ({ killed_workers: [1] })) as unknown as TaskActionsDeps['forceCancelBacktest'],
    })
    await act(async () => {
      await ok.actions.onForceCancel('x')
    })
    expect(setError).toHaveBeenCalledWith('')

    const bad = setup([mk('running', 'y')], {
      setError,
      forceCancelBacktest: vi.fn(async () => { throw new Error('权限不足') }) as unknown as TaskActionsDeps['forceCancelBacktest'],
    })
    await act(async () => {
      await bad.actions.onForceCancel('y')
    })
    expect(setError).toHaveBeenCalledWith(expect.stringContaining('强制停止失败'))
    expect(setError).toHaveBeenCalledWith(expect.stringContaining('权限不足'))
  })
})
