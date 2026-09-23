import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

/**
 * 公式编辑框（v1.19.86 起：textarea + **行号** + **语法着色（含 `{…}` 注释）** + **Ctrl+F 查找**）。
 *
 * 为什么不用 CodeMirror / Monaco：本项目前端**零编辑器依赖**（package.json 只有 react / recharts /
 * axios / xlsx），为一个公式框引入整套编辑器（几百 KB + 语言包）不划算 ⇒ 用
 * **「透明 textarea（在上接管输入/光标/选区）+ `<pre>`（在下画彩色文字）+ 行号槽」** 三层法。
 * ⚠ 三层的**字体/字号/行高/内边距/换行策略必须完全一致** —— 唯一真源是 `M`。
 *
 * ════════════════════════════════════════════════════════════════════════════════════════
 * ★★★ v1.20.56（2026-09-23）**滚动策略重做** —— 用户第 6 次反馈"滚动时字符重叠" ✗
 *
 * 旧做法（v1.19.103 起）：只有 textarea 自己滚 ✓，`<pre>` 与行号槽 `overflow:hidden` +
 *   `transform: translate(±scrollTop)` **靠 JS 反向位移跟随** ✗。
 *   ⇒ **病根**：滚动条在**合成器线程**上滚 textarea ✓，而 `transform` 要等**主线程**跑 JS ✗
 *   ⇒ 快速拖动滚动条时两者**差至少一帧** ⇒ 行号/彩色文字与正文错位 ⇒ 用户看到"字符重叠/重影" ✗
 *   （用户 2026-09-23 截图：行号数字叠在正文的字上 ✓）。**这类误差靠调 JS 时序永远治不完** ✗。
 * 新做法：★ **只留一个滚动宿主（`hostRef`）** ✓ —— 行号槽与 `<pre>`/`<textarea>` 都是它的
 *   **普通子元素** ⇒ 它们**由浏览器原生一起滚动** ✓ ⇒ **JS 同步整个删掉** ✓✓ ⇒ 从根上不可能错位 ✓。
 *   · 行号槽用 `position: sticky; left: 0` ✓ ⇒ **横向**滚动时钉住不跑 ✓、**纵向**随宿主自然滚 ✓；
 *   · `<pre>` 在**正常流**里 ⇒ 由它决定内容宽高 ✓；`<textarea>` 绝对定位**严丝合缝**盖在上面 ✓
 *     （同一份 metrics ✓ ⇒ 逐像素对齐 ✓），且**自己永不滚动**（`overflow:hidden` ✓）。
 *   ⚠ 唯一还需 JS 的地方：**把光标/查找命中滚进可视区** ✓（`revealCaret` / `scrollToMatch`）——
 *     它滚的是**宿主** ✓，且"差一帧"完全不可见 ✓（不像图层错位那样刺眼 ✓）。
 *     另外：textarea 是 `overflow:hidden` 的滚动容器 ✗ ⇒ 浏览器"露光标"时会偷偷滚它自己 ✗
 *     ⇒ 每次滚完把它的 `scrollTop/scrollLeft` 归零 ✓（否则光标画在错位置 ✗）。
 *
 * ★★ 同版本第二修：**「插入函数」后光标被扔到末尾** ✗（用户 2026-09-23：插入 `TURNOVERRATE`
 *   后"怎么滚动到最后一行去了" ✓）。病根：编辑器是**受控**组件 ⇒ 父组件 `onChange` 后 React 会
 *   重设 `textarea.value` ✗ ⇒ 浏览器**把选区塌到值末尾** ✗ ⇒ 视图随之滚到最后一行 ✓；
 *   旧实现靠父组件 `requestAnimationFrame` 抢着补选区 ✗ ⇒ 时序不保证 ✓。
 *   ⇒ 改为编辑器**自带** `insertText()`（`FormulaEditorHandle` ✓）：插入时记下目标光标位 ✓，
 *     在 `useLayoutEffect([value])`（**DOM 更新后、绘制前** ✓）里恢复选区并滚到插入点 ✓ ⇒ 时序自洽 ✓。
 * ════════════════════════════════════════════════════════════════════════════════════════
 */
