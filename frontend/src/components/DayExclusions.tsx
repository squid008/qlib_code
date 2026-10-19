/**
 * 日截面剔除（★ v2.9.41，2026-10-19 ✓ 从 `App.tsx` 拆出的**第一块** ✓）。
 *
 * 口径（与单因子测试"剔除 ST(T+1) / 创 / 科"同义 ✓，但回测**以调仓当日为准** ✓，没有未来函数 ✓）：
 * 三个开关分别剔除 ST/退市整理 ✓、创业板（SZ30 ✓）、科创板（SH688 ✓）。
 *
 * ## 为什么先拆它（而不是更"显眼"的大块 ✗）
 *
 * `App.tsx` 有 **30 个 useState + 10 个 useEffect + 39 个内部函数** ✗ ⇒ 大部分区块一拆就要传
 * 十几个 props（比不拆更难读 ✗）。这一块只依赖**三个布尔 + 一个回调** ✓，是最干净的第一刀 ✓。
 *
 * ⚠ 拆组件时的铁律 ✓：**JSX 一个字都不改**（包括 `title` 提示文案 ✓）——
 *   这些提示是用户唯一的解释来源 ✓，重构时最容易顺手"优化"掉 ✗。
 */

export type DayExclusionKey = 'exclude_st' | 'exclude_stock_gem' | 'exclude_stock_kcb'

interface DayExclusionsProps {
  excludeSt: boolean
  excludeGem: boolean
  excludeKcb: boolean
  onToggle: (key: DayExclusionKey, next: boolean) => void
}

const ITEMS: Array<{
  key: DayExclusionKey
  label: string
  title: string
  pick: (p: DayExclusionsProps) => boolean
}> = [
  {
    key: 'exclude_st',
    label: '剔除ST/退市',
    title: '调仓当日处于 ST/*ST/退市整理的股票不买入（模型打分时直接剔除，已持有的也会随调仓卖出）',
    pick: (p) => p.excludeSt,
  },
  {
    key: 'exclude_stock_gem',
    label: '剔除创业板',
    title: '剔除创业板（SZ30 号段，20% 涨跌幅），全程不参与选股',
    pick: (p) => p.excludeGem,
  },
  {
    key: 'exclude_stock_kcb',
    label: '剔除科创板',
    title: '剔除科创板（SH688，20% 涨跌幅），全程不参与选股',
    pick: (p) => p.excludeKcb,
  },
]

export default function DayExclusions(props: DayExclusionsProps) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
      <span className="text-sm text-slate-500">日截面剔除：</span>
      {ITEMS.map((it) => (
        <label
          key={it.key}
          className="flex items-center gap-1.5 text-sm cursor-pointer"
          title={it.title}
        >
          <input
            type="checkbox"
            checked={it.pick(props)}
            onChange={(e) => props.onToggle(it.key, e.target.checked)}
          />
          <span>{it.label}</span>
        </label>
      ))}
    </div>
  )
}
