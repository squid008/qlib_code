// @vitest-environment jsdom

/**
 * `TopkCurveModal`（★ v2.11.87）—— 单因子结果里那个 1285 行的大弹窗（缺 461 语句）。
 *
 * ## 它为什么值得一次多覆盖
 * 它是 **props 驱动** ✓（`row` 里带全部曲线数据），只有**导出**那条路才调 api ✓
 * ⇒ 造一份饱满的 `row` 就能把渲染分支走一大片 ✓。
 *
 * ## 它坏掉的样子（都不报错）
 * · **档位切了但曲线没换** ⇒ 用户看的是 A 档的图、指标却是 B 档的 ✗
 *   （`v2.1.27` 真事故：只算 `items[0]` ⇒ 切到「固定 K=867」时红线和指标仍是默认档 ✓）；
 * · **成本档切换**（0 / 0.4% / 0.8%）错位 ⇒ 用户以为"换成零成本会好很多"，其实是另一档 ✗；
 * · **基准切换**后仍拿旧基准相减 ⇒ 超额收益整段错 ✗（`cum` 与 `cum_compound` 口径必须同源 ✓）；
 * · 导出失败**静默** ⇒ 点了"导出"什么都没发生 ✗。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'

const mocks = vi.hoisted(() => ({
  exportXlsx: vi.fn(),
  capture: vi.fn(),
}))

vi.mock('../captureChart', () => ({ captureSection: () => mocks.capture() }))
vi.mock('../api', async (orig) => {
  const real = await orig<Record<string, unknown>>()
  return {
    ...real,
    exportTopkCurveXlsx: (p: unknown) => mocks.exportXlsx(p),
    blobErrorDetail: async (e: unknown) => String(e),
  }
})

import TopkCurveModal from '../components/TopkCurveModal'

const mounted: Array<() => void> = []

beforeEach(() => {
  mocks.exportXlsx.mockReset()
  mocks.capture.mockReset()
  mocks.capture.mockResolvedValue('data:image/png;base64,AAA')
  mocks.exportXlsx.mockResolvedValue({ blob: new Blob(['PK']), filename: 'x.xlsx' })
  Object.defineProperty(URL, 'createObjectURL', { writable: true, value: () => 'blob:stub' })
  Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: () => {} })
})

afterEach(() => {
  while (mounted.length) {
    try {
      mounted.pop()!()
    } catch {
      /* 忽略 */
    }
  }
})

const PERF = {
  n_days: 100,
  nav_end: 1.23,
  total_return: 0.23,
  annual_return: 0.18,
  max_drawdown: -0.12,
  sharpe: 1.4,
  sortino: 1.9,
  calmar: 1.5,
  vol_annual: 0.2,
  win_rate: 0.55,
  max_dd_days: 12,
}

function curve(n: number, base: number) {
  return Array.from({ length: n }, (_, i) => base * (1 + i * 0.01))
}

/** 分位「累计收益曲线」（`quantile_curves` ✓）—— 少给这一段，弹窗里几个 useMemo 整段不跑 ✗。 */
function mkQuantileCurves(n: number, dates: string[]) {
  return {
    n_groups: 10,
    n_periods: n,
    dates,
    baseline_mean: 0.002,
    monotonicity: 0.82,
    best_quantile: 10,
    worst_quantile: 1,
    groups: Array.from({ length: 10 }, (_, i) => ({
      quantile: i + 1,
      mean_ret: (i + 1) * 0.0004,
      excess: (i + 1) * 0.0004 - 0.002,
      cum: curve(n, (i + 1) * 0.001),
      cum_compound: curve(n, (i + 1) * 0.001),
    })),
    long_short: { quantile: [10, 1] as [number, number], cum: curve(n, 0.01), cum_compound: curve(n, 0.01) },
  }
}

