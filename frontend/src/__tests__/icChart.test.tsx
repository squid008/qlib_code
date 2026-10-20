import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import ICChart from '../components/ICChart'
import type { ICAnalysis, ICSegment } from '../types'

/**
 * `ICChart` 的**服务端渲染**测试（★ v2.10.26，2026-10-20 ✓）。
 *
 * ## 为什么是 `renderToStaticMarkup`（而不是 jsdom + 点击）
 *
 * 与 `metricCards.test.tsx` 同因 ✓：CI（Linux + Node 20）走 jsdom 环境不稳 ✗，
 * 而**服务端渲染**零依赖 ✓。代价是**测不到点击**（切 tab / 切分段 ✗）——
 * ⇒ 这里只钉\"**默认态渲染出来的东西对不对**\" ✓，那恰好是用户第一眼看到的 ✓。
 *
 * ## 为什么图表组件值得测（覆盖率 17.9% 的那一半）
 *
 * recharts 的 `<LineChart>` 内部在 SSR 下画不出东西 ✓，但**它外面那层**才是容易错的 ✓：
 * 默认看哪个 tab ✓、分段列表从哪个 tab 取 ✓、「汇总」什么时候出现 ✓、
 * 徽章小数位 ✓、`null` 会不会被渲染成 \"ICIR: null\" ✗。
 * 最后那条是本文最重要的：**`null` 必须整块不渲染** ✓（不是显示 0、也不是显示 null ✗）。
 */
const seg = (over: Partial<ICSegment> = {}): ICSegment => ({
  segment: '2024',
  points: [
    { date: '2024-01-02', ic: 0.1, rank_ic: 0.05 },
    { date: '2024-01-03', ic: -0.2, rank_ic: -0.1 },
  ],
  mean_ic: 0.01234,
  icir: 0.1234,
  mean_rank_ic: 0.05678,
  // ⚠ 别用 `0.8765` ✗：它在二进制里是 `0.87649999…` ⇒ `toFixed(3)` 给 **0.876** ✓
  //   （JS 的 toFixed 不是"四舍五入"那么简单 ✓，落在这类边界上会与直觉相反 ✗）
  rank_icir: 0.87654,
  ...over,
})

const html = (data?: ICAnalysis | null) => renderToStaticMarkup(<ICChart data={data} />)

describe('ICChart（静态语义）', () => {
  it('无数据 ⇒ 明确的空态文案', () => {
    expect(html(null)).toContain('暂无 IC 数据')
    expect(html(undefined)).toContain('暂无 IC 数据')
  })

  it('训练/测试都为空数组 ⇒ 同样是空态（不是空图 ✗）', () => {
    expect(html({ train: [], test: [] })).toContain('暂无 IC 数据')
  })

  it('⭐ 默认展示**测试集**，且只列测试集的分段', () => {
    const out = html({ test: [seg({ segment: '测试段A' })], train: [seg({ segment: '训练段B' })] })
    expect(out).toContain('测试集 IC')
    expect(out).toContain('测试段A')
    expect(out).not.toContain('训练段B')
  })

  it('⭐ 徽章小数位按口径：平均IC 4 位、ICIR 3 位、RankIC 4 位、RankICIR 3 位', () => {
    const out = html({ test: [seg()] })
    expect(out).toContain('平均IC: 0.0123')
    expect(out).toContain('ICIR: 0.123')
    expect(out).toContain('RankIC: 0.0568')
    expect(out).toContain('RankICIR: 0.877')
  })

  it('⭐⭐ `null` 的 ICIR / RankIC 相关徽章**整块不渲染**（不许显示 null 或 0 ✗）', () => {
    const out = html({ test: [seg({ icir: null, mean_rank_ic: null, rank_icir: null })] })
    expect(out).not.toContain('ICIR:')
    expect(out).not.toContain('RankIC:')
    expect(out).not.toContain('RankICIR:')
    // 但平均 IC 是必填字段 ⇒ 必须在 ✓
    expect(out).toContain('平均IC:')
  })

  it('⭐ 「汇总」只在对应 tab 的 merged 真有 points 时出现', () => {
    const noMerged = html({ test: [seg()] })
    expect(noMerged).not.toContain('>汇总<')

    const withEmptyMerged = html({
      test: [seg()],
      merged_test: { segment: '汇总', points: [], mean_ic: 0 },
    })
    expect(withEmptyMerged).not.toContain('>汇总<')

    const withMerged = html({ test: [seg()], merged_test: { segment: '汇总', points: [{ date: '2024-01-02', ic: 0.1 }], mean_ic: 0 } })
    expect(withMerged).toContain('汇总')
  })

  it('⭐ 训练集那条过拟合警示必须常驻（默认看测试集也要在）', () => {
    const out = html({ test: [seg()] })
    expect(out).toContain('训练集 IC 为样本内指标')
    expect(out).toContain('请以测试集 IC 为准')
  })

  it('只有训练集数据时不崩：落到**分段级**空态（不是整块空态 ✓）', () => {
    // ⚠ 实测：组件有**三种**空状态 ✗→✓
    //   ① 完全没数据 ⇒「暂无 IC 数据」；② 有数据但当前分段为空 ⇒「**该分段**暂无 IC 数据」；
    //   ③ 正常 ⇒ 图表。我第一版用 `not.toContain('暂无 IC 数据')` ✗ ⇒ 被②的子串命中 ✓。
    const out = html({ train: [seg({ segment: '训练段B' })] })
    expect(out).toContain('训练集 / 测试集 IC')
    expect(out).toContain('该分段暂无 IC 数据')
    expect(out).not.toContain('训练段B')
  })

  it('单点曲线也能渲染（平均 IC 标线要能不炸）', () => {
    const out = html({ test: [seg({ points: [{ date: '2024-01-02', ic: 0.3 }] })] })
    expect(out).toContain('平均IC: 0.0123')
  })
})
