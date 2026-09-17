import { useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

/**
 * 公式编辑框（v1.19.86）：textarea + **行号** + **语法着色（含 `{…}` 注释）** + **Ctrl+F 查找**。
 *
 * 用户 2026-09-17：
 *   ① `{注释}` 要能识别（后端 Lexer 已按此支持；这里把注释渲染成灰色斜体）；
 *   ② Ctrl+F 能搜字符/单词；③ 要行号；④ 编辑区加宽（布局改在 FormulaPanel）。
 *
 * 为什么不用 CodeMirror / Monaco：本项目前端**零编辑器依赖**（package.json 只有 react / recharts /
 * axios / xlsx），为一个公式框引入整套编辑器（几百 KB + 语言包）不划算 ⇒ 用经典
 * **「透明 textarea（在上来接管输入/选区/光标）+ 底层 `<pre>`（在下画彩色文字）」** 双层法。
 * ⚠ 两层必须**字体/字号/行高/内边距/换行策略完全一致**，否则高亮会错位 —— 唯一真源是 `M`：
 */
const M = {
  fontFamily:
    'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
  fontSize: 11.5,
  lineHeight: 18, // px —— 行号/高亮层/输入层都按它算高度
  padding: '6px 8px',
  tabSize: 4,
} as const

// 着色：① `{…}` 注释 ② 数字 ③ 函数名（标识符紧跟 `(`）
const TOKEN_RE = /(\{[^}]*\})|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*(?=\s*\())/g

