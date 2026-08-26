import { useEffect, useMemo, useState } from 'react'
import type { TradeRecord } from '../types'

interface Props {
  trades: TradeRecord[]
}

// 格式化数字
const fmt = (v: number | null | undefined, digits = 2) => {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

// 方向标签
const DirBadge = ({ dir }: { dir: number }) =>
  dir === 1 ? (
    <span className="px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-600">
      买入
    </span>
  ) : (
    <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-600">
      卖出
    </span>
  )

export default function TradeLog({ trades }: Props) {
  const [filter, setFilter] = useState<'all' | 'buy' | 'sell'>('all')
  const [keyword, setKeyword] = useState('')
  const [month, setMonth] = useState<string>('') // 'YYYY-MM'，空=全部月份
  // 分页：大回测可能有数千笔调仓，分页避免一次性渲染过多 DOM 卡顿
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 200

  // 按月份分组
  const months = useMemo(() => {
    const map = new Map<string, number>()
    for (const t of trades) {
      const m = (t.date || '').slice(0, 7)
      if (m) map.set(m, (map.get(m) || 0) + 1)
    }
    return Array.from(map.entries()).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
  }, [trades])

  // 筛选变化时回到第一页
  useEffect(() => {
    setPage(1)
  }, [filter, keyword, month])

  // 应用筛选 + 月份过滤
  const filtered = useMemo(() => {
    return trades.filter((t) => {
      if (filter === 'buy' && t.direction !== 1) return false
      if (filter === 'sell' && t.direction !== -1) return false
      if (keyword && !t.instrument.toLowerCase().includes(keyword.toLowerCase())) return false
      if (month && (t.date || '').slice(0, 7) !== month) return false
      return true
    })
  }, [trades, filter, keyword, month])

  // 分页切片
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const pageItems = useMemo(
    () => filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [filtered, safePage]
  )

  const buyCount = trades.filter((t) => t.direction === 1).length
  const sellCount = trades.filter((t) => t.direction === -1).length
  const totalCost = trades.reduce((s, t) => s + (t.trade_cost || 0), 0)

  // 当前月份索引（用于上一月/下一月）
  const curMonthIdx = month ? months.findIndex(([m]) => m === month) : -1

  const goPrevMonth = () => {
    if (curMonthIdx > 0) setMonth(months[curMonthIdx - 1][0])
  }
  const goNextMonth = () => {
    if (curMonthIdx >= 0 && curMonthIdx < months.length - 1) setMonth(months[curMonthIdx + 1][0])
  }
  const clearMonth = () => setMonth('')

  return (
    <section id="trade-log" className="bg-white dark:bg-slate-800 rounded-xl shadow p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-lg font-semibold">
          调仓记录
          <span className="ml-2 text-sm font-normal text-slate-500">
            {trades.length} 笔 | 买入 {buyCount} / 卖出 {sellCount} | 总成本 {fmt(totalCost, 0)} 元
          </span>
        </h2>
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="text"
            placeholder="搜索代码"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            className="border rounded px-2 py-1 text-sm"
          />
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as typeof filter)}
            className="border rounded px-2 py-1 text-sm"
          >
            <option value="all">全部</option>
            <option value="buy">仅买入</option>
            <option value="sell">仅卖出</option>
          </select>
        </div>
      </div>

      {/* 月份分页工具条 */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          onClick={goPrevMonth}
          disabled={!month || curMonthIdx <= 0}
          className="border rounded px-3 py-1 text-sm disabled:opacity-40"
        >
          ◀ 上一月
        </button>
        <input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="border rounded px-2 py-1 text-sm"
        />
        <button
          onClick={goNextMonth}
          disabled={!month || curMonthIdx >= months.length - 1}
          className="border rounded px-3 py-1 text-sm disabled:opacity-40"
        >
          下一月 ▶
        </button>
        {month && (
          <button
            onClick={clearMonth}
            className="border rounded px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
          >
            显示全部月份
          </button>
        )}
        <span className="text-xs text-slate-400">
          {month ? `当前：${month}（${filtered.length} 笔）` : `全部月份（${filtered.length} 笔）`}
        </span>
        {/* 月份快捷选择 */}
        {months.length > 0 && (
          <span className="text-xs text-slate-400">
            月份分布：
            {months.map(([m, c]) => (
              <button
                key={m}
                onClick={() => setMonth(m)}
                className={`mx-0.5 px-1.5 py-0.5 rounded text-xs ${
                  month === m ? 'bg-blue-600 text-white' : 'text-blue-500 hover:bg-blue-50'
                }`}
                title={`${m}: ${c} 笔`}
              >
                {m.slice(5)}
              </button>
            ))}
          </span>
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-slate-400 py-6 text-center">该月份/条件下暂无调仓记录</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-slate-500">
                <th className="py-2 pr-3">日期</th>
                <th className="py-2 pr-3">代码</th>
                <th className="py-2 pr-3">方向</th>
                <th className="py-2 pr-3 text-right">目标股数</th>
                <th className="py-2 pr-3 text-right">成交价</th>
                <th className="py-2 pr-3 text-right">成交额</th>
                <th className="py-2 pr-3 text-right">成本(费用+滑点)</th>
                <th className="py-2 pr-3 text-right">成交率</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((t, i) => (
                <tr key={i} className="border-b border-slate-100 dark:border-slate-700">
                  <td className="py-2 pr-3 whitespace-nowrap">{t.date}</td>
                  <td className="py-2 pr-3 font-mono">{t.instrument}</td>
                  <td className="py-2 pr-3">
                    <DirBadge dir={t.direction} />
                  </td>
                  <td className="py-2 pr-3 text-right">{fmt(t.amount, 0)}</td>
                  <td className="py-2 pr-3 text-right">{fmt(t.deal_price, 4)}</td>
                  <td className="py-2 pr-3 text-right">{fmt(t.trade_value, 0)}</td>
                  <td className="py-2 pr-3 text-right">{fmt(t.trade_cost, 0)}</td>
                  <td className="py-2 pr-3 text-right">
                    {t.ffr === null || t.ffr === undefined
                      ? '-'
                      : `${(t.ffr * 100).toFixed(1)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页控件 */}
      {filtered.length > PAGE_SIZE && (
        <div className="flex flex-wrap items-center justify-between gap-2 mt-4 pt-3 border-t border-slate-100 dark:border-slate-700">
          <span className="text-xs text-slate-400">
            共 {filtered.length} 笔 · 第 {safePage}/{totalPages} 页
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={safePage <= 1}
              className="border rounded px-3 py-1 text-sm disabled:opacity-40"
            >
              ◀ 上一页
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={safePage >= totalPages}
              className="border rounded px-3 py-1 text-sm disabled:opacity-40"
            >
              下一页 ▶
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
