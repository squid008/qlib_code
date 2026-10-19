import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import MetricCards from '../components/MetricCards'
import type { BacktestResult } from '../types'

/**
 * `MetricCards` 的**服务端渲染**测试（★ 2026-10-19 ✓）。
 *
 * ## 为什么用 `renderToStaticMarkup` 而不是 jsdom
 *
 * CI（Linux + Node 20）上**只要走 jsdom 环境就崩** ✗
 * （`TypeError: Cannot read properties of undefined (reading 'bind')`，0 个用例被收集 ✓），
 * 而本地 Windows 正常 ✓ —— 这是环境问题、不是测试问题 ✓。
 *
 * 但"组件渲染不炸 + 关键文案在"这类安全网**根本不需要 DOM** ✓：
 * `renderToStaticMarkup` 直接把组件渲成 HTML 字符串 ✓（纯 Node ✓，CI 能跑 ✓）。
 * ⇒ 先建这层网 ✓；将来要测**交互**（点击/输入/展开 ✗）时再解决 jsdom ✓（配置里留有 TODO ✓）。
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
