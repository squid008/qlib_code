import { useEffect, useState } from 'react'
import {
  listBacktests,
  getBacktestSnapshot,
  getBacktestImageUrl,
  type BacktestSnapshot,
} from '../api'
import type { BacktestTask, BacktestRequest } from '../types'

interface Props {
  onUseParams: (params: BacktestRequest, taskId: string) => void
  onReuseBacktest: (params: BacktestRequest, taskId: string) => void
  onViewResult: (taskId: string) => void
}

interface Row {
  taskId: string
  task: BacktestTask
  snapshot?: BacktestSnapshot
}

export default function HistoryPanel({ onUseParams, onReuseBacktest, onViewResult }: Props) {
  const [rows, setRows] = useState<Row[]>([])
  const [loading, setLoading] = useState(false)
  const [viewImg, setViewImg] = useState<{ taskId: string; name: string; url: string } | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const tasks = await listBacktests()
      const taskIds = Object.keys(tasks).filter(
        (id) => tasks[id].status === 'success',
      )
      // 倒序（最新在前）
      const sorted = taskIds.sort((a, b) => (a < b ? 1 : -1))
      const list: Row[] = []
      // 只展示最近 20 个，避免太多请求
      for (const id of sorted.slice(0, 20)) {
        let snapshot: BacktestSnapshot | undefined
        try {
          snapshot = await getBacktestSnapshot(id)
        } catch {
          snapshot = undefined
        }
        list.push({ taskId: id, task: tasks[id], snapshot })
      }
      setRows(list)
    } catch {
      setRows([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const handleUse = (row: Row) => {
    if (row.snapshot?.params) {
      onUseParams(row.snapshot.params, row.taskId)
    }
  }

  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">历史回测</h2>
        <button
          onClick={load}
          disabled={loading}
          className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50 disabled:opacity-50"
        >
          {loading ? '加载中...' : '刷新'}
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-slate-400 py-6 text-center">暂无历史回测</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-slate-500">
                <th className="py-2 pr-3">产物目录</th>
                <th className="py-2 pr-3">模型</th>
                <th className="py-2 pr-3">区间</th>
                <th className="py-2 pr-3 text-right">资金(万)</th>
                <th className="py-2 pr-3 text-right">年化</th>
                <th className="py-2 pr-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const p = row.snapshot?.params
                const navImg = row.snapshot?.images?.nav_curve
                  ? getBacktestImageUrl(row.taskId, 'nav_curve.png')
                  : null
                return (
                  <tr key={row.taskId} className="border-b border-slate-100 dark:border-slate-700">
                    <td className="py-2 pr-3 font-mono text-xs" title={row.taskId}>
                      {row.snapshot?.dir_name || row.taskId}
                    </td>
                    <td className="py-2 pr-3">{p?.model || '-'}</td>
                    <td className="py-2 pr-3">
                      {p ? `${p.start_date} ~ ${p.end_date}` : '-'}
                    </td>
                    <td className="py-2 pr-3 text-right">
                      {p ? ((p.initial_capital || 0) / 10000).toLocaleString() : '-'}
                    </td>
                    <td className="py-2 pr-3 text-right">
                      {row.task.result?.annualized_return !== null &&
                      row.task.result?.annualized_return !== undefined
                        ? `${(row.task.result.annualized_return * 100).toFixed(2)}%`
                        : '-'}
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      <div className="flex gap-1">
                        <button
                          onClick={() => onViewResult(row.taskId)}
                          className="px-2 py-0.5 rounded text-xs border border-slate-300 text-slate-600 hover:bg-slate-50 dark:text-slate-300"
                          title="查看该次回测的曲线/调仓/训练产物"
                        >
                          查看
                        </button>
                        <button
                          onClick={() => onReuseBacktest(row.snapshot?.params!, row.taskId)}
                          disabled={!row.snapshot?.params}
                          className="px-2 py-0.5 rounded text-xs bg-green-600 text-white hover:bg-green-700 disabled:opacity-40"
                          title="直接用该次回测的参数和模型权重开始回测（复用模型，不重新训练）"
                        >
                          复用回测
                        </button>
                        <button
                          onClick={() => handleUse(row)}
                          disabled={!row.snapshot?.params}
                          className="px-2 py-0.5 rounded text-xs bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
                        >
                          复用参数
                        </button>
                        {navImg && (
                          <button
                            onClick={() =>
                              setViewImg({ taskId: row.taskId, name: 'nav_curve.png', url: navImg })
                            }
                            className="px-2 py-0.5 rounded text-xs border text-blue-600 hover:bg-blue-50"
                          >
                            曲线
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 图片预览弹窗 */}
      {viewImg && (
        <div
          className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-6"
          onClick={() => setViewImg(null)}
        >
          <div
            className="bg-white dark:bg-slate-800 rounded-xl p-4 max-w-4xl w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold">{viewImg.name}</h3>
              <button
                onClick={() => setViewImg(null)}
                className="text-slate-400 hover:text-slate-600 text-xl"
              >
                ×
              </button>
            </div>
            <img src={viewImg.url} alt={viewImg.name} className="w-full rounded" />
          </div>
        </div>
      )}
    </section>
  )
}
