import { describe, expect, it, vi } from 'vitest'

import { isTerminal, pollOneTask, TASK_LOST_MESSAGE } from '../taskPoll'
import type { BacktestTask } from '../types'

/**
 * 任务轮询的纯逻辑（★ v2.11.10 —— 从 `App.tsx` 抽出后第一次能测）。
 *
 * ## 这里钉的是**两个真实的线上坑**
 *
 * ### ① 404 ⇒ 卡片冻结在最后一帧（v1.19.10）
 * 后端重启会**清空内存任务表** ⇒ 轮询拿到 404 ✓。原写法直接 `return t` ✗
 * ⇒ 卡片停在最后那一帧进度 ✓，用户看到「**初始化 Qlib 3% 特别慢**」✓
 * —— 其实是任务早被后端杀掉了 ✓（真因 ✓）。
 *
 * ### ② `cancelling` 不是终态
 * 用户点了取消 ⇒ 后端状态是 `cancelling` ✓（**还在收尾** ✓）。
 * 若把它当终态 ⇒ 停止轮询 ⇒ **卡片永远停在"取消中…"** ✗
 * 而其实几秒后就该变成 `cancelled` ✓。
 */

const T = (over: Partial<BacktestTask> = {}): BacktestTask =>
  ({
    task_id: 'T1',
    status: 'running',
    progress: 42,
    message: '初始化 Qlib',
    created_at: '2024-01-02T00:00:00',
    ...over,
  }) as unknown as BacktestTask

describe('isTerminal', () => {
  it('三种终态 ✓', () => {
    expect(isTerminal('success')).toBe(true)
    expect(isTerminal('failed')).toBe(true)
    expect(isTerminal('cancelled')).toBe(true)
  })

  it('⭐⭐ `cancelling` **不是**终态（后端还在收尾，停止轮询会让卡片卡在"取消中…" ✗）', () => {
    expect(isTerminal('cancelling')).toBe(false)
  })

  it('运行中/排队都不算终态 ✓', () => {
    expect(isTerminal('running')).toBe(false)
    expect(isTerminal('queued')).toBe(false)
  })
})

describe('pollOneTask', () => {
  it('⭐ 已是终态 ⇒ **不发请求**（省一次无意义的往返 ✓）', async () => {
    const get = vi.fn()
    const got = await pollOneTask(T({ status: 'success' }), get)
    expect(get).not.toHaveBeenCalled()
    expect(got.status).toBe('success')
  })

  it('正常运行 ⇒ 返回后端的最新对象 ✓', async () => {
    const latest = T({ status: 'running', progress: 77 })
    const got = await pollOneTask(T(), async () => latest)
    expect(got).toBe(latest)
  })

  it('⭐⭐⭐ 404（后端重启清空了任务表）⇒ 标成 `cancelled` 并写清原因', async () => {
    const err = { response: { status: 404 } }
    const got = await pollOneTask(T({ status: 'running', progress: 3 }), async () => {
      throw err
    })
    expect(got.status, '404 必须终止轮询 ✓（否则卡片永远冻结在最后一帧 ✗）').toBe('cancelled')
    expect(got.message).toBe(TASK_LOST_MESSAGE)
    // ⚠ 文案必须**告诉用户产物还在** ✓ —— 否则用户以为白跑了 ✗
    expect(got.message).toContain('历史')
  })

  it('⭐⭐ 404 时**不修改入参**（返回新对象，原对象保持原样 ✓）', async () => {
    const orig = T({ progress: 3 })
    await pollOneTask(orig, async () => {
      throw { response: { status: 404 } }
    })
    expect(orig.status, '入参不能被就地改掉 ✓（React state 依赖这个 ✓）').toBe('running')
  })

  it('⭐ 其它错误（500 / 网络抖动）⇒ **原样返回**，不标中止（下一轮自愈 ✓）', async () => {
    const orig = T({ progress: 55 })
    const got500 = await pollOneTask(orig, async () => {
      throw { response: { status: 500 } }
    })
    expect(got500).toBe(orig)
    const gotNet = await pollOneTask(orig, async () => {
      throw new Error('network')
    })
    expect(gotNet).toBe(orig)
  })
})
