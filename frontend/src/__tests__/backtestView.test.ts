/**
 * `resolveResultView` 单测（★ v2.9.49 ✓）—— 纯函数 ✓，不需要 DOM ✓。
 *
 * ## 这个文件存在的唯一理由：把**两条线上 bugfix 的规则**钉死 ✓
 *
 * 1. **v1.19.39**：partial 的状态**必须查实时任务列表** ✗→✓
 *    （`viewResult` 是点击那一刻的快照 ✓，用户取消后它还写着 `running` ✗
 *    ⇒ 界面一直显示「⏳ 滚动训练进行中」，用户以为没取消掉 ✗）；
 * 2. **v1.19.74**：**归因表对应的任务**要跟"正在显示结果的任务"对齐 ✓
 *    （`partialTaskId` 是 `viewResult` 优先 ✓，而结果/产物是"实时成功任务优先" ✓
 *    ⇒ 两者不一致时表格会来自另一个任务 ✗ —— 用户 2026-09-16 见过"未在公式库中找到的因子"✓）。
 *
 * 这两条写在注释里没人会记得 ✓，写成断言就会一直守着 ✓。
 */
import { describe, expect, it } from 'vitest'

import { resolveResultView, type TaskLike } from '../logic/backtestView'

function t(id: string, status: string, over: Partial<TaskLike> = {}): TaskLike {
  return { task_id: id, status, ...over }
}

const NAV = [{ date: '2024-01-02', value: 1, benchmark: 1 }]

