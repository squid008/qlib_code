/**
 * `formulaList.ts` 的自检（**零依赖、零配置** ✓）—— 直接跑：
 *
 *     cd frontend && node src/formulaList.check.ts
 *
 * 为什么放在 `src/` 而不是新建测试框架：仓库前端**没有** vitest/jest（也不打算为两个纯函数引进来 ✗），
 * 而 node ≥ 23 能**原生执行 `.ts`**（类型擦除 ✓）⇒ 一个文件搞定 ✓；同时它在 `tsconfig` 的 `include` 里 ✓
 * ⇒ `npx tsc --noEmit`（仓库闸门的三件套之一 ✓）会**顺带类型检查**它 ⇒ 不会悄悄烂掉 ✓。
 * 样例用**线上真实公式名**（2026-09-25 的 68 条 ✓）⇒ 覆盖的是真会出现的中英混排/拼音场景 ✓。
 */
import { arrangeFormulas, formulaKey, matchRank, pinyinInitial } from './formulaList.ts'

/** 线上真实公式名（68 条，2026-09-25 取 ✓）。 */
const REAL_NAMES = [
  'ALPHA143_S4', '深跌3', '深跌2', '龙腾四海8', '黄金回踩', '趋势顶底', '冰谷火焰', '市值', '市值对数',
  '负市值', '负市值对数', '趋势顶底离开底部', '主力资金', '选股', 'CWH_BREAK_WAIT20_F1', 'CUP_POOL', 'CCC',
  'CCCMA250', 'CWH_MIX_D_PCT_R40', 'XG', 'XG2', '二浪平台突破近似', '二浪平台突破近似1', '二浪加强', '强突破',
  'LLT', '黏合强突破', '过顶0', '过顶', '蹦极新生', 'CPX', '空中加油3', 'ALPHA143', 'ALPHA143_60',
  'ALPHA143原版', 'AR4_COMBO', '超户市值比', '超户', '步步高', '复苏', '缓升', '快升', 'B点', 'S点', '多头',
  '空头', '三步擒龙资金', '股力值', '地量', '量王', '倍量', '天量', '量能系数', '偏离度', '强势要素',
  '纵横趋势突破线', '纵横趋势底部线', '纵横趋势K1线', '纵横趋势K2线', '纵横趋势K3线', '深跌10', '深跌20',
  '深跌60', '深跌30', '大阳起势', '冰火两重天', '多空博弈', 'AR13_ID60',
]

const items = REAL_NAMES.map((name) => ({ name, text: 'OUT:CLOSE;' }))
const item = (name: string, text = 'OUT:CLOSE;') => ({ name, text })

let passed = 0
function ok(cond: boolean, label: string): void {
  if (!cond) throw new Error('✗ 断言失败：' + label)
  passed += 1
}
function eq<T>(actual: T, expected: T, label: string): void {
  const a = JSON.stringify(actual)
  const b = JSON.stringify(expected)
  if (a !== b) throw new Error(`✗ ${label}\n   实际 = ${a}\n   期望 = ${b}`)
  passed += 1
}
const names = (list: { name: string }[]) => list.map((x) => x.name)

// ── ① 拼音首字母 ───────────────────────────────────────────────────────────────
eq(pinyinInitial('龙'), 'L', '龙→L')
eq(pinyinInitial('腾'), 'T', '腾→T')
eq(pinyinInitial('四'), 'S', '四→S')
eq(pinyinInitial('海'), 'H', '海→H')
eq(pinyinInitial('深'), 'S', '深→S')
eq(pinyinInitial('跌'), 'D', '跌→D')
eq(pinyinInitial('黄'), 'H', '黄→H')
eq(pinyinInitial('金'), 'J', '金→J')
eq(pinyinInitial('冰'), 'B', '冰→B')
eq(pinyinInitial('火'), 'H', '火→H')
eq(pinyinInitial('强'), 'Q', '强→Q')
eq(pinyinInitial('A'), '', '非汉字返回空串')

// ★ 全字母覆盖（每字母 2 字；每个字母组都验一遍 ⇒ 换锚字/换环境都会立刻暴露 ✗）
//   注：`龙`/`日` 正是"经典锚字表"会算错的两个（见 formulaList.ts 的注释 ✓）。
const PROBE: Record<string, string> = {
  A: '阿爱', B: '八冰', C: '擦才', D: '大东', E: '额恶', F: '发复', G: '股高', H: '黄火',
  J: '金加', K: '快空', L: '龙量', M: '妈民', N: '拿能', O: '哦噢', P: '偏平', Q: '强七',
  R: '然日', S: '深三', T: '天腾', W: '王挖', X: '选新', Y: '阳油', Z: '值主',
}
const probeTotal = Object.values(PROBE).join('').length
let probeBad: string[] = []
for (const [letter, chars] of Object.entries(PROBE)) {
  for (const ch of chars) {
    if (pinyinInitial(ch) !== letter) probeBad.push(`${ch}→${pinyinInitial(ch) || '空'}(应${letter})`)
  }
}
eq(probeBad, [], `拼音首字母 ${probeTotal} 字探针（覆盖 23 个字母）零错`)
ok(Object.keys(PROBE).length === 23, '探针覆盖了 23 个拼音首字母（无 I/U/V ✓）')

