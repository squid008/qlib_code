import { useMemo } from 'react'

import type { BacktestTask } from '../types'

/**
 * 任务卡片上的四个动作（★ v2.9.48，2026-10-19 ✓ —— 从 `App.tsx` 抽出的**逻辑**，不是 JSX）。
 *
 * ## 为什么抽这个（而不是再拆一块 JSX ✗）
 *
 * 「进度：多任务并行显示」那块**早就是组件了**（`TaskStatusPanel` ✓）——
 * 真正占地方的是 `App.tsx` 里**内联的四个处理函数**（约 78 行 ✓）：刷新清理 ✓、
 * 全部取消 ✓、单个取消 ✓、强制停止 ✓。它们和 JSX 无关 ✓、只跟状态和 API 有关 ✓
 * ⇒ 适合搬到 hook 里 ✓（组件继续只负责画 ✓）。
 *
 * ## 四条行为，必须**逐条保住** ✗（都是线上踩过的 ✓）
 *
 * 1. **刷新 = 把已结束的任务"标记为已清理"并移出列表** ✓ —— 结束态包括
 *    `success / failed / cancelled` ✓（少一个就会出现"点了清理还留着"✗）；
 *    并且**同时写 ref 与 localStorage** ✓（否则刷新页面，已清理的结果又"复活"✗）；
 * 2. **取消全部** ✓：只取消 `running / pending / cancelling` ✓；
 *    单个失败**不许**阻塞其他 ✓（`Promise.all` + 每个都 try ✓）；
 * 3. **乐观更新** ✓：受理后立刻把状态改成 `cancelling` + 文案「正在取消...」✗→✓
 *    （不这么做，用户点完要等下一个轮询周期才看到变化 ✓）；
 * 4. **强制停止** ✓：`killed_workers` 为空时要给出**可读解释** ✓
 *    （"未发现活动的取数进程（可能已自行结束）"✓），失败则把原因写进 `setError` ✓。
 */

export interface TaskActionsDeps {
  tasks: BacktestTask[]
  setTasks: (updater: (prev: BacktestTask[]) => BacktestTask[]) => void
  setError: (msg: string) => void
  /** 已清理任务 id 的**即时**副本 ✓（state 更新是异步的 ✗，取消时必须以 ref 为准 ✓） */
  clearedTaskIdsRef: { current: Set<string> }
  persistCleared: (next: Set<string>) => void
  setClearedTaskIds: (next: Set<string>) => void
  cancelBacktest: (taskId: string) => Promise<unknown>
  forceCancelBacktest: (taskId: string) => Promise<{ killed_workers: unknown[] }>
}

/** 结束态 ✓（点"清理"时会被移出列表 ✓） */
const FINISHED = ['success', 'failed', 'cancelled']
/** 活跃态 ✓（"取消全部"只针对它们 ✓） */
const ACTIVE = ['running', 'pending', 'cancelling']

export function useTaskActions(deps: TaskActionsDeps) {
  const {
    tasks, setTasks, setError, clearedTaskIdsRef,
    persistCleared, setClearedTaskIds, cancelBacktest, forceCancelBacktest,
  } = deps

  return useMemo(() => {
    /** 刷新：把已结束的任务标记为已清理并移出列表 ✓ */
    const onRefresh = () => {
      const removed = tasks.filter((t) => FINISHED.includes(t.status))
      const next = new Set(clearedTaskIdsRef.current)
      removed.forEach((t) => next.add(t.task_id))
      setClearedTaskIds(next)
      persistCleared(next)
      setTasks((prev) => prev.filter((t) => !FINISHED.includes(t.status)))
    }

    /** 取消全部活跃任务 ✓（单个失败不阻塞其他 ✓） */
    const onCancelAll = async () => {
      const active = tasks.filter((t) => ACTIVE.includes(t.status))
      await Promise.all(
        active.map(async (t) => {
          try {
            await cancelBacktest(t.task_id)
          } catch {
            /* 单个失败不阻塞其他 ✓（用户的诉求是"尽量都停"✓） */
          }
        }),
      )
      // 乐观更新 ✓：立即标记为 cancelling（等轮询会有 1~2 秒空窗 ✗）
      setTasks((prev) =>
        prev.map((t) =>
          t.status === 'running' || t.status === 'pending'
            ? { ...t, status: 'cancelling' as const, message: '正在取消...' }
            : t,
        ),
      )
    }

    /** 单个取消 ✓ */
    const onCancelOne = async (taskId: string) => {
      try {
        await cancelBacktest(taskId)
      } catch {
        /* 单个任务取消失败不阻塞其他 ✓ */
      }
      setTasks((prev) =>
        prev.map((t) =>
          t.task_id === taskId && t.status !== 'cancelling'
            ? { ...t, status: 'cancelling' as const, message: '正在取消...' }
            : t,
        ),
      )
    }

    /** 强制停止 ✓（普通取消失效时用：卡在取数进程里，CPU/磁盘都掉到 0 ✓） */
    const onForceCancel = async (taskId: string) => {
      try {
        const r = await forceCancelBacktest(taskId)
        setTasks((prev) =>
          prev.map((t) =>
            t.task_id === taskId
              ? {
                ...t,
                status: 'cancelling' as const,
                message: '强制停止中（正在终止取数进程）...',
              }
              : t,
          ),
        )
        setError(
          r.killed_workers.length
            ? ''
            : '已强制停止：未发现活动的取数进程（可能已自行结束），任务已直接标记为已停止',
        )
      } catch (e) {
        setError(`强制停止失败：${e instanceof Error ? e.message : String(e)}`)
      }
    }

    return { onRefresh, onCancelAll, onCancelOne, onForceCancel }
    // ⚠ 依赖里带上 `tasks` ✓（处理函数是"当前列表的快照"✓ —— 与原来的内联写法一致 ✓）
  }, [tasks, setTasks, setError, clearedTaskIdsRef,
    persistCleared, setClearedTaskIds, cancelBacktest, forceCancelBacktest])
}
