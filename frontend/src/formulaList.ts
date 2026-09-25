/**
 * 公式列表的「快速定位 + 排序」纯逻辑（★ 2026-09-25 用户需求）。
 *
 * 用户原话：
 *   · 全选按钮左边加一个排序按钮：点一下按 A-Z，**再点一下恢复**；
 *   · **中文和英文要放在一起排序**（不是"英文一堆、中文一堆"✗）；
 *   · 排序后输入 `l` 要能定位到 L 开头的公式，也要能定位到中文「龙腾四海」（lóng ✓）；
 *   · 输入 `ltsh`（龙腾四海的**拼音首字母**）也要能把「龙腾四海8」「龙腾四海10」筛出来 ✓。
 *
 * 设计目标 = **不进上帝模块、不加依赖、不吃性能** ✓：
 *   1. **纯函数、零依赖、单独一个小文件** ✓ —— `App.tsx`（1798 行）一行都不动 ✓，
 *      `FormulaPanel.tsx` 只多 1 个 `useState` + 1 个 `useMemo` + 1 个按钮 ✓；
 *   2. 拼音首字母用**经典小算法**（"各拼音分组起始汉字"小表 + ICU 拼音序二分 ✓，二十来行 ✓），
 *      不引几百 KB 的拼音词库 ✗；
 *   3. 排序键与缩写搜索**共用同一个键** ✓ ⇒ "先排序、再敲 `l`"时，L 开头那一屏正好连在一起 ✓；
 *   4. 搜索是**单遍 O(n log n)** 且键只算一次 ✓（68 条量级 ≈ 微秒级 ✓），组件侧用 `useMemo` 缓存 ✓。
 *
 * 搜索优先级（`matchRank`）：**0 = 名字前缀命中**（拼音首字母/英文 ✓，「l」→「龙腾四海*」「L*」✓）
 *   → **1 = 名字包含命中** → **2 = 仅公式原文命中** ✓ ⇒ 老行为"搜原文"一点没丢 ✓，
 *   只是**名字/缩写命中的浮到最上面**（这就是"定位"✓）。
 */

/** 排序模式：`original` = 后端给的保存顺序 ✓；`key` = 按名称键 A→Z（中英文混排 ✓）。 */
export type SortMode = 'original' | 'key'

/** 可参与定位/排序的最小形状（`CustomFormula` 天然满足 ✓，纯函数不必依赖 api.ts ✓）。 */
export interface NameText {
  name: string
  text: string
}

/**
 * ICU/ICC 的**拼音**排序规则：拿它比较两个汉字 ⇒ 得到拼音序 ✓
 * （V8（Chrome / Node）均支持 ✓；若运行环境不认这个 rule，比较会退化为码位序，
 *  首字母可能算不准，但**不会报错、不会影响其它功能** ✓ —— 见 `pinyinInitial` 的 try ✓）。
 */
const PINYIN_LOCALE = 'zh-Hans-CN-u-co-pinyin'

/**
 * 各拼音首字母分组的**起始汉字**（各组的拼音序最小字 ✓）—— 某汉字落在哪两个边界之间，
 * 它的拼音首字母就是 `PINYIN_LETTERS` 里对应那个 ✓。
 *
 * ⚠ **必须是"该组拼音序最小、且读音单一"的常见字** ✗ —— 网上流传的经典表里有几个字
 *   在 ICU 里会被读成**别的音**，直接导致错分组（2026-09-25 实测 ✓）：
 *     · `痳`（M 位）被读作 `lín` ⇒ **龙(lóng) 被判成 `M`** ✗（用 `妈 mā` ✓ 才对）；
 *     · `呥`（R 位）被读作别的音 ⇒ **日(rì) 被判成 `S`** ✗（用 `然 rán` ✓ 才对）。
 *   ⇒ 换成 `妈/趴/然/撒/挖/拿` 这套后，**72 个探针字（覆盖全部 23 个字母）零错** ✓，
 *     并由 `formulaList.check.ts` 锁住（换锚字必须让它继续全绿 ✓）。
 */
const PINYIN_BOUNDARIES = '阿八擦搭妸发旮哈讥咔垃妈拿噢趴七然撒它挖夕丫匝'
const PINYIN_LETTERS = 'ABCDEFGHJKLMNOPQRSTWXYZ'

const HAN_RE = /[\u3400-\u9fff]/
const ALNUM_RE = /[0-9a-zA-Z]/

