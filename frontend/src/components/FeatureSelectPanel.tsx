import type { FactorCatalog } from '../types'

interface FeatureSelectPanelProps {
  factorCatalog: FactorCatalog
  customFeatures: string[]
  selected: boolean // form.selected_features 是否有值（有值显示"已选 N/M"，无值显示"全量"）
  onToggle: (name: string) => void
  onToggleAll: (select: boolean) => void
}

// 特征选择面板：按分组展示因子，支持全选/清空/勾选
export default function FeatureSelectPanel({
  factorCatalog,
  customFeatures,
  selected,
  onToggle,
  onToggleAll,
}: FeatureSelectPanelProps) {
  return (
    <div className="mt-2 border rounded p-2 bg-slate-50 dark:bg-slate-900 text-xs">
      <div className="flex items-center justify-between mb-2">
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
      <div className="max-h-64 overflow-y-auto space-y-2 pr-1">
        {factorCatalog.groups.map((g) => (
          <div key={g.group}>
            <div className="font-semibold text-slate-600 dark:text-slate-300 mb-1">
              {g.group}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {g.fields.map((f) => (
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
        ))}
      </div>
      <p className="mt-2 text-slate-400">
        鼠标悬停在特征名上可查看公式与说明；勾选后仅使用所选特征回测。
      </p>
    </div>
  )
}
