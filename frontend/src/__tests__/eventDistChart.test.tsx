/**
 * `EventDistChart` 测试（★ v2.9.77，2026-10-19 ✓ —— 只测**能可靠断言**的部分 ✓）。
 *
 * ## ⚠ 先说测不了什么（不假装 ✗）
 *
 * 图内是 `recharts` ✓，而 `ResponsiveContainer` 在 jsdom 里量不到尺寸 ⇒ **SVG 内断言不可靠** ✗。
 * 所以这里只钉两块**纯 DOM** 的东西 ✓，而它们恰好就是 v1.19.82 改版的两个重点 ✓：
 *
 * 1. **图外那行统计值** ✓（改版时特意把中位数/均值/分位数**全挪到图外** ✓ —— 因为贴在
 *    图顶会被裁掉、彼此会压住 ✗）：断言 12 项名称齐 ✓、每项带**同色标记** ✓、
 *    数值按"百分比 2 位 / 尾部与极值 1 位"格式化 ✓、缺失一律 `-` ✓；
 * 2. **没有分布数据时的文案** ✓（旧后端产物 ✓）：`（该结果没有分布数据 … 点「重新计算」即可补上）` ✓
 *    —— 换成空白/报错，用户就不知道该怎么办了 ✗。
 *
 * 另外顺手钉住"**数据形状不合法就当作没有**" ✓（`counts.length !== edges.length - 1` ✓），
 * 否则会画出**错位的桶** ✗（比不画更糟 ✓）。
 */
// @vitest-environment jsdom
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import EventDistChart from '../components/EventDistChart'

// ⚠ jsdom **没有** `ResizeObserver` ✗，而 recharts 的 `ResponsiveContainer` 需要它 ✓
//   ⇒ 不加桩就直接 `ReferenceError: ResizeObserver is not defined` ✓（实测抓到 ✓）。
//   注意：这里只是让它"能挂载" ✓，不代表图内尺寸正确 ✗（jsdom 拿不到真实宽高 ✓），
//   所以图内 SVG 仍然不断言 ✓（文件头已说明 ✓）。
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  })
})

async function mount(props: React.ComponentProps<typeof EventDistChart>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(<EventDistChart {...props} />)
  })
  return host
}

const EDGES = [-0.1, -0.05, 0, 0.05, 0.1]
const COUNTS = [3, 7, 20, 10]

describe('EventDistChart（能可靠断言的部分）', () => {
  it('数据不完整/形状不合法 ⇒ 当作"没有分布数据"，并给出可操作的下一步', async () => {
    for (const bad of [
      { edges: null, counts: null },
      { edges: EDGES, counts: null },
      { edges: EDGES, counts: [1, 2] },        // 长度对不上 ✗
      { edges: [-0.1], counts: [1, 2] },       // edges 太短 ✗
    ]) {
      const el = await mount({ ...bad, k: 5, stat: null })
      expect(el.textContent).toContain('没有分布数据')
      expect(el.textContent).toContain('重新计算')   // 告诉用户怎么办 ✓
    }
  })

  it('⭐ 图外统计行：12 项齐全（含样本），每项带同色标记', async () => {
    const el = await mount({
      edges: EDGES, counts: COUNTS, k: 5,
      stat: { n: 40, mean: 0.0123, median: 0.008, win: 0.55, p01: -0.05, p05: -0.02,
              p25: 0.001, p75: 0.02, p95: 0.04, p99: 0.09, min: -0.08, max: 0.12 },
    })
    for (const name of ['中位数', '均值', 'p75', 'p25', 'p95', 'p05', 'p99', 'p01',
                        '胜率(>0)', '最小', '最大', '样本']) {
      expect(el.textContent).toContain(name)
    }
    // 12 个色标（▍）✓
    expect((el.textContent || '').split('▍').length - 1).toBe(12)
  })

  it('⭐ 数值格式：位置/分位 2 位、尾部与极值 1 位、缺失 `-`、样本原样', async () => {
    const el = await mount({
      edges: EDGES, counts: COUNTS, k: 5,
      stat: { n: 40, mean: 0.0123, median: null, p99: 0.1234, min: -0.0812, max: null },
    })
    expect(el.textContent).toContain('1.23%')     // 均值 2 位 ✓
    expect(el.textContent).toContain('12.3%')     // p99 1 位 ✓
    expect(el.textContent).toContain('-8.1%')     // 最小 1 位 ✓
    expect(el.textContent).toContain('中位数 = -') // 缺失 ✓（不是 0.00% ✗）
    expect(el.textContent).toContain('最大 = -')
    expect(el.textContent).toContain('样本 = 40')  // 样本是整数、不换算 ✓
  })
})
