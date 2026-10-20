import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import LayerChart from '../components/LayerChart'
import type { LayerPoint, LayerReturns } from '../types'

/**
 * `LayerChart` 的**服务端渲染**测试（★ v2.10.26，2026-10-20 ✓ —— 补上前端覆盖率的第二块）。
 *
 * ## 为什么只测「外层」
 *
 * 图例（`Group1..5` / `多空(1-5)` / `池内等权` / `基准` ✓）画在 recharts 的 `<Legend>` 里 ✓，
 * 而 `<ResponsiveContainer>` 在 SSR 下宽高为 0 ✗ ⇒ 内部**根本不渲染** ✓
 * ⇒ 这里**测不到**图例与开关 ✓（如实说明，不假装 ✗）。
 *
 * ## 能测、且值得测的是「三种状态」
 *
 * ① 完全没数据 ⇒「暂无分层数据」✓（不画空图 ✗）；
 * ② 有分段但该段 `groups` 为空 ⇒「**该分段**暂无分层数据」✓；
 * ③ 正常 ⇒ 渲染分段名 ✓。
 * ②③ 的区分最容易写错（用一个 `暂无` 兜住所有情况 ⇒ 用户看不出"到底是有数据还是没数据"✗）。
 */
const pt = (over: Partial<LayerPoint> = {}): LayerPoint => ({
  date: '2024-01-02',
  Group1: 1.01,
  Group2: 1.02,
  Group3: 1.03,
  Group4: 1.04,
  Group5: 1.05,
  long_short: 0.04,
  long_average: 1.03,
  ...over,
})

const html = (data?: LayerReturns | null) => renderToStaticMarkup(<LayerChart data={data} />)

describe('LayerChart（静态语义）', () => {
  it('无数据 ⇒ 明确的空态文案', () => {
    expect(html(null)).toContain('暂无分层数据')
    expect(html(undefined)).toContain('暂无分层数据')
  })

  it('segments 为空 + merged 为空 ⇒ 同样是空态', () => {
    expect(html({ segments: [], merged: null })).toContain('暂无分层数据')
  })

  it('⭐ 有分段 ⇒ 渲染分段名（且不是空态）', () => {
    const out = html({ segments: [{ segment: '段1', groups: [pt()] }] })
    expect(out).toContain('段1')
    expect(out).not.toContain('暂无分层数据')
  })

  it('⭐ 分段存在但该段无数据 ⇒ 落到**分段级**空态（与整体空态分开 ✓）', () => {
    const out = html({ segments: [{ segment: '段2', groups: [] }] })
    expect(out).toContain('该分段暂无分层数据')
    expect(out).not.toContain('>暂无分层数据<')
  })

  it('只有 merged（无分段）也应当不算空态', () => {
    const out = html({ segments: [], merged: { segment: '汇总', groups: [pt()] } })
    expect(out).not.toContain('>暂无分层数据<')
    expect(out).toContain('汇总')
  })
})
