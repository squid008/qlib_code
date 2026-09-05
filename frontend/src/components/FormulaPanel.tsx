import { useRef, useState } from 'react'
import type { CustomFormula } from '../api'
import FormulaHandbookModal from './FormulaHandbookModal'

interface FormulaPanelProps {
  customFormulas: CustomFormula[]
  selectedFormulaIds: Set<string>
  formulaInput: string
  formulaError: string
  formulaTranslating: boolean
  editingId: string | null
  editingText: string
  onInputChange: (v: string) => void
  onEditingTextChange: (v: string) => void
  onAdd: () => void
  onToggle: (id: string) => void
  onToggleAll: (select: boolean) => void
  onStartEdit: (f: CustomFormula) => void
  onSaveEdit: () => void
  onCancelEdit: () => void
  onRemove: (id: string) => void
}

// 自定义公式编辑面板：左侧编辑区 + 右侧公式列表（跨整行）
export default function FormulaPanel({
  customFormulas,
  selectedFormulaIds,
  formulaInput,
  formulaError,
  formulaTranslating,
  editingId,
  editingText,
  onInputChange,
  onEditingTextChange,
  onAdd,
  onToggle,
  onToggleAll,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onRemove,
}: FormulaPanelProps) {
  // 函数手册弹窗（弹窗开关/选中均为本组件局部 UI 状态，不进 App）
  const [handbookOpen, setHandbookOpen] = useState(false)
  const newAreaRef = useRef<HTMLTextAreaElement | null>(null)
  const editAreaRef = useRef<HTMLTextAreaElement | null>(null)
  const lastFocusRef = useRef<'new' | 'edit'>('new')

  // 双击手册函数/字段 → 插入到最近聚焦的公式编辑框光标处（编辑中默认插编辑框）
  const insertToken = (token: string) => {
    const target: 'new' | 'edit' =
      editingId != null && lastFocusRef.current === 'edit' ? 'edit' : 'new'
    const area = target === 'edit' ? editAreaRef.current : newAreaRef.current
    const cur = target === 'edit' ? editingText : formulaInput
    const onChange = target === 'edit' ? onEditingTextChange : onInputChange
    const start = area?.selectionStart ?? cur.length
    const end = area?.selectionEnd ?? start
    onChange(cur.slice(0, start) + token + cur.slice(end))
    // 恢复焦点与光标到插入点之后
    requestAnimationFrame(() => {
      if (!area) return
      area.focus()
      lastFocusRef.current = target
      const pos = start + token.length
      area.setSelectionRange(pos, pos)
    })
  }

  return (
    <div className="mt-2 border rounded p-3 bg-slate-50 dark:bg-slate-900 text-xs">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* 左侧：编辑区（占 1/3） */}
        <div>
          <p className="text-slate-500 mb-1">
            粘贴益盟/通达信公式（每条 1 个输出，可含 <code className="text-slate-600">A:=...</code> 中间变量），
            编译后保存为自定义因子（刷新不丢失），勾选后才参与回测。示例：
          </p>
          <p className="text-slate-400 mb-2 leading-relaxed break-all">
            <code className="text-[10px]">A:=MA(CLOSE,5); 长期线:A+100;</code>
          </p>
          <textarea
            ref={newAreaRef}
            value={formulaInput}
            onChange={(e) => onInputChange(e.target.value)}
            onFocus={() => {
              lastFocusRef.current = 'new'
            }}
            rows={5}
            placeholder="如：A:=MA(CLOSE,5); 长期线:A+100;"
            className="w-full border rounded px-2 py-1 font-mono text-[11px] bg-white dark:bg-slate-800"
          />
          <div className="flex items-center gap-2 mt-1.5">
            <button
              type="button"
              onClick={() => setHandbookOpen(true)}
              title="函数/字段手册：双击函数名插入到公式光标处"
              className="px-2 py-1 rounded border text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              插入函数
            </button>
            <button
              type="button"
              onClick={onAdd}
              disabled={formulaTranslating}
              className="px-2 py-1 rounded bg-emerald-600 text-white text-xs disabled:opacity-50"
            >
              {formulaTranslating ? '编译中...' : '编译并保存'}
            </button>
          </div>
          {formulaError && (
            <p className="mt-1 text-red-500 text-[11px] break-all">{formulaError}</p>
          )}
        </div>
        {/* 右侧：公式列表（占 2/3） */}
        <div className="md:col-span-2">
          {customFormulas.length > 0 ? (
            <>
              <div className="flex items-center justify-between mb-1">
                <span className="text-slate-500">
                  已选 {selectedFormulaIds.size} / {customFormulas.length} 个
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
              <ul className="space-y-1 max-h-72 overflow-y-auto pr-1">
                {customFormulas.map((f) => (
                  <li key={f.id} className="border rounded px-2 py-1 bg-white dark:bg-slate-800">
                    {editingId === f.id ? (
                      <div>
                        <textarea
                          ref={editAreaRef}
                          value={editingText}
                          onChange={(e) => onEditingTextChange(e.target.value)}
                          onFocus={() => {
                            lastFocusRef.current = 'edit'
                          }}
                          rows={5}
                          className="w-full border rounded px-2 py-1 font-mono text-[11px] bg-white dark:bg-slate-800"
                        />
                        <div className="flex items-center gap-2 mt-1">
                          <button
                            type="button"
                            onClick={onSaveEdit}
                            disabled={formulaTranslating}
                            className="px-2 py-0.5 rounded bg-emerald-600 text-white text-[11px] disabled:opacity-50"
                          >
                            {formulaTranslating ? '编译中...' : '保存'}
                          </button>
                          <button
                            type="button"
                            onClick={onCancelEdit}
                            className="px-2 py-0.5 rounded border text-slate-500 text-[11px] hover:bg-slate-100 dark:hover:bg-slate-700"
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-start gap-2">
                        <input
                          type="checkbox"
                          className="accent-emerald-600 shrink-0 mt-0.5"
                          checked={selectedFormulaIds.has(f.id)}
                          onChange={() => onToggle(f.id)}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-emerald-700 dark:text-emerald-300 truncate">
                              {f.name}
                            </span>
                            <span className="flex-1" />
                            <button
                              type="button"
                              onClick={() => onStartEdit(f)}
                              className="text-blue-500 hover:text-blue-700 text-[11px] shrink-0"
                            >
                              编辑
                            </button>
                            <button
                              type="button"
                              onClick={() => onRemove(f.id)}
                              className="text-red-400 hover:text-red-600 text-xs shrink-0"
                            >
                              ✕
                            </button>
                          </div>
                          <div
                            className="text-slate-500 font-mono text-[10px] mt-0.5 truncate"
                            title={f.text}
                          >
                            {f.text}
                          </div>
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-slate-400">
                勾选的自定义因子会与所选特征一起进入回测；删除公式需点列表右侧 ✕。
              </p>
            </>
          ) : (
            <p className="text-slate-400 italic">尚未保存任何自定义公式，在左侧输入后点"编译并保存"即可添加。</p>
          )}
        </div>
      </div>
      {/* 函数手册弹窗（独立小组件）：双击函数/字段名插入到最近聚焦的公式编辑框 */}
      <FormulaHandbookModal
        open={handbookOpen}
        onClose={() => setHandbookOpen(false)}
        onInsert={insertToken}
      />
    </div>
  )
}
