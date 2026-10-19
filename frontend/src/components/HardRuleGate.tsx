/**
 * 硬规则闸门（★ v2.9.42，2026-10-19 ✓ —— 从 `App.tsx` 拆出的第二块 ✓）。
 *
 * 语义：**只允许**满足条件的股票进入候选 ✓（**留空 = 不限** ✓）。
 * 三个字段：市值下限(亿) ✓、市值上限(亿) ✓、股价下限(元) ✓。
 *
 * ## 口径细节（拆组件时最容易弄错的两点 ✗）
 *
 * 1. **留空必须是 `null`，不是 `0`** ✓ —— `0` 会被后端理解成"市值必须为 0（亿）"✗
 *    ⇒ 一只票都选不出来 ✓；`null` 才是"不限"✓。所以转换只能发生在**这一处** ✓：
 *    `'' ⇒ null` / 其余 `⇒ Number(...)` ✓。
 * 2. **`Number` 转换在本组件内完成** ✓（对外只给 `(key, number|null)` ✓）——
 *    如果让调用方各自转，迟早在别处写成 `parseFloat(...) || 0` ✗ 又把"空"变成 0 ✓。
 *
 * ⚠ JSX 与文案**原样搬过来** ✓（`type=number` / `min=0` / `w-28` / `step=0.01` 全部保留 ✓），
 *   布局断言（`test_frontend_structure.py`）也继续有效 ✓。
 */

export type HardFilterKey = 'min_mktcap_bn' | 'max_mktcap_bn' | 'min_price'

interface HardRuleGateProps {
  minMktcapBn?: number | null
  maxMktcapBn?: number | null
  minPrice?: number | null
  onChange: (key: HardFilterKey, value: number | null) => void
}

const FIELDS: Array<{
  key: HardFilterKey
  label: string
  step?: number
  pick: (p: HardRuleGateProps) => number | null | undefined
}> = [
  { key: 'min_mktcap_bn', label: '市值下限(亿)', pick: (p) => p.minMktcapBn },
  { key: 'max_mktcap_bn', label: '市值上限(亿)', pick: (p) => p.maxMktcapBn },
  { key: 'min_price', label: '股价下限(元)', step: 0.01, pick: (p) => p.minPrice },
]

export default function HardRuleGate(props: HardRuleGateProps) {
  return (
    <div className="mt-2">
      <span className="text-sm text-slate-500">硬规则闸门：只允许满足以下条件的股票进入候选（留空=不限）</span>
      <div className="mt-1 flex flex-wrap gap-x-5 gap-y-2">
        {FIELDS.map((f) => (
          <label className="block" key={f.key}>
            <span className="text-sm text-slate-500">{f.label}</span>
            <input
              type="number"
              min={0}
              step={f.step}
              className="mt-1 w-28 border rounded px-2 py-1"
              value={f.pick(props) ?? ''}
              onChange={(e) => props.onChange(f.key, e.target.value === '' ? null : Number(e.target.value))}
            />
          </label>
        ))}
      </div>
    </div>
  )
}
