/**
 * 交易设置 · **成交约束**（★ v2.9.46，2026-10-19 ✓ —— `App.tsx` 第六刀 ✓）。
 *
 * 三个字段：成交量限制(比例) ✓、涨跌停限制（勾选「封板不可交易」✓）、每手股数 ✓
 * —— 「交易设置」三组的**最后一组** ✓（前两组：`TradingBasics` ✓ / `CostSettings` ✓）。
 *
 * ## 口径细节（搬的时候最容易改坏 ✓）
 *
 * 1. **「空」必须是 `null`，不是 `0`** ✓ —— 成交量限制 `0` 等于"一手都不能成交"✗、
 *    每手股数 `0` 会直接除零 ✗；`null` 才表示"不限 / 用默认"✓；
 * 2. **涨跌停限制是个"布尔外观、数值内核"** ✗：状态里存的是 `limit_threshold`
 *    （开启 = **`0.1`** ✓ / 关闭 = `null` ✓），界面是勾选框 ✓。
 *    ⇒ 本组件对外只发**布尔** ✓，`0.1` 这个魔法值以 `LIMIT_THRESHOLD_ON` **单点定义** ✓
 *    （散布在 App 里迟早写错成 `0.05`/`0.2` ✗ —— 那会**改变可成交判定** ✗）；
 * 3. **`title` 里那串涨跌幅规则是用户唯一的解释** ✓（主板 10% / 创业科创 20% / 北交 30% /
 *    ST 5% / 退市整理 10% ✓）—— 逐字保留 ✓。
 */

/** 开启涨跌停限制时写入 `limit_threshold` 的值（**唯一来源** ✓，App 也从这里取 ✓）。 */
export const LIMIT_THRESHOLD_ON = 0.1

interface ExecutionLimitsProps {
  /** 成交量限制比例（`null` = 不限 ✓） */
  volumeThreshold?: number | null
  /** 涨跌停限制是否开启（状态里是 `limit_threshold != null` ✓） */
  limitEnabled: boolean
  /** 每手股数（`null` = 用默认 ✓） */
  tradeUnit?: number | null
  onVolumeThreshold: (next: number | null) => void
  onLimitEnabled: (next: boolean) => void
  onTradeUnit: (next: number | null) => void
}

export default function ExecutionLimits(props: ExecutionLimitsProps) {
  return (
    <>
      <label className="block">
        <span className="text-sm text-slate-500">成交量限制(比例)</span>
        <input
          type="number"
          step="0.05"
          className="mt-1 w-full border rounded px-2 py-1"
          value={props.volumeThreshold ?? ''}
          onChange={(e) =>
            props.onVolumeThreshold(e.target.value === '' ? null : Number(e.target.value))}
        />
      </label>

      <div className="block">
        <span className="text-sm text-slate-500">涨跌停限制</span>
        <label
          className="mt-1 flex items-center gap-1.5 text-sm cursor-pointer border rounded px-2 h-[35px] bg-white dark:bg-slate-900"
          title="自动识别：主板 10%、创业/科创 20%、北交 30%；数据有交易所涨跌停价标签时直接按标签（ST 5%/退市整理 10% 也自动正确）。取消=不设涨跌停（理想成交）"
        >
          <input
            type="checkbox"
            checked={props.limitEnabled}
            onChange={(e) => props.onLimitEnabled(e.target.checked)}
          />
          <span>封板不可交易</span>
        </label>
      </div>

      <label className="block">
        <span className="text-sm text-slate-500">每手股数</span>
        <input
          type="number"
          className="mt-1 w-full border rounded px-2 py-1"
          value={props.tradeUnit ?? ''}
          onChange={(e) =>
            props.onTradeUnit(e.target.value === '' ? null : Number(e.target.value))}
        />
      </label>
    </>
  )
}
