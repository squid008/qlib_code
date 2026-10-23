/// <reference types="vite/client" />
/**
 * **前端不许再碰 Excel 库**（★ v2.11.68 升级 —— 原来是「只许写、不许解析」✓）。
 *
 * ## v2.11.68：依赖已**彻底移除**（技术债 P2-#22 已清偿 ✓）
 * 正解不是"换库"，而是"**不用库**" ✓：
 * · 前端唯一那处 `await import('xlsx')`（单因子结果导出）已改成
 *   `POST /api/factors/export/aoa-xlsx` ⇒ **后端 openpyxl 生成** ✓（本项目既有约定：三条导出接口早就是这样）；
 * · `npm audit --omit=dev` 现在 **0 vulnerabilities** ✓ ⇒ CI 的 npm audit 从
 *   "只卡 critical" **收紧到 high** ✓（闸门变严才是真收益 ✓）；
 * · 换 `exceljs` 试过但**更差** ✗（185 个包、审计变 3 漏洞含 1 high ✓）。
 * ⇒ 本文件现在守两件事：① **不许引入任何 Excel 库**（本文件末尾那条）
 *   ② 若将来真引回来，**不许解析**（下面这条原样保留 ✓）。
 *
 * ## 为什么要这条守卫（原委，保留）
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


/** 任何 Excel 库的**引入**形态（静态 / 动态 / CJS 全算）—— ★ v2.11.68 新增守卫 ✓。 */
const EXCEL_LIB_IMPORT =
  /(?:from\s+['"](?:xlsx|exceljs|excel4node)['"]|import\(\s*['"](?:xlsx|exceljs|excel4node)['"]\s*\)|require\(\s*['"](?:xlsx|exceljs|excel4node)['"]\s*\))/i

it('⭐⭐ v2.11.68 起：前端**不得再引入任何 Excel 库**（导出已交后端）', () => {
  const offenders: string[] = []
  for (const [path, source] of Object.entries(SOURCES)) {
    if (path.includes('__tests__')) continue
    source.split('\n').forEach((ln, i) => {
      // ⚠⚠ **必须先剥注释** ✗→✓ —— 我第一版直接测原文 ⇒ 把"说明里举的例子"当成引入
      //   ⇒ 报 `SingleFactorTestPanel.tsx:L632 // 原来这里是 await import('xlsx')` ✗
      //   （那是一条**解释为什么删掉**的注释 ✓）。守卫误报会被当噪音删掉 ✗ —— 本文件
      //   docstring 里自己写过这条风险 ✓，结果我还是踩了 ✓。
      const t = ln.trim()
      // 三种注释形态都直接跳过（`//` 行注释 / `*` 与 `/*` 块注释体）✓
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) return
      const code = ln.replace(/\/\/.*$/, '').replace(/\/\*.*?\*\//g, '')
      if (EXCEL_LIB_IMPORT.test(code)) offenders.push(`${path}:L${i + 1} ${t.slice(0, 70)}`)
    })
  }
  expect(
    offenders,
    '前端又在引入 Excel 库 ✗ —— 导出请走后端 `POST /api/factors/export/aoa-xlsx` ✓\n' +
      '（npm 的 SheetJS 已停维护 ⇒ 2 条 high 无 fix；exceljs 更差 ⇒ 185 个包）\n' +
      offenders.join('\n'),
  ).toEqual([])
})

it('⭐ 新守卫自身有效（正/负样本各测一遍）', () => {
  // 正样本：**正是 v2.11.68 删掉的那一行** ✓
  expect(EXCEL_LIB_IMPORT.test("const mod: any = await import('xlsx')")).toBe(true)
  expect(EXCEL_LIB_IMPORT.test('import * as XLSX from "xlsx"')).toBe(true)
  expect(EXCEL_LIB_IMPORT.test("const X = require('exceljs')")).toBe(true)
  // 负样本：走新接口**不能**误报（否则守卫会被当噪音删掉 ✗）
  expect(EXCEL_LIB_IMPORT.test('await exportAoaXlsx({ filename, sheets })')).toBe(false)
  expect(EXCEL_LIB_IMPORT.test("const p = '/api/factors/export/aoa-xlsx'")).toBe(false)
})
