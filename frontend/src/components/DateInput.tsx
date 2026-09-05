import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'

// 年月日三段数字输入 + 日历按钮（外观接近原生 date 输入框）。
// 实现：三段为【非受控输入 + refs 直接读写 DOM value】——每段输入即时反映，不依赖 state
// 中间态，杜绝"快速输入时某段少一位/变 00/01 串位"问题。
// - 输入满 4 位年份自动跳到月份、满 2 位月份自动跳到日期；Backspace 空段跳回上一段
// - 失焦只对"单数字月/日"补零（1→01），年不足 4 位不补（避免出现 0202）
// - 外部 value 变化（复用历史/日历选定）在非编辑态同步写回三段
// - 右侧日历按钮：弹出月历，点日期直接选定
// - onComplete：日期"填到完整"时触发（日输入满 2 位或日历选定），供父组件跳到下一个日期框
// - 暴露 focusYear()：让"结束日期"的年份框聚焦
export interface DateInputHandle {
  focusYear: () => void
}
interface DateInputProps {
  value: string // 'YYYY-MM-DD'
  onChange: (v: string) => void
  className?: string
  onComplete?: () => void
  fontSize?: string // 三段数字的字体大小 class，默认 text-xs（单因子面板用）；回测表单可传 text-sm 与 TOPK 等输入对齐
}

const LENS = [4, 2, 2]
const WEEK_HEAD = ['一', '二', '三', '四', '五', '六', '日']

function splitValue(v: string): string[] {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '')
  return m ? [m[1], m[2], m[3]] : ['', '', '']
}

function joinParts(p: string[]): string {
  return /^\d{4}$/.test(p[0]) && /^\d{2}$/.test(p[1]) && /^\d{2}$/.test(p[2])
    ? `${p[0]}-${p[1]}-${p[2]}`
    : ''
}

function validDate(y: string, m: string, d: string): boolean {
  const Y = +y
  const M = +m
  const D = +d
  return Y >= 1900 && Y <= 2100 && M >= 1 && M <= 12 && D >= 1 && D <= 31
}

