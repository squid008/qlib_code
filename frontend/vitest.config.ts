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
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // 只跑一次就退出（CI 用 ✓）；本地想看 watch 就 `npx vitest`
    watch: false,
  },
})
