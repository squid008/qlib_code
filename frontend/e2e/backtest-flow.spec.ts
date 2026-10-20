import { expect, test } from '@playwright/test'

import { FE_VERSION } from '../src/version'

/**
 * 全链路 E2E（**API 契约级**，★ v2.10.7，2026-10-20 ✓）。
 *
 * ## 它覆盖的是"前端自己的编排"
 *
 * 真实链路 = 填表 → `POST /api/backtest` → 轮询 `GET /api/backtest/{id}` → 完成后
 * `GET .../result` → 渲染指标卡 ✓。这条**编排逻辑**（谁先谁后、什么时候切到结果区、
 * 失败怎么收尾）只有整条跑一遍才看得见 ✓，单测只能覆盖其中一段 ✓。
 *
 * ## 为什么 stub 而不是起真后端
 *
 * 真后端要 qlib + 行情数据（CI 没有 ✗），而且一次回测是**分钟级** ✓ ⇒ 放进 CI 会让
 * 每个 PR 多等 10 分钟以上 ✗。这里用"**契约级 stub**"：请求/响应形状照真实接口写 ✓，
 * 于是能验证"**前端在正确的响应下会正确渲染**"✓。
 * ⚠ **如实标注**：它**不能**证明后端算得对 ✗ —— 那是 `test_engine_golden`（NAV 指纹 ✓）
 * 与后端 1,500+ 条用例的事 ✓，两者分工不同 ✓。
 */

const TASK_ID = 'T-E2E'

const RESULT = {
  task_id: TASK_ID,
  annualized_return: 0.2345,
  annualized_excess_return: 0.1122,
  sharpe: 1.87,
  max_drawdown: -0.1234,
  win_rate: 0.5678,
  benchmark_return: 0.101,
  total_return: 0.4567,
  // ⚠ 曲线给**最短的合法形状** ✓（两条点）；指标卡不依赖它 ✓，但净值图会读 ✓
  nav: [
    { date: '2024-01-02', value: 1.0, benchmark: 1.0 },
    { date: '2024-01-03', value: 1.01, benchmark: 1.002 },
  ],
  trades: [],
}