function highlight(text: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  TOKEN_RE.lastIndex = 0
  while ((m = TOKEN_RE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const key = `${m.index}:${m[0]}`
    if (m[1] !== undefined) {
      // 注释（含大括号本身）：整段灰掉 —— 一眼看出"这段不参与计算"
      out.push(
        <span key={key} className="text-slate-400 dark:text-slate-500 italic">
          {m[0]}
        </span>,
      )
    } else if (m[2] !== undefined) {
      out.push(
        <span key={key} className="text-amber-600 dark:text-amber-400">
          {m[0]}
        </span>,
      )
    } else {
      out.push(
        <span key={key} className="text-blue-600 dark:text-blue-400">
          {m[0]}
        </span>,
      )
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export interface FormulaEditorProps {
  value: string
  onChange: (v: string) => void
  onFocus?: () => void
  rows?: number
  placeholder?: string
  /** 把内部 textarea 暴露给父组件（「插入函数」要在光标处插 token） */
  innerRef?: (el: HTMLTextAreaElement | null) => void
}

export default function FormulaEditor({
  value,
  onChange,
  onFocus,
  rows = 6,
  placeholder,
  innerRef,
}: FormulaEditorProps) {
  const inner = useRef<HTMLTextAreaElement | null>(null)
  const gutterRef = useRef<HTMLDivElement | null>(null)
  const backdropRef = useRef<HTMLPreElement | null>(null)
  const findInputRef = useRef<HTMLInputElement | null>(null)
  const [caret, setCaret] = useState(0)
  const [findOpen, setFindOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [hitIdx, setHitIdx] = useState(0)

  const lines = useMemo(() => value.split('\n'), [value])
  const activeLine = useMemo(
    () => value.slice(0, caret).split('\n').length,
    [value, caret],
  )

  // 匹配位置（大小写不敏感，逐个不重叠）
  const hits = useMemo(() => {
    const q = query
    if (!q) return [] as number[]
    const hay = value.toLowerCase()
    const needle = q.toLowerCase()
    const out: number[] = []
    let i = hay.indexOf(needle)
    while (i >= 0) {
      out.push(i)
      i = hay.indexOf(needle, i + Math.max(1, needle.length))
    }
    return out
  }, [value, query])

  const syncScroll = () => {
    const a = inner.current
    if (!a) return
    if (gutterRef.current) gutterRef.current.scrollTop = a.scrollTop
    if (backdropRef.current) {
      backdropRef.current.scrollTop = a.scrollTop
      backdropRef.current.scrollLeft = a.scrollLeft
    }
  }

  const jumpTo = (idx: number) => {
    const a = inner.current
    if (!a || hits.length === 0) return
    const n = ((idx % hits.length) + hits.length) % hits.length
    setHitIdx(n)
    const start = hits[n]
    a.focus()
    a.setSelectionRange(start, start + query.length)
    setCaret(start)
  }

  const openFind = () => {
    const a = inner.current
    // 有选中内容 ⇒ 直接拿来当搜索词（省一次输入 ✓）
    if (a && a.selectionEnd > a.selectionStart) {
      setQuery(value.slice(a.selectionStart, a.selectionEnd))
    }
    setHitIdx(0)
    setFindOpen(true)
    requestAnimationFrame(() => findInputRef.current?.focus())
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const mod = e.ctrlKey || e.metaKey
    if (mod && (e.key === 'f' || e.key === 'F')) {
      e.preventDefault()
      openFind()
      return
    }
    if (e.key === 'F3') {
      e.preventDefault()
      jumpTo(hitIdx + (e.shiftKey ? -1 : 1))
      return
    }
    if (e.key === 'Escape' && findOpen) {
      setFindOpen(false)
      return
    }
    if (mod && (e.key === 'g' || e.key === 'G')) {
      e.preventDefault()
      jumpTo(hitIdx + (e.shiftKey ? -1 : 1))
    }
  }

  const metricsStyle = {
    fontFamily: M.fontFamily,
    fontSize: M.fontSize,
    lineHeight: `${M.lineHeight}px`,
    padding: M.padding,
    tabSize: M.tabSize,
    whiteSpace: 'pre' as const,
    margin: 0,
    border: 0,
  }
  const bodyHeight = rows * M.lineHeight + 12

  return (
    <div className="border rounded bg-white dark:bg-slate-800 overflow-hidden">
      <div className="flex" style={{ height: bodyHeight }}>
        {/* 行号槽：与输入层同步滚动（overflow:hidden + 程序化 scrollTop） */}
        <div
          ref={gutterRef}
          aria-hidden
          className="shrink-0 select-none overflow-hidden text-right text-slate-400 dark:text-slate-500 bg-slate-50 dark:bg-slate-900 border-r border-slate-200 dark:border-slate-700"
          style={{ ...metricsStyle, width: `${Math.max(2, String(lines.length).length) + 1.5}ch` }}
        >
          {lines.map((_l, i) => (
            <div
              key={i}
              className={
                i + 1 === activeLine
                  ? 'text-emerald-600 dark:text-emerald-400 font-semibold'
                  : ''
              }
            >
              {i + 1}
            </div>
          ))}
        </div>
        {/* 双层：底层 <pre> 画彩色文字，上层透明 textarea 接管输入/光标/选区 */}
        <div className="relative flex-1 min-w-0">
          <pre
            ref={backdropRef}
            aria-hidden
            className="absolute inset-0 overflow-hidden pointer-events-none text-slate-800 dark:text-slate-100"
            style={metricsStyle}
          >
            {highlight(value)}
            {'\n'}
          </pre>
          <textarea
            ref={(el) => {
              inner.current = el
              innerRef?.(el)
            }}
            value={value}
            onChange={(e) => {
              onChange(e.target.value)
              setCaret(e.target.selectionStart)
            }}
            onScroll={syncScroll}
            onKeyDown={onKeyDown}
            onKeyUp={(e) => setCaret(e.currentTarget.selectionStart)}
            onClick={(e) => setCaret(e.currentTarget.selectionStart)}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
            onFocus={() => onFocus?.()}
            wrap="off"
            spellCheck={false}
            placeholder={placeholder}
            className="absolute inset-0 resize-none outline-none overflow-auto bg-transparent placeholder:text-slate-400"
            style={{
              ...metricsStyle,
              color: 'transparent',
              caretColor: 'rgb(15 23 42)',
              overflowWrap: 'normal',
            }}
          />
        </div>
      </div>
      {/* Ctrl+F 查找条（textarea 只能高亮一处 ⇒ 用选区跳转 + 计数提示） */}
      {findOpen && (
        <div className="flex items-center gap-1 px-2 py-1 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-[11px]">
          <input
            ref={findInputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setHitIdx(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                jumpTo(hitIdx + (e.shiftKey ? -1 : 1))
              } else if (e.key === 'Escape') {
                e.preventDefault()
                setFindOpen(false)
                inner.current?.focus()
              }
            }}
            placeholder="查找（Ctrl+F，Enter 下一个 / Shift+Enter 上一个）"
            className="flex-1 min-w-0 border rounded px-1.5 py-0.5 bg-white dark:bg-slate-800"
          />
          <span className="text-slate-500 shrink-0 tabular-nums">
            {hits.length === 0 ? '无匹配' : `${hitIdx + 1} / ${hits.length}`}
          </span>
          <button
            type="button"
            onClick={() => jumpTo(hitIdx - 1)}
            title="上一个 (Shift+Enter)"
            className="px-1.5 rounded border text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
          >
            ↑
          </button>
          <button
            type="button"
            onClick={() => jumpTo(hitIdx + 1)}
            title="下一个 (Enter / F3)"
            className="px-1.5 rounded border text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
          >
            ↓
          </button>
          <button
            type="button"
            onClick={() => {
              setFindOpen(false)
              inner.current?.focus()
            }}
            title="关闭 (Esc)"
            className="px-1.5 rounded border text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  )
}
