import { useMemo, useState } from 'react'
import type { ModelArtifacts as ModelArtifactsType } from '../types'

interface Props {
  artifacts: ModelArtifactsType
}

export default function ModelArtifacts({ artifacts }: Props) {
  const [showWeights, setShowWeights] = useState(false)
  const [showFeatures, setShowFeatures] = useState(false)
  const [showModel, setShowModel] = useState(false)

  // 滚动训练时，artifacts.segments 为每段的交付物数组
  const isMultiSeg = Array.isArray(artifacts.segments) && artifacts.segments.length > 0
  const [segIdx, setSegIdx] = useState(0)
  const active = isMultiSeg ? artifacts.segments![segIdx] || artifacts.segments![0] : artifacts

  const sortedWeights = useMemo(() => {
    const fw = active.linear?.feature_weights
    if (!fw || fw.length === 0) return []
    return [...fw].sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight))
  }, [active])

  const modelName = active.model_info?.model || '未知'
  const isLinear = !!active.linear
  const featureNames = active.feature_names || []

  const downloadModel = () => {
    if (!active.model_file) return
    const blob = new Blob([active.model_file], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${modelName}_model.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const downloadWeights = () => {
    const rows = sortedWeights.length > 0 ? sortedWeights : (active.linear?.weights || []).map((w, i) => ({ feature: featureNames[i] || `f${i}`, weight: w }))
    const csv = ['feature,weight']
    for (const r of rows) csv.push(`${r.feature},${r.weight}`)
    const blob = new Blob([csv.join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${modelName}_weights.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const downloadFeatures = () => {
    if (featureNames.length === 0) return
    const blob = new Blob([featureNames.join('\n')], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${modelName}_features.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const params = active.params || {}

  return (
    <section className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-lg font-semibold">
          训练产物（可复现）
          <span className="ml-2 text-sm font-normal text-slate-500">
            {modelName} · {featureNames.length} 个特征
            {active.model_info?.num_trees ? ` · ${active.model_info.num_trees} 棵树` : ''}
            {isMultiSeg ? ` · 共 ${artifacts.segments!.length} 段` : ''}
          </span>
        </h2>
        <div className="flex items-center gap-2">
          {/* 滚动训练段选择器 */}
          {isMultiSeg && (
            <select
              value={segIdx}
              onChange={(e) => setSegIdx(Number(e.target.value))}
              className="border rounded px-2 py-1 text-sm"
            >
              {artifacts.segments!.map((_, i) => (
                <option key={i} value={i}>
                  段 {i + 1}
                </option>
              ))}
            </select>
          )}
          {active.model_file && (
            <button
              onClick={downloadModel}
              className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
            >
              下载模型文件
            </button>
          )}
          {active.linear && (
            <button
              onClick={downloadWeights}
              className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
            >
              下载权重CSV
            </button>
          )}
          {featureNames.length > 0 && (
            <button
              onClick={downloadFeatures}
              className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
            >
              下载特征列表
            </button>
          )}
        </div>
      </div>

      <div className="space-y-4 text-sm">
        {/* 线性模型公式 */}
        {isLinear && (
          <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4">
            <div className="font-semibold mb-2">模型公式</div>
            <code className="text-xs bg-white dark:bg-slate-800 p-2 rounded block overflow-x-auto">
              score = {active.linear?.intercept?.toFixed(6)}
              {sortedWeights.length > 0 && (
                <span>
                  {' '}
                  {sortedWeights.slice(0, 5).map((w, i) => (
                    <span key={i}>
                      {w.weight >= 0 ? '+ ' : '- '}
                      {Math.abs(w.weight).toFixed(6)}×{w.feature}{' '}
                    </span>
                  ))}
                  {sortedWeights.length > 5 && '...'}
                </span>
              )}
            </code>
            <div className="mt-3 flex items-center gap-2">
              <button
                onClick={() => setShowWeights(!showWeights)}
                className="text-blue-600 hover:underline"
              >
                {showWeights ? '收起' : '查看'}全部 {sortedWeights.length} 个权重
              </button>
            </div>
            {showWeights && (
              <div className="mt-2 max-h-64 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-500 border-b">
                      <th className="py-1 pr-3">#</th>
                      <th className="py-1 pr-3">特征</th>
                      <th className="py-1 pr-3 text-right">权重</th>
                      <th className="py-1 pr-3 text-right">|权重|</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedWeights.map((w, i) => (
                      <tr key={i} className="border-b border-slate-100 dark:border-slate-700">
                        <td className="py-1 pr-3">{i + 1}</td>
                        <td className="py-1 pr-3 font-mono">{w.feature}</td>
                        <td className="py-1 pr-3 text-right">{w.weight.toFixed(6)}</td>
                        <td className="py-1 pr-3 text-right">{Math.abs(w.weight).toFixed(6)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* 树模型超参数 */}
        {Object.keys(params).length > 0 && (
          <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4">
            <div className="font-semibold mb-2">模型超参数</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(params).map(([k, v]) => (
                <span
                  key={k}
                  className="px-2 py-1 rounded bg-white dark:bg-slate-800 text-xs border"
                >
                  <span className="text-slate-500">{k}</span>: <b>{String(v)}</b>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 树模型特征重要性 */}
        {Array.isArray(active.feature_importance) && active.feature_importance.length > 0 && (
          <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4">
            <div className="font-semibold mb-2">
              特征重要性（树模型权重）
              <span className="ml-2 text-xs font-normal text-slate-500">
                按分裂增益累计，Top{Math.min(10, active.feature_importance.length)}
              </span>
            </div>
            <div className="space-y-1.5">
              {active.feature_importance.slice(0, 10).map((f, i) => {
                const max = active.feature_importance![0]?.importance || 1
                const pct = max > 0 ? Math.round((f.importance / max) * 100) : 0
                return (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-5 text-right text-slate-400">{i + 1}</span>
                    <span className="font-mono truncate flex-1">{f.feature}</span>
                    <div className="flex-1 h-2 rounded bg-slate-200 dark:bg-slate-700 overflow-hidden">
                      <div
                        className="h-full rounded bg-gradient-to-r from-blue-500 to-indigo-500"
                        style={{ width: `${Math.max(2, pct)}%` }}
                      />
                    </div>
                    <span className="w-16 text-right text-slate-500">{f.importance.toFixed(4)}</span>
                  </div>
                )
              })}
            </div>
            {active.feature_importance.length > 10 && (
              <p className="mt-2 text-xs text-slate-400">
                共 {active.feature_importance.length} 个特征，仅展示前 10 个
              </p>
            )}
          </div>
        )}

        {/* 树模型文件 */}
        {active.model_file && (
          <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4">
            <div className="font-semibold mb-2">
              模型文件
              <span className="ml-2 text-xs font-normal text-slate-500">
                （{(active.model_file.length / 1024).toFixed(0)} KB，可直接加载复现）
              </span>
            </div>
            <button
              onClick={() => setShowModel(!showModel)}
              className="text-blue-600 hover:underline"
            >
              {showModel ? '收起' : '查看'}模型内容
            </button>
            {showModel && (
              <pre className="mt-2 max-h-72 overflow-auto text-[10px] bg-white dark:bg-slate-800 rounded p-3 whitespace-pre-wrap break-all">
                {active.model_file.slice(0, 20000)}
                {active.model_file.length > 20000 && '\n... (已截断)'}
              </pre>
            )}
          </div>
        )}

        {/* 特征列表 */}
        {featureNames.length > 0 && (
          <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4">
            <div className="font-semibold mb-2">特征列表（{featureNames.length} 个）</div>
            <button
              onClick={() => setShowFeatures(!showFeatures)}
              className="text-blue-600 hover:underline"
            >
              {showFeatures ? '收起' : '查看'}特征
            </button>
            {showFeatures && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {featureNames.map((f) => (
                  <span
                    key={f}
                    className="px-2 py-0.5 rounded bg-white dark:bg-slate-800 text-xs border font-mono"
                  >
                    {f}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 空状态 */}
        {!isLinear && Object.keys(params).length === 0 && !active.model_file && (
          <p className="text-slate-400">该模型暂无额外的可复现产物</p>
        )}
      </div>
    </section>
  )
}