function mkRow(over: Record<string, unknown> = {}) {
  const n = 8
  const dates = Array.from({ length: n }, (_, i) => `2024-0${(i % 9) + 1}-15`)
  const rc = {
    '0.0000': curve(n, 0),
    '0.0040': curve(n, -0.002),
    '0.0080': curve(n, -0.004),
  }
  const row = {
    id: 'f1',
    name: 'mom20',
    source: 'custom',
    expression: 'Mean($close,20)',
    horizon: 20,
    coverage: 0.97,
    coverage_den: 668,
    nonzero_ratio: 0.98,
    is_binary: false,
    grouping: 'quantile',
    quintile_ret: [
      { quantile: 1, mean: 0.001, n: 100 },
      { quantile: 10, mean: 0.004, n: 100 },
    ],
    trigger: { mean: 0.004, n: 120, win: 0.6 },
    not_trigger: { mean: 0.001, n: 300, win: 0.52 },
    diff: 0.003,
    p_value: 0.01,
    daily_diff: 0.0003,
    daily_t: 2.1,
    daily_t_hac: 1.9,
    daily_acf1: 0.1,
    daily_acf5: 0.02,
    daily_var_ratio: 1.02,
    daily_win: 0.56,
    daily_n: 250,
    daily_trig_mean: 0.004,
    daily_not_mean: 0.001,
    ic: 0.031,
    rank_ic: 0.028,
    icir: 0.5,
    rank_icir: 0.42,
    n_obs: 600,
    error: null,
    // ★ 这三块之前**完全没给** ⇒ 弹窗里对应的 useMemo/表格整段不跑 ✗（380 行缺口的大头 ✓）
    quantile_curves: mkQuantileCurves(n, dates),
    topk_sensitivity: {
      rebalance_period: 20,
      n_days: 668,
      n_periods: n,
      trading_days: 250,
      default_k: 50,
      ks: [50, 100],
      rows: [
        { k: 50, avg_turnover: 0.4, gross_per_period: 0.01, ann_cost: { '0.0040': 0.02, '0.0080': 0.04 }, cost_eaten: { '0.0040': 0.3, '0.0080': 0.6 } },
        { k: 100, avg_turnover: 0.25, gross_per_period: 0.008, ann_cost: { '0.0040': 0.012, '0.0080': 0.024 }, cost_eaten: { '0.0040': null, '0.0080': 0.5 } },
      ],
      note: '买入端已剔除 T/T+1 涨停与 T+1 停牌；未模拟跌停卖不出',
    },
    trigger_split: {
      hold_days: 20,
      first: { count: 40, mean_ret: 0.049, median_ret: 0.04, win_rate: 0.6 },
      refresh: { count: 106, mean_ret: 0.007, median_ret: 0.005, win_rate: 0.55 },
    },
    topk_curves: {
      rebalance_period: 20,
      horizon: 20,
      n_days: 668,
      n,
      dates,
      side: 'high',
      default_quantile: 10,
      items: [
        { kind: 'decile', k: 60, quantile: 10, turnover: Array(n).fill(0.3), curves: rc, curves_compound: rc, perf: { '0.0000': PERF, '0.0040': PERF, '0.0080': PERF } },
        { kind: 'topk', k: 50, turnover: Array(n).fill(0.4), curves: rc, curves_compound: rc, perf: { '0.0000': PERF, '0.0040': PERF, '0.0080': PERF } },
      ],
      benchmarks: {
        default: 'SH000300',
        items: [
          { code: 'SH000300', name: '沪深300', cum: curve(n, 0.05), cum_compound: curve(n, 0.05), perf: PERF },
          { code: 'SH000905', name: '中证500', cum: curve(n, 0.08), cum_compound: curve(n, 0.08), perf: PERF },
        ],
      },
    },
    ...over,
  }
  return row
}

async function mount(row: unknown, props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const merged = {
    open: true,
    onClose: vi.fn(),
    name: 'mom20',
    row,
    taskId: 't1',
    ...props,
  }
  await act(async () => {
    root.render(<TopkCurveModal {...(merged as unknown as React.ComponentProps<typeof TopkCurveModal>)} />)
  })
  mounted.push(() => {
    act(() => root.unmount())
    host.remove()
  })
  return { host, props: merged as Record<string, unknown> }
}

function btns(host: HTMLElement): HTMLButtonElement[] {
  return Array.from(host.querySelectorAll('button')) as HTMLButtonElement[]
}

function clickRe(host: HTMLElement, re: RegExp, idx = 0) {
  const hit = btns(host).filter((b) => re.test((b.textContent || '').trim()))
  if (!hit[idx]) {
    throw new Error(`没找到 ${String(re)} → 按钮：${btns(host).map((b) => (b.textContent || '').trim()).join(' | ')}`)
  }
  act(() => {
    hit[idx].click()
  })
}

