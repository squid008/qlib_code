import { expect, test } from '@playwright/test'

import { FE_VERSION } from '../src/version'

/**
 * 冒烟 E2E（★ v2.10.5，2026-10-20 ✓）—— 第 ③ 件的**骨架** ✓。
 *
 * ## 它到底能抓到什么（jsdom 抓不到的）
 *
 * 1. **真实浏览器里整棵 React 树能不能跑起来** ✓ —— 我们已有的 200+ 条前端测试都跑在
 *    jsdom 里 ✓，它没有布局引擎 ✓、没有 `ResizeObserver` ✗（`EventDistChart` 那次就是
 *    靠打桩才挂得上 ✓）、也不会真跑 `import()` / CSS ✓
 *    ⇒ **打包/构建层面的错只会在真浏览器里露头** ✗（例如某个依赖用了只在浏览器存在的 API ✓）。
 * 2. **未捕获异常为零** ✓ —— 这条最值钱 ✓：白屏往往不报错 ✓，只在 console 里留一条
 *    `Uncaught TypeError` ✗；这里把它变成**断言** ✓。
 * 3. **顶栏版本号 = 前端自己的 `FE_VERSION`** ✓ —— 版本对齐是这套工程里反复出问题的地方 ✓
 *    （四处版本 + `test_version_sync` ✓），真浏览器里再核一次 ✓。
 *
 * ## 为什么先把 `/api/**` 全部 stub
 *
 * 骨架阶段不该依赖后端 ✓（CI 里起 FastAPI 要 qlib 数据 ✗）。
 * ⚠ 但要**如实标注**：这测的是"**前端在 API 正常时能渲染**" ✓，
 * 不是"前后端联调通了" ✗ —— 后者是下一轮的全链路 E2E ✓。
 */

// ⚠ 只 stub **首屏会打**的这几个 ✓，**不要**用 catch-all 兜 `{}` ✗ ——
//   实测：`{}` 缺 `datasets`/`items` 等字段 ⇒ App 渲染时 TypeError ✗ ⇒ 两条用例全红 ✓
//   （"为了防 500 而返回空对象"反而把页面弄崩 ✓，这是很典型的一步走错 ✗）。
//   未列出的 `/api/*` 会落到 vite 代理 ✓ ⇒ CI 里得到 500 ✗ ⇒ 只是一条
//   `Failed to load resource` 的 console 提示 ✓，**不影响渲染** ✓（下面已过滤 ✓）。
const STUBS: Array<[string, unknown]> = [
  ['**/api/version*', { version: FE_VERSION, fe_version: FE_VERSION }],
  ['**/api/datasets*', { active: 'cn_data', datasets: [] }],
  ['**/api/backtests*', { items: [], retention: null }],
  ['**/api/factors*', { items: [] }],
  ['**/api/health*', { status: 'ok' }],
  ['**/api/tasks*', { items: [], task: null }],
  ['**/api/models*', { items: [] }],
]

test.beforeEach(async ({ page }) => {
  // ⚠ 顺序很重要：**先 stub 再 goto** ✓（否则首个请求已经打到真后端 ✗）
  for (const [pattern, json] of STUBS) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) }),
    )
  }
})

test('首屏能渲染、顶栏有版本号、无未捕获异常', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() !== 'error') return
    const text = m.text()
    // ★ 2026-10-20（CI 实测 ✗→✓）：**"加载资源失败"不是代码 bug** ✗
    //   它是浏览器对 4xx/5xx 的**网络提示** ✓，和我们 stub 的响应形状无关 ✓；
    //   把它算进"未捕获异常"会让这条断言变成"代理配置检查"✗，与初衷不符 ✓。
    //   ⇒ 只排除这一类 ✓（其余 `console.error` 仍然算 ✓ —— 真错误不能放过 ✗）。
    if (text.includes('Failed to load resource')) return
    errors.push('console.error: ' + text)
  })

  await page.goto('/')

  // ① 不是白屏 ✓（`#root` 里真的渲染出了内容 ✓）
  const root = page.locator('#root')
  await expect(root).toBeVisible()
  await expect
    .poll(async () => ((await root.innerText()) || '').trim().length, { timeout: 10_000 })
    .toBeGreaterThan(50)

  // ② 顶栏显示的是**前端自己**的版本号 ✓
  await expect(page.getByText(FE_VERSION, { exact: false }).first()).toBeVisible()

  // ③ **零未捕获异常** ✓ —— 白屏类问题通常在 console 里只剩这一条 ✗
  expect(errors, '真实浏览器里出现未捕获错误 ✗：\n' + errors.join('\n')).toEqual([])
})

test('接口返回空数据时不白屏', async ({ page }) => {
  // 全部 stub 成"空" ✓ ⇒ 页面必须仍然可用 ✓（空态而不是崩溃 ✗）
  await page.goto('/')
  await expect(page.locator('#root')).toBeVisible()
  const text = (await page.locator('#root').innerText()).trim()
  expect(text.length).toBeGreaterThan(50)
  // 空态下不该出现错误标记 ✓（这里只钉"页面还在"✓，具体空态文案由 jsdom 测试负责 ✓）
  expect(text).not.toContain('Uncaught')
})
