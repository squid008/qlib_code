import { expect, test } from '@playwright/test'

import { FE_VERSION } from '../src/version'

/**
 * 任务生命周期与历史空态（★ v2.10.51，2026-10-21 ✓）。
 *
 * ## 为什么补这两条
 *
 * `backtest-flow.spec.ts` 走的是**顺利路径**（提交 → success → 看结果 ✓）；
 * 但用户实际最容易踩的是另外两条：
 * ① **提交完想撤销** ⇒ 依赖「一键取消所有」真的发出 `POST /api/backtest/{id}/cancel` ✓；
 * ② **历史为空**（新机器 / 刚清理过）⇒ 页面**不许白屏、不许崩** ✓。
 * 这两条以前零覆盖 ✗（E2E 只有 3 条，全是顺利路径 ✓）。
 *
 * ## 仍然只用"契约级 stub"
 *
 * 不起真后端（要 qlib + 行情 + 分钟级 ✗）。⚠ 如实标注：它证明的是**前端的编排** ✓，
 * 不是后端算得对 ✓。
 */

const TASK_ID = 'T-CANCEL'

/** 公共 stub：与 `backtest-flow.spec.ts` 同源 ✓（少一个都可能导致首屏卡住 ✗）。 */
async function stubShell(page: import('@playwright/test').Page, historyItems: unknown[]) {
  await page.addInitScript(() => {
    try { localStorage.clear() } catch { /* 忽略 ✓ */ }
  })
  await page.route('**/api/version*', (r) =>
    r.fulfill({ json: { version: FE_VERSION, fe_version: FE_VERSION } }))
  await page.route('**/api/datasets*', (r) =>
    r.fulfill({ json: { active: 'cn_data', datasets: [] } }))
  await page.route('**/api/backtests*', (r) => r.fulfill({ json: {} }))
  // ⚠ 历史路由**必须在泛化规则之后注册** ✓（后注册者优先 ✓，实测踩过 ✗）
  await page.route('**/api/backtests/history*', (r) =>
    r.fulfill({ json: { items: historyItems, retention: null } }))
  await page.route('**/api/factors*', (r) => r.fulfill({ json: { items: [] } }))
  await page.route('**/api/health*', (r) => r.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/tasks*', (r) => r.fulfill({ json: {} }))
  await page.route('**/api/models*', (r) => r.fulfill({ json: { items: [] } }))
}

test('任务运行中点「一键取消所有」⇒ 真的发出 cancel 请求', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await stubShell(page, [])
  await page.route('**/api/backtest/capacity*', (r) =>
    r.fulfill({ json: { available: 3, max: 3 } }))
  // 轮询**一直 running** ✓ —— 只有运行中的任务才该被取消 ✓（已结束的按钮不该误报 ✗）
  await page.route(`**/api/backtest/${TASK_ID}`, (r) =>
    r.fulfill({ json: { task_id: TASK_ID, status: 'running', progress: 30, message: '跑着呢' } }))
  await page.route(`**/api/backtest/${TASK_ID}/cancel*`, (r) =>
    r.fulfill({ json: { status: 'ok' } }))
  await page.route(`**/api/backtest/${TASK_ID}/artifacts*`, (r) => r.fulfill({ json: {} }))
  await page.route(`**/api/backtest/${TASK_ID}/snapshot*`, (r) => r.fulfill({ json: {} }))
  await page.route('**/api/backtest', (r) => r.fulfill({ json: { task_id: TASK_ID } }))

  await page.goto('/')
  const start = page.getByRole('button', { name: '开始回测' })
  await expect(start).toBeEnabled({ timeout: 20_000 })
  await start.click()

  // 任务卡上屏 ✓（先确认"确实有活着的任务"，否则取消按钮可能是禁用的 ✓）
  await expect(page.getByText(TASK_ID).first()).toBeVisible({ timeout: 20_000 })

  // ⭐ 核心断言：点「一键取消所有」**必须真的发出 cancel 请求** ✓
  //   （按钮 disabled / 没绑 handler / taskId 传空，这里都会红 ✓）
  const cancelled = page.waitForRequest(
    (r) => r.method() === 'POST' && /\/api\/backtest\/[^/]+\/cancel/.test(r.url()),
    { timeout: 20_000 },
  )
  await page.getByRole('button', { name: '一键取消所有' }).click()
  const req = await cancelled
  expect(req.url(), 'cancel 请求要带**具体任务号** ✓（不能是空 id ✗）').toContain(TASK_ID)

  expect(errors, '取消期间出现未捕获错误 ✗：\n' + errors.join('\n')).toEqual([])
})

test('历史为空 ⇒ 不白屏、不出「查看」按钮、不崩', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await stubShell(page, [])
  await page.route('**/api/backtest/capacity*', (r) =>
    r.fulfill({ json: { available: 3, max: 3 } }))
  await page.route('**/api/backtests/history*', (r) =>
    r.fulfill({ json: { items: [], retention: null } }))

  await page.goto('/')

  // ⭐ 主体还在（不是白屏 ✗）—— 表单按钮是"页面活着"的最低证据 ✓
  await expect(page.getByRole('button', { name: '开始回测' })).toBeEnabled({ timeout: 20_000 })

  // ⭐ 空历史 ⇒ **一个「查看」按钮都不许有** ✓
  //   （渲染出幽灵行 / 拿旧缓存顶上，都会红 ✓）
  await expect(page.getByTestId(/^history-view-/)).toHaveCount(0)

  expect(errors, '空历史下出现未捕获错误 ✗：\n' + errors.join('\n')).toEqual([])
})
