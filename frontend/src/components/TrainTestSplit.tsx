/**
 * 训练/测试划分（含滚动训练）（★ v2.9.47，2026-10-19 ✓ —— `App.tsx` 第七刀 ✓）。
 *
 * 两种划分方式：
 * · `single` = 一次性训练（回测前窗口训练 ✓）；
 * · `custom` = **自定义滚动训练**（每周期重训 ✓）—— 只有它才显示"训练/测试窗口 + 单位"✓。
 *
 * ## 为什么这两段说明必须一字不落带过来 ✓
 *
 * 它们是用户理解"**为什么这样不会用到未来数据**"的唯一来源 ✓：
 * "用测试窗口起点往前 N 单位的最新数据重新训练…训练集只使用当时已发生的数据，避免未来数据泄漏" ✓
 * 以及"滚动模式下信号后处理会在**每个测试段**用该段训练窗重训门控模型" ✓。
 * ⇒ 重构时把这类说明删掉/改写，用户就再也判断不了自己的配置**有没有前视** ✗ —— 这比排版重要得多 ✓。
 *
 * ## 口径
 *
 * · 窗口数走 **`Number(...)`** ✓（与 App 原逻辑一致 ✓，`min=1` ✓）；
 * · 单位固定三档 **天/周/月** ✓（少一档会让保存过的配置读不出来 ✗）。
 */

export type TimeUnit = 'day' | 'week' | 'month'

export type SplitField = 'split_mode' | 'train_win' | 'train_unit' | 'test_win' | 'test_unit'

export const SPLIT_MODE_OPTIONS = [
  { value: 'single', label: '一次性训练（回测前窗口训练）' },
  { value: 'custom', label: '自定义滚动训练（每周期重训）' },
] as const

export const UNIT_OPTIONS = [
  { value: 'day', label: '天' },
  { value: 'week', label: '周' },
  { value: 'month', label: '月' },
] as const

interface TrainTestSplitProps {
  /** 'single' / 'custom' ✓ */
  splitMode: string
  trainWin: number
  trainUnit: string
  testWin: number
  testUnit: string
  onChange: (field: SplitField, value: string | number) => void
}

export default function TrainTestSplit(props: TrainTestSplitProps) {
  const isRolling = props.splitMode === 'custom'
  return (
    <div className="mt-5 pt-4 border-t border-slate-200 dark:border-slate-700">
      <h3 className="text-sm font-semibold text-slate-600 dark:text-slate-300 mb-3">
        训练/测试划分
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="block">
          <span className="text-sm text-slate-500">划分方式</span>
          <select
            className="mt-1 w-full border rounded px-2 py-1"
            value={props.splitMode}
            onChange={(e) => props.onChange('split_mode', e.target.value)}
          >
            {SPLIT_MODE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>

        {isRolling && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <div>
              <span className="text-sm text-slate-500">训练窗口</span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full border rounded px-2 py-1"
                value={props.trainWin}
                onChange={(e) => props.onChange('train_win', Number(e.target.value))}
              />
            </div>
            <div>
              <span className="text-sm text-slate-500">训练单位</span>
              <select
                className="mt-1 w-full border rounded px-2 py-1"
                value={props.trainUnit}
                onChange={(e) => props.onChange('train_unit', e.target.value)}
              >
                {UNIT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <span className="text-sm text-slate-500">测试窗口</span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full border rounded px-2 py-1"
                value={props.testWin}
                onChange={(e) => props.onChange('test_win', Number(e.target.value))}
              />
            </div>
            <div>
              <span className="text-sm text-slate-500">测试单位</span>
              <select
                className="mt-1 w-full border rounded px-2 py-1"
                value={props.testUnit}
                onChange={(e) => props.onChange('test_unit', e.target.value)}
              >
                {UNIT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>
        )}
      </div>
      {isRolling && (
        <>
          <p className="mt-2 text-xs text-slate-400">
            滚动训练：每个测试窗口开始时，用「测试窗口起点往前 N 单位」的最新数据重新训练模型，
            再预测并回测该测试窗口。各段账户资金连续，净值曲线无缝衔接。训练集只使用当时已发生的数据，
            避免未来数据泄漏。
          </p>
          <p className="mt-1 text-xs text-slate-400">
            滚动模式下信号后处理（硬规则闸门 / Meta-Gate 风控）可用：会在每个测试段用
            「该段训练窗」重新训练门控模型后应用（与主模型同段重训），而非一次性模型套全程。
          </p>
        </>
      )}
    </div>
  )
}