describe('resolveResultView（纯逻辑）', () => {
  it('完整成功任务 ⇒ r / shownTaskId 就位，partial 为空', () => {
    const task = t('A', 'success', { result: { nav: NAV } })
    const v = resolveResultView({
      task, tasks: [task], viewResult: null, viewArtifacts: null, artifacts: { x: 1 },
    })
    expect(v.shouldRender).toBe(true)
    expect(v.r).toEqual({ nav: NAV })
    expect(v.shownTaskId).toBe('A')
    expect(v.shownRunning).toBe(false)
    expect(v.partial).toBeNull()
    expect(v.a).toEqual({ x: 1 })
  })

  it('⭐ v1.19.39：快照说 running、但实时列表里已没有 ⇒ 视为 cancelled（不能一直显示"进行中"）', () => {
    const v = resolveResultView({
      task: null,
      tasks: [],                                   // ← 已被"刷新"清掉 ✓
      viewResult: { task_id: 'B', partial_result: { nav: NAV } },   // ← 旧快照 ✗
      viewArtifacts: null,
      artifacts: null,
    })
    expect(v.partialTaskId).toBe('B')
    expect(v.partialStatus).toBe('cancelled')
    expect(v.partialRunning).toBe(false)
  })

  it('实时列表里确实是 running ⇒ partialStatus=running（这时才显示"进行中"✓）', () => {
    const v = resolveResultView({
      task: null,
      tasks: [t('B', 'running')],
      viewResult: { task_id: 'B', partial_result: { nav: NAV } },
      viewArtifacts: null,
      artifacts: null,
    })
    expect(v.partialStatus).toBe('running')
    expect(v.partialRunning).toBe(true)
  })

  it('⭐ v1.19.74：成功任务 A + 历史查看 B ⇒ **shownTaskId 必须是 A**（归因表对齐 ✓）', () => {
    const task = t('A', 'success', { result: { nav: NAV } })
    const v = resolveResultView({
      task,
      tasks: [task],
      viewResult: { task_id: 'B', result: { nav: NAV } },
      viewArtifacts: null,
      artifacts: null,
    })
    expect(v.shownTaskId).toBe('A')       // 结果/产物对应的任务 ✓
    expect(v.partialTaskId).toBe('B')     // partial 仍是 viewResult 优先 ✓（两条口径本来就不一样 ✓）
  })

  it('v1.20.50：有实时任务就用它的产物；没有实时任务才用历史查看的产物', () => {
    const withTask = resolveResultView({
      task: t('A', 'running'),
      tasks: [t('A', 'running')],
      viewResult: null,
      viewArtifacts: { v: 'history' },
      artifacts: { v: 'live' },
    })
    expect(withTask.a).toEqual({ v: 'live' })

    const withoutTask = resolveResultView({
      task: null, tasks: [], viewResult: null,
      viewArtifacts: { v: 'history' }, artifacts: { v: 'live' },
    })
    expect(withoutTask.a).toEqual({ v: 'history' })
  })

  it('partial 的来源：运行中用实时任务的，否则用 viewResult 携带的', () => {
    const running = resolveResultView({
      task: t('A', 'running', { partial_result: { nav: NAV, segments_done: 3 } }),
      tasks: [t('A', 'running')], viewResult: null,
      viewArtifacts: null, artifacts: null,
    })
    expect(running.partial?.segments_done).toBe(3)

    const stopped = resolveResultView({
      task: t('A', 'failed'),
      tasks: [t('A', 'failed')],
      viewResult: { task_id: 'A', partial_result: { nav: NAV, segments_done: 7 } },
      viewArtifacts: null, artifacts: null,
    })
    expect(stopped.partial?.segments_done).toBe(7)
  })

  it('shouldRender：只有产物 / 只有 partial nav 也要渲染；三样都没有才不渲染', () => {
    const onlyArtifacts = resolveResultView({
      task: null, tasks: [], viewResult: null, viewArtifacts: { v: 1 }, artifacts: null,
    })
    expect(onlyArtifacts.shouldRender).toBe(true)

    const onlyPartialNav = resolveResultView({
      task: t('A', 'running', { partial_result: { nav: NAV } }),
      tasks: [t('A', 'running')], viewResult: null, viewArtifacts: null, artifacts: null,
    })
    expect(onlyPartialNav.shouldRender).toBe(true)

    const empty = resolveResultView({
      task: null, tasks: [], viewResult: null, viewArtifacts: null, artifacts: null,
    })
    expect(empty.shouldRender).toBe(false)
    expect(empty.r).toBeNull()
    expect(empty.partial).toBeNull()
  })

  it('⭐ 历史回测表格的**两个位置**：内部 / 外部，互斥且不自相矛盾', () => {
    // ① 有完整结果、没有 partial ⇒ 表格画在**结果区内部** ✓，外部不画 ✓
    const full = resolveResultView({
      task: t('A', 'success', { result: { nav: NAV } }),
      tasks: [t('A', 'success')], viewResult: null, viewArtifacts: null, artifacts: null,
    })
    expect(full.showHistoryInResult).toBe(true)
    expect(full.showHistoryStandalone).toBe(false)

    // ② 运行中且有 partial ⇒ **内部不画**（partial 图占了位置 ✓，再画一份会重复 ✗），
    //    但**外部仍画** ✓ —— 因为此时还没有"结果"，用户仍需要历史表格去看/续测别的任务 ✓
    //    （⚠ 我第一版断言"两处都不画"是错的 ✗，与原来的行为不符 ✓ —— 断言把它抓出来了 ✓）
    const running = resolveResultView({
      task: t('A', 'running', { partial_result: { nav: NAV } }),
      tasks: [t('A', 'running')], viewResult: null, viewArtifacts: null, artifacts: null,
    })
    expect(running.showHistoryInResult).toBe(false)
    expect(running.showHistoryStandalone).toBe(true)

    // ③ 查看"未完成但已跑段"的历史任务 ⇒ 仍画在**内部** ✓（便于续测/看其他任务 ✓）
    const viewing = resolveResultView({
      task: null, tasks: [],
      viewResult: { task_id: 'B', partial_result: { nav: NAV } },
      viewArtifacts: null, artifacts: null,
    })
    expect(viewing.showHistoryInResult).toBe(true)
  })

  it('⭐ 既无结果也无产物 ⇒ 结果区不渲染，但历史表格**要单独渲染**（用于复用旧参数 ✓）', () => {
    const v = resolveResultView({
      task: null, tasks: [], viewResult: null, viewArtifacts: null, artifacts: null,
    })
    expect(v.shouldRender).toBe(false)
    expect(v.showHistoryStandalone).toBe(true)
    expect(v.showHistoryInResult).toBe(true)      // 它本来也在"内部"逻辑里为真 ✓（但外层不渲染 ⇒ 不会重复 ✓）
  })

  it('空 partial.nav 不算"有部分结果"（避免渲染一张空图 ✗）', () => {
    const v = resolveResultView({
      task: t('A', 'running', { partial_result: { nav: [] } }),
      tasks: [t('A', 'running')], viewResult: null, viewArtifacts: null, artifacts: null,
    })
    expect(v.shouldRender).toBe(false)
  })
})
