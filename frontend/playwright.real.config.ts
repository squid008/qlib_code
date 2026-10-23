import { defineConfig, devices } from '@playwright/test'

/**
 * **真后端 E2E**（★ v2.11.54）—— 和 `playwright.config.ts`（全 mock）**并存** ✓，互不影响。
 *
 * ## 为什么单独一个 config 而不是加个 env 开关
 * 现有那套（`e2e/*.spec.ts`）的精髓是 **`page.route('**\/api/*')` 全拦** ✓ ——
 * 它测"前端在 API 正常时能渲染"✓，快、无需 python ✓。
 * 真后端这套需要 **uvicorn + qlib + 数据** ✗ ⇒ 塞进同一个 config 会让 PR CI 变慢变脆 ✗。
 * ⇒ 两条独立命令：
 * ```
 * npm run test:e2e        # mock，PR CI 跑（秒级）
 * npm run test:e2e:real   # 真后端，夜间 job / 本机跑（分钟级）
 * ```
 *
 * ## 后端复用（`reuseExistingServer: true`）为什么这次可以
 * 现有 config 里明令**永远 false** ✓ —— 因为 5174 那次**接管了别人的应用**还"通过"了 ✗
 * （E2E 最危险的失败模式：静默测错对象 ✓）。
 * 这里后端允许复用，但**安全性由 spec 兜住** ✓：第一条断言就是
 * `GET /api/version` 必须是**本项目**的版本 ✓ + `/api/metrics?format=text` 必须含
 * `qlib_uptime_seconds` ✓ —— 换了别的服务，这两条立刻红 ✓。
 * （前端仍 `reuseExistingServer: false` + 独立端口 5298 ✓，保持原有硬规矩 ✓。）
 */
const PORT = 5298
const BASE = `http://127.0.0.1:${PORT}`
const BE = 'http://127.0.0.1:8001'

// Windows 上 `python`；类 Unix 上 `python3` ✓（CI 用 setup-python 装的都叫 python3 ✓）
const PY = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')

export default defineConfig({
  testDir: './e2e-real',
  // 真后端首个请求可能触发 qlib init（几秒）+ 前端冷启动 ⇒ 给足时间 ✓
  timeout: 90_000,
  expect: { timeout: 20_000 },
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      // ⚠ `--host 127.0.0.1` 不能省 ✗：vite 代理目标是 127.0.0.1:8001 ✓，
      //   uvicorn 若绑别的地址/协议栈会"服务起来了但代理打不通"✓（实测踩过 ✓）。
      command: `${PY} -m uvicorn app.main:app --host 127.0.0.1 --port 8001`,
      cwd: '../backend',
      url: `${BE}/api/version`,
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command: `npm run dev -- --port ${PORT} --strictPort --host 127.0.0.1`,
      url: BASE,
      reuseExistingServer: false,
      timeout: 90_000,
    },
  ],
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