// ── ② 排序键 / 缩写键（★ 中英文混排的关键：同一串小写字母 ✓）──────────────────
eq(formulaKey('ALPHA143_S4'), 'alpha143s4', '英文名保留下划线以外的字母数字')
eq(formulaKey('深跌3'), 'sd3', '深跌3→sd3')
eq(formulaKey('龙腾四海8'), 'ltsh8', '龙腾四海8→ltsh8')
eq(formulaKey('黄金回踩'), 'hjhc', '黄金回踩→hjhc')
ok(REAL_NAMES.every((n) => formulaKey(n).length > 0), '68 条真实公式名都能算出非空键')

// ── ③ 用户要的定位：`l` / `ltsh` / `龙腾四海` ────────────────────────────────
const with10 = [...items, item('龙腾四海10'), item('龙腾四海2')]
eq(names(arrangeFormulas(with10, 'ltsh', 'key')),
  ['龙腾四海2', '龙腾四海8', '龙腾四海10'], 'ltsh ⇒ 三条龙腾四海（且 2<8<10 数字序 ✓）')
eq(names(arrangeFormulas(items, 'ltsh', 'key')), ['龙腾四海8'], 'ltsh 在真实库里只命中龙腾四海8 ✓')
eq(names(arrangeFormulas(items, '龙腾四海', 'key')), ['龙腾四海8'], '中文查询「龙腾四海」✓')

const lHits = arrangeFormulas(items, 'l', 'key')
ok(lHits.length > 0, '输入 l 有命中')
const lPrefix = lHits.filter((_, i) => matchRank(lHits[i], 'l') === 0).length
ok(lPrefix >= 4, '输入 l 至少 4 条名字/缩写前缀命中（LLT/量王/量能系数/龙腾四海8 ✓）')
ok(names(lHits).includes('LLT') && names(lHits).includes('龙腾四海8'),
  '输入 l：英文名 LLT 与中文名 龙腾四海8 **都**能被定位到 ✓')
ok(lHits.findIndex((x) => x.name === '龙腾四海8') < lPrefix,
  '龙腾四海8 落在前缀命中组内（= 排在最上面 ✓）')
// 前缀命中（0）必须整组排在"仅原文命中"（2）之前 ✓ ⇒ 这就是"定位"的保证 ✓
const lRanks = lHits.map((x) => matchRank(x, 'l'))
ok(lRanks.every((r, i) => i === 0 || lRanks[i - 1] <= r), '输入 l：命中优先级单调不减 ✓')

// ── ④ 排序：A-Z（中英文混排）与"恢复原序" ────────────────────────────────────
const sorted = arrangeFormulas(items, '', 'key')
const keys = sorted.map((x) => formulaKey(x.name))
ok(keys.every((k, i) => i === 0 || keys[i - 1].localeCompare(k, 'en', { numeric: true }) <= 0),
  '按名排序后键序单调不减（真的 A→Z ✓）')
// 最前面应是 ALPHA143 家族（a 开头 ✓）。⚠ 只断言"是这 4 条、且都以 alpha143 开头"，
// 不钉家族内部先后 —— 那是 ICU 对"数字 vs 字母"的排序细节，跨 Node/Chrome 版本可能不同 ✓。
eq([...keys.slice(0, 4)].sort(), ['alpha143', 'alpha14360', 'alpha143s4', 'alpha143yb'],
  '最前面 4 条就是 ALPHA143 家族（a 开头 ✓）')
ok(keys.slice(0, 4).every((k) => k.startsWith('alpha143')), 'ALPHA143 家族聚在列表最前 ✓')
// 混排证据：冰谷火焰(b…) 排在 CCC(c…) 之前、又排在 AR13_ID60(a…) 之后 ⇒ 中英交错 ✓
const idx = (n: string) => names(sorted).indexOf(n)
ok(idx('AR13_ID60') < idx('冰谷火焰') && idx('冰谷火焰') < idx('CCC') && idx('CCC') < idx('黄金回踩'),
  '中英文混排：AR13_ID60(a) → 冰谷火焰(b) → CCC(c) → 黄金回踩(h) ✓')
// 数字序：深跌2 < 深跌3 < 深跌10 < 深跌20 < 深跌30 < 深跌60 ✓
ok(idx('深跌2') < idx('深跌3') && idx('深跌3') < idx('深跌10') && idx('深跌10') < idx('深跌20')
  && idx('深跌20') < idx('深跌30') && idx('深跌30') < idx('深跌60'), '数字按数值排序（深跌2<3<10<20<30<60 ✓）')
eq(names(arrangeFormulas(items, '', 'original')), REAL_NAMES, '原序模式 = 保存顺序原样（点"恢复"就是它 ✓）')
eq(names(arrangeFormulas(sorted, '', 'original')).length, REAL_NAMES.length, '恢复原序不丢条目 ✓')

// ── ⑤ 搜索优先级（老行为不丢：还能搜原文 ✓）────────────────────────────────
eq(matchRank(item('龙腾四海8'), 'l'), 0, '名字前缀命中 = 0')
eq(matchRank(item('CCC', 'MA(CLOSE,5)'), 'l'), 2, '只出现在原文里 = 2（仍能搜到 ✓ 但排在后面 ✓）')
eq(matchRank(item('CCC', 'MA(CLOSE,5)'), 'zzz'), 3, '不命中 = 3')
eq(names(arrangeFormulas([item('CCC', 'MA(CLOSE,5)')], 'close', 'key')), ['CCC'], '仍支持按原文搜索 ✓')

console.log(`✓ formulaList 自检全部通过（${passed} 条断言，样例 = 线上 68 条真实公式名）`)
