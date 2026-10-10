import { useEffect, useRef, useState } from 'react'
import type { DatasetsStatus } from '../types'

/**
 * 数据集（口径）切换器 ✓ —— 用户 2026-10-10 定的交互：
 *  · **收起时只显示短标签**（如 "tushare 口径" ✓）；
 *  · **点开后才显示完整信息**（目录名、自动检测的日历末日、股票数/字段数、口径说明、字段基准提示 ✓）；
 *  · 选项来自后端 `GET /api/datasets` ✓ —— 同事那边没有 cn_data2/cn_data3、或自建目录**没放标记文件**
 *    时，后端根本不会返回它 ✓ ⇒ 前端自然不显示 ✓（不需要前端判断 ✗）。
 */
interface Props {
  datasets: DatasetsStatus | null
  onSwitch: (name: string) => void
  /** 正在切换中（禁用交互 ✓） */
  busy?: boolean
}

export default function DatasetPicker({ datasets, onSwitch, busy }: Props) {
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)

  // 点外面 / 按 Esc 关闭 ✓（原生 select 会自己处理，这里要手写 ✓）
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const list = datasets?.datasets || []
  const active = list.find((d) => d.name === datasets?.active) || null
  const onlyOne = list.length <= 1

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        className={
          'mt-1 w-full border rounded px-2 py-1 text-left flex items-center justify-between ' +
          (onlyOne || busy ? 'bg-slate-100 text-slate-500 cursor-default' : 'bg-white hover:border-slate-400')
        }
        onClick={() => !onlyOne && !busy && setOpen((v) => !v)}
        title={onlyOne ? '只有一个数据集可用' : '切换数据集（后端全局生效）'}
      >
        {/* ★ 收起时只显示短标签 ✓ */}
        <span className="truncate">{active ? active.label : (datasets ? '（无可用数据集）' : '加载中…')}</span>
        {!onlyOne && <span className="ml-2 text-slate-400">▾</span>}
      </button>

      {/* 收起状态下的一行小字：只给"多少个字段"这种最小信息 ✓（完整信息在展开里 ✓） */}
      {active && !open && (
        <span className="mt-1 block text-xs text-slate-400">
          {active.codes} 只 · {active.fields} 字段
          {active.only_price ? '（仅行情）' : ''}
        </span>
      )}

      {open && (
        <div className="absolute z-30 mt-1 w-[26rem] max-w-[90vw] bg-white border border-slate-300 rounded shadow-lg">
          {list.map((d) => {
            const isActive = d.name === datasets?.active
            return (
              <button
                key={d.name}
                type="button"
                className={'w-full text-left px-3 py-2 border-b last:border-b-0 hover:bg-slate-50 ' + (isActive ? 'bg-blue-50' : '')}
                onClick={() => {
                  setOpen(false)
                  if (!isActive) onSwitch(d.name)
                }}
              >
                {/* ★ 展开后才显示完整信息 ✓ */}
                <div className="flex items-center gap-2">
                  <span className="font-medium text-slate-800">{d.label}</span>
                  <span className="text-xs text-slate-500">{d.name}</span>
                  {isActive && <span className="text-xs text-blue-600">当前</span>}
                </div>
                <div className="text-xs text-slate-500 mt-0.5">
                  自动检测：{d.calendar_days} 天（{d.calendar_first} ~ <b>{d.calendar_last}</b>）· {d.codes} 只 · {d.fields} 字段
                  {d.has_fin ? '' : ' · 无财务'}
                  {d.has_mf ? '' : ' · 无资金流'}
                  {d.has_chip ? '' : ' · 无筹码'}
                </div>
                {d.convention && <div className="text-xs text-slate-500 mt-0.5">口径：{d.convention}</div>}
                {d.note && <div className="text-xs text-slate-400 mt-0.5">{d.note}</div>}
                {/* 字段基准不一致 ⇒ **只提示，不拦** ✓ */}
                {!!d.field_note && (
                  <div className={'text-xs mt-0.5 ' + (d.fields_extra_count || d.fields_missing_count ? 'text-amber-600' : 'text-slate-400')}>
                    {d.field_note}
                  </div>
                )}
                {d.only_price && (
                  <div className="text-xs text-amber-600 mt-0.5">
                    ⚠ 仅行情字段：FINANCE(q)/资金流/筹码类公式取不到数（按 NaN 处理）
                  </div>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
