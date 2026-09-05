import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

// 年月日三段数字输入：键盘输入满 4 位年份自动跳到月份，满 2 位月份自动跳到日期；
// 只有年月日都完整且合法时才对外回调完整 YYYY-MM-DD（避免把中间态传给表单校验）。
interface DateInputProps {
  value: string // 'YYYY-MM-DD'
  onChange: (v: string) => void
  className?: string
}

const LENS = [4, 2, 2]

function splitValue(v: string): string[] {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '')
  return m ? [m[1], m[2], m[3]] : ['', '', '']
}

export default function DateInput({ value, onChange, className = '' }: DateInputProps) {
  const refs = [
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
    useRef<HTMLInputElement>(null),
  ]
  const [parts, setParts] = useState<string[]>(() => splitValue(value))

  // 外部 value 变化（复用历史/重置）时同步三段
  useEffect(() => {
    setParts(splitValue(value))
  }, [value])

  const emit = (p: string[]) => {
    if (p[0].length !== 4 || p[1].length !== 2 || p[2].length !== 2) return
    const y = +p[0]
    const mo = +p[1]
    const d = +p[2]
    if (!(y >= 1900 && y <= 2100 && mo >= 1 && mo <= 12 && d >= 1 && d <= 31)) return
    onChange(`${p[0]}-${p[1]}-${p[2]}`)
  }

  const changeAt = (i: number, raw: string) => {
    const s = raw.replace(/\D/g, '').slice(0, LENS[i])
    const p = parts.map((x, k) => (k === i ? s : x))
    setParts(p)
    if (s.length === LENS[i]) {
      if (i < 2) refs[i + 1].current?.focus() // 年满4→月；月满2→日
      else emit(p) // 日满2 → 触发完整日期
    }
  }

  const onKey = (i: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && (e.target as HTMLInputElement).value === '' && i > 0) {
      refs[i - 1].current?.focus() // 空删→回上一段
    }
  }

  const onBlur = () => {
    // 已有内容补零（1→01），空段保持空白避免出现 "00"
    const p = parts.map((x, i) =>
      x ? (i === 0 ? x.padStart(4, '0') : x.padStart(2, '0')) : x,
    )
    setParts(p)
    emit(p) // 失焦时若已完整（如粘贴/补零后）触发一次
  }

  return (
    <span className={`inline-flex items-center ${className}`}>
      {parts.map((p, i) => (
        <span key={i} className="inline-flex items-center">
          <input
            ref={refs[i]}
            value={p}
            inputMode="numeric"
            placeholder={['YYYY', 'MM', 'DD'][i]}
            onChange={(e) => changeAt(i, e.target.value)}
            onKeyDown={(e) => onKey(i, e)}
            onFocus={(e) => e.target.select()}
            onBlur={onBlur}
            maxLength={LENS[i]}
            className={`border rounded px-1 py-1 text-center text-xs bg-white dark:bg-slate-900 ${
              i === 0 ? 'w-[3.6rem]' : 'w-[2.5rem]'
            }`}
          />
          {i < 2 && <span className="text-slate-400 mx-0.5">-</span>}
        </span>
      ))}
    </span>
  )
}