const M = {
  fontFamily:
    'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
  fontSize: 11.5,
  lineHeight: 18, // px —— 行号/高亮层/输入层都按它算高度
  padding: '6px 8px',
  paddingTop: 6, // 与 M.padding 的第一项一致（`revealCaret` 要按它算纵坐标 ✓）
  tabSize: 4,
} as const

/** 给**水平滚动条**留的高度（px）：滚动条属于宿主 ✓，会盖住最后一行 ✗
 *  （用户 2026-09-17：「公式编辑区最后一行被 X 轴滚动条挡住啦」）
 *  ⇒ 两层都在底部留出同样高度 ✓。 */
const SCROLLBAR = 16

// 着色：① `{…}` 注释 ② 数字 ③ 函数名（标识符紧跟 `(`）
const TOKEN_RE = /(\{[^}]*\})|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*(?=\s*\())/g

/** 每个字符的"着色类"（注释/数字/函数名）。逐字符标记是为了能和**查找高亮**叠加 ——
 *  命中有可能落在注释/数字/函数名内部（比如搜 `MA`），两层必须能在同一段文字上共存 ✓。 */
function tokenClasses(text: string): string[] {
  const tok = new Array<string>(text.length).fill('')
  let m: RegExpExecArray | null
  TOKEN_RE.lastIndex = 0
  while ((m = TOKEN_RE.exec(text)) !== null) {
    const cls =
      m[1] !== undefined
        ? 'text-slate-400 dark:text-slate-500 italic'   // {注释}：整段灰掉
        : m[2] !== undefined
          ? 'text-amber-600 dark:text-amber-400'        // 数字
          : 'text-blue-600 dark:text-blue-400'          // 函数名
    tok.fill(cls, m.index, m.index + m[0].length)
  }
  return tok
}

/** 查找命中的起点（大小写不敏感、不重叠）。 */
function matchRanges(text: string, query: string): number[] {
  if (!query) return []
  const hay = text.toLowerCase()
  const needle = query.toLowerCase()
  const out: number[] = []
  let i = hay.indexOf(needle)
  while (i >= 0) {
    out.push(i)
    i = hay.indexOf(needle, i + Math.max(1, needle.length))
  }
  return out
}

/** 按"连续同（着色类 + 高亮档）"合并成 span（公式就几百字符 ⇒ 逐字符合并开销可忽略 ✓）。 */
function renderText(
  text: string,
  toks: string[],
  hits: number[],
  hitIdx: number,
  qlen: number,
): ReactNode[] {
  const n = text.length
  if (n === 0) return []
  const tint = new Uint8Array(n)                 // 0 无 / 1 其它命中 / 2 当前命中
  hits.forEach((s, i) => tint.fill(i === hitIdx ? 2 : 1, s, Math.min(n, s + qlen)))
  const out: ReactNode[] = []
  let i = 0
  while (i < n) {
    const t = tint[i]
    const c = toks[i]
    let j = i + 1
    while (j < n && tint[j] === t && toks[j] === c) j++
    const cls = [
      c,
      t === 2
        ? 'bg-amber-400/70 dark:bg-amber-500/50 rounded-sm'
        : t === 1
          ? 'bg-yellow-200/60 dark:bg-yellow-600/25'
          : '',
    ]
      .filter(Boolean)
      .join(' ')
    const s = text.slice(i, j)
    out.push(cls ? <span key={`${i}-${j}`} className={cls}>{s}</span> : s)
    i = j
  }
  return out
}

/** ★ v1.20.56：给父组件的**编辑句柄**（取代原来的裸 textarea `innerRef` ✗）。
 *
 *  为什么必须换（用户 2026-09-23：「插入 `TURNOVERRATE` 后光标/视图跳到末尾」✗）：
 *  父组件自己 `onChange(...)` + `requestAnimationFrame(setSelectionRange)` ✗ ⇒ 抢不过 React
 *  对**受控** textarea 的 `value` 重设（浏览器会把选区塌到末尾 ✗）。
 *  交回编辑器做 ✓ ⇒ 它能在"DOM 已更新、尚未绘制"的 `useLayoutEffect` 里精准恢复光标 ✓。 */
