/**
 * `TaskStatusPanel` 交互测试（★ v2.9.68，2026-10-19 ✓ —— 前端交互补强第一批）。
 *
 * ## 为什么挑它
 *
 * 它是"多任务并行"的唯一界面 ✓：状态、进度、取消、强制停止、续测全在这里 ✓，
 * 而此前**一条测试都没有** ✗。更关键的是它里面有几条**改坏了界面照样能看** 的约定 ✗：
 *
 * 1. **卡片点击 = 查看**，而卡片里的按钮必须 `stopPropagation` ✓ ——
 *    漏了 ⇒ 点「取消」会顺带把这张卡片选中 ✗（界面看起来完全正常，只是行为莫名 ✓）；
 * 2. **「一键取消所有」只在有活跃任务时出现** ✓ —— 常显 ⇒ 用户在没有任务时点它，
 *    会误以为"系统出问题了"✗；
 * 3. **「强制停止」只在 `cancelling` 时出现** ✓ —— 提前出现 ⇒ 用户可能在正常训练时
 *    误杀取数进程，**同时中断其它正在取数的回测** ✗（组件里专门写了警示 ✓）；
 * 4. **「续测」只对 `failed/cancelled` 出现** ✓ —— 对 `success` 出现 ⇒ 用户会试着"续测"已完成的任务 ✗。
 *
 * ⚠ 用 `react-dom/client` + 原生事件 ✓（不引 `@testing-library/react` —— 它是 peer 依赖，
 *   lockfile 里没装它 ✓，见 `modelParamsForm.test.tsx` 的说明 ✓）。
 */
// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import TaskStatusPanel from '../components/TaskStatusPanel'
import type { BacktestTask } from '../types'

function task(over: Partial<BacktestTask> = {}): BacktestTask {
  // ⚠ 用 `as unknown as BacktestTask` ✓：这里只关心界面用到的字段 ✓，
  //   把 `BacktestTask` 的全部字段抄一遍只会让测试在类型加字段时碎掉 ✗。
  return {
    task_id: 'T1',
    status: 'running',
    progress: 42,
    message: '训练中…',
    ...over,
  } as unknown as BacktestTask
}

function renderInto(ui: React.ReactElement): HTMLElement {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(ui)
  })
  return host
}

function buttons(el: HTMLElement, label: string): HTMLButtonElement[] {
  return (Array.from(el.querySelectorAll('button')) as HTMLButtonElement[]).filter(
    (b) => (b.textContent || '').trim() === label,
  )
}

const noop = () => {}

describe('TaskStatusPanel', () => {
  it('标题显示总数，并只在有活跃任务时显示「X 个进行中」与「一键取消所有」', () => {
    const done = renderInto(
      <TaskStatusPanel
        tasks={[task({ task_id: 'A', status: 'success', progress: 100, message: '完成' })]}
        onRefresh={noop}
        onCancelAll={noop}
        onCancelOne={noop}
      />,
    )
    expect(done.textContent).toContain('共 1 个')
    expect(done.textContent).not.toContain('个进行中')
    expect(buttons(done, '一键取消所有')).toHaveLength(0)

    const live = renderInto(
      <TaskStatusPanel
        tasks={[task({ task_id: 'A', status: 'running' }), task({ task_id: 'B', status: 'pending' })]}
        onRefresh={noop}
        onCancelAll={noop}
        onCancelOne={noop}
      />,
    )
    expect(live.textContent).toContain('共 2 个')
    expect(live.textContent).toContain('2 个进行中')
    expect(buttons(live, '一键取消所有')).toHaveLength(1)
  })

  it('点击卡片 ⇒ onSelectTask(task_id)', () => {
    const onSelectTask = vi.fn()
    const el = renderInto(
      <TaskStatusPanel tasks={[task()]} onRefresh={noop} onCancelAll={noop}
        onCancelOne={noop} onSelectTask={onSelectTask} />,
    )
    const card = el.querySelector('.border.rounded-lg') as HTMLElement
    act(() => {
      card.click()
    })
    expect(onSelectTask).toHaveBeenCalledWith('T1')
  })

  it('⭐ 点「取消」只取消，**不得**顺带选中卡片（stopPropagation ✓）', () => {
    const onSelectTask = vi.fn()
    const onCancelOne = vi.fn()
    const el = renderInto(
      <TaskStatusPanel tasks={[task()]} onRefresh={noop} onCancelAll={noop}
        onCancelOne={onCancelOne} onSelectTask={onSelectTask} />,
    )
    const btn = buttons(el, '取消')[0]
    expect(btn).toBeTruthy()
    act(() => {
      btn.click()
    })
    expect(onCancelOne).toHaveBeenCalledWith('T1')
    expect(onSelectTask).not.toHaveBeenCalled()
  })

  it('⭐ 「强制停止」只在 cancelling 时出现，且同样不触发选中', () => {
    const onSelectTask = vi.fn()
    const onForceCancel = vi.fn()

    const running = renderInto(
      <TaskStatusPanel tasks={[task({ status: 'running' })]} onRefresh={noop}
        onCancelAll={noop} onCancelOne={noop} onForceCancel={onForceCancel}
        onSelectTask={onSelectTask} />,
    )
    expect(buttons(running, '强制停止')).toHaveLength(0)

    const cancelling = renderInto(
      <TaskStatusPanel tasks={[task({ status: 'cancelling' })]} onRefresh={noop}
        onCancelAll={noop} onCancelOne={noop} onForceCancel={onForceCancel}
        onSelectTask={onSelectTask} />,
    )
    const f = buttons(cancelling, '强制停止')[0]
    expect(f).toBeTruthy()
    act(() => {
      f.click()
    })
    expect(onForceCancel).toHaveBeenCalledWith('T1')
    expect(onSelectTask).not.toHaveBeenCalled()
  })

  it('⭐ 「续测」只对 failed/cancelled 出现；success 不出现', () => {
    const onResume = vi.fn()
    const el = renderInto(
      <TaskStatusPanel tasks={[task({ status: 'failed' })]} onRefresh={noop}
        onCancelAll={noop} onCancelOne={noop} onResume={onResume} />,
    )
    expect(el.textContent).toContain('未完成')
    const r = buttons(el, '续测')[0]
    expect(r).toBeTruthy()
    act(() => {
      r.click()
    })
    expect(onResume).toHaveBeenCalledWith('T1')

    const ok = renderInto(
      <TaskStatusPanel tasks={[task({ status: 'success', progress: 100, message: '完成' })]}
        onRefresh={noop} onCancelAll={noop} onCancelOne={noop} onResume={onResume} />,
    )
    expect(buttons(ok, '续测')).toHaveLength(0)
  })

  it('进度文案保留一位小数；cancelling 时给出「卡住了怎么办」的提示', () => {
    const el = renderInto(
      <TaskStatusPanel tasks={[task({ progress: 42, message: '训练中…' })]} onRefresh={noop}
        onCancelAll={noop} onCancelOne={noop} />,
    )
    expect(el.textContent).toContain('42.0% - 训练中…')

    const c = renderInto(
      <TaskStatusPanel tasks={[task({ status: 'cancelling', progress: 60, message: '正在取消...' })]}
        onRefresh={noop} onCancelAll={noop} onCancelOne={noop} onForceCancel={noop} />,
    )
    expect(c.textContent).toContain('正在等待当前训练/回测块结束')
    expect(c.textContent).toContain('强制停止')
  })
})