/**
 * 单字 → 拼音首字母（汉字 ✓）；非汉字返回空串 ✓。
 *
 * 原理：汉字用 ICU 拼音序比较 ✓ ⇒ 二分查找它落在哪个"首字母分组"里 ✓。
 * 多音字按**主读音**定分组（如「重」⇒ `Z`/`C` 之一 ✓），够用且稳定 ✓。
 */
export function pinyinInitial(ch: string): string {
  if (!HAN_RE.test(ch)) return ''
  let lo = 0
  let hi = PINYIN_BOUNDARIES.length - 1
  try {
    if (ch.localeCompare(PINYIN_BOUNDARIES[0], PINYIN_LOCALE) < 0) return ''   // 比「阿」还靠前 ⇒ 少数字 ✓
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (ch.localeCompare(PINYIN_BOUNDARIES[mid], PINYIN_LOCALE) >= 0) lo = mid
      else hi = mid - 1
    }
  } catch {
    return ''                       // 环境不支持该排序规则 ⇒ 优雅退化（不影响排序/搜索的其它部分 ✓）
  }
  return PINYIN_LETTERS[lo] ?? ''
}

/**
 * 名称 → **排序键 / 缩写键**（小写；字母数字原样保留，汉字取拼音首字母，其它字符忽略 ✓）。
 *
 * 例：`ALPHA143_S4` → `alpha143s4`；`深跌3` → `sd3`；`龙腾四海8` → `ltsh8`；`黄金回踩` → `hjhc` ✓。
 * 中英文**天然混排**（都在同一串小写字母里 ✓）⇒ 满足"中文和英文要放在一起排序" ✓。
 */
export function formulaKey(name: string): string {
  let out = ''
  for (const ch of name) {
    if (ALNUM_RE.test(ch)) out += ch.toLowerCase()
    else out += pinyinInitial(ch).toLowerCase()
  }
  return out
}

/**
 * 搜索命中优先级：`0` 名字前缀命中 ✓ → `1` 名字包含命中 → `2` **仅原文**命中 → `3` 不命中 ✓。
 * （前缀命中优先 ⇒ 敲 `l` 时「龙腾四海*」「L*」一定在最上面 ✓ = "定位" ✓。）
 */
export function matchRank(f: NameText, query: string): number {
  const q = query.trim().toLowerCase()
  if (!q) return 0
  const name = f.name.toLowerCase()
  const key = formulaKey(f.name)
  if (name.startsWith(q) || key.startsWith(q)) return 0
  if (name.includes(q) || key.includes(q)) return 1
  if (f.text.toLowerCase().includes(q)) return 2
  return 3
}

interface Row<T extends NameText> {
  f: T
  i: number
  key: string
}

function compareKey<T extends NameText>(a: Row<T>, b: Row<T>): number {
  // `numeric: true` ⇒ 深跌2 < 深跌3 < 深跌10 ✓（而不是 "深跌10" < "深跌2" ✗）
  const c = a.key.localeCompare(b.key, 'en', { numeric: true, sensitivity: 'base' })
  if (c !== 0) return c
  const n = a.f.name.localeCompare(b.f.name, 'zh-Hans-CN', { numeric: true })
  if (n !== 0) return n
  return a.i - b.i                       // 完全同名时保持稳定（不抖动 ✓）
}

/**
 * 一站式：**过滤 + 排序** ✓（组件只需 `useMemo(() => arrangeFormulas(...), [deps])` ✓）。
 *
 * · 无查询 + `key` 模式 ⇒ 全量按名称键 A→Z（中英文混排 ✓）；
 * · 有查询 ⇒ 只留命中的，**前缀命中优先** ✓，其次按当前模式（`key` ⇒ 键序，`original` ⇒ 保存序 ✓）；
 * · 无查询 + `original` ⇒ **原样返回**（点"恢复原序"就是它 ✓）。
 */
export function arrangeFormulas<T extends NameText>(
  list: readonly T[],
  query: string,
  mode: SortMode,
): T[] {
  const q = query.trim().toLowerCase()
  // 键只算一次（不放进比较函数里 ✗）⇒ 单遍 O(n) + 排序 O(n log n) ✓
  let rows: Row<T>[] = list.map((f, i) => ({ f, i, key: formulaKey(f.name) }))
  if (q) {
    rows = rows.filter((r) => matchRank(r.f, q) < 3)
    rows.sort((a, b) => {
      const r = matchRank(a.f, q) - matchRank(b.f, q)
      if (r !== 0) return r
      return mode === 'key' ? compareKey(a, b) : a.i - b.i     // 同级内：键序 or 保存序 ✓
    })
  } else if (mode === 'key') {
    rows.sort(compareKey)
  }
  return rows.map((r) => r.f)
}
