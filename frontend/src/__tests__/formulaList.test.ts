/**
 * `formulaList` 测试（★ v2.9.80，2026-10-19 ✓ —— 纯逻辑，此前零测试 ✗）。
 *
 * ## 它管的是"用户每天要用的定位"
 *
 * 用户原话（文件头）：「排序后输入 `l` 要能定位到 L 开头的公式，也要能定位到中文「龙腾四海」（lóng）」、
 * 「输入 `ltsh`（拼音首字母）也要能把「龙腾四海8」「龙腾四海10」筛出来」✓。
 *
 * ## 两条真实踩过的坑（这里专门钉住 ✓）
 *
 * 1. **ICU 拼音锚字必须读音单一** ✗：网上流传的表里 `痳` 在 ICU 读作 `lín` ⇒ **龙(lóng) 被判成 M** ✗；
 *    `呥` 读作别的音 ⇒ **日(rì) 被判成 S** ✗（注释写着 2026-09-25 实测 ✓）
 *    ⇒ 本文件直接断言 **'龙'⇒L ✓、'日'⇒R ✓** —— 换锚字若把它们搞错，这里立刻红 ✓；
 * 2. **数字要按"数值"排序** ✓（`深跌2 < 深跌3 < 深跌10` ✓，而不是字符串序把 10 排到 2 前面 ✗）；
 * 3. **`pinned` 不再参与浮顶** ✓（v2.1.46：顺序只由后端 `sort_key` 决定 ✓ —— 取消置顶后
 *    要**留在原处**方便手工排序 ✓）。
 */
import { describe, expect, it } from 'vitest'
import { arrangeFormulas, formulaKey, matchRank, pinyinInitial } from '../formulaList'

const F = (name: string, text = '', pinned = false) => ({ name, text, pinned })

describe('pinyinInitial（ICU 拼音序二分）', () => {
  it('⭐ 两个踩过坑的字：龙 ⇒ L、日 ⇒ R（锚字换错就会变 M / S ✗）', () => {
    expect(pinyinInitial('龙')).toBe('L')
    expect(pinyinInitial('日')).toBe('R')
  })

  it('常用字与分组边界', () => {
    expect(pinyinInitial('阿')).toBe('A')
    expect(pinyinInitial('八')).toBe('B')
    expect(pinyinInitial('深')).toBe('S')     // shēn ✓
    expect(pinyinInitial('黄')).toBe('H')
    expect(pinyinInitial('回')).toBe('H')
  })

  it('非汉字 ⇒ 空串（不抛 ✗）', () => {
    expect(pinyinInitial('A')).toBe('')
    expect(pinyinInitial('1')).toBe('')
    expect(pinyinInitial('_')).toBe('')
    expect(pinyinInitial('')).toBe('')
  })
})

describe('formulaKey（排序键 / 缩写键）', () => {
  it('⭐ 文件头给的四个例子逐一核对', () => {
    expect(formulaKey('ALPHA143_S4')).toBe('alpha143s4')
    expect(formulaKey('深跌3')).toBe('sd3')
    expect(formulaKey('龙腾四海8')).toBe('ltsh8')
    expect(formulaKey('黄金回踩')).toBe('hjhc')
  })

  it('中英文混在同一串里（这就是"中文英文放一起排序" ✓）', () => {
    const k = formulaKey('L龙')
    expect(k).toBe('ll')
  })
})

describe('matchRank（搜索优先级）', () => {
  const item = F('龙腾四海8', 'MA(CLOSE,5)')

  it('空查询 ⇒ 0（不筛选 ✓）', () => {
    expect(matchRank(item, '   ')).toBe(0)
  })

  it('⭐ 前缀命中（名字或拼音首字母）⇒ 0', () => {
    expect(matchRank(item, '龙')).toBe(0)
    expect(matchRank(item, 'ltsh')).toBe(0)
    expect(matchRank(item, 'L')).toBe(0)      // 大小写不敏感 ✓
  })

  it('包含命中 ⇒ 1；只在原文命中 ⇒ 2；都不中 ⇒ 3', () => {
    expect(matchRank(item, '腾四')).toBe(1)
    expect(matchRank(item, 'ma(')).toBe(2)
    expect(matchRank(item, 'zzz')).toBe(3)
  })
})

describe('arrangeFormulas（过滤 + 排序）', () => {
  const list = [F('龙腾四海8', 'X'), F('ALPHA143_S4', 'Y'), F('深跌10'), F('深跌2'), F('深跌3')]

  it('无查询 + original ⇒ **原样返回**（"恢复原序"就是它 ✓）', () => {
    expect(arrangeFormulas(list, '', 'original').map((f) => f.name)).toEqual(
      list.map((f) => f.name),
    )
  })

  it('⭐ 无查询 + key ⇒ 按名称键 A→Z，且数字按数值（2 < 3 < 10 ✓）', () => {
    const got = arrangeFormulas(list, '', 'key').map((f) => f.name)
    // 深跌* 的键都是 `sd*` ⇒ 按数值升序 ✓；ALPHA… 键 `alpha…` 最前 ✓
    expect(got[0]).toBe('ALPHA143_S4')
    const shen = got.filter((n) => n.startsWith('深跌'))
    expect(shen).toEqual(['深跌2', '深跌3', '深跌10'])
  })

  it('⭐ 有查询 ⇒ 前缀命中优先，其次包含，最后原文', () => {
    const mixed = [F('MA5', 'CLOSE'), F('龙腾四海', 'A'), F('L龙', 'B')]
    const got = arrangeFormulas(mixed, 'l', 'original').map((f) => f.name)
    // 前缀命中的（`L龙` 名字前缀、`龙腾四海` 键前缀）排在"仅原文命中"的 MA5 之前 ✓
    expect(got.indexOf('MA5')).toBe(got.length - 1)
  })

  it('⭐ 同名时保持稳定（不抖动 ✓）', () => {
    const dup = [F('同名', 'a'), F('同名', 'b'), F('同名', 'c')]
    const got = arrangeFormulas(dup, '', 'key')
    expect(got.map((f) => f.text)).toEqual(['a', 'b', 'c'])
  })

  it('⭐ nameOnly=true ⇒ 排除"只在原文命中"的（敲 l 不再命中一大片 ✓）', () => {
    const l = [F('MA5', 'CLOSE'), F('龙腾四海', 'A')]
    expect(arrangeFormulas(l, 'l', 'original', true).map((f) => f.name)).toEqual(['龙腾四海'])
    // 关掉开关 ⇒ 原文里的 close 也能命中 ✓（两种口味都要能用 ✓）
    expect(arrangeFormulas(l, 'close', 'original', false).map((f) => f.name)).toEqual(['MA5'])
    expect(arrangeFormulas(l, 'close', 'original', true)).toEqual([])
  })

  it('⭐ pinned 不参与排序（顺序由后端 sort_key 决定 ✓，取消置顶要留在原处 ✓）', () => {
    const p = [F('b', '', true), F('a', '', false), F('c', '', true)]
    // original 模式下：pinned 不该把 b/c 提到最前 ✓
    expect(arrangeFormulas(p, '', 'original').map((f) => f.name)).toEqual(['b', 'a', 'c'])
    // key 模式下：只按键排（a < b < c）✓
    expect(arrangeFormulas(p, '', 'key').map((f) => f.name)).toEqual(['a', 'b', 'c'])
  })
})
