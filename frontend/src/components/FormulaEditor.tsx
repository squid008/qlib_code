import { useEffect, useMemo, useRef, useState } from 'react'
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

/** 给**水平滚动条**留的高度（px）：两层都是 `overflow:auto`，横向滚动条会盖住最后一行 ✗
 *  （用户 2026-09-17：「公式编辑区最后一行被 X 轴滚动条挡住啦」）
 *  ⇒ `<pre>` 与 textarea **两层都**在底部留出同样高度，并把它算进默认高度 ✓。 */
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

  // ⚠⚠ v1.19.99（2026-09-18 修 **CRLF 导致选区/高亮错行**）：本编辑器是**三层叠加**
  //   （行号槽 + `<pre>` 高亮层 + 透明 `<textarea>`）。`<textarea>` 由**浏览器按规范把
  //   `\r\n`/`\r` 原生归一成 `\n`** 再排版 ✗ —— 若行号槽与 `<pre>` 用**原始 value**，
  //   遇到从记事本/通达信粘进来的 **CRLF** 文本就会**多算一次换行** ⇒ 两层行盒**错开一行**
  //   ⇒ 光标/选区画到下一行（用户实测：鼠标在第 20 行拖动、高亮的却是第 21 行的字 ✗，
  //   而行号显示本身是"对"的 ✓）。修法：**三层共用同一份归一化文本** ✓。
  const text = useMemo(() => value.replace(/\r\n?/g, '\n'), [value])
  const lines = useMemo(() => text.split('\n'), [text])
  const activeLine = useMemo(
    () => text.slice(0, caret).split('\n').length,
    [text, caret],
  )

  // 查找命中 + 着色层
  const hits = useMemo(() => matchRanges(text, query), [text, query])
  const toks = useMemo(() => tokenClasses(text), [text])
  // ⚠⚠ v1.19.100 **着色层必须是"一个表达式"**（2026-09-18 第三次修，前两次治标 ✗）：
  //   原写法是 `<pre> {renderText(...)} {'\n'} </pre>` —— React 会把**表达式之间的缩进/换行
  //   也渲染进 `<pre>`**（`whiteSpace:'pre'` 下空白全部可见 ✗），再加那个**尾随 `{'\n'}`**
  //   ⇒ 着色层比 `<textarea>` **多出至少一行** ⇒ 两层行盒**从某行起逐行错开**（用户实测：
  //   光标在第 28 行、高亮画第 29 行的字 ✗），且末尾多出的行被 `overflow:hidden` **裁掉**
  //   ⇒ **最后一行永远选不到** ✗。根因**不是 CRLF**（那只影响换行语义 ✓ 已另修 ✓），
  //   而是**着色层的行数 ≠ 输入层的行数** ✗。
  //   ⇒ 铁律：**着色层只能由一个表达式产生、两侧不留任何 JSX 空白、不追加尾随换行** ✓，
  //     这样它的行数**恒等于** `text.split('\n').length`，与行号槽 / textarea **三方同源** ✓。
  const backdrop = renderText(text, toks, findOpen ? hits : [], hitIdx, query.length)

  const syncScroll = () => {
    const a = inner.current
    if (!a) return
    // ⚠⚠ v1.19.103 **改用 `transform` 位移跟随**（2026-09-18 用户第 5 次反馈"还有重影" ✓）：
    //   旧写法给 `<pre>`/行号槽也设 `scrollTop` ⇒ 要求它们**自身可滚** ✗ ⇒ 于是必须让三者的
    //   `scrollHeight/clientHeight/滚动条占位`**逐像素相同** ✗ —— 而滚动条宽高（15~17px ≈
    //   一行高 18px）随"是否需要滚动"动态变化 ⇒ 永远对不齐 ⇒ **越滚越错、重影** ✗。
    //   ⇒ 改为：**只有 textarea 滚动** ✓，另两层 `overflow:hidden` + **`transform: translate()`
    //     反向位移** ⇒ 完全不依赖滚动盒尺寸 ⇒ 误差源从根上消失 ✓。
    if (gutterRef.current) {
      gutterRef.current.style.transform = `translateY(${-a.scrollTop}px)`
    }
    if (backdropRef.current) {
      backdropRef.current.style.transform = `translate(${-a.scrollLeft}px, ${-a.scrollTop}px)`
    }
  }

  // 等宽字符宽度（canvas 实测 ⇒ 横向定位准；用于把命中滚到可见区中间）
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

  /** 把第 `start` 个字符处的命中滚到**可视区中间**（用户 2026-09-17：「按 Ctrl+F 查找要能
   *  配合滚动条自动滚到那个位置，鼠标按那两个上下箭头也要能自动滚动定位到那里」）。
   *  ⚠ 只靠 `setSelectionRange` **不保证**滚动（未聚焦时尤其不动）⇒ 这里显式算出行/列并设置
   *    `scrollTop` / `scrollLeft`，再 `syncScroll()` 让高亮层与行号槽跟上 ✓。 */
  const scrollToMatch = (start: number, len: number) => {
    const a = inner.current
    if (!a) return
    // ⚠ 用归一化后的 `text`（与 textarea 同一份串）⇒ 行列换算不会因 CRLF 偏一行 ✓
    const line = text.slice(0, start).split('\n').length - 1
    const col = start - (text.lastIndexOf('\n', start - 1) + 1)
    const lh = M.lineHeight
    const top = line * lh - (a.clientHeight - lh) / 2
    a.scrollTop = Math.max(0, Math.min(top, a.scrollHeight - a.clientHeight))
    const cw = charWidth()
    const left = col * cw - (a.clientWidth - len * cw) / 2
    a.scrollLeft = Math.max(0, Math.min(left, a.scrollWidth - a.clientWidth))
    syncScroll()                     // 程序化滚动不会自动触发 sync ⇒ 手动来一次（保险）
  }

  const jumpTo = (idx: number) => {
    const a = inner.current
    if (!a || hits.length === 0) return
    const n = ((idx % hits.length) + hits.length) % hits.length
    setHitIdx(n)
    const start = hits[n]
    const len = query.length
    // 查找条**开着**时焦点留在输入框里（否则没法连续打字/回车 ✗）—— 命中位置由高亮层的
    // 底色标出（当前命中=琥珀、其它命中=淡黄）；关着时（F3 / Ctrl+G 触发）才把焦点给编辑框 ✓
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

  /** 关闭查找面板：**必须把选中态取消**（用户 2026-09-17：「取消搜索（Esc 或点关闭按钮）后
   *  为啥那些关键字还是选中状态？」）—— 之前只 `setFindOpen(false)`，而 `jumpTo` 留下的
   *  `setSelectionRange` 还在 ⇒ 面板一关、焦点回到编辑框，那段选区就显形了 ✗。
   *  处理：把光标**收到当前命中的末尾**（不改内容、不留选区）+ 清掉高亮底色 ✓。 */
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
    // ⚠ 底部再留出**水平滚动条**的高度：否则最后一行会被 X 轴滚动条盖住 ✗
    //   （两层都要留同样高度、且 `bodyHeight` 里也把它算进去 ⇒ 默认高度仍显示 `rows` 行 ✓）
    paddingBottom: SCROLLBAR,
    tabSize: M.tabSize,
    whiteSpace: 'pre' as const,
    margin: 0,
    border: 0,
  }
  const bodyHeight = rows * M.lineHeight + 12 + SCROLLBAR

  return (
    // ⚠ **可纵向拖动拉长**（用户 2026-09-17：「之前那个可以拖动拉长的给我加回来」——
    //   旧版是原生 `<textarea>`，Tailwind preflight 给它 `resize: vertical`，换成双层编辑框后
    //   我写死了高度 ⇒ 拖拽把手没了 ✗）。
    //   实现：拖动改的是**外层的内联 `height`**（浏览器直接写 style），而 `bodyHeight` 在 React 里
    //   是**常量**、不参与 diff ⇒ 之后的重渲染**不会**把用户拖出来的高度冲掉 ✓。
    //   ✗ 千万别把 height 改成随 state 变化的动态值（那样每次渲染都会弹回默认高度）。
    <>
    <div
      className="flex flex-col border rounded bg-white dark:bg-slate-800 overflow-hidden resize-y"
      style={{ height: bodyHeight, minHeight: M.lineHeight * 3 + 12 }}
    >
      <div className="flex flex-1 min-h-0">
        {/* 行号槽：与输入层同步滚动（overflow:hidden + 程序化 scrollTop） */}
        <div
          ref={gutterRef}
          aria-hidden
          className="shrink-0 select-none pointer-events-none overflow-hidden text-right text-slate-400 dark:text-slate-500 bg-slate-50 dark:bg-slate-900 border-r border-slate-200 dark:border-slate-700"
          // ⚠ v1.19.103：`overflow:hidden` + `syncScroll` 用 `transform` 位移跟随 ✓
          //   （**绝不**再让本层自己滚 ✗ —— 滚动条尺寸会变成"一行"的误差源 ✓）。
          //   `alignSelf:flex-start` + 高度 auto ⇒ 内容多长就多高，位移后底部不露白 ✓。
          style={{
            ...metricsStyle,
            overflow: 'hidden',
            alignSelf: 'flex-start',
            height: 'auto',
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
        {/* 双层：底层 <pre> 画彩色文字，上层透明 textarea 接管输入/光标/选区 */}
        <div className="relative flex-1 min-w-0">
          <pre
            ref={backdropRef}
            aria-hidden
            // ⚠ `inset-0` 会**钉死高度 = 容器高** ✗ ⇒ 配合 `transform` 位移时**底部会露白** ✗
            //   ⇒ 改成只钉上/左/右（高度由**内容**决定 ✓），位移多远都有内容 ✓。
            className="absolute top-0 left-0 w-full pointer-events-none text-slate-800 dark:text-slate-100"
            // ⚠ v1.19.103：**本层永不自己滚动** ✓ —— `overflow:hidden`，跟随靠 `transform`（syncScroll ✓）
            //   ⇒ 与 textarea 的滚动条占位无关 ⇒ 不再差那十几像素（用户 2026-09-18 第 5 次反馈 ✓）。
            style={{ ...metricsStyle, overflow: 'hidden' }}
          >{backdrop}</pre>
          <textarea
            ref={(el) => {
              inner.current = el
              innerRef?.(el)
            }}
            value={text}
            onChange={(e) => {
              // ⚠⚠ v1.19.101 **归一化必须在这里兜底**（用户 2026-09-18：「复制进来的时候自动把
              //   换行符转成没问题的形式不就得了」✓ 说得对 ✓）—— 只在 `onPaste` 清 `\r` 不够 ✗：
              //   拖拽/输入法/插件/程序化赋值都不会走 `onPaste` ⇒ `\r` 会滞留在 **textarea 的 DOM 值**
              //   里，而着色层用的是已归一化的 `text` ⇒ **两层永远差 1 行** ✗（现象：第 28 行位置
              //   画出第 29 行的字、最后一行在着色层里没有对应 ⇒ "选不到" ✗）。
              // ⚠ 但**不能无脑 replace**：受控 textarea 的值一旦被改写，React 会重设 DOM ⇒ 光标
              //   被扔到末尾 ✗（上一轮踩过 ✓）。⇒ 折中：**只有真含 `\r` 时才动**（极少 ✓），
              //   并**按删掉的字符数把光标还原** ✓；不含 `\r` 时走原路、绝不动 DOM ✓。
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
              //   ⇒ `\r` 继续留在 textarea 的 DOM 值里 ✗ ⇒ 着色层（用干净的 `text`）与它**差 1 行**
              //   ⇒ 就是用户看到的"第 28 行显示第 29 行的字、最后一行选不到" ✗✓✓（这次根治 ✓）。
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
      </div>
      {/* ★ v1.20.54：查找条移到编辑框**外面** ✓
          原来它是编辑框（固定 `height` + `overflow-hidden` ✗）的**内部子元素** ⇒ 一出现就要么
          挤掉编辑区一行、要么溢出到下方提示文字上 ⇒ 用户 2026-09-23 截图报"搜索的时候重叠了" ✗。
          移到外部后：① 绝不会被裁切/挤压 ✓；② 查找时编辑区**保持原高度** ✓；③ 与下方提示各自成行 ✓。 */}
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
    </>
  )
}
