/**
 * 交易设置 · **费用与滑点**（★ v2.9.45，2026-10-19 ✓ —— `App.tsx` 第五刀 ✓）。
 *
 * 四个字段：买入手续费(%) ✓、卖出手续费(%) ✓、滑点/冲击成本(%) ✓、最低手续费(元) ✓
 * （「交易设置」三组里的第二组 ✓，与第一组 `TradingBasics` 同一层 ✓）。
 *
 * ## ★ 这块最要命的一条：**界面是百分数，账里是比率** ✗
 *
 * `form.open_cost` 这类字段在状态里是**比率**（0.0013 = 万分之十三 ✓），而输入框显示的是
 * **百分数**（0.13 ✓）⇒ 显示 `× 100` ✓、写回 `÷ 100` ✓。
 * ⚠ 这一处若搞反，回测手续费会变成**100 倍**（或 1/100）✗ —— 净值曲线会明显异常，
 *   但"页面看起来完全正常"✓，很容易被当成"策略变好了/变差了" ✗。
 * ⇒ 本组件对外**只收发比率** ✓（`onCost(field, ratio)` ✓），转换统一收在这里 ✓，
 *   调用方没有机会漏乘漏除 ✓。
 *
 * ⚠ 最低手续费(元) 是**金额**，不做任何换算 ✓（`Number(...)` ✓，与 App 原逻辑一致 ✓）。
 */

export type CostField = 'open_cost' | 'close_cost' | 'impact_cost'

interface CostSettingsProps {
  /** 买入手续费**比率**（0.0013 = 0.13% ✓） */
  openCost: number
  /** 卖出手续费比率 ✓ */
  closeCost: number
  /** 滑点 / 冲击成本比率 ✓ */
  impactCost: number
  /** 最低手续费（元 ✓，金额、不换算 ✓） */
  minCost: number
  /** 三个"百分数"字段的写回：**传比率** ✓（不是百分数 ✗） */
  onCost: (field: CostField, ratio: number) => void
  /** 最低手续费写回（元 ✓） */
  onMinCost: (yuan: number) => void
}

const PCT_FIELDS: Array<{ key: CostField; label: string; pick: (p: CostSettingsProps) => number }> = [
  { key: 'open_cost', label: '买入手续费(%)', pick: (p) => p.openCost },
  { key: 'close_cost', label: '卖出手续费(%)', pick: (p) => p.closeCost },
  { key: 'impact_cost', label: '滑点/冲击成本(%)', pick: (p) => p.impactCost },
]

export default function CostSettings(props: CostSettingsProps) {
  return (
    <>
      {PCT_FIELDS.map((f) => (
        <label className="block" key={f.key}>
          <span className="text-sm text-slate-500">{f.label}</span>
          <input
            type="number"
            step="0.01"
            className="mt-1 w-full border rounded px-2 py-1"
            value={f.pick(props) * 100}
            onChange={(e) => props.onCost(f.key, Number(e.target.value) / 100)}
          />
        </label>
      ))}

      <label className="block">
        <span className="text-sm text-slate-500">最低手续费(元)</span>
        <input
          type="number"
          className="mt-1 w-full border rounded px-2 py-1"
          value={props.minCost}
          onChange={(e) => props.onMinCost(Number(e.target.value))}
        />
      </label>
    </>
  )
}