export interface FormulaEditorHandle {
  /** 在**当前光标处**插入文本，并把光标放到插入内容之后（视图滚到该处 ✓，不跳到末尾 ✓） */
  insertText: (token: string) => void
  /** 聚焦编辑器 */
  focus: () => void
}

export interface FormulaEditorProps {
  value: string
  onChange: (v: string) => void
  onFocus?: () => void
  rows?: number
  placeholder?: string
  /** 拿到编辑句柄（「插入函数」用 ✓） */
  handleRef?: (h: FormulaEditorHandle | null) => void
}

export default function FormulaEditor({
  value,
  onChange,
  onFocus,
  rows = 6,
  placeholder,
  handleRef,
}: FormulaEditorProps) {
  const inner = useRef<HTMLTextAreaElement | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const gutterRef = useRef<HTMLDivElement | null>(null)
  const findInputRef = useRef<HTMLInputElement | null>(null)
  const [caret, setCaret] = useState(0)
  const [findOpen, setFindOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [hitIdx, setHitIdx] = useState(0)
  /** 待恢复的光标位（插入文本后 ✓；`useLayoutEffect` 消费一次 ✓） */
  const pendingCaret = useRef<number | null>(null)

  // ⚠ v1.19.99：`<textarea>` 由**浏览器按规范把 `\r\n`/`\r` 归一成 `\n`** 再排版 ✗ ⇒
  //   若行号槽与 `<pre>` 用**原始 value**，遇到从记事本/通达信粘进来的 **CRLF** 就会多算一次换行
  //   ⇒ 三层行盒错开一行 ✗。修法：**三层共用同一份归一化文本** ✓。
  const text = useMemo(() => value.replace(/\r\n?/g, '\n'), [value])
  const lines = useMemo(() => text.split('\n'), [text])
  const activeLine = useMemo(
    () => text.slice(0, Math.min(caret, text.length)).split('\n').length,
    [text, caret],
  )

  // 查找命中 + 着色层
  const hits = useMemo(() => matchRanges(text, query), [text, query])
  const toks = useMemo(() => tokenClasses(text), [text])
  // ⚠⚠ v1.19.100 铁律：**着色层必须由"一个表达式"产生** ✓ —— 两侧不留任何 JSX 空白、不追加尾随换行 ✗
  //   （否则着色层比 textarea 多出至少一行 ⇒ 从某行起逐行错开、最后一行永远选不到 ✗）。
  const backdrop = renderText(text, toks, findOpen ? hits : [], hitIdx, query.length)

  // 等宽字符宽度（canvas 实测 ⇒ 横向定位准；用于把光标/命中滚到可见区 ✓）
  const cwRef = useRef(0)
  const charWidth = () => {
    if (cwRef.current > 0) return cwRef.current
    try {
      const c = document.createElement('canvas').getContext('2d')
      if (c) {
        c.font = `${M.fontSize}px ${M.fontFamily}`
        cwRef.current = c.measureText('MMMMMMMMMM').width / 10
      }
    } catch {
      cwRef.current = 0
    }
    if (!cwRef.current) cwRef.current = M.fontSize * 0.6
    return cwRef.current
  }

  /** 把 textarea 自己的滚动归零 ✓ —— 它是 `overflow:hidden` 的滚动容器 ✗，
   *  浏览器"露光标"时会偷偷滚它 ⇒ 光标会画在错位置 ✗（真正的滚动只允许发生在宿主上 ✓）。 */
  const resetInnerScroll = () => {
    const a = inner.current
    if (a && (a.scrollTop !== 0 || a.scrollLeft !== 0)) {
      a.scrollTop = 0
      a.scrollLeft = 0
    }
  }

  /** 第 `pos` 个字符的行/列（按归一化文本 ✓，与 textarea 同一份串 ✓）。 */
  const lineColOf = (pos: number) => {
    const p = Math.max(0, Math.min(pos, text.length))
    const before = text.slice(0, p)
    return {
      line: before.split('\n').length - 1,
      col: p - (before.lastIndexOf('\n') + 1),
    }
  }

  /** 把第 `pos` 个字符滚进可视区（**滚宿主** ✓；已在区内就一动不动 ⇒ 打字时不乱跳 ✓）。 */
  const revealCaret = (pos: number) => {
    const host = hostRef.current
    if (!host) return
    const { line, col } = lineColOf(pos)
    const lh = M.lineHeight
    const gutW = gutterRef.current?.offsetWidth ?? 0
    const cw = charWidth()
    const top = M.paddingTop + line * lh              // 内容相对宿主的纵坐标 ✓
    const left = gutW + M.paddingTop + col * cw       // 横坐标（行号槽宽 + 内边距 ✓）
    if (top < host.scrollTop) {
      host.scrollTop = Math.max(0, top - lh)
    } else if (top + lh > host.scrollTop + host.clientHeight) {
      host.scrollTop = top + lh - host.clientHeight
    }
    if (left < host.scrollLeft) {
      host.scrollLeft = Math.max(0, left - gutW - 8)
    } else if (left + cw > host.scrollLeft + host.clientWidth - 8) {
      host.scrollLeft = left + cw - host.clientWidth + 8
    }
    resetInnerScroll()
  }

  /** 把第 `start` 个字符处的命中滚到**可视区中间**（用户 2026-09-17：Ctrl+F 查找要能自动滚动定位 ✓）。 */
  const scrollToMatch = (start: number, len: number) => {
    const host = hostRef.current
    if (!host) return
    const { line, col } = lineColOf(start)
    const lh = M.lineHeight
    const gutW = gutterRef.current?.offsetWidth ?? 0
    const cw = charWidth()
    const top = M.paddingTop + line * lh
    host.scrollTop = Math.max(0, Math.min(top - (host.clientHeight - lh) / 2,
                                         host.scrollHeight - host.clientHeight))
    const left = gutW + M.paddingTop + col * cw
    host.scrollLeft = Math.max(0, Math.min(left - (host.clientWidth - len * cw) / 2,
                                           host.scrollWidth - host.clientWidth))
    resetInnerScroll()
  }

  const jumpTo = (idx: number) => {
    const a = inner.current
    if (!a || hits.length === 0) return
    const n = ((idx % hits.length) + hits.length) % hits.length
    setHitIdx(n)
    const start = hits[n]
    const len = query.length
    // 查找条**开着**时焦点留在输入框里（否则没法连续打字/回车 ✗）—— 命中位置由高亮层的底色标出；
    // 关着时（F3 / Ctrl+G 触发）才把焦点给编辑框 ✓
    if (!findOpen) a.focus()
    a.setSelectionRange(start, start + len)
    setCaret(start)
    scrollToMatch(start, len)
  }

  // 搜索词一变就**跳到第一个命中并滚过去**（"边打边找"的手感 ✓）
  useEffect(() => {
    if (findOpen && hits.length > 0) jumpTo(Math.min(hitIdx, hits.length - 1))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hits])

  // ★★ v1.20.56：**插入后恢复光标** ✓ —— 必须在 `useLayoutEffect`（DOM 更新完、尚未绘制 ✓）里做，
  //   否则 React 重设 `value` 时浏览器已经把选区塌到末尾 ✗（= 用户看到的"跳到最后一行" ✓）。
  useLayoutEffect(() => {
    const p = pendingCaret.current
    if (p == null) return
    pendingCaret.current = null
    const a = inner.current
    if (!a) return
    const pos = Math.max(0, Math.min(p, text.length))
    a.focus()
    a.setSelectionRange(pos, pos)
    setCaret(pos)
    revealCaret(pos)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const insertText = (token: string) => {
    const a = inner.current
    if (!a) return
    const s = Math.min(a.selectionStart ?? text.length, text.length)
    const e = Math.min(Math.max(a.selectionEnd ?? s, s), text.length)
    pendingCaret.current = s + token.length
    onChange(text.slice(0, s) + token + text.slice(e))
  }

  const handle = useMemo<FormulaEditorHandle>(
    () => ({
      insertText,
      focus: () => inner.current?.focus(),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text, onChange],
  )
  useEffect(() => {
    handleRef?.(handle)
    return () => handleRef?.(null)
  }, [handle, handleRef])

  const openFind = () => {
    const a = inner.current
    // 有选中内容 ⇒ 直接拿来当搜索词（省一次输入 ✓）
    if (a && a.selectionEnd > a.selectionStart) {
      setQuery(text.slice(a.selectionStart, a.selectionEnd))
    }
    setHitIdx(0)
    setFindOpen(true)
    requestAnimationFrame(() => findInputRef.current?.focus())
  }

  /** 关闭查找面板：**必须把选中态取消**（用户 2026-09-17：「取消搜索后为啥关键字还是选中状态？」）
   *  ⇒ 把光标**收到当前命中的末尾**（不改内容、不留选区）+ 清掉高亮底色 ✓。 */
  const closeFind = (focusEditor = true) => {
    setFindOpen(false)
    const a = inner.current
    if (a) {
      if (hits.length > 0) {
        const start = hits[Math.min(hitIdx, hits.length - 1)]
        const end = start + query.length
        a.setSelectionRange(end, end)
        setCaret(end)
      }
      if (focusEditor) a.focus()
    }
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
      e.preventDefault()
      closeFind(false)          // 焦点本来就在编辑框里 ⇒ 不夺回焦点，但**要取消选中** ✓
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
    // ⚠ 底部再留出**水平滚动条**的高度：否则最后一行会被滚动条盖住 ✗（滚动条现在属于宿主 ✓）
    paddingBottom: SCROLLBAR,
    tabSize: M.tabSize,
    whiteSpace: 'pre' as const,
    margin: 0,
    border: 0,
  }
  const bodyHeight = rows * M.lineHeight + 12 + SCROLLBAR

  return (
    // ⚠ **可纵向拖动拉长**（用户 2026-09-17 要求保留 ✓）：拖动改的是**外层内联 height**，
    //   而 `bodyHeight` 在 React 里是**常量**、不参与 diff ⇒ 重渲染不会把用户拖出来的高度冲掉 ✓。
    //   ✗ 千万别把它改成随 state 变化的动态值（那样每次渲染都会弹回默认高度）。
    <>
      <div
        className="flex flex-col border rounded bg-white dark:bg-slate-800 overflow-hidden resize-y"
        style={{ height: bodyHeight, minHeight: M.lineHeight * 3 + 12 }}
      >
        {/* ★ v1.20.56：**唯一的滚动宿主** ✓ —— 三层都是它的普通子元素 ⇒ 浏览器原生同步滚动 ✓
            ⇒ **不需要任何 JS 同步** ✓（旧版靠 `transform` 跟随 ⇒ 合成器/主线程差一帧 ⇒ 重影 ✗）。 */}
        <div ref={hostRef} className="flex-1 min-h-0 overflow-auto">
          <div className="flex items-stretch" style={{ minWidth: '100%' }}>
            {/* 行号槽：`sticky left-0` ⇒ 横向滚动时钉住 ✓、纵向随宿主自然滚 ✓（零同步 ✓）。
                ⚠ 必须有**不透明底色**（正文会滑到它下面 ✓）+ 更高 `z-index` ✓。 */}
            <div
              ref={gutterRef}
              aria-hidden
              className="sticky left-0 z-10 shrink-0 select-none pointer-events-none text-right text-slate-400 dark:text-slate-500 bg-slate-50 dark:bg-slate-900 border-r border-slate-200 dark:border-slate-700"
              style={{
                ...metricsStyle,
                position: 'sticky',
                width: `${Math.max(2, String(lines.length).length) + 1.5}ch`,
              }}
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
            {/* 内容列：`<pre>` 在**正常流**里定宽高 ✓；透明 textarea 绝对覆盖其上（同一份 metrics ✓）。
                ⚠ textarea `overflow:hidden` ⇒ **它自己永不滚动** ✓（滚动只发生在宿主 ✓）。 */}
            <div className="relative flex-1">
              <pre
                aria-hidden
                className="pointer-events-none text-slate-800 dark:text-slate-100"
                style={metricsStyle}
              >{backdrop}</pre>
              <textarea
                ref={(el) => {
                  inner.current = el
                }}
                value={text}
                onChange={(e) => {
                  // ⚠⚠ v1.19.101 **归一化兜底**（拖拽/输入法/插件/程序化赋值都不走 `onPaste` ✗）
                  //   ⇒ `\r` 会滞留在 textarea 的 DOM 值里，而着色层用的是已归一化的 `text`
                  //   ⇒ 两层**行数不同** ⇒ 光标/选区错行 ✗。
                  //   ⚠ 但不能无脑 replace：受控 textarea 的值一旦被改写，React 会重设 DOM ⇒ 光标被扔到末尾 ✗。
                  //   ⇒ 折中：**只有真含 `\r` 时才动**（极少 ✓），并按删掉的字符数把光标还原 ✓。
                  const el = e.currentTarget
                  const raw = el.value
                  const pos = el.selectionStart
                  const head = raw.slice(0, pos)
                  const cleanHead = head.replace(/\r\n?/g, '\n')
                  if (cleanHead === head) {
                    onChange(raw)                       // 99.9% 的输入：路径与原来完全一致 ✓
                    setCaret(pos)
                    return
                  }
                  const clean = raw.replace(/\r\n?/g, '\n')
                  onChange(clean)
                  // ⚠⚠ 必须**手工把 DOM 值也纠正**（`el.value = clean`）✗ —— 否则 React 会认为
                  //   "新旧 value 相同"（归一化后的 clean 常常正好等于上一轮的 text ✓）⇒ **不重设 DOM**
                  //   ⇒ `\r` 继续留在 DOM 里 ✗ ⇒ 两层差 1 行 ✗（v1.19.101 根治的坑 ✓）。
                  el.value = clean
                  const np = cleanHead.length            // 光标左移被删掉的 `\r` 个数 ✓
                  requestAnimationFrame(() => {
                    el.setSelectionRange(np, np)
                    setCaret(np)
                  })
                }}
                onPaste={(e) => {
                  // 粘贴是 `\r` 的主要入口 ⇒ 这里就清掉（手工插入以保住光标 ✓，与上面的兜底互为保险 ✓）
                  const raw = e.clipboardData?.getData('text')
                  if (raw == null) return
                  const clean = raw.replace(/\r\n?/g, '\n')
                  e.preventDefault()
                  const el = e.currentTarget
                  const s = el.selectionStart
                  const en = el.selectionEnd
                  onChange(text.slice(0, s) + clean + text.slice(en))
                  requestAnimationFrame(() => {
                    const pos = s + clean.length
                    el.setSelectionRange(pos, pos)
                    setCaret(pos)
                  })
                }}
                onScroll={resetInnerScroll}     // ⚠ 浏览器"露光标"会偷滚它 ⇒ 归零 ✓
                onKeyDown={onKeyDown}
                onKeyUp={(e) => {
                  resetInnerScroll()
                  setCaret(e.currentTarget.selectionStart)
                }}
                onClick={(e) => setCaret(e.currentTarget.selectionStart)}
                onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
                onFocus={() => onFocus?.()}
                wrap="off"
                spellCheck={false}
                placeholder={placeholder}
                className="absolute inset-0 resize-none outline-none overflow-hidden bg-transparent placeholder:text-slate-400"
                style={{
                  ...metricsStyle,
                  color: 'transparent',
                  caretColor: 'rgb(15 23 42)',
                  overflowWrap: 'normal',
                }}
              />
            </div>
          </div>
        </div>
        {/* 查找条在编辑框**外面**（v1.20.54 用户报"搜索时重叠" ✗ ⇒ 移到外部后绝不会被裁切/挤压 ✓） */}
        {findOpen && (
          <div className="mt-1 flex items-center gap-1 px-2 py-1 rounded border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 text-[11px]">
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
                  closeFind()      // 关面板 + 取消选中 + 焦点回编辑框 ✓
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
              onClick={() => {
                jumpTo(hitIdx - 1)
                findInputRef.current?.focus()   // 切完继续留在查找框（能接着打字/回车）
              }}
              title="上一个 (Shift+Enter)"
              className="px-1.5 rounded border text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              ↑
            </button>
            <button
              type="button"
              onClick={() => {
                jumpTo(hitIdx + 1)
                findInputRef.current?.focus()
              }}
              title="下一个 (Enter / F3)"
              className="px-1.5 rounded border text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              ↓
            </button>
            <button
              type="button"
              onClick={() => closeFind()}
              title="关闭 (Esc)"
              className="px-1.5 rounded border text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              ✕
            </button>
          </div>
        )}
      </div>
    </>
  )
}
