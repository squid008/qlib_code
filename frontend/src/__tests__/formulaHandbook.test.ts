/**
 * `formulaHandbook` 测试（★ v2.9.81，2026-10-19 ✓ —— 手册数据 + 检索，此前只被间接用到 ✗）。
 *
 * ## 为什么手册数据值得测
 *
 * 手册是用户**唯一**能自己查"这个函数怎么用"的地方 ✓。它出问题的方式很具体：
 *  · **展示顺序被打乱** ✗ —— 用户 2026-09-13 定过口径：前 6 个固定是 `c/h/l/o/v/amount` ✓，
 *    其余**全部**（字段 + 函数一起）按英文字母 A→Z ✓（v1.19.20 加，好处是**新增条目不用手动插位置** ✓）；
 *  · **`desc` 里写了 Markdown 星号** ✗ —— 本文件的 `desc` 是**纯文本渲染** ✓（注释反复强调 ✓），
 *    星号会**原样显示**给用户 ✓；
 *  · **后端加了、手册忘写** ✗ —— 这类有后端测试守着（`test_formula_handbook_sync.py` ✓），
 *    但"手册里写错/重复/顺序乱"只能在这里守 ✓。
 */
import { describe, expect, it } from 'vitest'
import { FORMULA_HANDBOOK, filterHandbook } from '../formulaHandbook'

describe('展示顺序（用户定过的口径）', () => {
  it('⭐ 前 6 个固定是 CORE 行情字段，且顺序就是 c/h/l/o/v/amount', () => {
    expect(FORMULA_HANDBOOK.slice(0, 6).map((e) => e.name)).toEqual([
      'CLOSE', 'HIGH', 'LOW', 'OPEN', 'VOL', 'AMOUNT',
    ])
  })

  it('⭐ 其余全部（字段 + 函数一起）按名字 A→Z（新增条目不用手动插位置 ✓）', () => {
    const rest = FORMULA_HANDBOOK.slice(6).map((e) => e.name)
    const sorted = [...rest].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    expect(rest).toEqual(sorted)
    // 抽查几对容易搞错的（`HHV` 必须在 `HHVBARS` 前 ✓；`L2_AMO` 在 `LLV` 前 ✓）
    expect(rest.indexOf('HHV')).toBeLessThan(rest.indexOf('HHVBARS'))
    expect(rest.indexOf('L2_AMO')).toBeLessThan(rest.indexOf('LLV'))
  })

  it('核心字段不会在后面重复出现', () => {
    const rest = FORMULA_HANDBOOK.slice(6).map((e) => e.name)
    for (const core of ['CLOSE', 'HIGH', 'LOW', 'OPEN', 'VOL', 'AMOUNT']) {
      expect(rest).not.toContain(core)
    }
  })
})

describe('数据完整性', () => {
  it('名字唯一、kind 合法、abbr/desc 都非空', () => {
    const names = FORMULA_HANDBOOK.map((e) => e.name)
    expect(new Set(names).size).toBe(names.length)
    for (const e of FORMULA_HANDBOOK) {
      expect(['func', 'field']).toContain(e.kind)
      expect(e.abbr.trim()).not.toBe('')
      expect(e.desc.trim()).not.toBe('')
    }
  })

  it('⭐ desc 里不许有 Markdown 星号（纯文本渲染 ⇒ 星号会原样显示给用户 ✗）', () => {
    const bad = FORMULA_HANDBOOK.filter((e) => e.desc.includes('**')).map((e) => e.name)
    expect(bad).toEqual([])
  })

  it('几个"历史上容易漏"的条目都在（含 2026-10-15 新加的入场价字段）', () => {
    for (const n of ['DATE', 'ENTRY_PRICE', 'ENTRY_FIRST', 'FINANCE', 'COST', 'WINNER',
                     'BLOCKSETNUM', 'INSUM', 'SMA', 'BARSSINCE']) {
      expect(FORMULA_HANDBOOK.some((e) => e.name === n), `手册缺 ${n} ✗`).toBe(true)
    }
  })
})

describe('filterHandbook（检索口径）', () => {
  it('空关键词 ⇒ 全量（就是展示顺序 ✓）', () => {
    expect(filterHandbook('')).toBe(FORMULA_HANDBOOK)
    expect(filterHandbook('   ')).toBe(FORMULA_HANDBOOK)
  })

  it('按英文名检索，大小写不敏感', () => {
    const got = filterHandbook('ema_tdx').map((e) => e.name)
    expect(got).toEqual(['EMA_TDX'])
    expect(filterHandbook('HHV').map((e) => e.name)).toContain('HHV')
  })

  it('⭐ 按中文缩写也能搜到（用户更可能记得"收盘价"而不是 CLOSE ✓）', () => {
    const got = filterHandbook('收盘价').map((e) => e.name)
    expect(got).toEqual(['CLOSE'])
    expect(filterHandbook('换手率').map((e) => e.name)).toEqual(['TURNOVERRATE'])
  })

  it('检索前后空白被忽略；搜不到返回空数组（不抛 ✗）', () => {
    expect(filterHandbook('  ma  ').some((e) => e.name === 'MA')).toBe(true)
    expect(filterHandbook('zzzz-not-exist')).toEqual([])
  })
})
