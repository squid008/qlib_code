import { useState } from 'react'
import type { BacktestTask } from '../types'

interface TaskStatusPanelProps {
  tasks: BacktestTask[]
  onRefresh: () => void
  onCancelAll: () => void
  onCancelOne: (taskId: string) => void
  /** 强制停止（v1.19.35）：普通取消是协作式的，卡在取数进程里时无效 ⇒ 额外杀掉取数 worker。
   *  仅当任务停在「cancelling」时才显示该按钮（避免误伤同进程内其它取数中的回测）。 */
  onForceCancel?: (taskId: string) => void
  // 断点续跑：未完成（失败/已停止）的滚动回测，hover 显示"续测"按钮
  onResume?: (taskId: string) => void
  // 点击任务卡片：选中并展示该任务的曲线/已跑段（类似历史"查看"），再点取消展示
  onSelectTask?: (taskId: string) => void
  // 当前选中的任务 ID（用于卡片高亮）
  selectedTaskId?: string | null
}

// 任务状态区：多任务并行显示（每任务一张卡片：状态+进度条+单独取消），支持刷新/一键取消
export default function TaskStatusPanel({
  tasks,
  onRefresh,
  onCancelAll,
  onCancelOne,
  onForceCancel,
  onResume,
  onSelectTask,
  selectedTaskId,
}: TaskStatusPanelProps) {
  // 记录悬停的任务 ID，用 inline style 控制"续测"按钮显隐（不依赖 Tailwind hover 变体）
  const [hoverId, setHoverId] = useState<string | null>(null)
  const activeCount = tasks.filter(
    (t) => t.status === 'running' || t.status === 'pending' || t.status === 'cancelling',
  ).length
  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold">
            任务状态
            <span className="text-sm text-slate-400 ml-2">
              （共 {tasks.length} 个
              {activeCount > 0 && `，${activeCount} 个进行中`}）
            </span>
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={onRefresh}
              className="px-3 py-1 rounded text-xs bg-slate-500 text-white hover:bg-slate-600"
            >
              刷新
            </button>
            <span className="text-xs text-slate-400">
              请注意：点击后会清除完成、失败、已停止的任务状态
            </span>
          </div>
        </div>
        {activeCount > 0 && (
          <button
            onClick={onCancelAll}
            className="px-3 py-1 rounded text-xs bg-red-600 text-white hover:bg-red-700"
          >
            一键取消所有
          </button>
        )}
      </div>
      <div className="space-y-3">
        {tasks.map((t) => {
          const isActive = t.status === 'running' || t.status === 'pending' || t.status === 'cancelling'
          return (
            <div
              key={t.task_id}
              onClick={() => onSelectTask?.(t.task_id)}
              title="点击查看该任务的曲线/已跑段，再点一次取消"
              className={`border rounded-lg p-3 cursor-pointer transition-colors ${
                selectedTaskId === t.task_id
                  ? 'border-blue-500 ring-1 ring-blue-400'
                  : isActive
                    ? 'border-blue-200 bg-blue-50/30 dark:bg-blue-900/10'
                    : 'border-slate-200'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm text-slate-600 truncate" title={t.display_name || t.task_id}>
                    {t.display_name || t.task_id}
                  </span>
                  {t.status === 'failed' || t.status === 'cancelled' ? (
                    // 未完成（失败/已停止）：默认显示"未完成"，鼠标悬停变为"续测"按钮（滚动回测可断点续跑）。
                    // 用 React state + inline style 控制显隐，不依赖 Tailwind hover 变体（当前环境不生成）。
                    <span
                      className="relative inline-block shrink-0 py-1 pl-1 -mr-1"
                      onMouseEnter={() => setHoverId(t.task_id)}
                      onMouseLeave={() => setHoverId(null)}
                    >
                      <span
                        className="px-2 py-0.5 rounded text-xs bg-gray-200 text-gray-700 transition-opacity duration-100"
                        style={{ opacity: hoverId === t.task_id ? 0 : 1 }}
                      >
                        未完成
                      </span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          onResume?.(t.task_id)
                        }}
                        className="absolute left-0 top-0 px-2 py-0.5 rounded text-xs bg-emerald-600 text-white transition-opacity duration-100"
                        style={{ opacity: hoverId === t.task_id ? 1 : 0 }}
                        title="从断点继续滚动回测（跳过已完成段）"
                      >
                        续测
                      </button>
                    </span>
                  ) : (
                    <span
                      className={`px-2 py-0.5 rounded text-xs shrink-0 ${
                        t.status === 'success'
                          ? 'bg-green-100 text-green-700'
                          : t.status === 'cancelling'
                            ? 'bg-orange-100 text-orange-700'
                            : 'bg-blue-100 text-blue-700'
                      }`}
                    >
                      {t.status}
                    </span>
                  )}
                </div>
                {isActive && (
                  <div className="flex items-center gap-2 shrink-0">
                    {/* 强制停止（v1.19.35）：只在「取消后仍停在 cancelling」时出现 ——
                        普通取消是协作式的（只在阶段边界检查），任务卡在 joblib/loky 取数内部
                        （实测：多任务并发共享同一 loky 池导致死锁，CPU/磁盘都为 0）时永远等不到
                        检查点。此按钮会杀掉取数 worker 让任务立刻收尾。
                        ⚠ 会同时中断同进程内其它正在取数的回测 ⇒ 故给明确警示文案。 */}
                    {t.status === 'cancelling' && onForceCancel && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          onForceCancel(t.task_id)
                        }}
                        className="px-3 py-1 rounded text-xs bg-orange-600 text-white hover:bg-orange-700"
                        title={
                          '强制停止：杀掉该进程里卡住的取数进程并重置进程池，任务**立刻**标记为已停止（不再等线程响应）。\n' +
                          '适用于「点了取消但长时间不动」的情况（卡在 joblib/loky 取数内部，此时 CPU 与磁盘都是 0）。\n' +
                          '⚠ 取数进程池是同进程共享的 ⇒ 会一并中断其它正在取数的回测。'
                        }
                      >
                        强制停止
                      </button>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        onCancelOne(t.task_id)
                      }}
                      className="px-3 py-1 rounded text-xs bg-red-600 text-white hover:bg-red-700"
                    >
                      取消
                    </button>
                  </div>
                )}
              </div>
              <div className="w-full bg-slate-200 rounded-full h-3">
                <div
                  className={`h-3 rounded-full transition-all ${
                    t.status === 'failed'
                      ? 'bg-red-500'
                      : t.status === 'cancelled'
                        ? 'bg-gray-400'
                        : 'bg-blue-600'
                  }`}
                  style={{ width: `${t.progress}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {t.progress.toFixed(1)}% - {t.message}
                {t.status === 'cancelling' && (
                  <span className="ml-1 text-orange-600">
                    （正在等待当前训练/回测块结束，训练块完成后会停止；若长时间不动 —— 多半卡在取数进程里
                    —— 用上方「强制停止」）
                  </span>
                )}
              </p>
            </div>
          )
        })}
      </div>
    </section>
  )
}
