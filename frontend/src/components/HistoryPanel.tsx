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
  // 并发回测容量信息（用于"复用回测"按钮在并发满时禁用，与"开始回测"按钮行为一致）
  capacity?: { available: number; running: number; max_concurrent: number } | null
  // 外部触发刷新的 key：值变化时组件会重新加载历史（用于"任务取消/完成后自动刷新"）
  refreshKey?: number
}

interface Row {
  item: HistoryItem
  snapshot?: BacktestSnapshot
  loading: boolean
}

export default function HistoryPanel({ onUseParams, onReuseBacktest, onViewResult, refreshKey, capacity }: Props) {
  const [rows, setRows] = useState<Row[]>([])
  const [page, setPage] = useState(1)  // 当前页码（1-based）
  const [jumpPageInput, setJumpPageInput] = useState('')  // 输入框页码
  const [loading, setLoading] = useState(false)
  const PAGE_SIZE = 20
  const [viewImg, setViewImg] = useState<{ taskId: string; name: string; url: string } | null>(null)
  // 批量删除模式：开启时每行出现勾选框；选中的 task_id 集合；批量删除确认弹窗
  const [batchMode, setBatchMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmBatchDel, setConfirmBatchDel] = useState<Row[] | null>(null)
  const [batchDeleting, setBatchDeleting] = useState(false)
  // 单个删除确认（页面内弹窗，兼容内置浏览器不弹 window.confirm）
  const [confirmDel, setConfirmDel] = useState<Row | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 生成分页页码数组：当前页前后显示 2 个，两端显示，中间过多用省略号
  const buildPageItems = (current: number, total: number): (number | '...')[] => {
    if (total <= 7) {
      return Array.from({ length: total }, (_, i) => i + 1)
    }
    const pages: (number | '...')[] = []
    const addPage = (p: number) => {
      const last = pages[pages.length - 1]
      if (last !== p) pages.push(p)
    }
    addPage(1)
    if (current > 4) pages.push('...')
    for (let p = Math.max(2, current - 2); p <= Math.min(total - 1, current + 2); p++) {
      addPage(p)
    }
    if (current < total - 3) pages.push('...')
    addPage(total)
    return pages
  }

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 外部触发刷新：refreshKey 变化时重新加载历史（任务状态变化后让删除按钮等刷新）
  useEffect(() => {
    if (refreshKey === undefined) return
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey])

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

  // 批量删除：把选中的行集合弹出确认弹窗
  const handleBatchDelete = () => {
    const sel = rows.filter((r) => selectedIds.has(r.item.dir_name))
    if (sel.length === 0) return
    setConfirmBatchDel(sel)
  }
  const confirmBatchDelete = async () => {
    if (!confirmBatchDel || confirmBatchDel.length === 0) return
    setBatchDeleting(true)
    try {
      // 逐个删除（串行避免后端并发删除冲突），失败的允许继续
      for (const r of confirmBatchDel) {
        try {
          await deleteBacktest(r.item.task_id)
        } catch {
          /* 单个失败不阻断其他 */
        }
      }
      setConfirmBatchDel(null)
      setSelectedIds(new Set())
      setBatchMode(false)
      await load()
    } finally {
      setBatchDeleting(false)
    }
  }

  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">历史回测</h2>
        <div className="flex items-center gap-2">
          {/* 批量删除开关：开启后每行出现勾选框，再点一次关闭 */}
          <button
            onClick={() => {
              if (batchMode) {
                // 关闭：清掉选择
                setBatchMode(false)
                setSelectedIds(new Set())
                setConfirmBatchDel(null)
              } else {
                setBatchMode(true)
              }
            }}
            className={`border rounded px-3 py-1 text-sm ${
              batchMode
                ? 'border-blue-300 bg-blue-50 text-blue-600 hover:bg-blue-100'
                : 'text-blue-600 hover:bg-blue-50'
            }`}
          >
            {batchMode ? '取消批量' : '批量删除'}
          </button>
          <button
            onClick={load}
            disabled={loading}
            className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50 disabled:opacity-50"
          >
            {loading ? '加载中...' : '刷新'}
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-slate-400 py-6 text-center">暂无历史回测</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" style={{ overflow: 'visible' }}>
            <thead>
              <tr className="border-b text-left text-slate-500">
                <th className="py-2 pr-3 w-12">#</th>
                <th className="py-2 pr-3">产物目录</th>
                <th className="py-2 pr-3">模型</th>
                <th className="py-2 pr-3">区间</th>
                <th className="py-2 pr-3 text-right">资金(万)</th>
                <th className="py-2 pr-3 whitespace-nowrap relative">
                  <span>操作</span>
                  {/* 批量删除：用绝对定位贴在"操作"右边，不撑大列宽（避免整列变宽） */}
                  {batchMode && selectedIds.size > 0 && (
                    <button
                      onClick={handleBatchDelete}
                      style={{ position: 'absolute', left: '100%', top: '50%', transform: 'translateY(-50%)', marginLeft: 8 }}
                      className="px-2 py-0.5 rounded text-xs bg-red-600 text-white hover:bg-red-700 whitespace-nowrap"
                    >
                      确定删除（{selectedIds.size}）
                    </button>
                  )}
                </th>
              </tr>
            </thead>
            <tbody>
              {(() => {
                const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
                const safePage = Math.min(page, totalPages)
                const pageRows = rows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)
                return pageRows.map((row) => {
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
                // 并发回测上限：满了就不能提交新任务（包括复用回测/复用参数）
                const capacityAtMax = capacity != null && capacity.available <= 0
                // 删除：只有"不在运行"的任务才能删除（运行中删产物目录会破坏回测）
                const isRunning = !!row.item.is_task_running
                const canDelete = !isRunning
                const checked = selectedIds.has(row.item.dir_name)
                return (
                  <tr
                    key={row.item.dir_name}
                    className="border-b border-slate-100 dark:border-slate-700"
                  >
                    <td className="py-2 pr-3 text-slate-400">
                      {row.item.seq != null ? row.item.seq : ''}
                    </td>
                    <td
                      className="py-2 pr-3 font-mono text-xs"
                      title={row.item.task_id}
                    >
                      {row.item.dir_name}
                      {/* "未完成"标签：既没有 result.json，又不在内存中运行（避免把正在跑的任务误标为未完成） */}
                      {!row.item.has_result && !row.item.is_task_running && (
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
                    {batchMode && (
                      <td className="py-2 pr-3">
                        <input
                          type="checkbox"
                          disabled={!canDelete}
                          checked={checked}
                          onChange={(e) => {
                            setSelectedIds((prev) => {
                              const next = new Set(prev)
                              if (e.target.checked) next.add(row.item.dir_name)
                              else next.delete(row.item.dir_name)
                              return next
                            })
                          }}
                          title={canDelete ? '勾选此行' : '该任务正在运行，无法删除'}
                          className="w-4 h-4"
                        />
                      </td>
                    )}
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
                          disabled={!canReuseParams || capacityAtMax}
                          className="px-2 py-0.5 rounded text-xs bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed"
                          title={
                            !canReuseParams
                              ? '该目录无 params.json，无法复用'
                              : capacityAtMax
                                ? `已达并发回测上限（${capacity!.max_concurrent} 个），请等待现有任务完成`
                                : '直接用该次回测的参数和模型权重开始回测（复用模型，不重新训练）'
                          }
                        >
                          复用回测
                        </button>
                        <button
                          onClick={() => handleUse(row)}
                          disabled={!canReuseParams || capacityAtMax}
                          className="px-2 py-0.5 rounded text-xs bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
                          title={
                            !canReuseParams
                              ? '该目录无 params.json，无法复用'
                              : capacityAtMax
                                ? `已达并发回测上限（${capacity!.max_concurrent} 个），请等待现有任务完成`
                                : '把该次回测的参数填入表单（不立即提交，需点开始回测才提交）'
                          }
                        >
                          复用参数
                        </button>
                        <button
                          onClick={() => handleDelete(row)}
                          disabled={!canDelete}
                          className="px-2 py-0.5 rounded text-xs bg-red-600 text-white hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed"
                          title={
                            canDelete
                              ? '删除该回测的产物目录（含参数/结果/模型，不可恢复）'
                              : '该回测正在运行，无法删除（请等待其完成/失败/取消后再删）'
                          }
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
                })
              })()}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页导航 */}
      {rows.length > PAGE_SIZE && (
        <div className="flex items-center justify-center gap-2 mt-4 flex-wrap">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="px-3 py-1 rounded text-sm border text-slate-600 hover:bg-slate-50 dark:text-slate-300 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            上一页
          </button>
          {buildPageItems(page, Math.max(1, Math.ceil(rows.length / PAGE_SIZE))).map((it, idx) =>
            it === '...' ? (
              <span key={`e-${idx}`} className="px-1 text-slate-400">…</span>
            ) : (
              <button
                key={it}
                onClick={() => setPage(Number(it))}
                className={`px-3 py-1 rounded text-sm border ${
                  page === it
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'text-slate-600 hover:bg-slate-50 dark:text-slate-300'
                }`}
              >
                {it}
              </button>
            ),
          )}
          <button
            onClick={() => setPage((p) => Math.min(Math.max(1, Math.ceil(rows.length / PAGE_SIZE)), p + 1))}
            disabled={page >= Math.max(1, Math.ceil(rows.length / PAGE_SIZE))}
            className="px-3 py-1 rounded text-sm border text-slate-600 hover:bg-slate-50 dark:text-slate-300 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            下一页
          </button>
          {/* 页码输入跳转 */}
          <div className="flex items-center gap-1 ml-2">
            <span className="text-xs text-slate-400">跳至</span>
            <input
              type="number"
              min={1}
              max={Math.max(1, Math.ceil(rows.length / PAGE_SIZE))}
              value={jumpPageInput}
              onChange={(e) => setJumpPageInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const n = parseInt(jumpPageInput, 10)
                  const max = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
                  if (!isNaN(n) && n >= 1 && n <= max) {
                    setPage(n)
                  }
                  setJumpPageInput('')
                }
              }}
              className="w-16 px-2 py-1 rounded border text-sm dark:bg-slate-700 dark:text-slate-200"
              placeholder="页码"
            />
            <button
              onClick={() => {
                const n = parseInt(jumpPageInput, 10)
                const max = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
                if (!isNaN(n) && n >= 1 && n <= max) {
                  setPage(n)
                }
                setJumpPageInput('')
              }}
              className="px-2 py-1 rounded text-sm border text-blue-600 hover:bg-blue-50 dark:text-slate-300"
            >
              跳转
            </button>
          </div>
        </div>
      )}

      {/* 批量删除确认弹窗 */}
      {confirmBatchDel && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-6">
          <div className="bg-white dark:bg-slate-800 rounded-xl p-5 max-w-lg w-full">
            <h3 className="font-semibold mb-3">确认批量删除</h3>
            <p className="text-sm text-slate-500 mb-1">
              将删除以下 {confirmBatchDel.length} 个回测的产物目录（含参数/结果/模型）：
            </p>
            <ul className="text-xs font-mono bg-slate-50 dark:bg-slate-900 rounded p-2 mb-3 max-h-40 overflow-y-auto">
              {confirmBatchDel.map((r) => (
                <li key={r.item.dir_name} className="py-0.5">{r.item.dir_name}</li>
              ))}
            </ul>
            <p className="text-sm text-red-500 mb-5">该操作不可恢复，请确认。</p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmBatchDel(null)}
                disabled={batchDeleting}
                className="px-3 py-1.5 rounded text-sm border text-slate-600 hover:bg-slate-50 dark:text-slate-300 disabled:opacity-50"
              >
                取消
              </button>
              <button
                onClick={confirmBatchDelete}
                disabled={batchDeleting}
                className="px-3 py-1.5 rounded text-sm bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {batchDeleting ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
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
