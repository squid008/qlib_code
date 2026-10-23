/// <reference types="vite/client" />
/**
 * **前端只许「写」xlsx，不许「解析」xlsx**（★ v2.11.49，技术债 P2-#22 的防线）。
 *
 * ## 为什么要这条守卫
 * `package.json` 里的 `xlsx`（SheetJS）在 npm 上**永远停在 0.18.5（2022）** ✗ ——
 * 官方把后续版本发到自家 CDN 了，所以 `npm audit` 对它有 **2 条 high 且「No fix available」**：
 *   · GHSA-4r6h-8v6p-xvw6 —— **原型链污染**（解析恶意表格时）
 *   · GHSA-5pgg-2g8v-p4x9 —— **ReDoS**（解析恶意表格时）
 *
 * **现在不要紧** ✓：两个漏洞都只在**解析不可信输入**时触发，而本项目前端**只做生成**
 * （`book_new` / `aoa_to_sheet` / `write` ✓），用户上传的 Excel 是**后端**用
 * `pandas.read_excel` 解析的（Python 侧，另受 `pip-audit` 管 ✓）。
 *
 * **但它随时可能变要紧** ✗：只要以后有人为了做「前端预览 / 导入 Excel」写一句
 * `XLSX.read(file)`，这两条就变成真的了 —— 而这种改动**看起来完全无害** ✓，
 * code review 也未必拦得住 ✓。
 * ⇒ 钉成**会红的测试** ✓：一旦出现解析调用，CI 立刻报错并指向这段说明 ✓。
 *
 * ## 实现说明
 * 用 **Vite 的 `import.meta.glob`** 读源码 ✓ 而不是 `node:fs` ✗ ——
 * 前端 tsconfig **没有 `@types/node`** ✓（为一条测试加依赖不值得 ✗），
 * 用 glob 既不用加依赖、也不用手写目录遍历 ✓。
 *
 * ## 例外
 * 若将来**确实**评估过要在前端解析（例如只解析自家生成的文件 ✓ 不接外部输入 ✓），
 * 在那一行加 `// xlsx-parse-ok: <理由>` 即可放行 ✓ ——
 * 这样能 `git grep` 出所有例外，而不是整条测试被删掉 ✗。
 */
import { expect, it } from 'vitest'

/** 允许放行的标记（必须带理由 ✓）。 */
const ALLOW_TAG = 'xlsx-parse-ok'

/**
 * 解析类 API（**只匹配解析，不匹配写**）。
 *
 * ⚠ 刻意**不**匹配 `write` / `aoa_to_sheet` / `book_new` / `book_append_sheet` ✓ ——
 * 那些是安全的生成路径 ✓，误报会让守卫被当成噪音删掉 ✗。
 */
const PARSE_PATTERNS: Array<{ re: RegExp; what: string }> = [
  { re: /\bXLSX\.readFile\s*\(/, what: 'XLSX.readFile（解析文件）' },
  { re: /\bXLSX\.read\s*\(/, what: 'XLSX.read（解析工作簿）' },
  { re: /\bxlsx\.read\s*\(/, what: 'xlsx.read（解析工作簿）' },
  { re: /\.readFile\s*\(/, what: 'readFile（解析文件）' },
  { re: /\bsheet_to_json\s*\(/, what: 'sheet_to_json（把表转 JSON）' },
]

/** 在一段源码里找解析调用 → `['L12 XLSX.read（解析工作簿）：…']`（带行号便于定位 ✓）。 */
export function findXlsxParsing(source: string): string[] {
  const hits: string[] = []
  source.split('\n').forEach((line, i) => {
    if (line.includes(ALLOW_TAG)) return
    for (const { re, what } of PARSE_PATTERNS) {
      if (re.test(line)) {
        hits.push(`L${i + 1} ${what}：${line.trim().slice(0, 80)}`)
        break
      }
    }
  })
  return hits
}

/** `src/` 下的全部源码（**排除 `__tests__`** ✓ —— 否则会扫到本文件里的正样本 ✗）。 */
const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

it('⭐⭐ 前端源码里**不得出现** xlsx 解析调用（只许写）', () => {
  const offenders: string[] = []
  for (const [path, source] of Object.entries(SOURCES)) {
    if (path.includes('__tests__')) continue
    for (const h of findXlsxParsing(source)) offenders.push(`${path}  ${h}`)
  }
  expect(
    offenders,
    '发现 xlsx **解析**调用 ✗ —— 这会让 GHSA-4r6h-8v6p-xvw6（原型链污染）与 ' +
      'GHSA-5pgg-2g8v-p4x9（ReDoS）变成真的 ✗\n' +
      '  ⇒ 要么把解析放后端（`parsers/base.py` 已用 pandas ✓），' +
      `要么在该行加 \`// ${ALLOW_TAG}: <理由>\` ✓\n` +
      offenders.join('\n'),
  ).toEqual([])
})

it('⭐ 守卫自身有效（否则正则写错就是**静默失效** ✗）', () => {
  // 正样本：必须被抓到
  expect(findXlsxParsing('const wb = XLSX.read(buf, { type: "array" })')).toHaveLength(1)
  expect(findXlsxParsing('  const rows = XLSX.utils.sheet_to_json(ws)')).toHaveLength(1)
  expect(findXlsxParsing('await XLSX.readFile("a.xlsx")')).toHaveLength(1)

  // 负样本：生成路径**不能**误报（误报会让守卫被当噪音删掉 ✗）
  expect(findXlsxParsing('const wb = XLSX.utils.book_new()')).toEqual([])
  expect(
    findXlsxParsing('XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(aoa), "s1")'),
  ).toEqual([])
  expect(findXlsxParsing('XLSX.write(wb, { type: "array" })')).toEqual([])

  // 放行标记：带理由时跳过 ✓（便于 git grep 出所有例外）
  expect(
    findXlsxParsing('const wb = XLSX.read(buf) // xlsx-parse-ok: 只读自家生成的文件'),
  ).toEqual([])
})

it('⭐ 确实扫到了源码（防止 glob 写错 ⇒ 空集恒绿 ✗）', () => {
  const n = Object.keys(SOURCES).filter((p) => !p.includes('__tests__')).length
  // 实测 47 个（2026-10-23 ✓）；阈值只做"别是空集"的下限保护 ✓，不锁具体数字 ✗
  // （锁具体数字会在每次加文件时误报 ✓，而这条要防的是"glob 写错 ⇒ 空集恒绿" ✗）
  expect(n, '扫到的源码文件太少 ⇒ glob 模式写错了 ✗').toBeGreaterThan(30)
})
