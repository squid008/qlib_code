import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import MetricCards from '../components/MetricCards'
import type { BacktestResult } from '../types'

/**
 * `MetricCards` 的**服务端渲染**测试（★ 2026-10-19 ✓；原因已在 v2.10.28 更正 ✗→✓）。
 *
 * ## ⚠ 这里曾经写着"CI 上走 jsdom 就崩"——**那是错的** ✗（v2.10.28 追清 ✓）
 *
 * 原始症状确实存在过 ✓（`TypeError: Cannot read properties of undefined (reading 'bind')`）；
 * 但真因**不是 jsdom 不可用** ✗，而是测试环境**缺浏览器 API 桩** ——
 * 这次在组件交互测试里复现出的根因是 `ReferenceError: ResizeObserver is not defined` ✓
 * （recharts 的 `<ResponsiveContainer>` 要用它 ✓，而 jsdom **不实现** ✓）。
 * ⇒ 在 `frontend/vitest.setup.ts` 补上 `ResizeObserver` / `matchMedia` 桩之后，
 *   **交互测试可以正常跑** ✓（见 `icChartInteractive.test.tsx` ✓）。
 *
 * ## 那为什么这个文件仍然用 `renderToStaticMarkup`（是**选择**，不是**妥协** ✓）
 *
 * `MetricCards` 是**纯展示**组件（props → HTML，零交互、零副作用 ✓）：
 * `renderToStaticMarkup` **更快**（纯 Node ✓ 不启 jsdom ✓）且**更能保证"服务端也能渲"** ✓
 * （这份 HTML 本来就要经 API 返回给前端做快照 ✓）。
 * ⇒ 纯展示组件继续用 SSR ✓；**需要点击/输入**的组件才用 jsdom ✓（不要再被上面那句话挡住 ✗）。
 *
 * ## 守什么
 *
 * 1. 8 张卡片**一张不少** ✓（少一张 = 指标被静默吞掉 ✗）；
 * 2. **空值显示 `--`** ✓（不是 `NaN%` / `undefined` ✗ —— 这是最常见的丑陋 bug ✓）；
 * 3. 百分比口径：输入 0.1234 ⇒ `12.34%` ✓；夏普**不乘 100** ✓（无量纲 ✓）；
 * 4. **超额最大回撤**：手算净值序列，核对数值 ✓（这段逻辑藏在内联函数里、没有任何别的测试 ✓）。
 */
function mkResult(nav: Array<{ value: number; benchmark: number }> = []) {
  return {
    annualized_return: 0.1234,
    annualized_excess_return: 0.0567,
    sharpe: 1.234,
    max_drawdown: -0.0891,
    win_rate: 0.55,
    benchmark_return: 0.0667,
    total_return: 0.4321,
    nav,
  } as unknown as BacktestResult
}

describe('MetricCards（服务端渲染）', () => {
  it('8 张卡片一张不少，且中文标题齐全', () => {
    const html = renderToStaticMarkup(<MetricCards result={mkResult()} />)
    for (const label of [
      '年化收益', '超额收益', '夏普比率', '最大回撤',
      '胜率', '基准收益', '累计收益', '超额最大回撤',
    ]) {
      expect(html, `缺少指标卡「${label}」✗`).toContain(label)
    }
  })

  it('百分比 ×100 两位小数；夏普**不乘 100**', () => {
    const html = renderToStaticMarkup(<MetricCards result={mkResult()} />)
    expect(html).toContain('12.34%')      // 年化收益 ✓
    expect(html).toContain('5.67%')       // 超额收益 ✓
    expect(html).toContain('-8.91%')      // 最大回撤（负号必须保留 ✓）
    expect(html).toContain('1.23')        // 夏普 1.234 ⇒ 1.23 ✓（若乘 100 会变 123.40 ✗）
    expect(html).not.toContain('123.40')
  })

  it('缺值一律显示 `--`（绝不出现 NaN% / undefined）', () => {
    const r = mkResult()
    ;(r as unknown as Record<string, unknown>).annualized_return = null
    ;(r as unknown as Record<string, unknown>).sharpe = undefined
    const html = renderToStaticMarkup(<MetricCards result={r} />)
    expect(html).not.toContain('NaN')
    expect(html).not.toContain('undefined')
    expect(html).toContain('--')
  })

  it('超额最大回撤：手算净值序列', () => {
    // 超额 = value / benchmark：1.0 → 1.2（峰）→ 0.96 ⇒ 回撤 = (0.96-1.2)/1.2 = -20% ✓
    const nav = [
      { value: 1.0, benchmark: 1.0 },      // excess 1.00
      { value: 1.2, benchmark: 1.0 },      // excess 1.20 ← 峰
      { value: 0.96, benchmark: 1.0 },     // excess 0.96 ⇒ -20.00%
    ]
    const html = renderToStaticMarkup(<MetricCards result={mkResult(nav)} />)
    expect(html, '超额最大回撤应为 -20.00% ✓').toContain('-20.00%')
  })

  it('没有净值（或 benchmark 非法）⇒ 超额最大回撤显示 `--`，不是 0 ✓', () => {
    const html = renderToStaticMarkup(
      <MetricCards result={mkResult([{ value: 1.0, benchmark: 0 }])} />)
    // 该卡片的 value 必须是 `--`（0 会被误读成"没有回撤"✗）
    const idx = html.indexOf('超额最大回撤')
    expect(idx).toBeGreaterThan(0)
    expect(html.slice(idx, idx + 160)).toContain('--')
  })
})
