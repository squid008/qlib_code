// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import TopkCurveModal from '../components/TopkCurveModal'

/**
 * `TopkCurveModal` 的**骨架**测试（★ v2.10.29，2026-10-20 ✓）。
 *
 * ## 为什么这一轮只写"骨架"
 *
 * 它是 **1157 行**的弹窗，数据来自 `row.topk_curves.items[]` ✓ —— 而 `TopKCurveItem`
 * 的字段我只从注释与 `PerfCard` 的用法里看到一部分（`kind` / `quantile` / `k` /
 * `curves_compound` / `cum_compound` … ✗）⇒ **凭猜造夹具会红一片，且红的原因不是产品 bug** ✗
 * （前几轮已经吃过这个亏：`FILTER`/`Count`/`Greater` 都是我猜错语义 ✓）。
 * ⇒ 本轮只钉**不需要猜**的两条**结构性契约** ✓；真正的深测（切 K 档 / 切成本档 /
 * 算术累加隐藏绩效卡 ✓）留给"先读准 `TopKCurveItem` 再写"的专门一轮 ✓。
 *
 * ## ⚠ 一个已知的可测点（写在这里免得忘 ✓）
 *
 * L804：`basis === 'compound' && (itemPerf || benchPerf)` 才渲染绩效卡 ⇒
 * **「算术累加」口径下绩效卡必须消失** ✓（因为回撤/波动只对净值有定义 ✓）。
 * 这条是本弹窗**最有业务含义**的交互断言 ✓，等夹具就位后补 ✓。
 */

let root: Root | null = null
let host: HTMLDivElement | null = null

function mount(ui: React.ReactElement) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root!.render(ui))
  return host
}

afterEach(() => {
  act(() => root?.unmount())
  host?.remove()
  root = null
  host = null
})

describe('TopkCurveModal（骨架）', () => {
  it('`open=false` ⇒ 什么都不渲染（弹窗关闭时不该占 DOM ✓）', () => {
    const h = mount(
      <TopkCurveModal open={false} onClose={() => {}} name="f" row={null} taskId="t" />,
    )
    expect(h.innerHTML).toBe('')
  })

  it('`open=true` 但 `row=null` ⇒ **不抛异常**（数据没回来时也不能白屏崩 ✗）', () => {
    expect(() => {
      mount(<TopkCurveModal open onClose={() => {}} name="f" row={null} taskId="t" />)
    }).not.toThrow()
  })

  it('`open=true` 且曲线为空 ⇒ 不抛（空 items 是合法状态 ✓）', () => {
    const row = { topk_curves: { items: [] }, topk_sensitivity: null, quantile_curves: null }
    expect(() => {
      mount(
        <TopkCurveModal
          open
          onClose={() => {}}
          name="f"
          // 这里**故意只给最小形状** ✓：测的是"字段缺失也不崩"✓，不是业务渲染 ✓
          row={row as never}
          taskId="t"
        />,
      )
    }).not.toThrow()
  })

  it('`onClose` 在关闭态下不会被调用（没错触关闭回调 ✓）', () => {
    const onClose = vi.fn()
    mount(<TopkCurveModal open={false} onClose={onClose} name="f" row={null} />)
    expect(onClose).not.toHaveBeenCalled()
  })
})

// --------------------------------------------------------------- 交互（真·业务语义 ✓）

const PERF = {
  n_days: 10,
  nav_end: 1.2,
  total_return: 0.2,
  annual_return: 0.3,
  max_drawdown: -0.1,
  sharpe: 1.5,
  sortino: 1.4,
  calmar: 3,
  vol_annual: 0.2,
  win_rate: 0.6,
  max_dd_days: 3,
}

const ITEM = {
  kind: 'decile' as const,
  k: 10,
  quantile: 1,
  turnover: [0, 0.5],
  curves: { '0.0000': [0, 0.1], '0.0040': [0, 0.09], '0.0080': [0, 0.08] },
  curves_compound: { '0.0000': [0, 0.1], '0.0040': [0, 0.09], '0.0080': [0, 0.08] },
  perf: { '0.0000': PERF, '0.0040': PERF, '0.0080': PERF },
}

const ROW = {
  topk_curves: {
    rebalance_period: 5,
    horizon: 5,
    n_days: 10,
    n: 2,
    dates: ['2024-01-02', '2024-01-09'],
    side: 'high' as const,
    default_quantile: 1,
    items: [ITEM],
    benchmarks: null,
  },
  topk_sensitivity: null,
  quantile_curves: null,
}

const clickText = (el: HTMLElement, text: string) => {
  const btn = Array.from(el.querySelectorAll('button')).find((b) => b.textContent === text)
  if (!btn) {
    throw new Error('找不到按钮「' + text + '」，实际有：'
      + Array.from(el.querySelectorAll('button')).map((b) => b.textContent).join(' / '))
  }
  act(() => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

const openModal = () => mount(
  <TopkCurveModal open onClose={() => {}} name="测试因子" row={ROW as never} taskId="t1" />,
)

describe('TopkCurveModal（交互）', () => {
  it('默认「复利（实盘）」：绩效卡三档成本按钮都在', () => {
    const h = openModal()
    expect(h.textContent).toContain('曲线口径')
    expect(h.textContent).toContain('复利（实盘）')
    expect(h.textContent).toContain('无成本')
    expect(h.textContent).toContain('往返 0.004')
    expect(h.textContent).toContain('往返 0.008')
  })

  it('⭐⭐ 切到「算术累加」⇒ **绩效卡整块消失**（回撤/波动对累加值没有定义 ✓）', () => {
    const h = openModal()
    expect(h.textContent).toContain('往返 0.004')

    clickText(h, '算术累加')
    // 口径说明还在 ✓（用户仍能看到曲线）
    expect(h.textContent).toContain('两口径不可混比')
    // 但绩效卡必须没了 ✓（算术累加不是净值 ⇒ 年化/回撤/夏普都无从定义 ✗）
    expect(h.textContent).not.toContain('往返 0.004')
    expect(h.textContent).not.toContain('无成本')
  })

  it('切回复利 ⇒ 绩效卡回来（可来回切，状态不粘 ✗）', () => {
    const h = openModal()
    clickText(h, '算术累加')
    expect(h.textContent).not.toContain('往返 0.004')
    clickText(h, '复利（实盘）')
    expect(h.textContent).toContain('往返 0.004')
  })

  it('组合卡标题标出「最强分位组 Q1」（分位档口径 ✓）', () => {
    const h = openModal()
    expect(h.textContent).toContain('最强分位组 Q1')
  })
})
