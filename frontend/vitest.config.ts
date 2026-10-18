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
  },
})
