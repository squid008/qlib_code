/**
 * 交易设置 · **资金与口径**（★ v2.9.44，2026-10-19 ✓ —— `App.tsx` 第四刀 ✓）。
 *
 * 「交易成本与成交设置」原本是 **146 行 / 10 个字段** ✗ —— 一次搬走会要 **20+ props** ✗
 * ⇒ 按语义先分成三块，这是第一块（**3 个字段** ✓）：
 * 起始总资产(万元) ✓、成交价基准 ✓、复权方式 ✓。
 *
 * ## 口径细节（搬的时候必须逐字保留 ✓）
 *
 * · **成交价基准 5 档** ✓（收盘/开盘/VWAP/(开+收)/2/(开高低收)/4 ✓）——
 *   档位少了会让用户选不到原来的口径 ✗，**回测结果会变** ✗；
 * · **复权方式默认 `none`（不复权）** ✓（`form.price_adjust || 'none'` ✓ —— 不是 `backward` ✗）；
 *   `title` 那句"前复权与后复权在比率类因子/收益率上数学等价"是**用户唯一的解释** ✓ 不能删 ✗；
 * · 起始总资产用 **`Number(...)`** ✓（与原来一致 ✓：清空会得到 `0` —— 这是既有行为 ✓，
 *   本次**不改语义** ✗，避免"重构顺手改了账"✓）。
 */

export const DEAL_PRICE_OPTIONS = [
  { value: 'close', label: '收盘价' },
  { value: 'open', label: '开盘价' },
  { value: 'vwap', label: '均价(成交量加权)' },
  { value: 'avg_co', label: '均价(开盘+收盘)/2' },
  { value: 'avg_ohlc', label: '均价(开收高低)/4' },
] as const

export const PRICE_ADJUST_OPTIONS = [
  { value: 'none', label: '不复权' },
  { value: 'forward', label: '前复权' },
  { value: 'backward', label: '后复权' },
] as const

interface TradingBasicsProps {
  /** 起始总资产（万元 ✓） */
  capitalWan: number
  /** 成交价基准（`DEAL_PRICE_OPTIONS` 之一 ✓）
   *  ⚠ 允许 `undefined` ✓ —— `form.deal_price` 在某些路径下确实可能没赋值 ✓
   *  （App 原代码就是直接 `value={form.deal_price}` ✓，这里**保持同一语义** ✗ 不改 ✓）。 */
  dealPrice?: string
  /** 复权方式（空值按 `none` 处理 ✓，与 App 原逻辑一致 ✓） */
  priceAdjust?: string
  onCapitalWan: (next: number) => void
  onDealPrice: (next: string) => void
  onPriceAdjust: (next: string) => void
}

export default function TradingBasics(props: TradingBasicsProps) {
  return (
    <>
      <label className="block">
        <span className="text-sm text-slate-500">起始总资产(万元)</span>
        <input
          type="number"
          step="100"
          className="mt-1 w-full border rounded px-2 py-1"
          value={props.capitalWan}
          onChange={(e) => props.onCapitalWan(Number(e.target.value))}
        />
      </label>

      <label className="block">
        <span className="text-sm text-slate-500">成交价基准</span>
        <select
          className="mt-1 w-full border rounded px-2 py-1"
          value={props.dealPrice}
          onChange={(e) => props.onDealPrice(e.target.value)}
        >
          {DEAL_PRICE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      <label className="block" title="前复权与后复权在比率类因子/收益率上数学等价（仅价格绝对值不同）">
        <span className="text-sm text-slate-500">复权方式</span>
        <select
          className="mt-1 w-full border rounded px-2 py-1"
          value={props.priceAdjust || 'none'}
          onChange={(e) => props.onPriceAdjust(e.target.value)}
        >
          {PRICE_ADJUST_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
    </>
  )
}
