/**
 * `topkCurveLogic`（★ v2.11.59 从 1319 行的 `TopkCurveModal` 外提 ✓）的测试。
 *
 * ## 为什么值得测
 * 这些函数以前埋在组件里，**一条测试都没有** ✗。其中 `niceAxis()` 是"看图才看得出错"的那种：
 * 刻度算错**不会报错**，只是**图会误导人** ✓（比如刻度不等距、或没有 0 基线 ⇒
 * 让人以为"全程在水上" ✗，这是最典型的"静默错"✓）。
 */
import { describe, expect, it } from 'vitest'

import {
  fmtRatio,
  itemLabel,
  nav,
  niceAxis,
  pct,
  signedPct,
} from '../components/topkCurveLogic'

describe('显示用格式化（空值必须给 `-`，不能给 0 或 NaN）', () => {
  it('pct：小数 → 百分数，2 位小数', () => {
    expect(pct(0.01234)).toBe('1.23%')
    expect(pct(-0.5)).toBe('-50.00%')
    expect(pct(0.5, 1)).toBe('50.0%')
  })

  it('⭐ 空值 / NaN / Infinity 一律 `-`（否则图上会出现 "NaN%" ✗）', () => {
    expect(pct(null)).toBe('-')
    expect(pct(undefined)).toBe('-')
    expect(pct(Number.NaN)).toBe('-')
    expect(pct(Number.POSITIVE_INFINITY)).toBe('-')
    expect(signedPct(Number.NaN)).toBe('-')
    expect(fmtRatio(null)).toBe('-')
  })

  it('signedPct：正数带 `+`，负数自然带 `-`', () => {
    expect(signedPct(1.3422)).toBe('+134.22%')
    expect(signedPct(-0.031)).toBe('-3.10%')
    expect(signedPct(0)).toBe('+0.00%')
  })

  it('nav：累计值 → 净值（+1）；缺失保持 null 让图上断线', () => {
    expect(nav(0)).toBe(1)
    expect(nav(-0.25)).toBe(0.75)
    expect(nav(null)).toBeNull()
    expect(nav(Number.NaN)).toBeNull()
  })

  it('fmtRatio：2 位小数', () => {
    expect(fmtRatio(1.23456)).toBe('1.23')
    expect(fmtRatio(0)).toBe('0.00')
  })
})

describe('niceAxis（Y 轴刻度：整数百分比 + 等距 + 必含 0）', () => {
  const cases: Array<[number, number]> = [
    [-0.71, 4.99],
    [0.02, 0.9],
    [-0.13, -0.02],
    [0, 0],
    [-1.5, 1.5],
    [0.001, 0.003],
  ]

  it('⭐ domain 必须**覆盖**输入范围（否则曲线会被裁掉 ✗）', () => {
    for (const [lo, hi] of cases) {
      const { domain } = niceAxis(lo, hi)
      expect(domain[0], `lo=${lo} hi=${hi} ⇒ domain=${domain}`).toBeLessThanOrEqual(lo)
      expect(domain[1], `lo=${lo} hi=${hi} ⇒ domain=${domain}`).toBeGreaterThanOrEqual(hi)
    }
  })

  it('⭐ 刻度必须**等距**且**落在整数百分比**上（用户明确要求 ✓）', () => {
    for (const [lo, hi] of cases) {
      const { ticks } = niceAxis(lo, hi)
      expect(ticks.length).toBeGreaterThanOrEqual(2)
      const step = ticks[1] - ticks[0]
      for (let i = 1; i < ticks.length; i++) {
        expect(ticks[i] - ticks[i - 1], `lo=${lo} hi=${hi} ticks=${ticks}`).toBeCloseTo(step, 10)
      }
      for (const t of ticks) {
        expect(Math.abs(t * 100 - Math.round(t * 100)), `刻度 ${t} 不是整数百分比 ✗`).toBeLessThan(1e-9)
      }
    }
  })

  it('⭐ 刻度必须**含 0**（否则看不出"在水上还是水下"✗）', () => {
    for (const [lo, hi] of cases) {
      const { ticks } = niceAxis(lo, hi)
      expect(ticks.some((t) => Math.abs(t) < 1e-12), `lo=${lo} hi=${hi} ticks=${ticks} 没有 0 ✗`).toBe(true)
    }
  })

  it('⭐ 分段数上限 8，且"够宽的范围"至少 2 段', () => {
    for (const [lo, hi] of cases) {
      const { ticks } = niceAxis(lo, hi)
      const segs = ticks.length - 1
      // ⚠⚠ 我这条断言**错了两次** ✗→✓，记下来：
      //   ① 第一版写"所有情况 ≥2 段" ⇒ `lo=hi=0` 红（全零序列给 1 段是合理的 ✓）；
      //   ② 第二版只放过全零 ⇒ `0.1%~0.3%` 又红 ✓ —— 因为刻度被约束成**整数百分比** ✓，
      //      `0.1%~0.3%` 只能落到 0%~1% ⇒ **本来就该 1 段** ✓。
      //   ⇒ 正确口径：**只有跨度 ≥2 个整数百分点时才要求 ≥2 段** ✓（这是函数的真实语义 ✓）。
      const wide = Math.abs(hi - lo) >= 0.02
      expect(segs, `lo=${lo} hi=${hi} ⇒ ${segs} 段（wide=${wide}）`).toBeGreaterThanOrEqual(
        wide ? 2 : 1,
      )
      expect(segs, `lo=${lo} hi=${hi} ⇒ ${segs} 段`).toBeLessThanOrEqual(8)
    }
  })

  it('数据恒为 0 时要给一段**最小可视范围**（domain 不能退化成 [0,0] ✗）', () => {
    const { domain } = niceAxis(0, 0)
    expect(domain[1]).toBeGreaterThan(domain[0])
  })
})

describe('itemLabel（明细档显示名）', () => {
  it('分位组标 Q几', () => {
    expect(itemLabel({ kind: 'decile', quantile: 3, k: 0.1 } as never, null)).toBe('最强分位组 Q3')
  })

  it('固定 K 档标 `固定 K=…`，默认档要加注', () => {
    const it = { kind: 'fixed', k: 5 } as never
    expect(itemLabel(it, 5)).toContain('（默认 10% 档）')
    expect(itemLabel(it, 10)).not.toContain('（默认')
  })
})
