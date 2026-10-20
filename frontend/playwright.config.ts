import { defineConfig, devices } from '@playwright/test'

/**
 * E2E 骨架（★ v2.10.5，2026-10-20 ✓）—— 三件事里的第 ③ 件 ✓。
 *
 * ## 为什么用**独立端口 5174**
 *
 * 开发时 5173 常被别人占着 ✓ ⇒ E2E 抢端口会**假红** ✗（`--strictPort` 直接启动失败 ✓）。
 * 用独立端口 + `reuseExistingServer: !CI` ✓：本地能复用、CI 一定新起 ✓。
 *
 * ## 为什么起 `dev` 而不是 `preview`
 *
 * `vite.config.ts` 里 **只有 `server.proxy` 有 `/api` 代理** ✓，`preview` 没有 ✗
 * ⇒ 起预览服务器时所有 `/api/*` 会 404 ✓（页面看起来"正常但空"✗，最难查 ✓）。
 *
 * ## 断言范围（诚实说明 ✓）
 *
 * 这一轮只做**冒烟**：真实浏览器里跑通整棵 React 树 ✓、**零未捕获异常** ✓、
 * 顶栏能显示版本号 ✓。**不做**"提交回测 → 等结果"那条链路 ✗ ——
 * 那需要真后端 + 真数据 + 分钟级等待 ✓，属下一轮的"全链路 E2E" ✓，不在这里假装 ✓。
 */
// ⚠⚠ 2026-10-20 实测教训（很重要 ✗→✓）：
//   第一版用 **5174** + `reuseExistingServer: !CI` ⇒ 本地跑时它**复用了 5174 上另一个应用** ✗
//   （截图里是"交易模拟器 / 用户名 / 口令 / v0.66.89"✓）⇒ 我测的根本**不是本项目** ✗，
//   而且还会"通过"✓ —— 这是 E2E 最危险的失败模式（**静默测错对象** ✗）。
//   ⇒ 两条硬规矩 ✓：① 端口挑冷门的 **5299** ✓；② **`reuseExistingServer: false` 永远** ✓
//   （配合 `--strictPort`：端口被占就明确失败 ✓，绝不悄悄接管别人的服务 ✗）。
const PORT = 5299
const BASE = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  // 冒烟测试**不该重试** ✗ —— 重试会掩盖"偶发崩溃"✓（这正是它要抓的东西 ✓）
  retries: 0,
  // 失败时留 trace ✓（线上 CI 只看日志很难还原 ✗）
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    // `--strictPort` ✓：端口被占就**明确失败** ✓，不要悄悄换端口再报"服务不健康"✗
    // ⚠ `--host 127.0.0.1` **不能省** ✗→✓：vite 默认绑 `localhost` ✓，在 Windows 上常先解析到
    //   **IPv6 `::1`** ✗，而 Playwright 轮询的是 `http://127.0.0.1:PORT` ✓ ⇒ 实测**干等 90s 超时** ✗
    //   （服务其实起来了 ✓，只是监听在另一个协议栈上 ✓，日志里还看不出来 ✓）。
    command: `npm run dev -- --port ${PORT} --strictPort --host 127.0.0.1`,
    url: BASE,
    // ★ 永远 false ✓（见文件头教训：复用到别人的服务会**静默测错对象** ✗）
    reuseExistingServer: false,
    timeout: 90_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
