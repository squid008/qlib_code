// Vitest 配置（★ v2.9.17，2026-10-18 ✓ 前端测试基建）
//
// 为什么现在才有：本仓库前端此前**零测试** ✗ ⇒ 所有 UI 改动只能靠"人眼看 + 刷新"✓
//   ⇒ 2.8.9 那轮表格布局改了 **6 次**都"没变" ✗（其实是列的硬约束没搞对 ✓）。
//
// 约定：
//   · `environment: 'jsdom'` ⇒ 组件级测试可直接 `render()` ✓；
//   · 纯逻辑测试（`sftLogic` ✓）不需要 DOM ✓，但同环境跑着没坏处 ✓；
//   · **不引入全局 setup**（jest-dom 的 matchers 按需 import ✓）⇒ 少一层隐式魔法 ✓。
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // ★★ v2.9.21（2026-10-18 ✓）：`jsdom` → `node` ✗→✓ ——
    //   CI 上（Linux + Node 20）只要走 jsdom 环境就**在 400ms 内崩** ✗：
    //   `TypeError: Cannot read properties of undefined (reading 'bind')`，**0 个用例被收集** ✓，
    //   而同一份代码在本地 Windows 全绿 ✓（11/11 ✓）⇒ 是环境初始化的问题，不是测试的问题 ✓。
    //   ⇒ 现有用例（`sftLogic`）全是**纯函数** ✓，压根本不需要 DOM ✓ ⇒ 用 `node` 环境绕开 ✓。
    //   ⚠ 等要写**组件级**用例（render / 点击 / 表格列宽 ✓）时，**必须换回 jsdom** ✓ ——
    //     到时要么升 Node、要么给 jsdom 钉一个 CI 能跑的版本 ✓（届时它会明确报错，不会静默 ✗）。
    environment: 'node',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // 只跑一次就退出（CI 用 ✓）；本地想看 watch 就 `npx vitest`
    watch: false,
    // ★★ v2.9.20（2026-10-18 ✓）：**默认线程池在 GitHub 的 Linux runner 上起不来** ✗ ——
    //   实测 CI 报 `TypeError: Cannot read properties of undefined (reading 'bind')`
    //   且 **0 个用例被收集** ✓（本地 Windows 完全正常 ✓ ⇒ 是 tinypool 的 worker 绑定问题 ✗）。
    //   ⇒ 换成**进程池**（forks ✓）：每个测试文件一个子进程 ✓，不依赖 worker_threads ✓。
    //   代价：启动稍慢（本项目 11 条用例、~0.5s ✓ 可忽略 ✓）。
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },

    // ★ v2.10.23（2026-10-20 ✓）：**前端覆盖率采集** ✗→✓
    //
    // 为什么现在才加：前端已有 181 条用例 ✓，但**覆盖了哪些组件完全未知** ✗ ——
    //   这是整个工程里最后一个"黑箱"✓（后端有 61.83% 与门槛 61 ✓，前端什么都不知道 ✗）。
    //
    // ⚠ 本轮**只采集、先不设门槛** ✓（两步法，与后端当初一样 ✓）：
    //   先让 CI 跑一次拿到**真实数字** ✓，下一版再按"实测值向下取整"设 thresholds ✓ ——
    //   凭感觉设一个数会让闸门一上线就红 ✗（或者松到没有意义 ✗）。
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary'],
      // 只算 `src/` ✓；测试自身与 e2e 不算 ✓（否则自己给自己加分 ✗）
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/**/__tests__/**', 'src/main.tsx', 'src/vite-env.d.ts'],
      // thresholds: {}   ← 下一版按 CI 实测值填 ✓
    },
  },
})
