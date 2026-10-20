/**
 * vitest 全局前置（★ v2.10.28，2026-10-20 ✓）。
 *
 * ## 为什么需要它（一条被传了两年的"事实" ✗→✓）
 *
 * `metricCards.test.tsx` 的注释写着「CI 上**只要走 jsdom 就崩**」✗ ⇒ 之后所有组件测试
 * 都退成 `renderToStaticMarkup` ✓ ⇒ **点击类断言一个都写不了** ✗。
 *
 * 本轮追到底：真正抛的不是 jsdom，而是
 *
 *     ReferenceError: ResizeObserver is not defined
 *
 * —— 它来自 recharts 的 `<ResponsiveContainer>` ✓（用 `ResizeObserver` 监听容器尺寸 ✓）。
 * jsdom **不实现** `ResizeObserver` ✗（也不实现 `matchMedia` / `IntersectionObserver` ✓）。
 * ⇒ 这不是"环境不可用" ✗，而是"**少一个 polyfill**"✓：补上就能跑 ✓。
 *
 * ## 规矩
 *
 * · 这里只放**测试环境缺失的浏览器 API 桩** ✓，不放业务逻辑 ✓；
 * · 桩要**最小**（`observe`/`unobserve`/`disconnect` 空实现即可 ✓）——
 *   真实布局行为在 jsdom 里本来就没有 ✓，别假装有 ✗；
 * · 断言**不要依赖尺寸** ✗（宽高恒 0 ⇒ 图表内部不渲染 ✓，能测的是它外层的逻辑 ✓）。
 */

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!('ResizeObserver' in globalThis)) {
  // @ts-expect-error —— 用最小桩替换，形状与 DOM 的构造函数一致即可
  globalThis.ResizeObserver = ResizeObserverStub
}

// `window.matchMedia` 有些组件在用（暗色主题判断 ✓），jsdom 同样没有
if (typeof window !== 'undefined' && !window.matchMedia) {
  // @ts-expect-error —— 只实现 `matches` 与事件三个空函数
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}
