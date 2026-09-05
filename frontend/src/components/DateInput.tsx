import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'

// 年月日三段数字输入，外观接近原生 date 输入框（单容器，内嵌无边框三段）。
// - 输入满 4 位年份自动跳到月份、满 2 位月份自动跳到日期；Backspace 空段跳回上一段
// - 失焦只对"单数字月/日"补零（1→01），年不足 4 位不补（避免出现 0202 这类假值）
// - 编辑期间不受外部 value 重置干扰；日期完整合法才对外回调完整 YYYY-MM-DD
// - onComplete：某次把日期"填到完整"时触发（日输入满 2 位时），供父组件做跨日期框跳转
// - 暴露 focusYear()：让"结束日期"的年份框聚焦（开始日期输完 → 自动跳过来）
export interface DateInputHandle {
  focusYear: () => void
}
interface DateInputProps {
  value: string // 'YYYY-MM-DD'
  onChange: (v: string) => void
  className?: string
  onComplete?: () => void
}

const LENS = [4, 2, 2]

function splitValue(v: string): string[] {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '')
  return m ? [m[1], m[2], m[3]] : ['', '', '']
}

function joinParts(p: string[]): string {
  return /^\d{4}$/.test(p[0]) && /^\d{2}$/.test(p[1]) && /^\d{2}$/.test(p[2])
    ? `${p[0]}-${p[1]}-${p[2]}`
    : ''
}

function isValidDate(y: string, mo: string, d: string): boolean {
  const yy = +y
  const mm = +mo
  const dd = +d
  return yy >= 1900 && yy <= 2100 && mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31
}

const DateInput = forwardRef<DateInputHandle, DateInputProps>(function DateInput(
  { value, onChange, className = '', onComplete },
  ref,
) {
  const refs = [
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
  ]
  const [parts, setParts] = useState<string[]>(() => splitValue(value))
  const [editing, setEditing] = useState(false)

  // 编辑过程中外部 value 变化不覆盖正在输入的三段；失焦后再与外部值保持同步
  useEffect(() => {
    if (!editing) setParts(splitValue(value))
  }, [value, editing])

  useImperativeHandle(ref, () => ({
    focusYear: () => refs[0].current?.focus(),
  }))

  const emit = (p: string[]) => {
    const v = joinParts(p)
    if (v && isValidDate(p[0], p[1], p[2])) onChange(v)
  }

  const changeAt = (i: number, raw: string) => {
    const s = raw.replace(/\D/g, '').slice(0, LENS[i])
    const p = parts.map((x, k) => (k === i ? s : x))
    setParts(p)
    if (s.length === LENS[i]) {
      if (i < 2) {
        refs[i + 1].current?.focus() // 年满4→月；月满2→日
      } else {
        emit(p) // 日满2 → 完整提交
        onComplete?.() // 供父组件把焦点跳到下一个日期框
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
    // 月/日单数字补零（1→01）；年必须是完整 4 位，不足不补（避免补出 0202 等假值）
    const p = parts.map((x, i) => {
      if (!x) return x
      if (i === 0) return x.length === 4 ? x : x
      return x.length === 1 ? `0${x}` : x
    })
    setParts(p)
    emit(p)
  }

  return (
    <span
      className={`inline-flex items-center border rounded px-1.5 py-1 bg-white dark:bg-slate-900 ${className}`}
    >
      {parts.map((p, i) => (
        <span key={i} className="inline-flex items-center">
          <input
            ref={refs[i]}
            value={p}
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
            className={`bg-transparent outline-none px-0.5 text-center text-xs text-slate-700 dark:text-slate-200 ${
              i === 0 ? 'w-[3.4rem]' : 'w-[1.7rem]'
            }`}
          />
          {i < 2 && <span className="text-slate-400 select-none">-</span>}
        </span>
      ))}
    </span>
  )
})

export default DateInput
