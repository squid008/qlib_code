import type { BacktestTask } from '../types'

interface TaskStatusPanelProps {
  tasks: BacktestTask[]
  onRefresh: () => void
  onCancelAll: () => void
  onCancelOne: (taskId: string) => void
  // 断点续跑：未完成（失败/已停止）的滚动回测，hover 显示"续测"按钮
  onResume?: (taskId: string) => void
}

// 任务状态区：多任务并行显示（每任务一张卡片：状态+进度条+单独取消），支持刷新/一键取消
export default function TaskStatusPanel({
  tasks,
  onRefresh,
  onCancelAll,
  onCancelOne,
  onResume,
}: TaskStatusPanelProps) {
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
              className={`border rounded-lg p-3 ${
                isActive ? 'border-blue-200 bg-blue-50/30 dark:bg-blue-900/10' : 'border-slate-200'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm text-slate-600 truncate" title={t.display_name || t.task_id}>
                    {t.display_name || t.task_id}
                  </span>
                  {t.status === 'failed' || t.status === 'cancelled' ? (
                    // 未完成（失败/已停止）：正常显示"未完成"，鼠标悬停变成"续测"按钮（滚动回测可断点续跑）
                    <span className="group relative inline-block shrink-0">
                      <span className="px-2 py-0.5 rounded text-xs bg-gray-200 text-gray-700 group-hover:invisible">
                        未完成
                      </span>
                      <button
                        onClick={() => onResume?.(t.task_id)}
                        disabled={!onResume}
                        className="px-2 py-0.5 rounded text-xs bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 invisible group-hover:visible absolute left-0 top-0"
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
                  <button
                    onClick={() => onCancelOne(t.task_id)}
                    className="px-3 py-1 rounded text-xs bg-red-600 text-white hover:bg-red-700"
                  >
                    取消
                  </button>
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
                    （正在等待当前训练/回测块结束，训练块完成后会停止）
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
