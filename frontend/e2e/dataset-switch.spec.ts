import { expect, test } from '@playwright/test'

import { FE_VERSION } from '../src/version'

/**
 * 数据集（口径）切换（★ v2.10.59，2026-10-21 ✓）。
 *
 * ## 为什么补这两条
 *
 * 项目历史上真实踩过「**看着切了、其实没切**」✗（`md/开发记录.md` 里记着：
 * 环境变量压过落盘、面板与缓存目录没跟着动 ⇒ 用户以为换了口径，结果还是老数据 ✓）。
 * 那条链路的**前端触发器**就是 `DatasetPicker` ⇒ 这里钉住两件事：
 * ① 点选另一个数据集 ⇒ **必须真的发出 `POST /api/datasets/active`，且 body 里是那个 name** ✓
 *    （只改本地 state 不改后端 = 经典假切换 ✗）；
 * ② **只有一个数据集时不许点开** ✓ —— 后端只返一套数据（同事机器常见 ✓）时，
 *    让用户点开一个空菜单纯属白费 ⇗ ✓。
 *
 * ⚠ 契约级 stub（与 `backtest-flow` / `task-lifecycle` 同源 ✓）：证明的是**前端编排** ✓，
 * 不是后端真的切了 ✓ —— 后者由 `test_datasets_*` 与人工「同因子两套数据触发数必须不同」兜底 ✓。
 */

const DS_TWO = {
  active: 'cn_data2',
  datasets: [
    {
      name: 'cn_data2', label: 'tushare 口径', calendar_days: 1200,
      calendar_first: '2019-01-02', calendar_last: '2024-01-31', codes: 5000,
      fields: 42, has_fin: true, has_mf: true, has_chip: true, convention: 'tushare',
    },
    {
      name: 'cn_data', label: '米筐口径', calendar_days: 900,
      calendar_first: '2020-01-02', calendar_last: '2023-12-29', codes: 4200,
      fields: 40, has_fin: true, has_mf: false, has_chip: false, convention: 'qlib',
    },
  ],
}

/** 只返回一套数据集（同事机器 / 只有主数据集的场景 ✓）。 */
const DS_ONE = { active: 'cn_data2', datasets: [DS_TWO.datasets[0]] }

async function stubShell(page: import('@playwright/test').Page, ds: unknown) {
  await page.addInitScript(() => {
    try { localStorage.clear() } catch { /* 忽略 ✓ */ }
  })
  await page.route('**/api/datasets/active*', (r) =>
    r.fulfill({ json: { active: 'cn_data', info: null } }))
  await page.route('**/api/datasets*', (r) => r.fulfill({ json: ds }))
  await page.route('**/api/version*', (r) =>
    r.fulfill({ json: { version: FE_VERSION, fe_version: FE_VERSION } }))
  await page.route('**/api/backtests*', (r) => r.fulfill({ json: { items: [], retention: null } }))
  await page.route('**/api/factors*', (r) => r.fulfill({ json: { items: [] } }))
  await page.route('**/api/health*', (r) => r.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/tasks*', (r) => r.fulfill({ json: {} }))
  await page.route('**/api/models*', (r) => r.fulfill({ json: { items: [] } }))
}

test('切换数据集 ⇒ 真的发出 POST /api/datasets/active，body 是选中的那个 name', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await stubShell(page, DS_TWO)
  await page.goto('/')

  // 收起态显示的是**当前生效**那套的短标签 ✓
  const picker = page.getByTitle('切换数据集（后端全局生效）')
  await expect(picker).toBeVisible({ timeout: 20_000 })
  await expect(picker).toContainText('tushare 口径')

  await picker.click()
  // 展开后两套都在 ✓（名称 + 目录名都要能看到 ✓ —— 用户靠目录名分辨）
  await expect(page.getByText('米筐口径')).toBeVisible()
  await expect(page.getByText('cn_data2')).toBeVisible()

  // ⭐ 核心：点另一套 ⇒ 必须真的发请求，且 body.name 就是它 ✓
  const switched = page.waitForRequest(
    (r) => r.method() === 'POST' && r.url().includes('/api/datasets/active'),
    { timeout: 20_000 },
  )
  await page.getByText('米筐口径').click()
  const req = await switched
  const body = req.postDataJSON() as Record<string, unknown>
  expect(body['name'], 'body 里必须是被选中的数据集名 ✓（发空 body = 后端没切 ✗）')
    .toBe('cn_data')

  expect(errors, '切换期间出现未捕获错误 ✗：\n' + errors.join('\n')).toEqual([])
})

test('只有一个数据集时：按钮不许点开（点开空菜单纯属白费 ✓）', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await stubShell(page, DS_ONE)
  let posted = 0
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/api/datasets/active')) posted += 1
  })

  await page.goto('/')
  const picker = page.getByTitle('只有一个数据集可用')
  await expect(picker).toBeVisible({ timeout: 20_000 })

  await picker.click()
  // 不展开（菜单里那条「当前」标记不该出现 ✓），也不发请求 ✓
  await expect(page.getByText('当前', { exact: true })).toHaveCount(0)
  expect(posted, '只有一个数据集时不许发切换请求 ✗').toBe(0)

  expect(errors, '出现未捕获错误 ✗：\n' + errors.join('\n')).toEqual([])
})
