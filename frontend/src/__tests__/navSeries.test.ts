/**
 * `components/navSeries.ts` 测试（★ v2.9.78，2026-10-19 ✓ —— 纯逻辑，此前零测试 ✗）。
 *
 * ## 这个文件存在的**唯一理由**就是"同一件事只能有一套口径" ✓（文件头原话 ✓）
 *
 * v1.19.60 从 `SignalTestPanel` 提取它，因为"单因子事件研究"面板也要画同款曲线 ⇒
 * 曲线名 / 配色 / 线型必须**只有一份** ✓，否则同一根曲线在两个页面会出现两种叫法或两种颜色 ✗。
 *
 * ⇒ 所以这里最值钱的不是"某个名字对不对"✗，而是三条**一致性不变量** ✓：
 * 1. **每个有名字的 key 都必须有颜色** ✓（漏了 ⇒ 落到轮转配色、和别的线撞色 ✗）；
 * 2. **每个有说明的 key 都必须有名字** ✓（否则 tooltip 里有解释、图例里却是英文 key ✗）；
 * 3. 未知 key 必须**安全降级**（原样返回 / 轮转配色 / 无虚线 ✓），不许抛 ✗。
 *
 * 另外钉住一处**用户定过的口径** ✓：`bench_equal`（未触发组等权参照线）的说明必须写着
 * **"同持有期"** ✓ —— 2026-10-17 判定"参照持 1 天、策略持 60 天"是**口径 bug** ✗，
 * 文案里若丢了这句，用户又会把差距当成 alpha ✗。
 */
import { describe, expect, it } from 'vitest'
import {
  NAV_COLOR,
  NAV_DESC,
  NAV_LABEL,
  allocLabel,
  navColor,
  navDash,
  navDesc,
  navLabel,
} from '../components/navSeries'

describe('navSeries：一致性不变量', () => {
  it('⭐ 每个有名字的 key 都有颜色（漏了就会与别的线撞色 ✗）', () => {
    const missing = Object.keys(NAV_LABEL).filter((k) => !NAV_COLOR[k])
    expect(missing).toEqual([])
  })

  it('⭐ 每个有说明的 key 都有名字与颜色（否则 tooltip 有解释、图例却是英文 ✗）', () => {
    for (const k of Object.keys(NAV_DESC)) {
      expect(NAV_LABEL[k], `NAV_DESC 里的 ${k} 没有中文名 ✗`).toBeTruthy()
      expect(NAV_COLOR[k], `NAV_DESC 里的 ${k} 没有配色 ✗`).toBeTruthy()
    }
  })

  it('颜色不能重复到"看着像同一根线"（关键几条必须互不相同 ✓）', () => {
    const keys = ['benchmark', 'cash_even', 'event_even', 'period_roll', 'bench_equal']
    const colors = keys.map((k) => NAV_COLOR[k])
    // ⚠ `benchmark` 与 `nav_official_bench` 都是灰系、但那是"两条基准"，允许相近 ✓；
    //   这里只要求上面这 5 条**互不相同** ✓（尤其 bench_equal 不能和灰的沪深300 混 ✓）
    expect(new Set(colors).size).toBe(keys.length)
  })
})

describe('navSeries：死区档位的自动展开', () => {
  it('`event_even_band0p05` ⇒ 死区 0.05%（`p` 还原成小数点 ✓）', () => {
    expect(allocLabel('event_even_band0p05')).toBe('事件等权 · 死区 0.05%（自动对比档）')
    expect(allocLabel('event_even_band0p1')).toContain('0.1%')
  })

  it('已知 key 走表、未知 key 原样返回（不抛 ✗）', () => {
    expect(navLabel('period_roll')).toBe(NAV_LABEL.period_roll)
    expect(navLabel('cash_even')).toBe('现金等分（不主动再平衡）')
    expect(navLabel('whatever_unknown')).toBe('whatever_unknown')
    expect(allocLabel('whatever_unknown')).toBe('whatever_unknown')
  })

  it('navDesc：已知有说明、死区档位自动生成、未知返回 undefined', () => {
    expect(navDesc('period_roll')).toContain('实盘口径')
    expect(navDesc('event_even_band0p05')).toContain('死区 0.05%')
    expect(navDesc('event_even_band0p05')).toContain('手续费')
    expect(navDesc('nope')).toBeUndefined()
  })
})

describe('navSeries：配色与线型', () => {
  it('未知 key ⇒ 4 色轮转（稳定可预期 ✓）', () => {
    const a = navColor('x1', 0)
    const b = navColor('x2', 1)
    const e = navColor('x5', 4)
    expect(a).not.toBe(b)
    expect(e).toBe(a)                     // 第 4 个回到第 0 个 ✓
    expect(navColor('cash_even', 0)).toBe(NAV_COLOR.cash_even)  // 已知 key 不吃轮转 ✓
  })

  it('虚线规则：下界/官方基准与现金等分是 5 3；三条基准是 4 4；其余实线', () => {
    expect(navDash('nav_exact_min')).toBe('5 3')
    expect(navDash('nav_official_bench')).toBe('5 3')
    expect(navDash('cash_even')).toBe('5 3')
    expect(navDash('benchmark')).toBe('4 4')
    expect(navDash('baseline')).toBe('4 4')
    expect(navDash('baseline_median')).toBe('4 4')
    expect(navDash('period_roll')).toBeUndefined()
    expect(navDash('unknown')).toBeUndefined()
  })
})

describe('navSeries：用户定过的口径（文案丢了就会看错结论 ✗）', () => {
  it('⭐ bench_equal 的说明必须写明「同持有期」', () => {
    const d = NAV_DESC.bench_equal
    expect(d).toContain('同持有期')
    expect(d).toContain('持有「持仓周期」那么多天')
    // 并明确指出它和灰色沪深300 基准**不是一回事** ✓
    expect(d).toContain('不是一回事')
  })

  it('event_even 的说明要点明「期间再触发 ⇒ 持有天数重置」与「费更高」', () => {
    const d = NAV_DESC.event_even
    expect(d).toContain('重置')
    expect(d).toContain('28.9%')      // 与 period_roll 的实测对比 ✓
  })
})
