import { expect, test } from '@playwright/test'

/**
 * **真后端 E2E**（★ v2.11.54）—— **一条 `page.route` 都没有** ✓，全打真实 `/api/*`。
 *
 * ## 它补的是什么（现有 7 条 E2E 补不上的）
 * 现有那 7 条把 `/api/**` **全 stub 掉** ✓ ⇒ 测的是"前端在 API 正常时能渲染"✓，
 * **前后端联调是否通了** 从来没被自动化验过 ✗（外部评审点得最准的一条之一 ✓）。
 *
 * ## 它断言什么（都是"只有真后端才成立"的事）
 * 1. `/api/version` 返回的版本 == 前端顶栏显示的版本 ✓（**联调链路通了**）；
 * 2. `/api/metrics?format=text` 里有 `qlib_uptime_seconds` ✓ —— 这是**本项目后端的指纹**✓，
 *    换成任何别的服务都不会有这个指标 ✓（防"静默测错对象"✗）；
 * 3. `/api/datasets` 返回里有 `active` 字段 ✓（真实的**当前数据集**，不是编的 ✓）；
 * 4. 真后端下打开首页：**零未捕获异常** ✓ + 不出现"加载失败"字样 ✓。
 */

test('真后端身份 + 版本联调：/api/version 与顶栏一致', async ({ page, request }) => {
  const r = await request.get('/api/version')
  expect(r.ok(), '真后端没起来（或 /api/version 挂了）').toBeTruthy()
  const body = (await r.json()) as { version?: string }
  expect(body.version, '后端没返回 version').toMatch(/^\d+\.\d+\.\d+$/)

  await page.goto('/')
  // 顶栏版本号格式是 `vX.Y.Z` ✓（与 FE_VERSION 同源）
  await expect(page.getByText(`v${body.version}`, { exact: false }).first()).toBeVisible()
})

test('⭐ 后端指纹：真后端才有 /api/metrics 的 qlib_uptime_seconds', async ({ request }) => {
  const r = await request.get('/api/metrics?format=text')
  expect(r.ok()).toBeTruthy()
  const text = await r.text()
  expect(text, '没有 qlib_uptime_seconds ⇒ 这个服务不是本项目后端 ✗').toContain(
    'qlib_uptime_seconds',
  )
  expect(text).toContain('qlib_http_requests_total')
})

test('真实数据集：/api/datasets 有 active 字段', async ({ request }) => {
  const r = await request.get('/api/datasets')
  expect(r.ok()).toBeTruthy()
  const body = (await r.json()) as { active?: string }
  expect(typeof body.active, 'active 缺失 ⇒ 响应不是真后端的').toBe('string')
})

test('真后端渲染首页：零未捕获异常、不出现"加载失败"', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  await page.goto('/')
  await expect(page.locator('body')).toContainText(/因子|回测|数据集/, { timeout: 20_000 })
  // 真后端下若接口 500，页面上会出现"失败/错误"字样 ⇒ 这里当断言 ✓
  await expect(page.locator('body')).not.toContainText('加载失败')
  expect(errors, '真浏览器里出现未捕获异常 ✗：' + errors.join(' | ')).toHaveLength(0)
})
