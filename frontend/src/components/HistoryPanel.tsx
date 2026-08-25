import { useEffect, useState } from 'react'
import {
  listBacktestsHistory,
  getBacktestSnapshot,
  getBacktestImageUrl,
  deleteBacktest,
  type BacktestSnapshot,
  type HistoryItem,
} from '../api'
import type { BacktestRequest } from '../types'

interface Props {
  onUseParams: (params: BacktestRequest, taskId: string) => void
  onReuseBacktest: (params: BacktestRequest, taskId: string) => void
  onViewResult: (taskId: string) => void
}

interface Row {
  item: HistoryItem
  snapshot?: BacktestSnapshot
  loading: boolean
}

export default function HistoryPanel({ onUseParams, onReuseBacktest, onViewResult }: Props) {
  const [rows, setRows] = useState<Row[]>([])
  const [loading, setLoading] = useState(false)
  const [viewImg, setViewImg] = useState<{ taskId: string; name: string; url: string } | null>(null)
  // 待删除确认（页面内弹窗，兼容内置浏览器不弹 window.confirm）
  const [confirmDel, setConfirmDel] = useState<Row | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 扫描 artifacts 目录下的所有回测产物
  const load = async () => {
    setLoading(true)
    try {
      const { items } = await listBacktestsHistory()
      // 倒序已在后端按目录名字典序倒序（最新在前）
      const list: Row[] = items.map((it) => ({ item: it, loading: true }))
      setRows(list)
      // 并发拉每个条目的 snapshot（拿 params 用于复用）
      await Promise.all(
        list.map(async (r) => {
          try {
            const snap = await getBacktestSnapshot(r.item.task_id)
            r.snapshot = snap
            r.loading = false
          } catch {
            r.loading = false
          }
        }),
      )
      // 触发刷新
      setRows([...list])
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
      onUseParams(row.snapshot.params, row.item.task_id)
    }
  }

  // 删除该次回测的产物目录（先打开确认弹窗）
  const handleDelete = (row: Row) => {
    setConfirmDel(row)
  }

  // 确认后真正执行删除
  const confirmDelete = async () => {
    if (!confirmDel) return
    setDeleting(true)
    try {
      await deleteBacktest(confirmDel.item.task_id)
      setConfirmDel(null)
      await load()
    } catch {
      setConfirmDel(null)
    } finally {
      setDeleting(false)
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
                <th className="py-2 pr-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const p = row.snapshot?.params
                const ms = row.item.meta_summary || {}
                const navImg = row.item.images?.['nav_curve.png']
                  ? getBacktestImageUrl(row.item.task_id, 'nav_curve.png')
                  : null
                // 只要能查看一样东西（结果或模型产物），"查看"就保持可用；
                // 只有 result.json 和模型产物都没有时才置灰
                const canView = row.item.has_result || row.item.has_artifacts
                // 复用参数/复用回测：有 params.json 即可
                const canReuseParams = row.item.has_params
                return (
                  <tr
                    key={row.item.dir_name}
                    className="border-b border-slate-100 dark:border-slate-700"
                  >
                    <td
                      className="py-2 pr-3 font-mono text-xs"
                      title={row.item.task_id}
                    >
                      {row.item.dir_name}
                      {!row.item.has_result && (
                        <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-amber-100 text-amber-700">
                          未完成
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3">{ms.model || p?.model || '-'}</td>
                    <td className="py-2 pr-3">
                      {ms.start_year && ms.end_year
                        ? `${ms.start_year} ~ ${ms.end_year}`
                        : p
                          ? `${p.start_date} ~ ${p.end_date}`
                          : '-'}
                    </td>
                    <td className="py-2 pr-3 text-right">
                      {p ? ((p.initial_capital || 0) / 10000).toLocaleString() : '-'}
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      <div className="flex gap-1">
                        <button
                          onClick={() => onViewResult(row.item.task_id)}
                          disabled={!canView}
                          className="px-2 py-0.5 rounded text-xs border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed dark:text-slate-300"
                          title={
                            canView
                              ? row.item.has_result
                                ? '查看该次回测的曲线/调仓/训练产物'
                                : '该目录无回测结果，但有模型产物，可查看训练产物（权重/特征）'
                              : '该目录既无回测结果也无模型产物（可能是复制的空目录），无法查看'
                          }
                        >
                          查看
                        </button>
                        <button
                          onClick={() => onReuseBacktest(row.snapshot?.params!, row.item.task_id)}
                          disabled={!canReuseParams}
                          className="px-2 py-0.5 rounded text-xs bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed"
                          title={
                            canReuseParams
                              ? '直接用该次回测的参数和模型权重开始回测（复用模型，不重新训练）'
                              : '该目录无 params.json，无法复用'
                          }
                        >
                          复用回测
                        </button>
                        <button
                          onClick={() => handleUse(row)}
                          disabled={!canReuseParams}
                          className="px-2 py-0.5 rounded text-xs bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          复用参数
                        </button>
                        <button
                          onClick={() => handleDelete(row)}
                          className="px-2 py-0.5 rounded text-xs bg-red-600 text-white hover:bg-red-700"
                          title="删除该回测的产物目录（含参数/结果/模型，不可恢复）"
                        >
                          删除
                        </button>
                        {navImg && (
                          <button
                            onClick={() =>
                              setViewImg({
                                taskId: row.item.task_id,
                                name: 'nav_curve.png',
                                url: navImg,
                              })
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

      {/* 删除确认弹窗（页面内，兼容内置浏览器） */}
      {confirmDel && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-6">
          <div className="bg-white dark:bg-slate-800 rounded-xl p-5 max-w-md w-full">
            <h3 className="font-semibold mb-3">确认删除</h3>
            <p className="text-sm text-slate-500 mb-1">
              确定删除回测「<span className="font-mono">{confirmDel.item.dir_name}</span>」吗？
            </p>
            <p className="text-sm text-red-500 mb-5">
              将删除该目录下的参数 / 结果 / 模型等全部文件，无法恢复。
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmDel(null)}
                disabled={deleting}
                className="px-3 py-1.5 rounded text-sm border text-slate-600 hover:bg-slate-50 dark:text-slate-300 disabled:opacity-50"
              >
                取消
              </button>
              <button
                onClick={confirmDelete}
                disabled={deleting}
                className="px-3 py-1.5 rounded text-sm bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