const DateInput = forwardRef<DateInputHandle, DateInputProps>(function DateInput(
  { value, onChange, className = '', onComplete, fontSize = 'text-xs' },
  ref,
) {
  const refs = [
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
  ]
  const wrapRef = useRef<HTMLSpanElement>(null)
  const [editing, setEditing] = useState(false)
  const [showCal, setShowCal] = useState(false)
  const [cal, setCal] = useState(() => calOf(value))

  const setSeg = (i: number, v: string) => {
    const el = refs[i].current
    if (el) el.value = v
  }
  const readParts = (): string[] =>
    refs.map((r) => (r.current ? r.current.value : ''))

  // 编辑过程中外部 value 变化不覆盖正在输入的三段；否则（外部重置/日历选定/复用历史）同步写回
  useEffect(() => {
    if (editing) return
    splitValue(value).forEach((v, i) => setSeg(i, v))
  }, [value, editing])

  // 点击组件外部关闭日历
  useEffect(() => {
    if (!showCal) return
    const h = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setShowCal(false)
    }
    window.addEventListener('mousedown', h)
    return () => window.removeEventListener('mousedown', h)
  }, [showCal])

  useImperativeHandle(ref, () => ({
    focusYear: () => refs[0].current?.focus(),
  }))

  const emit = (p: string[]) => {
    const v = joinParts(p)
    if (v && validDate(p[0], p[1], p[2])) onChange(v)
  }

  const changeAt = (i: number, raw: string) => {
    const s = raw.replace(/\D/g, '').slice(0, LENS[i])
    setSeg(i, s) // 直接写 DOM，即时生效
    if (s.length === LENS[i]) {
      if (i < 2) {
        refs[i + 1].current?.focus() // 年满4→月；月满2→日
      } else {
        emit(readParts()) // 日满2 → 完整提交（读各段最新 DOM 值）
        onComplete?.()
      }
    }
  }

  const onKey = (i: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && (e.target as HTMLInputElement).value === '' && i > 0) {
      refs[i - 1].current?.focus()
    }
  }

  const onBlur = () => {
    setEditing(false)
    // 月/日单数字补零（1→01）；年必须是完整 4 位，不足不补（避免 0202 等假值）
    const norm = (x: string, i: number) => {
      if (!x) return x
      if (i === 0) return x.length === 4 ? x : x
      return x.length === 1 ? `0${x}` : x
    }
    const p = readParts().map(norm)
    p.forEach((v, i) => setSeg(i, v))
    emit(p)
  }

  const pickDate = (dStr: string) => {
    splitValue(dStr).forEach((v, i) => setSeg(i, v))
    onChange(dStr)
    onComplete?.()
    setShowCal(false)
  }

  const today = new Date()
  const cy = cal.y
  const cm = cal.m
  const firstDow = (new Date(cy, cm - 1, 1).getDay() + 6) % 7 // 周一=0
  const daysInMonth = new Date(cy, cm, 0).getDate()
  const cells: (string | null)[] = [
    ...Array.from({ length: firstDow }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => {
      const d = String(i + 1).padStart(2, '0')
      return `${cy}-${String(cm).padStart(2, '0')}-${d}`
    }),
  ]
  const currentFull = joinParts(readParts())
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`

  return (
    <span
      ref={wrapRef}
      className={`relative inline-flex items-center border rounded px-1.5 py-1 bg-white dark:bg-slate-900 ${className}`}
    >
      {[0, 1, 2].map((i) => (
        <span key={i} className="inline-flex items-center">
          <input
            ref={refs[i]}
            defaultValue=""
            inputMode="numeric"
            placeholder={['YYYY', 'MM', 'DD'][i]}
            onChange={(e) => changeAt(i, e.target.value)}
            onKeyDown={(e) => onKey(i, e)}
            onFocus={(e) => {
              setEditing(true)
              e.target.select()
            }}
            onBlur={onBlur}
            maxLength={LENS[i]}
            className={`bg-transparent outline-none px-1 text-center ${fontSize} text-slate-700 dark:text-slate-200 ${
              i === 0 ? 'w-[4rem]' : 'w-[2.2rem]'
            }`}
          />
          {i < 2 && <span className="text-slate-400 select-none">-</span>}
        </span>
      ))}
      <button
        type="button"
        title="选择日期"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => {
          setCal(calOf(joinParts(readParts()) || value))
          setShowCal((s) => !s)
        }}
        className="ml-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 px-0.5"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      </button>

      {showCal && (
        <div className="absolute top-full mt-1 right-0 z-50 w-[16rem] bg-white dark:bg-slate-800 border rounded-lg shadow-lg p-2 text-xs">
          <div className="flex items-center justify-between mb-1">
            <button
              type="button"
              onClick={() => setCal(prevCal(cy, cm, -1))}
              className="px-1.5 py-0.5 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              ‹
            </button>
            <span className="font-semibold text-slate-600 dark:text-slate-200">
              {cy} 年 {cm} 月
            </span>
            <button
              type="button"
              onClick={() => setCal(prevCal(cy, cm, 1))}
              className="px-1.5 py-0.5 rounded text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              ›
            </button>
          </div>
          <div className="grid grid-cols-7 text-center text-slate-400 mb-1">
            {WEEK_HEAD.map((w) => (
              <span key={w} className="py-0.5">
                {w}
              </span>
            ))}
          </div>
          <div className="grid grid-cols-7 text-center">
            {cells.map((c, idx) =>
              c === null ? (
                <span key={`b${idx}`} />
              ) : (
                <button
                  key={c}
                  type="button"
                  onClick={() => pickDate(c)}
                  className={`py-0.5 rounded hover:bg-blue-100 dark:hover:bg-blue-900/40 ${
                    c === currentFull
                      ? 'bg-blue-600 text-white hover:bg-blue-600'
                      : c === todayStr
                        ? 'text-blue-600 dark:text-blue-300 font-semibold'
                        : 'text-slate-600 dark:text-slate-300'
                  }`}
                >
                  {c.slice(8)}
                </button>
              ),
            )}
          </div>
        </div>
      )}
    </span>
  )
})

function calOf(value: string): { y: number; m: number } {
  const m = /^(\d{4})-(\d{2})/.exec(value || '')
  if (m) {
    const y = +m[1]
    const mo = +m[2]
    if (y >= 1900 && y <= 2100 && mo >= 1 && mo <= 12) return { y, m: mo }
  }
  const n = new Date()
  return { y: n.getFullYear(), m: n.getMonth() + 1 }
}

function prevCal(y: number, m: number, delta: number): { y: number; m: number } {
  const d = new Date(y, m - 1 + delta, 1)
  return { y: d.getFullYear(), m: d.getMonth() + 1 }
}

export default DateInput
