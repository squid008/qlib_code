/**
 * Meta-Gate 概率风控（★ v2.9.43，2026-10-19 ✓ —— 从 `App.tsx` 拆出的第三块 ✓）。
 *
 * 语义：主模型 topK 候选内训练二分类 gate（学"未来收益>0" ✓），
 * 按日剔除 gate 概率最低的 `reject_ratio` 比例后回测 ✓；**排序仍由主模型决定 ✓，gate 只做风控闸门 ✓**。
 *
 * ## 为什么这块比前两块难拆（记录取舍 ✓）
 *
 * 它依赖 **10 个 props** ✗（开关 / 剔除比例 / 归因范围 / 已选附加特征 / 公式列表 / 5 个回调）——
 * 比"三布尔一块"重得多 ✓。但**搜索框的输入态 `query` 收进了组件内部** ✓
 * （原来在 `App.tsx` 里是一个只为这一块存在的 `useState` ✗ ⇒ 顺手清掉 ✓）：
 * 每拆一块，`App` 的状态就少一个 ✓。
 *
 * ## 口径细节（拆的时候最容易改坏 ✓）
 *
 * 1. **剔除比例默认 0.25，且夹在 `[0, 0.9]`** ✓（清空也回到 0.25 ✓ —— 不能变 0 ✗，
 *    0 等于"不剔除"✓ 会让用户以为开了风控却没效果 ✗）；
 * 2. **归因默认 `all`** ✓；三档文案里的耗时是**实测写死的** ✓（`36 段约 +12~18 分钟` ✓ —— 别删 ✗）；
 * 3. **附加特征块只在"开启 + 有可用公式"时出现** ✓；计数是 `已选 n / 总数` ✓（分母是**全部公式** ✓，
 *    不是过滤后的 ✓ —— 否则搜索时会显示成"已选 1 / 1"✗ 让人以为选满了 ✓）。
 */
import { useState } from 'react'

export interface MetaGateFormula {
  id: string
  name: string
  expression: string
}

interface MetaGatePanelProps {
  enabled: boolean
  /** 候选内剔除比例（0~0.9 ✓） */
  rejectRatio?: number | null
  /** 因子归因范围：'all' / 'seg1' / 'off' ✓ */
  attribution?: string | null
  /** 已勾选的附加特征（存的是 **qlib 表达式** ✓，不是 id ✗） */
  extraFeatures?: string[]
  formulas: MetaGateFormula[]
  onToggleEnabled: (next: boolean) => void
  onRatio: (next: number) => void
  onAttribution: (next: string) => void
  onExtraAll: (next: boolean) => void
  onToggleExtra: (formulaId: string) => void
}

const RATIO_DEFAULT = 0.25
const RATIO_MAX = 0.9

export default function MetaGatePanel(props: MetaGatePanelProps) {
  const [query, setQuery] = useState('')
  const selected = props.extraFeatures || []
  const q = query.trim().toLowerCase()
  const shown = q ? props.formulas.filter((f) => f.name.toLowerCase().includes(q)) : props.formulas

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
      <label
        className="flex items-center gap-1.5 text-sm cursor-pointer"
        title="主模型 topK 候选内训练二分类 gate（学'未来收益>0'），按日剔除 gate 概率最低的 reject_ratio 比例后回测；排序仍由主模型决定，gate 只做风控闸门。勾选下方'Gate 附加特征'（已保存的 01 触发 / 连续公式）即可把它们作为 gate 输入特征，由模型 feature importance 自动学习挑选——多个 01 因子就是走这个通道（无需再单独传表达式）"
      >
        <input
          type="checkbox"
          checked={props.enabled}
          onChange={(e) => props.onToggleEnabled(e.target.checked)}
        />
        <span>Meta-Gate 风控</span>
      </label>

      {props.enabled && (
        <label className="block">
          <span className="text-sm text-slate-500">候选内剔除比例</span>
          <input
            type="number"
            min={0}
            max={RATIO_MAX}
            step={0.05}
            className="mt-1 w-24 border rounded px-2 py-1"
            value={props.rejectRatio ?? RATIO_DEFAULT}
            onChange={(e) =>
              props.onRatio(e.target.value === ''
                ? RATIO_DEFAULT
                : Math.min(RATIO_MAX, Math.max(0, Number(e.target.value))))}
          />
        </label>
      )}

      {props.enabled && (
        <label className="block">
          <span
            className="text-sm text-slate-500"
            title="逐个单独试：把每条勾选的公式单独加进基线（主特征 + 主模型打分）重新训一个门控模型，比较验证集 AUC 提升 ⇒ 直接回答「哪个风控因子更有用」。结果写进产物，结果页出「Meta-Gate 因子归因」表；勾选越多越慢（每条公式多训一个模型）。⚠ 端到端实测：一个门控模型训练约 4.5~7.6s ⇒ 每段约 +20~30s ⇒ 36 段约 +12~18 分钟。"
          >
            因子归因（逐个公式单独试）
          </span>
          <select
            className="mt-1 border rounded px-2 py-1 text-sm"
            value={String(props.attribution ?? 'all')}
            onChange={(e) => props.onAttribution(e.target.value)}
          >
            <option value="all">每段都做（默认，36 段约 +12~18 分钟）</option>
            <option value="seg1">只做第 1 段（更省时间）</option>
            <option value="off">关（只记分裂增益占比，不额外训练）</option>
          </select>
        </label>
      )}

      {props.enabled && props.formulas.length > 0 && (
        <div className="mt-2 w-full">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm text-slate-500">
              Gate 附加特征（已选 {selected.length} / {props.formulas.length}，机器用
              importance 自动挑选，建议 0/1 触发类）
            </span>
            <span className="space-x-1">
              <button type="button" onClick={() => props.onExtraAll(true)} className="text-blue-600 hover:underline text-sm">
                全选
              </button>
              <span className="text-slate-300">|</span>
              <button type="button" onClick={() => props.onExtraAll(false)} className="text-blue-600 hover:underline text-sm">
                清空
              </button>
            </span>
          </div>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索公式名…（多公式快速定位）"
            className="w-full mb-1 border rounded px-2 py-1 text-xs"
          />
          <div className="flex flex-wrap gap-x-4 gap-y-1 max-h-44 overflow-y-auto pr-1">
            {shown.map((fm) => (
              <label key={fm.id} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={selected.includes(fm.expression)}
                  onChange={() => props.onToggleExtra(fm.id)}
                />
                <span>{fm.name}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
