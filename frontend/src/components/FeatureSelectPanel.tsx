import { useState } from 'react'
import type { FactorCatalog } from '../types'

interface FeatureSelectPanelProps {
  factorCatalog: FactorCatalog
  customFeatures: string[]
  selected: boolean // form.selected_features 是否有值（有值显示"已选 N/M"，无值显示"全量"）
  onToggle: (name: string) => void
  onToggleAll: (select: boolean) => void
}

// 特征选择面板：按分组展示因子，支持 搜索/全选/清空/勾选（数百特征可快速过滤）
export default function FeatureSelectPanel({
  factorCatalog,
  customFeatures,
  selected,
  onToggle,
  onToggleAll,
}: FeatureSelectPanelProps) {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  return (
    <div className="mt-2 border rounded p-2 bg-slate-50 dark:bg-slate-900 text-xs">
      <div className="flex items-center justify-between mb-1">
        <span className="text-slate-500">
          已选 {customFeatures.length} / {factorCatalog.total} 个特征
          {selected ? '' : '（全量）'}
        </span>
        <span className="space-x-1">
          <button type="button" onClick={() => onToggleAll(true)} className="text-blue-600 hover:underline">
            全选
          </button>
          <span className="text-slate-300">|</span>
          <button type="button" onClick={() => onToggleAll(false)} className="text-blue-600 hover:underline">
            清空
          </button>
        </span>
      </div>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="搜索特征名…（数百特征可快速定位）"
        className="w-full mb-2 border rounded px-2 py-1 bg-white dark:bg-slate-800"
      />
      <div className="max-h-64 overflow-y-auto space-y-2 pr-1">
        {factorCatalog.groups.map((g) => {
          const fields = q ? g.fields.filter((f) => f.name.toLowerCase().includes(q)) : g.fields
          if (fields.length === 0) return null
          return (
            <div key={g.group}>
              <div className="font-semibold text-slate-600 dark:text-slate-300 mb-1">{g.group}</div>
              <div className="flex flex-wrap gap-1.5">
                {fields.map((f) => (
                  <label
                    key={f.name}
                    title={`${f.name}\n公式: ${f.expression}\n${f.description}`}
                    className={`px-2 py-1 rounded border cursor-pointer select-none ${
                      customFeatures.includes(f.name)
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-white dark:bg-slate-700 border-slate-300 dark:border-slate-600 hover:border-blue-400'
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="hidden"
                      checked={customFeatures.includes(f.name)}
                      onChange={() => onToggle(f.name)}
                    />
                    {f.name}
                  </label>
                ))}
              </div>
            </div>
          )
        })}
        {q && fieldsTotal(factorCatalog, q) === 0 && (
          <p className="text-slate-400">无匹配特征（关键词：{q}）</p>
        )}
      </div>
      <p className="mt-2 text-slate-400">
        鼠标悬停在特征名上可查看公式与说明；勾选后仅使用所选特征回测。
      </p>
    </div>
  )
}

function fieldsTotal(catalog: FactorCatalog, q: string): number {
  let n = 0
  for (const g of catalog.groups) {
    for (const f of g.fields) if (f.name.toLowerCase().includes(q)) n += 1
  }
  return n
}