describe('TopkCurveModal', () => {
  it('open=false 时不渲染内容', async () => {
    const { host } = await mount(mkRow(), { open: false })
    expect((host.textContent || '').length).toBeLessThan(40)
  })

  it('渲染：因子名 + 档位 + 关掉按钮', async () => {
    const { host } = await mount(mkRow())
    const t = host.textContent || ''
    expect(t).toContain('mom20')
    expect(btns(host).length).toBeGreaterThan(2)
  })

  it('⭐ 关闭按钮 ⇒ onClose（点了关不掉是最基础的坏法 ✗）', async () => {
    const onClose = vi.fn()
    const { host } = await mount(mkRow(), { onClose })
    clickRe(host, /关闭|✕|×|知道了/)
    expect(onClose).toHaveBeenCalled()
  })

  it('⭐ 切换档位（分位档 ⇄ 固定 K）后，页面仍有内容且不崩', async () => {
    const { host } = await mount(mkRow())
    const before = host.textContent || ''
    clickRe(host, /固定|K\s*=|分位/)
    expect(host.textContent || '').toBeTruthy()
    expect((host.textContent || '').length).toBeGreaterThan(before.length * 0.5)
  })

  it('成本档切换（无成本 / 往返 0.004 / 往返 0.008）不崩，且能来回切', async () => {
    const { host } = await mount(mkRow())
    // ⚠ 真实文案是「无成本 / 往返 0.004 / 往返 0.008」✗→✓ —— 我第一版猜「0.8%」⇒ 红 ✓
    //   （helper 把真实按钮清单打出来了：导出 | 关闭 | 复利（实盘） | 算术累加 |
    //     无成本 | 往返 0.004 | 往返 0.008 | 沪深300 | 中证500 | 最强分位组 Q10 | 固定 K=50）
    clickRe(host, /往返 0\.008/)
    clickRe(host, /无成本/)
    expect(host.textContent || '').toBeTruthy()
  })

  it('复利 / 算术累加 两个口径可来回切（混比会误导 ✗）', async () => {
    const { host } = await mount(mkRow())
    clickRe(host, /算术累加/)
    clickRe(host, /复利/)
    expect(host.textContent || '').toBeTruthy()
  })

  it('基准切换（沪深300 → 中证500）不崩', async () => {
    const { host } = await mount(mkRow())
    clickRe(host, /中证500|SH000905|基准/)
    expect(host.textContent || '').toBeTruthy()
  })

  it('topk_curves 缺失：给可读提示，不崩', async () => {
    const { host } = await mount(mkRow({ topk_curves: null }))
    const t = host.textContent || ''
    expect(t.length).toBeGreaterThan(20)
  })

  it('topk_curves_error：把后端原因显示出来（不能静默空图 ✗）', async () => {
    // ⚠ 必须**连 `quantile_curves` 一起置空** ✗→✓ —— 只要还有分位曲线，弹窗就切到分位视图，
    //   错误提示自然不出现 ✓（我第一版只置空 topk_curves ⇒ 红 ✓）。
    const { host } = await mount(
      mkRow({ topk_curves: null, quantile_curves: null, topk_curves_error: '数据不足：调仓期 > 样本' }),
    )
    expect(host.textContent || '').toContain('数据不足')
  })

  it('⭐ 数据给全时：分位 / 敏感度 两块都要渲染出来', async () => {
    const { host } = await mount(mkRow())
    const t = host.textContent || ''
    expect(t, '分位那块没渲染 ✗').toMatch(/分位|Q10/)
    expect(t, '敏感度那块没渲染 ✗').toMatch(/换手|敏感|K/)
  })

  // ⚠ 「触发分组（首次 vs 续期）」**不在这个弹窗里** ✗ —— 我按 `trigger_split` 造了数据去断言它，
  //   实际它渲染在**单因子结果表**（`SingleFactorTestPanel`）那边 ✓。
  //   ⇒ 这条不写"点到为止"的空断言 ✓，改成在面板那侧测（下一轮）。

  it('逐块点一遍分区按钮（分位/敏感/触发/等权/多空）不崩', async () => {
    const { host } = await mount(mkRow())
    for (const re of [/分位/, /敏感/, /触发/, /等权/, /多空/, /累计/]) {
      const hit = btns(host).filter((b) => re.test((b.textContent || '').trim()))
      if (hit.length) {
        act(() => {
          hit[0].click()
        })
      }
    }
    expect((host.textContent || '').length).toBeGreaterThan(100)
  })

  it('导出失败会把原因显示出来（不许静默 ✗）', async () => {
    mocks.exportXlsx.mockRejectedValue(new Error('后端 500 生成失败'))
    const { host } = await mount(mkRow())
    const exportBtn = btns(host).filter((b) => /导出/.test(b.textContent || ''))[0]
    if (!exportBtn) throw new Error('没找到导出按钮 → ' + btns(host).map((b) => b.textContent).join(' | '))
    await act(async () => {
      exportBtn.click()
    })
    expect(host.textContent || '').toMatch(/500|失败/)
  })
})