// ⚠ 标题**只写它真的断言了什么** ✓（原来叫"…结果区出现指标卡" ✗ —— 那是计划，不是断言 ✓）
test('「开始回测」⇒ 创建请求（载荷契约）→ 轮询 success 上屏 → 历史「查看」渲染指标卡', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))

  // ---- 基础 stubs（与 smoke 同源 ✓）----
  await page.route('**/api/version*', (r) =>
    r.fulfill({ json: { version: FE_VERSION, fe_version: FE_VERSION } }))
  await page.route('**/api/datasets*', (r) =>
    r.fulfill({ json: { active: 'cn_data', datasets: [] } }))
  await page.route('**/api/backtests*', (r) => r.fulfill({ json: {} }))
  // ⭐ 历史列表：**结果面板的唯一入口**就在这里的「查看」按钮上 ✓
  //   （`HistoryItem` 形状照 `api.ts` 的 interface 写 ✓ —— 少一个 `has_*` 按钮就 disabled ✗）
  await page.route('**/api/backtests/history*', (r) =>
    r.fulfill({
      json: {
        items: [{
          task_id: TASK_ID,
          dir_name: `20240101_${TASK_ID}`,
          has_params: true,
          has_result: true,
          has_meta: false,
          has_artifacts: true,
          images: {},
          segments: [],
          meta_summary: { model: 'LightGBM', universe: 'csi300', start_year: '2024', end_year: '2024' },
        }],
        retention: null,
      },
    }))
  await page.route('**/api/factors*', (r) => r.fulfill({ json: { items: [] } }))
  await page.route('**/api/health*', (r) => r.fulfill({ json: { status: 'ok' } }))
  await page.route('**/api/tasks*', (r) => r.fulfill({ json: {} }))
  await page.route('**/api/models*', (r) => r.fulfill({ json: { items: [] } }))

  // ---- 回测链路（**顺序：具体 → 泛化** ✓）----
  await page.route('**/api/backtest/capacity*', (r) =>
    r.fulfill({ json: { available: 3, max: 3 } }))
  await page.route(`**/api/backtest/${TASK_ID}/result*`, (r) => r.fulfill({ json: RESULT }))
  await page.route(`**/api/backtest/${TASK_ID}/partial*`, (r) => r.fulfill({ json: {} }))
  await page.route(`**/api/backtest/${TASK_ID}/artifacts*`, (r) => r.fulfill({ json: {} }))
  await page.route(`**/api/backtest/${TASK_ID}/features*`, (r) => r.fulfill({ json: {} }))
  await page.route(`**/api/backtest/${TASK_ID}/snapshot*`, (r) => r.fulfill({ json: {} }))
  // 轮询：**第一次就给 success** ✓（省时间 ✓；这里不测"进度爬升"✗ —— 那是 taskStatusPanel 单测的事 ✓）
  await page.route(`**/api/backtest/${TASK_ID}`, (r) =>
    r.fulfill({ json: { task_id: TASK_ID, status: 'success', progress: 100, message: '' } }))
  await page.route('**/api/backtest', (r) =>
    r.fulfill({ json: { task_id: TASK_ID } }))

  // ⚠ 先清 `localStorage` ✗→✓：本应用会把任务列表**持久化在浏览器里** ✓
  //   ⇒ 上一次跑（甚至开发时的真实任务 ✓）会残留 ✓ ⇒ 页面会去拉**别人的** snapshot ✓
  //   （实测探针里看到一串 `GET /api/backtest/<别人的id>/snapshot` ✓ —— 就是这样来的 ✓）。
  await page.addInitScript(() => {
    try { localStorage.clear() } catch { /* 忽略 ✓ */ }
  })

  await page.goto('/')

  // 等首屏就绪 ✓ 再点，避免点在被 React 换掉的旧节点上 ✗
  const start = page.getByRole('button', { name: '开始回测' })
  await expect(start).toBeEnabled({ timeout: 20_000 })

  // ⭐ 核心断言：**点下去必须真的发出创建请求** ✓ —— 这一条覆盖了
  //   "表单校验通过 → 组装 payload → POST /api/backtest" 整段编排 ✓
  //   （按钮 disabled / `onClick` 接错 / payload 组装抛异常，这里都会红 ✓）
  const created = page.waitForRequest(
    (r) => r.method() === 'POST' && r.url().includes('/api/backtest'),
    { timeout: 20_000 },
  )
  await start.click()
  const req = await created
  const body = req.postDataJSON() as Record<string, unknown>
  expect(body, '创建请求必须带 body ✓').toBeTruthy()

  // ⭐ 载荷契约 ✓：表单 → payload 的字段名/类型不能漂 ✗
  //   （后端按这些键解析 ✓；改名不会报错，只会"用默认值跑一遍"✗，结果为 0 但界面正常 ✓）
  for (const key of ['universe', 'start_date', 'end_date', 'model', 'feature', 'topk']) {
    expect(body, `payload 缺字段 ${key} ✗`).toHaveProperty(key)
  }
  expect(String(body['start_date'])).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  expect(String(body['end_date'])).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  expect(Number(body['topk'])).toBeGreaterThan(0)

  // ⭐ 轮询闭环 ✓：任务必须出现在「任务状态」里 **并且显示 success** ✓ ——
  //   这条把"提交 → 轮询 `GET /api/backtest/{id}` → 反映到界面"整段都覆盖了 ✓
  //   （探针实测：stub 返回 success 后，卡片上会显示 `T-P success 100.0%` ✓）。
  // ⚠ `.first()` **必须加** ✗→✓：`T-E2E` 会同时出现在**任务卡**与**历史行**里 ✓
  //   ⇒ 不加就撞 Playwright 的 strict mode ✓（实测报 "resolved to 2 elements" ✓）
  await expect(page.getByText(TASK_ID).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('100.0%')).toBeVisible({ timeout: 20_000 })

  // ⭐⭐ 结果面板：**唯一入口是历史回测列表的「查看」按钮** ✓
  //   （实测 `handleViewResult` 只挂在 `HistoryPanel` 的按钮上 ✓；任务卡片只管"选中"✗ 不拉 `/result` ✓）
  //   ⇒ 先把一条历史记录 stub 出来 ✓，再点那个按钮 ✓ ⇒ 才会请求 `/result` 并渲染指标卡 ✓。
  await page.getByTestId(`history-view-${TASK_ID}`).click()
  // `夏普比率` 是**唯一可信标记** ✓（`年化收益` 会撞历史表头 ✗ —— 第一版就因此假通过 ✓）
  await expect(page.getByText('夏普比率')).toBeVisible({ timeout: 20_000 })
  // ⚠ `最大回撤` 必须 `exact` ✗→✓：它同时是「**超额**最大回撤」的子串 ✓（实测 2 个元素 ✓）
  await expect(page.getByText('最大回撤', { exact: true })).toBeVisible({ timeout: 20_000 })

  // ⚠ **如实标注（仍未做到）** ✗：还想断言"**结果面板**渲染指标卡" ✓。
  //   探针已经把原因追清了 ✓：提交后前端只请求 `GET /backtest/{id}` 与 `.../artifacts` ✓，
  //   **不请求 `/result`** ✗ ⇒ 结果面板是"**选中某个任务**"才加载的 ✓（不是自动渲染 ✓）。
  //   而"点卡片"这一步在 stub 环境里**不可靠** ✗ —— 本应用把任务列表持久化在 `localStorage` ✓，
  //   残留的旧任务会让点击命中别人 ✓（探针里出现一串 `GET /api/backtest/<别人的id>/snapshot` ✓）。
  //   ⇒ 要断言结果面板，得先给"任务卡片"一个**稳定选择器**（data-testid ✓），
  //     并确认 `/result` 的响应形状 ✓ —— 属下一轮 ✓，这里不写会误报的断言 ✗。
  //   （另：`夏普比率` 是唯一可用标记 ✓；`年化收益` 会撞历史表头 ✗ —— 第一版就因此假通过 ✓。）
  expect(errors, '提交回测期间出现未捕获错误 ✗：\n' + errors.join('\n')).toEqual([])
})
