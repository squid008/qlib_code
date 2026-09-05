import { useEffect, useMemo, useState } from 'react'
import { filterHandbook, type HandbookEntry } from '../formulaHandbook'

interface FormulaHandbookModalProps {
  open: boolean
  onClose: () => void
  onInsert: (token: string) => void // 插入内容（函数=名+(，字段=名）
}

// 公式函数手册弹窗（独立小组件，不占用 App 状态）：
// 双击函数/字段名插入到公式编辑窗口；点中某行时下方显示详细说明。
export default function FormulaHandbookModal({ open, onClose, onInsert }: FormulaHandbookModalProps) {
  const [kw, setKw] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [lastFocus, setLastFocus] = useState<'list' | 'input'>('input')

  const items = useMemo(() => filterHandbook(kw), [kw])

  // 打开时重置状态
  useEffect(() => {
    if (open) {
      setKw('')
      setSelected(null)
      setLastFocus('input')
    }
  }, [open])

  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])

  if (!open) return null

  const cur: HandbookEntry | undefined =
    selected != null ? items.find((i) => i.name === selected) : undefined

  const insert = (entry: HandbookEntry) => {
    const token = entry.kind === 'func' ? `${entry.name}(` : entry.name
    onInsert(token)
    // 插入后保持弹窗打开，便于连续插入；焦点回到编辑窗（由父组件处理）
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // 列表内方向键移动选中 / Enter 插入
    if (items.length === 0) return
    const idx = items.findIndex((i) => i.name === cur?.name)
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      const n = items[Math.min(idx + 1, items.length - 1)]
      setSelected(n.name)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const n = items[Math.max(idx - 1, 0)]
      setSelected(n.name)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (cur) insert(cur)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="w-[min(720px,92vw)] h-[min(560px,88vh)] flex flex-col bg-white dark:bg-slate-800 border rounded-lg shadow-xl text-xs">
        {/* 头部 */}
        <div className="flex items-center justify-between px-3 py-2 border-b">
          <span className="font-semibold text-slate-700 dark:text-slate-200">
            公式函数手册 <span className="text-slate-400 font-normal">（双击函数名插入，Esc 关闭）</span>
          </span>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600 px-1.5">
            ✕
          </button>
        </div>

        {/* 搜索框 */}
        <div className="px-3 py-2 border-b flex items-center gap-2">
          <input
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            placeholder="搜索函数/字段或中文名..."
            autoFocus
            className="flex-1 border rounded px-2 py-1 text-xs bg-white dark:bg-slate-900"
          />
          <span className="text-slate-400">{items.length} 条</span>
        </div>

        {/* 主体：左列表 + 右详情 */}
        <div className="flex-1 min-h-0 flex">
          {/* 两列表格 */}
          <div className="w-1/2 border-r overflow-y-auto" onMouseDown={() => setLastFocus('list')}>
            <table className="w-full border-collapse" onKeyDown={handleKeyDown}>
              <thead className="sticky top-0 bg-slate-100 dark:bg-slate-700 z-10">
                <tr className="text-slate-500">
                  <th className="text-left px-3 py-1 font-medium">函数/字段</th>
                  <th className="text-left px-3 py-1 font-medium">中文</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={2} className="px-3 py-4 text-center text-slate-400 italic">
                      无匹配
                    </td>
                  </tr>
                ) : (
                  items.map((it) => (
                    <tr
                      key={it.name}
                      className={`cursor-pointer border-b border-slate-100 dark:border-slate-700 ${
                        cur?.name === it.name
                          ? 'bg-emerald-50 dark:bg-emerald-900/40'
                          : 'hover:bg-slate-50 dark:hover:bg-slate-700/60'
                      }`}
                      onMouseDown={() => {
                        setSelected(it.name)
                        setLastFocus('list')
                      }}
                      onDoubleClick={() => insert(it)}
                      title={it.desc}
                    >
                      <td className="px-3 py-1 font-mono text-emerald-700 dark:text-emerald-300 select-text">
                        {it.name}
                      </td>
                      <td className="px-3 py-1 text-slate-600 dark:text-slate-300">{it.abbr}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* 右侧：详情框 */}
          <div className="w-1/2 flex flex-col min-h-0">
            <div className="px-3 py-1 border-b text-slate-400 bg-slate-50 dark:bg-slate-900">说明</div>
            <div className="flex-1 overflow-y-auto p-3 whitespace-pre-wrap font-mono text-[11px] text-slate-700 dark:text-slate-200">
              {cur ? (
                <>
                  <div className="mb-1">
                    <span className="text-emerald-700 dark:text-emerald-300 font-semibold">{cur.name}</span>
                    <span className="text-slate-400"> · {cur.abbr}</span>
                    <span className="ml-2 text-slate-400">[{cur.kind === 'func' ? '函数' : '字段'}]</span>
                  </div>
                  <div className="text-slate-600 dark:text-slate-300">{cur.desc}</div>
                </>
              ) : (
                <div className="text-slate-400 italic">
                  点击左侧函数/字段查看详细说明；双击名称插入公式（函数插入到括号前，如 BARSCOUNT( ）。
                </div>
              )}
            </div>
            <div className="px-3 py-1 border-t text-slate-400 bg-slate-50 dark:bg-slate-900">
              双击插入 · ↑↓ 选择 · Enter 插入 · Esc 关闭
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
