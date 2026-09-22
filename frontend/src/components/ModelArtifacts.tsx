import { useEffect, useMemo, useState } from 'react'
import { getBacktestFeatures } from '../api'
import type { FeatureFormula } from '../api'
import type { ModelArtifacts as ModelArtifactsType } from '../types'

interface Props {
  artifacts: ModelArtifactsType
  /** 任务 id：用来拉「特征名 ↔ 公式」对照（v1.19.75，特征表展示 + 下载 CSV） */
  taskId?: string
}

export default function ModelArtifacts({ artifacts, taskId }: Props) {
  const [showWeights, setShowWeights] = useState(false)
  const [showFeatures, setShowFeatures] = useState(false)
  const [showModel, setShowModel] = useState(false)
  // 特征 ↔ 公式对照（懒加载：展开特征列表时才请求）
  const [featRows, setFeatRows] = useState<FeatureFormula[] | null>(null)
  const [featErr, setFeatErr] = useState('')
  const [featQuery, setFeatQuery] = useState('')

  useEffect(() => {
    if (!showFeatures || !taskId || featRows || featErr) return
    getBacktestFeatures(taskId)
      .then((r) => setFeatRows(r.items || []))
      .catch((e: unknown) => setFeatErr(e instanceof Error ? e.message : String(e)))
  }, [showFeatures, taskId, featRows, featErr])

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

  // ★ v1.20.50：段号一律取后端给的 `model_info.segment`（如 "seg11" ⇒ 11 ✓）。
  //   ⚠ 原先直接显示「段 {数组下标+1}」✗ —— 而后端 `segments` 的顺序曾是**字符串序**
  //   （`seg1, seg10, seg11…` ✗）⇒ 编号会错 ✓（用户 2026-09-22 实测：下拉里的「段 3」其实是 seg11 ✓）。
  //   后端 v1.20.50 已改为**按数字段号排序** ✓；这里再取真实段号，双保险 ✓。
  const segNoOf = (m?: ModelArtifactsType | null, fallbackIdx?: number): string => {
    const raw = String(m?.model_info?.segment ?? '').replace(/[^0-9]/g, '')
    const n = Number(raw)
    if (Number.isFinite(n) && n > 0) return String(n)
    return fallbackIdx != null ? String(fallbackIdx + 1) : '?'
  }

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

  /** CSV 字段转义：公式里常有逗号/引号 ⇒ 必须双引号包裹并把内部引号翻倍 */
  const csvCell = (v: string) => `"${String(v ?? '').replace(/"/g, '""')}"`

  /** 下载特征表 CSV（v1.19.75）：第一列特征名、第二列公式（自定义公式 = 你保存的原文）。
   *  ⚠ 加 UTF-8 BOM，否则 Excel 打开中文与长公式会乱码。 */
  const downloadFeatureCsv = () => {
    const rows: FeatureFormula[] = featRows && featRows.length > 0
      ? featRows
      : featureNames.map((n) => ({ name: n, formula: '', kind: '', qlib_expr: '' }))
    const lines = ['特征名,公式']
    for (const r of rows) lines.push([csvCell(r.name), csvCell(r.formula)].join(','))
    const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${modelName}_features.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const params = active.params || {}

  /** 特征表展示行：优先后端给的「名称 + 公式」，没取到就退化成只有名称；支持过滤 */
  const featShown = useMemo(() => {
    const rows: FeatureFormula[] = featRows && featRows.length > 0
      ? featRows
      : featureNames.map((n) => ({ name: n, formula: '', kind: '', qlib_expr: '' }))
    const q = featQuery.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(
      (r) => r.name.toLowerCase().includes(q) || (r.formula || '').toLowerCase().includes(q),
    )
  }, [featRows, featureNames, featQuery])

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
              {artifacts.segments!.map((seg, i) => (
                <option key={i} value={i}>
                  段 {segNoOf(seg, i)}
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
              onClick={downloadFeatureCsv}
              title="CSV：第一列特征名、第二列公式（Alpha158/360 给 qlib 表达式；自定义公式给你保存的原文）"
              className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
            >
              下载特征表 CSV
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

        {/* 特征列表（v1.19.75：名称 + 公式；自定义公式显示用户保存的原文） */}
        {featureNames.length > 0 && (
          <div className="rounded-lg bg-slate-50 dark:bg-slate-900 p-4">
            <div className="font-semibold mb-2">
              特征列表（{featureNames.length} 个）
              <span className="ml-2 text-xs font-normal text-slate-500">
                左列特征名 · 右列公式（Alpha158/360 = qlib 表达式；自定义公式 = 你保存的原文）
              </span>
            </div>
            <button
              onClick={() => setShowFeatures(!showFeatures)}
              className="text-blue-600 hover:underline"
            >
              {showFeatures ? '收起' : '查看'}特征与公式
            </button>
            {showFeatures && (
              <div className="mt-2">
                {featErr && (
                  <p className="mb-2 text-xs text-amber-600">
                    公式获取失败：{featErr}（当前只显示特征名；下载 CSV 的第二列会是空的）
                  </p>
                )}
                <input
                  type="text"
                  value={featQuery}
                  onChange={(e) => setFeatQuery(e.target.value)}
                  placeholder="搜索特征名或公式…"
                  className="w-full mb-2 border rounded px-2 py-1 text-xs bg-white dark:bg-slate-800"
                />
                <div className="max-h-72 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-slate-500 border-b sticky top-0 bg-slate-50 dark:bg-slate-900">
                        <th className="py-1 pr-3">#</th>
                        <th className="py-1 pr-3">特征名</th>
                        <th className="py-1 pr-3">公式</th>
                        <th className="py-1 pr-3">来源</th>
                      </tr>
                    </thead>
                    <tbody>
                      {featShown.map((r, i) => (
                        <tr key={r.name} className="border-b border-slate-100 dark:border-slate-700">
                          <td className="py-1 pr-3 text-slate-400 align-top">{i + 1}</td>
                          <td className="py-1 pr-3 font-mono align-top whitespace-nowrap">{r.name}</td>
                          <td className="py-1 pr-3 font-mono align-top break-all text-slate-600 dark:text-slate-300">
                            {r.formula || '—'}
                          </td>
                          <td className="py-1 pr-3 align-top whitespace-nowrap text-slate-400">
                            {r.kind || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-1 text-[11px] text-slate-400">
                  共 {featShown.length} 行（点击上方「下载特征表 CSV」导出「特征名,公式」两列）
                </p>
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
